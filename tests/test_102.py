import tempfile
import unittest
from pathlib import Path

from dynosai_flow.token_usage import TokenUsageRecorder, estimate_error
from dynosai_flow.version import __version__


class DynosAI102Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_version_is_0102(self):
        self.assertEqual(__version__, "1.0.0rc8")

    def test_cursor_live_estimate_compares_against_context_read_not_fresh_input(self):
        # Real 0.10.1 Cursor/Luna acceptance shape: almost all input came from cache.
        estimated = {
            "input_tokens": 39332,
            "cached_input_tokens": 0,
            "output_tokens": 81,
            "reasoning_output_tokens": 567,
        }
        exact = {
            "input_tokens": 60,
            "cached_input_tokens": 640123,
            "output_tokens": 4442,
            "reasoning_output_tokens": None,
        }
        err = estimate_error("cursor", estimated, exact)
        self.assertEqual(err["comparison_basis"], "derived_operational_metrics")
        self.assertEqual(err["context_read_tokens"]["estimated"], 39332)
        self.assertEqual(err["context_read_tokens"]["exact"], 640183)
        self.assertEqual(err["context_read_tokens"]["error_pct"], -93.86)
        self.assertFalse(err["input_tokens"]["comparable"])
        self.assertIsNone(err["input_tokens"]["error_pct"])
        self.assertEqual(err["generated_tokens"]["exact"], 4442)
        self.assertEqual(err["processed_tokens"]["exact"], 644625)
        self.assertFalse(err["reasoning_output_tokens"]["comparable"])

    def test_live_cursor_summary_names_context_semantics_explicitly(self):
        rec = TokenUsageRecorder(self.tmp / "u", "cursor", "p")
        rec.add_estimate(input_tokens=100, output_tokens=5, reasoning_output_tokens=2)
        live = rec.current()["live_estimate"]
        self.assertEqual(live["input_semantics"], "observed_context_estimate_not_provider_fresh_input")
        self.assertEqual(live["observed_context_tokens_estimate"], 100)
        self.assertEqual(live["context_read_tokens"], 100)

    def test_final_schema_is_v3_and_error_is_semantic(self):
        rec = TokenUsageRecorder(self.tmp / "u2", "cursor", "p")
        rec.add_estimate(input_tokens=25, output_tokens=5)
        final = rec.observe_exact({"inputTokens": 100, "cacheReadTokens": 200, "outputTokens": 10}, source="cursor_result")
        self.assertEqual(final["schema_version"], 4)
        self.assertEqual(final["estimate_error"]["context_read_tokens"]["exact"], 300)
        self.assertEqual(final["estimate_error"]["context_read_tokens"]["error_pct"], -91.67)
        self.assertFalse(final["estimate_error"]["input_tokens"]["comparable"])

    def test_codex_input_remains_comparable(self):
        err = estimate_error(
            "codex",
            {"input_tokens": 80, "output_tokens": 8},
            {"input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 10},
        )
        self.assertTrue(err["input_tokens"]["comparable"])
        self.assertEqual(err["input_tokens"]["error_pct"], -20.0)
        self.assertEqual(err["context_read_tokens"]["exact"], 100)


if __name__ == "__main__":
    unittest.main()
