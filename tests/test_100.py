import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.acceptance import _scenario_prompt
from dynosai_flow.acceptance_logging import acceptance_usage
from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.cli import parser
from dynosai_flow.managed_runtime import ManagedProviderRuntime
from dynosai_flow.mcp import MCPServer, MCP_TOOL_PROFILES, ToolInputError, _validate_tool_arguments
from dynosai_flow.token_usage import (
    TokenUsageRecorder,
    aggregate_usage,
    codex_token_sample_from_rollout,
    cursor_usage_from_event,
    normalize_usage,
)
from dynosai_flow.version import __version__


class DynosAI100Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_version_is_0100(self):
        self.assertEqual(__version__, "1.0.0rc6")

    def test_cursor_exact_usage_is_normalized_without_double_counting_cache(self):
        raw = {"inputTokens": 179451, "outputTokens": 8018, "cacheReadTokens": 1008512, "cacheWriteTokens": 3}
        usage = normalize_usage("cursor", raw)
        self.assertEqual(usage["input_tokens"], 179451)
        self.assertEqual(usage["output_tokens"], 8018)
        self.assertEqual(usage["cached_input_tokens"], 1008512)
        self.assertEqual(usage["cache_write_input_tokens"], 3)
        self.assertIsNone(usage["total_tokens"])

    def test_codex_rollout_token_count_is_exact_and_keeps_reasoning(self):
        row = {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 986585,
                        "cached_input_tokens": 905984,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 7028,
                        "reasoning_output_tokens": 1003,
                        "total_tokens": 993613,
                    },
                    "last_token_usage": {"input_tokens": 1000, "output_tokens": 20},
                    "model_context_window": 258400,
                },
            },
        }
        sample = codex_token_sample_from_rollout(row)
        self.assertIsNotNone(sample)
        self.assertEqual(sample["usage"]["total_tokens"], 993613)
        self.assertEqual(sample["usage"]["reasoning_output_tokens"], 1003)
        self.assertEqual(sample["model_context_window"], 258400)

    def test_token_recorder_persists_live_estimate_then_provider_exact(self):
        root = self.tmp / "usage"
        recorder = TokenUsageRecorder(root, "cursor", "p1", model="grok-4.6", scenario="fibonacci")
        recorder.add_estimate(input_tokens=100, output_tokens=20, event="live_estimate")
        estimate = json.loads((root / "token-usage.json").read_text())
        self.assertEqual(estimate["quality"], "estimated")
        self.assertEqual(estimate["input_tokens"], 100)
        exact = recorder.observe_exact(
            {"inputTokens": 200, "outputTokens": 30, "cacheReadTokens": 500},
            source="cursor_result",
            event="provider_exact",
        )
        self.assertEqual(exact["quality"], "exact")
        self.assertEqual(exact["input_tokens"], 200)
        self.assertEqual(exact["output_tokens"], 30)
        self.assertIsNone(exact["provider_total_tokens"])
        self.assertIsNone(exact["reasoning_output_tokens"])
        # Exact provider counters replace estimates instead of being added to them.
        self.assertEqual(exact["observed_token_total"], 230)
        self.assertEqual(exact["context_read_tokens"], 700)
        self.assertEqual(exact["processed_tokens"], 730)
        self.assertEqual(exact["live_estimate"]["input_tokens"], 100)
        self.assertFalse(exact["estimate_error"]["input_tokens"]["comparable"])
        self.assertEqual(exact["estimate_error"]["context_read_tokens"]["error_pct"], -85.71)
        events = (root / "token-usage.jsonl").read_text().splitlines()
        self.assertGreaterEqual(len(events), 3)

    def test_cursor_final_result_parser(self):
        event = {"type": "result", "usage": {"inputTokens": 364470, "outputTokens": 20388, "cacheReadTokens": 2520576}}
        self.assertEqual(cursor_usage_from_event(event)["outputTokens"], 20388)
        self.assertIsNone(cursor_usage_from_event({"type": "assistant"}))

    def test_usage_status_reports_each_process_and_totals(self):
        root = self.tmp / "telemetry"
        case = root / "cases" / "cursor" / "fibonacci"
        case.mkdir(parents=True)
        (case / "state.json").write_text(json.dumps({"provider": "cursor", "scenario": "fibonacci", "phase": "provider", "updated_at": "2026-08-24T10:00:00Z"}))
        (case / "run.json").write_text("{}")
        (case / "token-usage.json").write_text(json.dumps({
            "provider": "cursor", "scenario": "fibonacci", "quality": "exact",
            "input_tokens": 10, "cached_input_tokens": 20, "cache_write_input_tokens": 0,
            "output_tokens": 3, "reasoning_output_tokens": 0, "total_tokens": 0,
            "observed_token_total": 13,
        }))
        result = acceptance_usage(root)
        self.assertEqual(len(result["processes"]), 1)
        self.assertEqual(result["totals"]["input_tokens"], 10)
        self.assertEqual(result["totals"]["output_tokens"], 3)
        self.assertEqual(result["totals"]["exact_processes"], 1)

    def test_usage_cli_show_and_watch_are_parseable(self):
        p = parser()
        show = p.parse_args(["usage", "show", "--log-dir", "/tmp/x"])
        watch = p.parse_args(["usage", "watch", "--interval", "1.5"])
        self.assertEqual(show.command, "usage")
        self.assertEqual(show.action, "show")
        self.assertEqual(watch.action, "watch")
        self.assertEqual(watch.interval, 1.5)

    def test_acceptance_surface_is_lean_and_removes_redundant_transition_tools(self):
        tools = MCP_TOOL_PROFILES["acceptance"]
        # 0.15 adds dynosai_retrieve_handle so compacted diffs/search remain retrievable.
        # Continue/overlay/find_symbol stay out: the surface must remain lean.
        self.assertEqual(len(tools), 15)
        self.assertIn("dynosai_get_next_action", tools)
        self.assertIn("dynosai_retrieve_handle", tools)
        self.assertNotIn("dynosai_continue", tools)
        self.assertNotIn("dynosai_refresh_overlay", tools)
        self.assertNotIn("dynosai_find_symbol", tools)

    def test_batch_decisions_and_reads_validate_but_have_bounded_size(self):
        _validate_tool_arguments("dynosai_register_decision", {
            "work_id": "DYN-1",
            "decisions": [{"question": "q1", "answer": "a1"}, {"question": "q2", "answer": "a2"}],
        })
        _validate_tool_arguments("dynosai_read", {
            "requests": [{"path": "a.py", "start_line": 1, "end_line": 3}, {"symbol_id": "sym-1"}],
        })
        with self.assertRaises(ToolInputError):
            _validate_tool_arguments("dynosai_read", {"requests": [{"symbol_id": str(i)} for i in range(9)]})
        with self.assertRaises(ToolInputError):
            _validate_tool_arguments("dynosai_register_decision", {"work_id": "DYN-1", "decisions": []})

    def test_managed_response_delta_omits_unchanged_agent_config_and_contract(self):
        server = MCPServer.__new__(MCPServer)
        server._managed_response_cache = {}
        server.provider_session = {"id": "ps-1"}
        server.session = None
        payload = {
            "agent_config": {"provider": "cursor", "activity": "implementation", "mode": "recommended", "rules": ["x" * 300]},
            "contract": {"operation": "implement", "work_id": "DYN-1", "instructions": "y" * 300},
        }
        with patch.dict(os.environ, {"DYNOSAI_MANAGED_AGENT": "1", "DYNOSAI_COMPACT_RESPONSES": "1"}, clear=False):
            first = server._compact_managed_result("dynosai_get_next_action", payload)
            second = server._compact_managed_result("dynosai_get_next_action", payload)
        self.assertIn("_dynosai_ref", first["agent_config"])
        self.assertTrue(second["agent_config"]["unchanged"])
        self.assertTrue(second["contract"]["unchanged"])
        self.assertGreater(second["response_optimization"]["estimated_bytes_omitted"], 0)
        # A new provider session must receive the full payload again; refs never
        # leak across sessions even if the content hash is identical.
        server.provider_session = {"id": "ps-2"}
        with patch.dict(os.environ, {"DYNOSAI_MANAGED_AGENT": "1", "DYNOSAI_COMPACT_RESPONSES": "1"}, clear=False):
            third = server._compact_managed_result("dynosai_get_next_action", payload)
        self.assertIn("_dynosai_ref", third["agent_config"])

    def test_acceptance_prompt_uses_batching_and_auto_advance(self):
        prompt = _scenario_prompt("cursor", "fibonacci")
        self.assertIn("ONE dynosai_register_decision", prompt)
        self.assertIn("dynosai_get_next_action with execute=true", prompt)
        self.assertIn("batch them in one dynosai_read", prompt)
        self.assertIn("Do not call dynosai_refresh_overlay", prompt)

    def test_managed_runtime_materializes_full_dynosai_skill_catalog_but_only_marks_current_skills_active(self):
        project = self.tmp / "project"
        project.mkdir()
        AgentConfiguration(project).init()
        runtime = ManagedProviderRuntime(project).prepare("cursor", activity="discovery")
        manifest = json.loads((Path(runtime.root) / "runtime.json").read_text())
        self.assertEqual(set(manifest["skill_catalog"]), {"debugging", "testing", "code-review"})
        self.assertEqual(manifest["active_skills"], ["debugging"])
        self.assertTrue(manifest["execution_optimization"]["provider_native_skill_catalog"])
        self.assertEqual(manifest["token_telemetry"]["cursor"], "estimated_live_exact_final")
        for skill in manifest["skill_catalog"]:
            self.assertTrue((Path(runtime.root) / "config" / "skills" / skill / "SKILL.md").exists())
        self.assertEqual(runtime.env["DYNOSAI_EXTERNAL_DEFAULTS_POLICY"], "ignore")
        self.assertEqual(runtime.env["DYNOSAI_AUTO_ADVANCE"], "1")
        self.assertEqual(runtime.env["DYNOSAI_COMPACT_RESPONSES"], "1")
        self.assertEqual(runtime.env["DYNOSAI_TOKEN_TELEMETRY"], "1")

    def test_aggregate_usage_keeps_input_output_and_quality_counts(self):
        total = aggregate_usage([
            {"quality": "exact", "input_tokens": 10, "output_tokens": 2, "observed_token_total": 12},
            {"quality": "estimated", "input_tokens": 5, "output_tokens": 1, "observed_token_total": 6},
        ])
        self.assertEqual(total["processes"], 2)
        self.assertEqual(total["exact_processes"], 1)
        self.assertEqual(total["estimated_processes"], 1)
        self.assertEqual(total["input_tokens"], 15)
        self.assertEqual(total["output_tokens"], 3)
        self.assertEqual(total["observed_token_total"], 18)


if __name__ == "__main__":
    unittest.main()
