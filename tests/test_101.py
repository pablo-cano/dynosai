import json
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.baselines import compare_to_baseline
from dynosai_flow.cli import parser
from dynosai_flow.model_routing import ACCEPTANCE_MODEL_PROFILES, ModelRoute, cursor_cli_selector
from dynosai_flow.token_usage import TokenUsageRecorder, aggregate_usage, normalize_usage, runtime_metrics
from dynosai_flow.version import __version__


class DynosAI101Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_version_is_0101(self):
        self.assertEqual(__version__, "0.16.0")

    def test_unknown_provider_counters_stay_null(self):
        usage = normalize_usage("cursor", {"inputTokens": 100, "outputTokens": 10, "cacheReadTokens": 50})
        self.assertEqual(usage["input_tokens"], 100)
        self.assertIsNone(usage["total_tokens"])
        self.assertIsNone(usage["reasoning_output_tokens"])

    def test_exact_provider_usage_is_separate_from_live_estimate(self):
        recorder = TokenUsageRecorder(self.tmp / "u", "cursor", "p")
        recorder.add_estimate(input_tokens=25, output_tokens=5, reasoning_output_tokens=3)
        final = recorder.observe_exact({"inputTokens": 100, "cacheReadTokens": 200, "outputTokens": 10}, source="cursor_result")
        self.assertEqual(final["provider_usage"]["input_tokens"], 100)
        self.assertEqual(final["live_estimate"]["input_tokens"], 25)
        self.assertEqual(final["live_estimate"]["observed_thinking_tokens_estimate"], 3)
        self.assertIsNone(final["reasoning_output_tokens"])
        self.assertEqual(final["context_read_tokens"], 300)
        self.assertEqual(final["generated_tokens"], 10)
        self.assertEqual(final["processed_tokens"], 310)
        self.assertFalse(final["estimate_error"]["input_tokens"]["comparable"])
        self.assertEqual(final["estimate_error"]["context_read_tokens"]["error_pct"], -91.67)

    def test_phase_snapshots_are_persisted_without_faking_exact_allocation(self):
        recorder = TokenUsageRecorder(self.tmp / "ph", "cursor", "p")
        recorder.set_identity(phase="discovery")
        recorder.add_estimate(input_tokens=20, output_tokens=2)
        recorder.set_identity(phase="implementation")
        recorder.add_estimate(input_tokens=10, output_tokens=1)
        recorder.observe_exact({"inputTokens": 100, "cacheReadTokens": 50, "outputTokens": 10}, source="cursor_result")
        final = recorder.finalize()
        self.assertTrue(final["phase_usage"])
        self.assertEqual(final["phase_usage"][0]["phase"], "discovery")
        self.assertEqual(final["phase_usage"][-1]["allocation_quality"], "mixed_unallocatable")

    def test_runtime_metrics_separate_mcp_and_reconnects(self):
        mcp = self.tmp / "mcp.jsonl"
        mcp.write_text("\n".join([
            json.dumps({"at":"2026-08-24T10:00:00+00:00","event":"mcp_tool_start","request_id":1}),
            json.dumps({"at":"2026-08-24T10:00:02+00:00","event":"mcp_tool_end","request_id":1}),
        ]))
        stream = self.tmp / "provider.jsonl"
        stream.write_text("\n".join([
            json.dumps({"type":"connection","subtype":"reconnecting"}),
            json.dumps({"type":"result","duration_ms":10000,"duration_api_ms":9000}),
        ]))
        metrics = runtime_metrics("cursor", 10, mcp_path=mcp, provider_stream_path=stream)
        self.assertEqual(metrics["mcp_calls"], 1)
        self.assertEqual(metrics["dynosai_mcp_seconds"], 2.0)
        self.assertEqual(metrics["provider_api_seconds"], 9.0)
        self.assertEqual(metrics["reconnect_count"], 1)
        self.assertEqual(metrics["non_mcp_seconds"], 8.0)

    def test_acceptance_economy_profile_uses_luna_medium_for_both_providers(self):
        self.assertEqual(ACCEPTANCE_MODEL_PROFILES["economy"]["cursor"], {"model":"gpt-5.6-luna","effort":"medium"})
        self.assertEqual(ACCEPTANCE_MODEL_PROFILES["economy"]["codex"], {"model":"gpt-5.6-luna","effort":"medium"})
        route = ModelRoute("cursor", "discovery", "gpt-5.6-luna", "medium", "test", "builtin", "session_or_resume_boundary")
        self.assertEqual(cursor_cli_selector(route), "gpt-5.6-luna-medium")

    def test_acceptance_cli_defaults_to_economy_but_can_select_baseline(self):
        p = parser()
        economy = p.parse_args(["debug", "acceptance", "--providers", "cursor", "--scenario", "fibonacci"])
        baseline = p.parse_args(["debug", "acceptance", "--providers", "cursor", "--scenario", "fibonacci", "--model-profile", "baseline"])
        self.assertEqual(economy.model_profile, "economy")
        self.assertEqual(baseline.model_profile, "baseline")

    def test_baseline_comparison_marks_cross_model_as_context_only(self):
        result = compare_to_baseline(
            provider="cursor", scenario="fibonacci", model="gpt-5.6-luna", effort="medium",
            usage={"input_tokens":100,"cached_input_tokens":50,"output_tokens":10,"context_read_tokens":150,"processed_tokens":160},
            runtime={"mcp_calls":10,"workflow_elapsed_seconds":100,"reconnect_count":0},
        )
        self.assertIsNotNone(result)
        self.assertFalse(result["comparable_model"])
        self.assertEqual(result["comparison_kind"], "cross_model_context_only")
        self.assertTrue(result["warning"])

    def test_aggregate_usage_uses_derived_fields_and_nullable_provider_total(self):
        total = aggregate_usage([
            {"quality":"exact","input_tokens":10,"cached_input_tokens":20,"output_tokens":2,"context_read_tokens":30,"generated_tokens":2,"processed_tokens":32,"provider_total_tokens":None},
        ])
        self.assertEqual(total["context_read_tokens"], 30)
        self.assertEqual(total["processed_tokens"], 32)
        self.assertIsNone(total["provider_total_tokens"])


if __name__ == "__main__":
    unittest.main()
