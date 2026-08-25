import json
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import _context_optimization_metrics
from dynosai_flow.model_control import ModelControlPlane
from dynosai_flow.model_routing import ModelRoute
from dynosai_flow.token_usage import align_usage_to_activity


class DynosAI113HardeningTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_boundary_identity_projection_eliminates_process_fallback(self):
        persisted = {
            "phase": "implementation",
            "quality": "exact",
            "source": "codex_app_server",
            "input_tokens": 482_952,
            "output_tokens": 3_700,
            "context_read_tokens": 482_952,
            "generated_tokens": 3_700,
            "processed_tokens": 486_652,
            "phase_usage": [
                {
                    "phase": "implementation",
                    "allocation_state": "live",
                    "delta_context_read_tokens": 246_000,
                    "delta_generated_tokens": 1_300,
                }
            ],
        }
        aligned = align_usage_to_activity(persisted, "validation")
        self.assertEqual(persisted["phase"], "implementation")  # input not mutated
        self.assertEqual(aligned["phase"], "validation")
        projected = [x for x in aligned["phase_usage"] if x.get("phase") == "validation"][-1]
        self.assertTrue(projected["identity_projection"])
        self.assertEqual(projected["delta_context_read_tokens"], 0)
        budget = ModelControlPlane.assess_budget("validation", aligned)
        self.assertEqual(budget.measurement_scope, "phase_live")
        self.assertEqual(budget.context_tokens, 0)
        self.assertEqual(budget.generated_tokens, 0)

    def test_already_aligned_live_usage_is_not_projected(self):
        usage = {
            "phase": "planning",
            "phase_usage": [{
                "phase": "planning",
                "allocation_state": "live",
                "delta_context_read_tokens": 12,
                "delta_generated_tokens": 3,
            }],
        }
        aligned = align_usage_to_activity(usage, "planning")
        row = aligned["phase_usage"][0]
        self.assertNotIn("identity_projection", row)
        budget = ModelControlPlane.assess_budget("planning", aligned)
        self.assertEqual(budget.measurement_scope, "phase_live")
        self.assertEqual(budget.context_tokens, 12)

    def test_success_outcome_clears_all_failure_metadata(self):
        root = self.tmp / "success"
        ctl = ModelControlPlane(root)
        state = ctl.record_outcome(
            "codex", "DYN-1", success=True, failure_kind="test",
            failure_fingerprint="stale-fingerprint", activity="validation",
        )
        event = state["history"][-1]
        self.assertTrue(event["success"])
        self.assertIsNone(event["failure_kind"])
        self.assertIsNone(event["failure_fingerprint"])
        self.assertFalse(event["counts_as_model_failure"])
        self.assertEqual(state["same_tier_retries"], 0)

    def test_failure_still_counts_normally(self):
        ctl = ModelControlPlane(self.tmp / "failure")
        state = ctl.record_outcome(
            "codex", "DYN-1", success=False, failure_kind="test",
            failure_fingerprint="fp-1", activity="validation",
        )
        event = state["history"][-1]
        self.assertTrue(event["counts_as_model_failure"])
        self.assertEqual(event["failure_kind"], "test")
        self.assertEqual(state["same_tier_retries"], 1)

    def test_candidate_route_is_not_reported_as_recommendation(self):
        root = self.tmp / "candidate"
        ctl = ModelControlPlane(root)
        decision = ctl.decide("codex", "planning", usage={"phase_usage": []})
        payload = decision.to_dict()
        self.assertEqual(payload["candidate_route"]["model"], "gpt-5.6-luna")
        self.assertIsNone(payload["route_recommended"])
        metrics = _context_optimization_metrics(self.tmp / "missing.jsonl", root, None)
        self.assertEqual(metrics["model_route_candidates"], 1)
        self.assertEqual(metrics["model_route_recommendations"], 0)

    def test_real_transition_has_candidate_and_recommendation(self):
        root = self.tmp / "recommendation"
        ctl = ModelControlPlane(root)
        initial = ModelRoute("codex", "discovery", "gpt-5.6-luna", "medium", "acceptance", "builtin", "turn_override")
        ctl.observe_provider_route("codex", "DYN-1", initial, verified=True, activity="discovery", initial_route=True)
        decision = ctl.decide("codex", "validation", work_id="DYN-1", usage={"phase_usage": []}, at_provider_boundary=False)
        payload = decision.to_dict()
        self.assertEqual(payload["candidate_route"]["effort"], "low")
        self.assertEqual(payload["route_recommended"]["effort"], "low")
        events = [json.loads(x) for x in (root / ".dynosai/runtime/model-control.jsonl").read_text().splitlines()]
        self.assertEqual(sum(1 for x in events if x.get("event") == "route_recommended"), 1)
        metrics = _context_optimization_metrics(self.tmp / "missing2.jsonl", root, None)
        self.assertGreaterEqual(metrics["model_route_candidates"], 1)
        self.assertEqual(metrics["model_route_recommendations"], 1)


if __name__ == "__main__":
    unittest.main()
