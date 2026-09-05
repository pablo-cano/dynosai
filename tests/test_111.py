import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from dynosai_flow.acceptance import _context_optimization_metrics
from dynosai_flow.db import Database
from dynosai_flow.mcp import ToolInputError, _validate_tool_arguments, advertised_tools, MCPServer
from dynosai_flow.mcp_protocol import PROTOCOL_2025_11
from dynosai_flow.engine import DynosAI
from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.managed_runtime import ManagedProviderRuntime
from dynosai_flow.model_control import ModelControlPlane, classify_validation_failure, retry_policy_for
from dynosai_flow.token_usage import runtime_metrics


class DynosAI111HardeningTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.tmp=Path(self.td.name)

    def test_read_schema_is_explicit_oneof_and_scope_action_is_enum(self):
        tools={x["name"]:x for x in advertised_tools()}
        read=tools["dynosai_read"]["inputSchema"]
        self.assertIn("oneOf",read)
        batch=next(x for x in read["oneOf"] if "requests" in (x.get("properties") or {}))
        self.assertIn("oneOf",batch["properties"]["requests"]["items"])
        scope=tools["dynosai_request_scope_extension"]["inputSchema"]["properties"]["action"]
        self.assertEqual(scope["enum"],["read","modify","create","delete","test"])

    def test_invalid_scope_action_is_repairable_input_error(self):
        with self.assertRaises(ToolInputError):
            _validate_tool_arguments("dynosai_request_scope_extension",{"action":"add","path":"tests/test_cli.py"})

    def test_scope_add_is_mcp_rejection_never_server_failure(self):
        root=self.tmp/"scope"; engine=DynosAI(root); engine.initialize("Scope","python","python -m unittest","all")
        server=MCPServer(root)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":PROTOCOL_2025_11}})
        response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_request_scope_extension","arguments":{"work_id":"DYN-X","path":"tests/test_cli.py","action":"add","reason":"test"}}})
        self.assertFalse(response["result"]["isError"],response)
        self.assertEqual(response["result"]["structuredContent"]["error_type"],"tool_input_validation")
        self.assertEqual(engine.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolRejected'")["n"],1)
        self.assertEqual(engine.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolFailed'")["n"],0)

    def test_no_tests_yet_is_not_a_model_failure(self):
        kind=classify_validation_failure("Ran 0 tests\nNO TESTS RAN",exit_code=5)
        self.assertEqual(kind,"validation_precondition")
        self.assertFalse(retry_policy_for(kind,1)["counts_as_model_failure"])

    def test_validation_rows_deduplicate_and_ignore_preconditions(self):
        root=self.tmp/"p"; db=Database(root); db.initialize(); now="2026-08-24T00:00:00+00:00"
        db.execute("INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,0,1,0,?,?)",("DYN-1","x","x","feature","implementing",now,now))
        for _ in range(2):
            db.execute("INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",("DYN-1","pytest","failed",5,"Ran 0 tests\nNO TESTS RAN",now))
        sig=ModelControlPlane(root)._work_signals("DYN-1")
        self.assertEqual(sig["consecutive_validation_failures"],0)
        self.assertEqual(sig["validation_precondition_observations"],2)
        db.execute("DELETE FROM validations WHERE work_id=?",("DYN-1",))
        for _ in range(2):
            db.execute("INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",("DYN-1","pytest","failed",1,"AssertionError test failed",now))
        sig=ModelControlPlane(root)._work_signals("DYN-1")
        self.assertEqual(sig["consecutive_validation_failures"],1)
        self.assertEqual(sig["deduplicated_validation_observations"],1)

    def test_process_fallback_can_never_trigger_costlier_route(self):
        ctl=ModelControlPlane(self.tmp/"control")
        d=ctl.decide("codex","validation",usage={"context_read_tokens":2_000_000,"generated_tokens":50_000},at_provider_boundary=False)
        self.assertEqual(d.budget.measurement_scope,"process_fallback")
        self.assertEqual(d.budget.status,"unallocatable")
        self.assertFalse(d.escalation["recommended"])
        self.assertIn("telemetry_not_comparable_no_escalation",d.reasons)

    def test_route_lifecycle_only_counts_verified_application(self):
        root=self.tmp/"routes"; ctl=ModelControlPlane(root)
        usage={"phase_usage":[{"phase":"validation","delta_context_read_tokens":20_000,"delta_generated_tokens":2_000}]}
        rec=ctl.decide("codex","validation",work_id="DYN-1",usage=usage,extra_signals={"tool_loop_stagnation":2},at_provider_boundary=False)
        self.assertTrue(rec.escalation["recommended"])
        self.assertIsNotNone(rec.pending_transition)
        self.assertIsNone(rec.route_requested)
        req=ctl.decide("codex","validation",work_id="DYN-1",usage={"phase_usage":[]},at_provider_boundary=True)
        self.assertIsNotNone(req.route_requested)
        self.assertIsNone(req.route_applied)
        ctl.observe_provider_route("codex","DYN-1",req.route_requested,verified=True,activity="validation")
        events=[json.loads(x) for x in (root/".dynosai/runtime/model-control.jsonl").read_text().splitlines()]
        self.assertTrue(any(x.get("event")=="route_applied" for x in events))
        self.assertTrue(any(x.get("event")=="route_verified" for x in events))

    def test_managed_codex_mcp_receives_acceptance_process_traces(self):
        project=self.tmp/"managed"; project.mkdir(); AgentConfiguration(project).init()
        trace=self.tmp/"mcp-activity.jsonl"; mirror=self.tmp/"mirror.jsonl"
        runtime=ManagedProviderRuntime(project).prepare("codex",activity="discovery",extra_env={
            "DYNOSAI_ACCEPTANCE_PROCESS_TRACE":str(trace),
            "DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR":str(mirror),
        })
        config=(Path(runtime.root)/"home"/"config.toml").read_text(encoding="utf-8")
        parsed=tomllib.loads(config)
        env=parsed["mcp_servers"]["dynosai"]["env"]
        self.assertEqual(env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE"],str(trace))
        self.assertEqual(env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR"],str(mirror))

    def test_runtime_metrics_fall_back_to_codex_app_server(self):
        stream=self.tmp/"codex-app-server.jsonl"
        rows=[
            {"at":"2026-08-24T10:00:00+00:00","payload":{"method":"item/started","params":{"item":{"type":"mcpToolCall","server":"dynosai","id":"c1"}}}},
            {"at":"2026-08-24T10:00:01+00:00","payload":{"method":"item/completed","params":{"item":{"type":"mcpToolCall","server":"dynosai","id":"c1"}}}},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n")
        r=runtime_metrics("codex",10,mcp_path=self.tmp/"missing.jsonl",provider_stream_path=stream)
        self.assertEqual(r["mcp_calls"],1)
        self.assertEqual(r["dynosai_mcp_seconds"],1.0)
        self.assertEqual(r["mcp_metrics_source"],"provider_stream_fallback")

    def test_optimization_falls_back_to_provider_structured_result(self):
        stream=self.tmp/"codex-app-server.jsonl"
        payload={"at":"2026-08-24T10:00:00+00:00","payload":{"method":"item/completed","params":{"item":{"type":"mcpToolCall","server":"dynosai","result":{"structuredContent":{"response_optimization":{"mode":"delta","estimated_bytes_omitted":12009}}}}}}}
        stream.write_text(json.dumps(payload)+"\n")
        r=_context_optimization_metrics(self.tmp/"missing.jsonl",self.tmp,stream)
        self.assertEqual(r["response_bytes_omitted"],12009)
        self.assertEqual(r["compacted_response_count"],1)
        self.assertEqual(r["optimization_metrics_source"],"provider_stream_fallback")


if __name__=="__main__": unittest.main()
