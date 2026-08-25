import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.acceptance import _context_optimization_metrics
from dynosai_flow.mcp import (
    ElicitationExchange,
    MCPServer,
    _resolve_exchange,
    _tool_result_payload,
)
from dynosai_flow.version import __version__


class DynosAI124StabilizationOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.root=Path(self.td.name)/"project"; self.root.mkdir(parents=True)

    def test_version_is_0124(self):
        self.assertEqual(__version__,"0.13.0")

    def test_codex_transport_uses_structured_content_once_with_small_text_envelope(self):
        value={
            "work":{"id":"DYN-0001","state":"implementing"},
            "contract":{"operation":"implement_tasks","payload":"x"*12000},
            "response_optimization":{"mode":"delta","estimated_bytes_omitted":123},
        }
        payload=_tool_result_payload(value,provider="codex",tool_name="dynosai_get_next_action")
        text=payload["content"][0]["text"]
        summary=json.loads(text)
        self.assertEqual(summary["authoritative"],"structuredContent")
        self.assertEqual(summary["work_id"],"DYN-0001")
        self.assertEqual(summary["state"],"implementing")
        self.assertEqual(payload["structuredContent"]["contract"]["payload"],"x"*12000)
        opt=payload["structuredContent"]["response_optimization"]
        self.assertEqual(opt["transport_mode"],"structured_primary")
        self.assertGreater(opt["transport_text_bytes_omitted"],11000)
        self.assertLess(len(text),500)

    def test_generic_transport_keeps_full_text_compatibility(self):
        value={"state":"implementing","payload":"abc"*100}
        payload=_tool_result_payload(value,provider="generic",tool_name="dynosai_get_next_action")
        self.assertEqual(json.loads(payload["content"][0]["text"]),value)
        self.assertEqual(payload["structuredContent"]["response_optimization"]["transport_mode"],"full_text_compatibility")
        self.assertEqual(payload["structuredContent"]["response_optimization"]["transport_text_bytes_omitted"],0)

    def test_plan_gate_execute_true_collapses_ready_to_implementation(self):
        class FakeDB:
            def __init__(self): self.audits=[]
            def audit(self,*args,**kwargs): self.audits.append((args,kwargs))
        class FakeEngine:
            def __init__(self): self.db=FakeDB()
        class FakeApp:
            def __init__(self): self.calls=[]
            def resolve_interaction(self,interaction_id,action,content):
                return {"work":{"id":"DYN-0001","state":"ready"}}
            def next_action(self,provider,wid,execute):
                self.calls.append((provider,wid,execute))
                return {
                    "work":{"id":wid,"state":"implementing" if execute else "ready"},
                    "next":"Implement wave" if execute else "Prepare implementation",
                    "contract":{"operation":"implement_tasks"} if execute else {"operation":"prepare_implementation"},
                    "human_interaction":None,
                    "context_checkpoint":{"ref":"CTX-1","estimated_bytes_avoided":0},
                    "agent_config":{"provider":provider,"activity":"implementation","mode":"managed"},
                    "model_control":{"route":{"model":"gpt-5.6-luna"}},
                }
        server=MCPServer.__new__(MCPServer)
        server.provider="codex"; server.app=FakeApp(); server.engine=FakeEngine(); server._managed_response_cache={}; server.provider_session={"id":"P1"}; server.session=None
        exchange=ElicitationExchange(
            "req-1",
            {"id":"ELIC-PLAN","work_id":"DYN-0001","kind":"gate_approval","gate":"plan","status":"pending"},
            {"work":{"id":"DYN-0001","state":"plan_review"}},
            True,
        )
        with patch.dict(os.environ,{"DYNOSAI_MANAGED_AGENT":"1","DYNOSAI_COMPACT_RESPONSES":"1"},clear=False):
            result=_resolve_exchange(server,exchange,"accept",{"decision":"approve","comments":"ok"})
        self.assertEqual(server.app.calls,[('codex','DYN-0001',True)])
        self.assertEqual(result["work"]["state"],"implementing")
        self.assertTrue(result["ready_to_implementation_collapsed"])
        self.assertTrue(any(args and args[0]=="ReadyImplementationCollapsed" for args,_ in server.engine.db.audits))

    def test_plan_gate_without_execute_does_not_cross_ready_boundary(self):
        class FakeDB:
            def audit(self,*args,**kwargs): pass
        class FakeEngine:
            db=FakeDB()
        class FakeApp:
            def __init__(self): self.calls=[]
            def resolve_interaction(self,*args,**kwargs): return {"work":{"id":"DYN-1","state":"ready"}}
            def next_action(self,provider,wid,execute):
                self.calls.append(execute)
                return {"work":{"id":wid,"state":"ready"},"next":"Prepare","contract":{"operation":"prepare_implementation"},"human_interaction":None,"context_checkpoint":None,"agent_config":None,"model_control":None}
        server=MCPServer.__new__(MCPServer)
        server.provider="codex"; server.app=FakeApp(); server.engine=FakeEngine(); server._managed_response_cache={}; server.provider_session={"id":"P1"}; server.session=None
        exchange=ElicitationExchange("r",{"id":"E","work_id":"DYN-1","kind":"gate_approval","gate":"plan"},{},False)
        result=_resolve_exchange(server,exchange,"accept",{"decision":"approve"})
        self.assertEqual(server.app.calls,[False])
        self.assertEqual(result["work"]["state"],"ready")
        self.assertNotIn("ready_to_implementation_collapsed",result)

    def test_acceptance_metrics_report_transport_and_ready_collapse(self):
        dyn=self.root/".dynosai"; dyn.mkdir();
        import sqlite3
        db=dyn/"knowledge.db"; con=sqlite3.connect(db)
        con.execute("CREATE TABLE audit(id INTEGER PRIMARY KEY,event_type TEXT,entity_id TEXT,payload TEXT)")
        con.execute("INSERT INTO audit(event_type,entity_id,payload) VALUES('ReadyImplementationCollapsed','DYN-1','{}')")
        con.commit(); con.close()
        trace=self.root/"mcp.jsonl"
        trace.write_text(json.dumps({
            "event":"mcp_tool_end","tool":"dynosai_get_next_action","status":"ok","response_bytes":2000,
            "response_optimization":{"transport_mode":"structured_primary","transport_text_bytes_omitted":9000}
        })+"\n",encoding="utf-8")
        metrics=_context_optimization_metrics(trace,self.root,None)
        self.assertEqual(metrics["transport_text_bytes_omitted"],9000)
        self.assertEqual(metrics["structured_primary_results"],1)
        self.assertEqual(metrics["ready_implementation_collapses"],1)


if __name__ == "__main__":
    unittest.main()
