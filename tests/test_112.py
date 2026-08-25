import json
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import _context_optimization_metrics
from dynosai_flow.model_control import ModelControlPlane
from dynosai_flow.model_routing import ModelRoute
from dynosai_flow.token_usage import TokenUsageRecorder


class DynosAI112ModelControlTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_active_phase_delta_is_available_live(self):
        usage = TokenUsageRecorder(self.tmp / "usage", "codex", "p1", model="gpt-5.6-luna")
        usage.set_identity(phase="discovery")
        usage.observe_exact(
            {"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10},
            source="test",
        )
        usage.set_identity(phase="planning")
        usage.observe_exact(
            {"input_tokens": 180, "cached_input_tokens": 70, "output_tokens": 15},
            source="test",
        )
        current = usage.current()
        planning = next(x for x in current["phase_usage"] if x["phase"] == "planning")
        self.assertEqual(planning["allocation_state"], "live")
        self.assertEqual(planning["delta_context_read_tokens"], 80)
        self.assertEqual(planning["delta_generated_tokens"], 5)
        budget = ModelControlPlane.assess_budget("planning", current)
        self.assertEqual(budget.measurement_scope, "phase_live")
        self.assertEqual(budget.context_tokens, 80)

    def test_instantaneous_phases_are_reported_as_zero(self):
        usage = TokenUsageRecorder(self.tmp / "usage2", "codex", "p2", model="gpt-5.6-luna")
        usage.set_identity(phase="discovery")
        usage.observe_exact({"input_tokens": 100, "output_tokens": 10}, source="test")
        usage.set_identity(phase="planning")
        usage.observe_exact({"input_tokens": 150, "output_tokens": 20}, source="test")
        usage.set_identity(phase="implementation")
        usage.observe_exact({"input_tokens": 200, "output_tokens": 30}, source="test")
        usage.set_identity(phase="validation")
        current = usage.current()
        rows = {x["phase"]: x for x in current["phase_usage"]}
        self.assertIn("specification", rows)
        self.assertIn("code_review", rows)
        self.assertEqual(rows["specification"]["delta_context_read_tokens"], 0)
        self.assertEqual(rows["code_review"]["delta_context_read_tokens"], 0)
        self.assertEqual(rows["specification"]["allocation_state"], "synthetic_zero")

    def test_hard_context_pressure_compacts_but_never_escalates(self):
        ctl = ModelControlPlane(self.tmp / "control")
        usage = {"phase_usage": [{
            "phase": "validation",
            "delta_context_read_tokens": 200_000,
            "delta_generated_tokens": 100,
            "allocation_state": "live",
        }]}
        decision = ctl.decide("codex", "validation", usage=usage, extra_signals={"repository_files": 0})
        self.assertEqual(decision.budget.measurement_scope, "phase_live")
        self.assertEqual(decision.budget.status, "hard_exceeded")
        self.assertIn("reevaluate_after_compaction", decision.budget.actions)
        self.assertFalse(decision.escalation["recommended"])
        self.assertEqual(decision.tier, "economy")
        self.assertIn("phase_token_pressure_compaction_first", decision.reasons)
        self.assertNotIn("context_pressure", decision.complexity.reasons)

    def test_initial_route_is_not_counted_as_model_control_transition(self):
        root = self.tmp / "initial"
        ctl = ModelControlPlane(root)
        initial = ModelRoute("codex", "discovery", "gpt-5.6-luna", "medium", "acceptance_economy", "builtin", "turn_override")
        ctl.observe_provider_route("codex", "DYN-1", initial, verified=True, activity="discovery", source="acceptance_provider", initial_route=True)
        events = [json.loads(x) for x in (root / ".dynosai/runtime/model-control.jsonl").read_text().splitlines()]
        names = [x["event"] for x in events]
        self.assertIn("initial_route_verified", names)
        self.assertNotIn("route_verified", names)
        metrics = _context_optimization_metrics(self.tmp / "missing.jsonl", root, None)
        self.assertEqual(metrics["initial_route_verified"], 1)
        self.assertEqual(metrics["model_control_transition_verified"], 0)

    def test_real_same_tier_effort_transition_medium_to_low(self):
        root = self.tmp / "transition"
        ctl = ModelControlPlane(root)
        initial = ModelRoute("codex", "discovery", "gpt-5.6-luna", "medium", "acceptance_economy", "builtin", "turn_override")
        ctl.observe_provider_route("codex", "DYN-1", initial, verified=True, activity="discovery", initial_route=True)

        rec = ctl.decide("codex", "validation", work_id="DYN-1", usage={"phase_usage": []}, at_provider_boundary=False)
        self.assertEqual(rec.route.model, "gpt-5.6-luna")
        self.assertEqual(rec.route.effort, "low")
        self.assertIsNotNone(rec.pending_transition)
        self.assertEqual(rec.pending_transition["reason"], ["same_tier_effort_optimization"])
        self.assertFalse(rec.escalation["recommended"])

        req = ctl.decide("codex", "validation", work_id="DYN-1", usage={"phase_usage": []}, at_provider_boundary=True)
        self.assertIsNotNone(req.route_requested)
        self.assertEqual(req.route_requested["model"], "gpt-5.6-luna")
        self.assertEqual(req.route_requested["effort"], "low")
        ctl.observe_provider_route("codex", "DYN-1", req.route_requested, verified=True, activity="validation")

        events = [json.loads(x) for x in (root / ".dynosai/runtime/model-control.jsonl").read_text().splitlines()]
        verified = [x for x in events if x.get("event") == "route_verified"]
        self.assertEqual(len(verified), 1)
        self.assertFalse(verified[0]["escalation"])
        self.assertEqual(verified[0]["route"]["effort"], "low")


if __name__ == "__main__":
    unittest.main()
