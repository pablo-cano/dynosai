from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.cli import parser
from dynosai_flow.debug import DebugE2ERunner, SCENARIOS
from dynosai_flow.cli import demo_spec, demo_plan
from unittest.mock import patch


class DebugE2ETests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="dynosai-debug-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp,ignore_errors=True))

    def test_cli_debug_e2e_contract(self):
        args=parser().parse_args(["debug","e2e","--agent","cursor","--scenario","fibonacci","--output",str(self.tmp/"result.zip")])
        self.assertEqual(args.command,"debug")
        self.assertEqual(args.debug_command,"e2e")
        self.assertEqual(args.agent,"cursor")
        self.assertEqual(args.scenario,"fibonacci")

    def test_debug_prompts_enforce_human_gates(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        spec=runner._phase_prompt("spec")
        plan=runner._phase_prompt("plan")
        impl=runner._phase_prompt("implementation")
        self.assertIn("spec_review",spec)
        self.assertIn("plan_review",plan)
        self.assertIn("code_review",impl)
        self.assertIn("No apruebes",spec)
        self.assertIn("python -m fibonacci N",spec)

    def test_debug_scenario_is_synthetic_and_deterministic(self):
        scenario=SCENARIOS["fibonacci"]
        self.assertEqual(scenario["language"],"python")
        self.assertEqual(len(scenario["decisions"]),7)
        self.assertIn("N=0",scenario["decisions"][2]["answer"])
        self.assertIn("exit code 1",scenario["decisions"][3]["answer"])

    def test_full_debug_harness_orchestration_with_simulated_agent(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace",timeout=30)

        def fake_agent(prompt,phase):
            e=runner.engine; wid=runner.work_id
            self.assertIsNotNone(e); self.assertIsNotNone(wid)
            if phase=="spec":
                e.continue_work(wid)
                e.submit_spec(wid,demo_spec(),"debug-test-agent")
                e.continue_work(wid)
            elif phase=="plan":
                e.submit_plan(wid,demo_plan(),"debug-test-agent")
            elif phase=="implementation":
                prep=e.continue_work(wid)
                wt=Path(prep["worktree"]); (wt/"src").mkdir(exist_ok=True); (wt/"tests").mkdir(exist_ok=True)
                (wt/"src"/"__init__.py").write_text("",encoding="utf-8")
                (wt/"src"/"fibonacci.py").write_text("def generate_fibonacci(n):\n    if not isinstance(n,int) or n<0: raise ValueError('bad')\n    out=[]; a,b=0,1\n    for _ in range(n): out.append(a); a,b=b,a+b\n    return out\n",encoding="utf-8")
                (wt/"src"/"cli.py").write_text("",encoding="utf-8")
                (wt/"tests"/"test_fibonacci.py").write_text("import unittest\nfrom src.fibonacci import generate_fibonacci\nclass T(unittest.TestCase):\n def test_five(self): self.assertEqual(generate_fibonacci(5),[0,1,1,2,3])\n def test_zero(self): self.assertEqual(generate_fibonacci(0),[])\n def test_negative(self):\n  with self.assertRaises(ValueError): generate_fibonacci(-1)\n",encoding="utf-8")
                task_ids=[x["id"] for x in e.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id",(wid,))]
                e.register_result(wid,"done",["src/__init__.py","src/fibonacci.py","src/cli.py","tests/test_fibonacci.py"],[],task_ids)
                e.continue_work(wid)
            return {"phase":phase,"simulated":True}

        runner.agent_runner=fake_agent
        with patch("dynosai_flow.engine.DynosAI.install_model",return_value={"installed":True}), \
             patch("dynosai_flow.engine.DynosAI.deep_doctor",return_value={"full_ready":True}), \
             patch("dynosai_flow.engine.DynosAI.sync",return_value={"ok":True}), \
             patch.object(runner,"_run_fibonacci_oracle",return_value={"scenario":"fibonacci","passed":True,"results":[]}):
            result=runner.run()
        self.assertEqual(result["status"],"passed",result)
        self.assertTrue(Path(result["bundle"]).exists())
        self.assertEqual(result["invariants"]["final_state"],"done")


    def test_efficiency_report_flags_cursor_transcript_access(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        runner.logs.mkdir(parents=True,exist_ok=True)
        stream=runner.logs/"cursor-plan.stream.jsonl"
        rows=[
            {"type":"tool_call","subtype":"started","tool_call":{"readToolCall":{"args":{"path":"/home/user/.cursor/projects/x/agent-transcripts/a.jsonl"}}}},
            {"type":"result","usage":{"inputTokens":10,"outputTokens":2,"cacheReadTokens":3,"cacheWriteTokens":0},"duration_ms":100},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        metrics=runner._cursor_stream_metrics(stream)
        self.assertEqual(metrics["usage"]["inputTokens"],10)
        self.assertEqual(len(metrics["outside_cursor_transcript_access"]),1)

    def test_efficiency_rejects_payload_rejections_and_agent_decisions(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        report={"mcp_failures":[],"mcp_validation_rejections":[{"id":1}],"agent_decision_calls":[],"outside_cursor_transcript_access":[]}
        with self.assertRaises(AssertionError): runner._assert_efficiency_safety(report)
        report={"mcp_failures":[],"mcp_validation_rejections":[],"agent_decision_calls":[{"id":2}],"outside_cursor_transcript_access":[]}
        with self.assertRaises(AssertionError): runner._assert_efficiency_safety(report)

    def test_debug_invariants_reject_missing_task_traceability(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        broken={
            "final_state":"done", "all_tasks_completed":True,
            "task_traceability":[{"id":"DYN-0001-T01","state":"completed","owner":None,"claimed_run":None,"depends_on":[],"files":["x.py"]}],
            "runs":[{"id":"RUN-0001","task_ids":[]}],
            "artifacts":[{"kind":"spec","status":"approved"},{"kind":"plan","status":"approved"}],
        }
        violations=runner._invariant_violations(broken)
        self.assertTrue(any("no owner" in x for x in violations),violations)
        self.assertTrue(any("no claimed_run" in x for x in violations),violations)


    def test_cursor_stream_metrics_detects_mcp_result_transport_error(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        stream=self.tmp/"cursor-plan.stream.jsonl"
        call_id="call-1"
        rows=[
            {"type":"tool_call","subtype":"started","tool_call":{"toolCallId":call_id,"mcpToolCall":{"args":{"toolName":"dynosai_find_symbol","args":{"query":"Ticket"}}}}},
            {"type":"tool_call","subtype":"completed","tool_call":{"toolCallId":call_id,"mcpToolCall":{"result":{"error":{"error":"Tool execution error","readToolDefReminder":"structuredContent Invalid input"}}}}},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        metrics=runner._cursor_stream_metrics(stream)
        self.assertEqual(metrics["tool_calls"],1)
        self.assertEqual(len(metrics["mcp_result_errors"]),1)
        self.assertEqual(metrics["mcp_result_errors"][0]["tool"],"dynosai_find_symbol")
        report={"mcp_failures":[],"mcp_stream_failures":metrics["mcp_result_errors"],"mcp_validation_rejections":[],"agent_decision_calls":[],"outside_cursor_transcript_access":[]}
        with self.assertRaises(AssertionError): runner._assert_efficiency_safety(report)


    def test_agent_tools_is_provider_spill_not_transcript_bypass(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        stream=self.tmp/"cursor-implementation.stream.jsonl"
        rows=[
            {"type":"tool_call","subtype":"started","tool_call":{"readToolCall":{"args":{"path":"/home/user/.cursor/projects/x/agent-tools/result.txt"}}}},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        metrics=runner._cursor_stream_metrics(stream)
        self.assertEqual(metrics["outside_cursor_transcript_access"],[])
        self.assertEqual(len(metrics["provider_output_spill_access"]),1)

    def test_cursor_stream_metrics_detects_oversized_mcp_output_spill(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        stream=self.tmp/"cursor-implementation.stream.jsonl"
        call_id="call-spill"
        rows=[
            {"type":"tool_call","subtype":"started","tool_call":{"toolCallId":call_id,"mcpToolCall":{"args":{"toolName":"dynosai_get_next_action","args":{"work_id":"DYN-0001","execute":True}}}}},
            {"type":"tool_call","subtype":"completed","tool_call":{"toolCallId":call_id,"mcpToolCall":{"result":{"success":{"content":[{"text":{"text":"","outputLocation":{"filePath":"/home/user/.cursor/projects/x/agent-tools/result.txt","sizeBytes":"40687","lineCount":"911"}}}]}}}}},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        metrics=runner._cursor_stream_metrics(stream)
        self.assertEqual(len(metrics["mcp_output_spills"]),1)
        self.assertEqual(metrics["mcp_output_spills"][0]["tool"],"dynosai_get_next_action")
        self.assertEqual(metrics["mcp_output_spills"][0]["size_bytes"],40687)
        report={"mcp_failures":[],"mcp_stream_failures":[],"mcp_validation_rejections":[],"agent_decision_calls":[],"outside_cursor_transcript_access":[],"provider_output_spill_access":[],"mcp_output_spills":metrics["mcp_output_spills"]}
        with self.assertRaises(AssertionError): runner._assert_efficiency_safety(report)


    def test_cursor_stream_metrics_classifies_orphan_dispatch_error_as_client_side(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        stream=self.tmp/"cursor-implementation.stream.jsonl"
        rows=[
            {"type":"tool_call","subtype":"completed","call_id":"orphan-1","tool_call":{"mcpToolCall":{"result":{"error":{"error":"Tool execution error","readToolDefReminder":"Invalid arguments:\nserver: Required\ntoolName: Required"}}}}},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        metrics=runner._cursor_stream_metrics(stream)
        self.assertEqual(metrics["mcp_result_errors"],[])
        self.assertEqual(len(metrics["client_mcp_dispatch_errors"]),1)
        self.assertEqual(metrics["client_mcp_dispatch_errors"][0]["classification"],"client_dispatch_before_server")

    def test_cursor_stream_metrics_still_blocks_attributable_dynosai_stream_error(self):
        runner=DebugE2ERunner(output=self.tmp/"result.zip",workspace=self.tmp/"workspace")
        stream=self.tmp/"cursor-plan.stream.jsonl"
        call_id="call-dynosai"
        rows=[
            {"type":"tool_call","subtype":"started","call_id":call_id,"tool_call":{"mcpToolCall":{"args":{"serverIdentifier":"dynosai","toolName":"dynosai_find_symbol","args":{"query":"x"}}}}},
            {"type":"tool_call","subtype":"completed","call_id":call_id,"tool_call":{"mcpToolCall":{"result":{"error":{"error":"Tool execution error"}}}}},
        ]
        stream.write_text("\n".join(json.dumps(x) for x in rows)+"\n",encoding="utf-8")
        metrics=runner._cursor_stream_metrics(stream)
        self.assertEqual(len(metrics["mcp_result_errors"]),1)
        self.assertEqual(metrics["client_mcp_dispatch_errors"],[])
        report={"mcp_failures":[],"mcp_stream_failures":metrics["mcp_result_errors"],"mcp_validation_rejections":[],"agent_decision_calls":[],"outside_cursor_transcript_access":[],"mcp_output_spills":[]}
        with self.assertRaises(AssertionError): runner._assert_efficiency_safety(report)

if __name__=="__main__": unittest.main()
