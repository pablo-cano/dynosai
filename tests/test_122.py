import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.context_control import ContextCheckpointStore
from dynosai_flow.engine import DynosAI
from dynosai_flow.mcp import MCPServer, ElicitationExchange, _resolve_exchange
from dynosai_flow.token_usage import TokenUsageRecorder
from dynosai_flow.util import json_dumps, utc_now
from dynosai_flow.version import __version__


class DynosAI122EfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.tmp=Path(self.td.name)

    def _engine_with_task(self):
        root=self.tmp/"project"; root.mkdir(parents=True,exist_ok=True)
        e=DynosAI(root); e.db.initialize(); now=utc_now()
        e.db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,worktree,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001","Fibonacci","Build fibonacci CLI","feature","implementing",0,1,100,str(root),now,now),
        )
        e.db.execute(
            "INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,depends_on,evidence_required,validation_commands,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("T01","DYN-0001","Implement core","core","ready",json_dumps(["R1"]),json_dumps(["A1"]),json_dumps(["fibonacci/__init__.py"]),"[]",json_dumps(["git_diff"]),"[]",now,now),
        )
        e.db.execute(
            "INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,depends_on,evidence_required,validation_commands,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("T02","DYN-0001","Add tests","tests","ready",json_dumps(["R1"]),json_dumps(["A2"]),json_dumps(["tests/test_cli.py"]),json_dumps(["T01"]),json_dumps(["test_result"]),json_dumps(["unit"]),now,now),
        )
        return e,root

    def test_version_is_0122(self):
        self.assertEqual(__version__,"1.0.0rc6")

    def test_context_preview_manifest_is_reused_until_task_file_changes(self):
        e,root=self._engine_with_task()
        calls=[]
        def fake_preview(work,max_tokens=3000,task_id=None,include_task_contract=True):
            calls.append((task_id,max_tokens,include_task_contract))
            return {"work_id":work["id"],"task_id":task_id,"purpose":"implementation","context_mode":"repository_delta_only" if not include_task_contract else "full_task_context","max_tokens":max_tokens,"estimated_tokens":12,"items":[],"impact":{"files":[],"tests":[],"rules":[]}}
        e.retrieval.context_preview=fake_preview
        first=e.context_preview("DYN-0001",1000,"T01",include_task_contract=False)
        second=e.context_preview("DYN-0001",1000,"T01",include_task_contract=False)
        self.assertEqual(first["id"],second["id"])
        self.assertEqual(len(calls),1)
        self.assertEqual(e.db.one("SELECT COUNT(*) n FROM context_manifests")["n"],1)
        path=root/"fibonacci"/"__init__.py"; path.parent.mkdir(parents=True,exist_ok=True); path.write_text("x=1\n",encoding="utf-8")
        # mtime_ns/size are part of the fingerprint; creating the approved file invalidates.
        third=e.context_preview("DYN-0001",1000,"T01",include_task_contract=False)
        self.assertNotEqual(first["id"],third["id"])
        self.assertEqual(len(calls),2)

    def test_implementation_contract_requests_repository_delta_not_duplicate_task_contract(self):
        e,_=self._engine_with_task()
        seen={}
        def fake_context(work_id,max_tokens=3000,task_id=None,include_task_contract=True):
            seen.update({"max_tokens":max_tokens,"task_id":task_id,"include":include_task_contract})
            return {"id":"CTX-1","estimated_tokens":0,"items":[],"impact":{"files":[],"tests":[],"rules":[]},"context_mode":"repository_delta_only"}
        e.context_preview=fake_context
        contract=e.action_contract("DYN-0001")
        self.assertEqual(contract["operation"],"implement_tasks")
        self.assertFalse(seen["include"])
        self.assertEqual(seen["max_tokens"],1000)
        self.assertEqual(contract["context"]["context_mode"],"repository_delta_only")
        self.assertEqual(contract["task_queue"]["current_task"]["id"],"T01")
        self.assertEqual(contract["task_queue"]["execution_wave"]["task_ids"],["T01","T02"])
        self.assertIn("tests/test_cli.py",contract["task_queue"]["execution_wave"]["files"])
        self.assertEqual(contract["task_queue"]["blocked_tasks"],[])
        self.assertTrue(any("whole wave atomically" in x or "Register the whole wave atomically" in x for x in contract["rules"]))

    def test_action_snapshot_delta_reuses_stable_next_and_tool_surface(self):
        server=MCPServer.__new__(MCPServer)
        server.provider_session={"id":"S1"}; server.session=None; server._managed_response_cache={}
        payload={
            "work":{"id":"DYN-1","state":"implementing"},
            "next":"Implement current task using the authoritative queue and avoid repeating unchanged workflow polling. "*4,
            "contract":{"operation":"implement_tasks","work_id":"DYN-1","task_queue":{"current_task":{"id":"T1"}}},
            "context_checkpoint":{"ref":"CTX-1","estimated_bytes_avoided":1000},
            "agent_config":{"provider":"codex","activity":"implementation","mode":"managed"},
            "model_control":{"route":{"model":"gpt-5.6-luna"}},
            "tool_surface":{"activity":"implementation","tool_count":9,"tools":["dynosai_get_next_action","dynosai_register_result","dynosai_request_scope_extension","dynosai_context_preview","dynosai_checkpoint","dynosai_read","dynosai_git_status","dynosai_git_diff","dynosai_run_validation"]},
            "transition":None,"human_interaction":None,
        }
        with patch.dict(os.environ,{"DYNOSAI_MANAGED_AGENT":"1","DYNOSAI_COMPACT_RESPONSES":"1"},clear=False):
            first=server._compact_managed_result("dynosai_get_next_action",payload)
            second=server._compact_managed_result("dynosai_get_next_action",payload)
        self.assertFalse(first["action_snapshot"]["unchanged"])
        self.assertTrue(second["action_snapshot"]["unchanged"])
        self.assertTrue(second["next"]["unchanged"])
        self.assertTrue(second["tool_surface"]["unchanged"])
        self.assertTrue(second["response_optimization"]["action_snapshot_reused"])
        self.assertGreater(second["response_optimization"]["action_snapshot_bytes_omitted"],0)

    def test_structural_estimator_uses_prompt_and_actual_mcp_wire_bytes(self):
        root=self.tmp/"usage"; root.mkdir()
        rec=TokenUsageRecorder(root,"codex","P1",model="gpt-5.6-luna",scenario="fibonacci")
        rec.add_estimate(input_tokens=1000,event="managed_prompt_estimate")
        trace=root/"mcp-activity.jsonl"
        trace.write_text("\n".join([
            json.dumps({"event":"mcp_tool_end","response_bytes":400}),
            json.dumps({"event":"mcp_tool_end","response_bytes":800}),
        ])+"\n",encoding="utf-8")
        current=rec.current(); structural=current["structural_live_estimate"]
        # prompt 1000 is re-read across three provider steps, early 100-token
        # response twice and later 200-token response once => 3400.
        self.assertEqual(structural["mcp_calls_observed"],2)
        self.assertEqual(structural["mcp_response_bytes"],1200)
        self.assertEqual(structural["context_read_tokens"],3400)
        self.assertFalse(structural["routing_authoritative"])
        rec.observe_exact({"input_tokens":5000,"cached_input_tokens":4000,"output_tokens":100},source="codex")
        exact=rec.current()
        self.assertEqual(exact["structural_estimate_error"]["exact"],5000)
        self.assertEqual(exact["structural_estimate_error"]["estimated"],3400)


    def test_post_gate_resumed_action_is_compacted_too(self):
        class FakeApp:
            def resolve_interaction(self,interaction_id,action,content):
                return {"work":{"id":"DYN-1","state":"implementing"}}
            def next_action(self,provider,wid,execute):
                return {
                    "work":{"id":"DYN-1","state":"implementing"},
                    "next":"Implement current task",
                    "contract":{"operation":"implement_tasks","work_id":"DYN-1"},
                    "context_checkpoint":{"ref":"CTX-1","estimated_bytes_avoided":700},
                    "agent_config":{"provider":"codex","activity":"implementation","mode":"managed"},
                    "model_control":{"route":{"model":"gpt-5.6-luna"}},
                    "transition":None,"human_interaction":None,
                }
        server=MCPServer.__new__(MCPServer)
        server.app=FakeApp(); server.provider="codex"; server.provider_session={"id":"S1"}; server.session=None; server._managed_response_cache={}
        exchange=ElicitationExchange(7,{"id":"ELIC-1","work_id":"DYN-1"},{"work":{"id":"DYN-1"}})
        with patch.dict(os.environ,{"DYNOSAI_MANAGED_AGENT":"1","DYNOSAI_COMPACT_RESPONSES":"1"},clear=False):
            refreshed=_resolve_exchange(server,exchange,"accept",{"decision":"approve"})
        self.assertIn("response_optimization",refreshed)
        self.assertEqual(refreshed["response_optimization"]["checkpoint_bytes_omitted"],700)
        self.assertEqual(refreshed["tool_surface"]["activity"],"implementation")
        self.assertEqual(refreshed["elicitation"]["id"],"ELIC-1")

    def test_checkpoint_refresh_after_task_progress_is_reusable(self):
        e,_=self._engine_with_task()
        # A direct capture simulates the task-result hook and proves that a second
        # capture with identical authoritative state reuses the same checkpoint ref.
        store=ContextCheckpointStore(e.root)
        first=store.capture(e,"DYN-0001",label="task-progress",reason="task_result")
        second=store.capture(e,"DYN-0001",label="task-progress",reason="task_result")
        self.assertEqual(first["ref"],second["ref"])
        self.assertTrue(second.get("reused"))


if __name__=="__main__":
    unittest.main()
