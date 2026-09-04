import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.engine import DynosAI
from dynosai_flow.acceptance import _context_optimization_metrics
from dynosai_flow.mcp import MCPServer
from dynosai_flow.util import json_dumps, utc_now
from dynosai_flow.version import __version__


class DynosAI123ExecutionWaveTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.root=Path(self.td.name)/"project"; self.root.mkdir(parents=True)

    def _engine(self):
        e=DynosAI(self.root); e.db.initialize(); now=utc_now()
        e.db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,worktree,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001","Fibonacci","Build fibonacci CLI","feature","implementing",0,1,100,str(self.root),now,now),
        )
        rows=[
            ("T01","Core","ready",["fibonacci/__init__.py","fibonacci/__main__.py"],[],["git_diff"],[]),
            ("T02","Tests","ready",["tests/test_fibonacci.py"],["T01"],["test_result"],["unit"]),
        ]
        for tid,title,state,files,deps,evidence,commands in rows:
            e.db.execute(
                "INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,depends_on,evidence_required,validation_commands,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid,"DYN-0001",title,title,state,json_dumps(["R1"]),json_dumps(["A1"]),json_dumps(files),json_dumps(deps),json_dumps(evidence),json_dumps(commands),now,now),
            )
        return e

    def test_version_is_0123(self):
        self.assertEqual(__version__,"1.0.0rc5")

    def test_dependency_chain_is_exposed_as_one_execution_wave(self):
        e=self._engine()
        queue=e._task_queue_contract("DYN-0001")
        wave=queue["execution_wave"]
        self.assertEqual(wave["task_ids"],["T01","T02"])
        self.assertTrue(wave["atomic_execution_allowed"])
        self.assertEqual(wave["registration_order"],["T01","T02"])
        self.assertTrue(wave["validation_required"])
        self.assertEqual(wave["validation_after"],["T01","T02"])
        self.assertEqual(wave["tasks"][1]["execution_status"],"ready_in_wave")
        self.assertTrue(wave["tasks"][1]["dependencies_satisfied_in_wave"])
        self.assertFalse(wave["tasks"][1]["dependencies_satisfied_at_wave_start"])
        self.assertEqual(wave["expected_orchestration_round_trips_saved"],1)
        self.assertEqual(queue["blocked_tasks"],[])
        self.assertIn("tests/test_fibonacci.py",wave["files"])

    def test_independent_ready_task_is_not_pulled_into_wave(self):
        e=self._engine(); now=utc_now()
        e.db.execute(
            "INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,depends_on,evidence_required,validation_commands,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("T03","DYN-0001","Docs","Docs","ready",json_dumps(["R1"]),json_dumps(["A3"]),json_dumps(["README.md"]),"[]",json_dumps(["git_diff"]),"[]",now,now),
        )
        wave=e._task_queue_contract("DYN-0001")["execution_wave"]
        self.assertEqual(wave["task_ids"],["T01","T02"])
        self.assertNotIn("T03",wave["task_ids"])

    def test_wave_is_stable_and_audited_once_across_polling(self):
        e=self._engine()
        first=e._task_queue_contract("DYN-0001")["execution_wave"]
        second=e._task_queue_contract("DYN-0001")["execution_wave"]
        self.assertEqual(first["id"],second["id"])
        n=e.db.one("SELECT COUNT(*) n FROM audit WHERE event_type='ExecutionWavePlanned' AND entity_id=?",(first["id"],))["n"]
        self.assertEqual(n,1)

    def test_action_contract_tells_agent_not_to_poll_between_wave_tasks(self):
        e=self._engine()
        e.context_preview=lambda *a,**k:{"id":"CTX-1","estimated_tokens":0,"items":[],"impact":{"files":[],"tests":[],"rules":[]},"context_mode":"repository_delta_only"}
        contract=e.action_contract("DYN-0001")
        wave=contract["task_queue"]["execution_wave"]
        self.assertEqual(wave["task_ids"],["T01","T02"])
        text=" ".join(contract["rules"])
        self.assertIn("Do not register T01 alone",text)
        self.assertIn("run dynosai_run_validation once",text)



    def test_acceptance_metrics_count_execution_waves_and_provider_inferences(self):
        e=self._engine()
        wave=e._task_queue_contract("DYN-0001")["execution_wave"]
        e.db.audit("ExecutionWaveAutoAdvanced","DYN-0001",{"remaining_tasks":0})
        rollout=self.root/".dynosai"/"runtime"/"managed-agents"/"codex"/"home"/"sessions"/"2026"/"08"/"25"/"rollout-test.jsonl"
        rollout.parent.mkdir(parents=True,exist_ok=True)
        rollout.write_text("\n".join([
            json.dumps({"type":"event_msg","payload":{"type":"token_count"}}),
            json.dumps({"type":"event_msg","payload":{"type":"token_count"}}),
            json.dumps({"type":"event_msg","payload":{"type":"patch_apply_end","success":True}}),
        ])+"\n",encoding="utf-8")
        trace=self.root/"mcp.jsonl"; trace.write_text("",encoding="utf-8")
        metrics=_context_optimization_metrics(trace,self.root,None)
        self.assertEqual(metrics["execution_waves"],1)
        self.assertEqual(metrics["execution_wave_tasks"],[2])
        self.assertEqual(metrics["execution_wave_expected_round_trips_saved"],1)
        self.assertEqual(metrics["execution_wave_auto_advances"],1)
        self.assertEqual(metrics["provider_inference_chunks"],2)
        self.assertEqual(metrics["native_file_change_actions"],1)

    def test_final_wave_result_auto_advances_to_code_review_gate(self):
        class FakeDB:
            def __init__(self): self.events=[]
            def audit(self,*args,**kwargs): self.events.append((args,kwargs))
        class FakeEngine:
            def __init__(self,root): self.root=root; self.db=FakeDB()
            def register_result(self,*args,**kwargs):
                return {"run_id":"RUN-1","work_id":"DYN-0001","status":"verified","remaining_tasks":0,"validation":{"passed":True}}
        class FakeApp:
            def next_action(self,provider,wid,execute):
                self.called=(provider,wid,execute)
                return {
                    "work":{"id":wid,"state":"code_review"},
                    "next":"Await code approval",
                    "contract":None,
                    "context_checkpoint":{"ref":"CTX-CODE","estimated_bytes_avoided":0},
                    "agent_config":{"provider":provider,"activity":"code_review","mode":"managed"},
                    "model_control":{"route":{"model":"gpt-5.6-luna"}},
                    "transition":{"from_state":"implementing","to_state":"code_review"},
                    "human_interaction":{"id":"ELIC-CODE","work_id":wid,"kind":"gate_approval","gate":"code","status":"pending"},
                }
        server=MCPServer.__new__(MCPServer)
        server.engine=FakeEngine(self.root); server.app=FakeApp(); server.provider="codex"
        server.session={"work_id":"DYN-0001","run_id":"RUN-1","id":"S1"}; server.provider_session={"id":"P1","work_id":"DYN-0001"}; server._managed_response_cache={}
        with patch('dynosai_flow.mcp.ContextCheckpointStore.capture',return_value={"ref":"CTX-TASK","estimated_bytes_avoided":0}):
            result=server.call_tool("dynosai_register_result",{
                "work_id":"DYN-0001","summary":"wave complete","files_changed":["fibonacci/__init__.py","tests/test_fibonacci.py"],
                "tests":[],"completed_tasks":["T01","T02"],
            })
        self.assertTrue(result["execution_wave_auto_advanced"])
        self.assertEqual(result["work"]["state"],"code_review")
        self.assertEqual(result["human_interaction"]["gate"],"code")
        self.assertEqual(server.app.called,("codex","DYN-0001",True))
        self.assertTrue(any(args and args[0]=="ExecutionWaveAutoAdvanced" for args,_ in server.engine.db.events))

    def test_execution_wave_limits_are_conservative(self):
        e=self._engine(); now=utc_now()
        e.db.execute(
            "INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,depends_on,evidence_required,validation_commands,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("T03","DYN-0001","More","More","ready",json_dumps(["R"]),json_dumps(["A"]),json_dumps(["x.py"]),json_dumps(["T02"]),json_dumps(["git_diff"]),"[]",now,now),
        )
        with patch.dict(os.environ,{"DYNOSAI_MAX_EXECUTION_WAVE_TASKS":"2"},clear=False):
            wave=e._task_queue_contract("DYN-0001")["execution_wave"]
        self.assertEqual(wave["task_ids"],["T01","T02"])


if __name__ == '__main__':
    unittest.main()
