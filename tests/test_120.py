import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.context_control import ContextCheckpointStore
from dynosai_flow.acceptance import _context_optimization_metrics
from dynosai_flow.estimator_calibration import UsageEstimatorCalibration
from dynosai_flow.mcp import MCPServer, phase_tool_surface
from dynosai_flow.model_control import ModelControlPlane
from dynosai_flow.predictive_routing import PredictiveRoutingMemory
from dynosai_flow.token_usage import TokenUsageRecorder
from dynosai_flow.version import __version__


class DynosAI120PredictiveEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_version_is_0120(self):
        self.assertEqual(__version__, "0.13.0")

    def test_calibration_learns_once_at_finalize_and_exceeds_old_50x_ceiling(self):
        cal_path = self.tmp / "calibration.json"
        with patch.dict(os.environ, {"DYNOSAI_TOKEN_CALIBRATION_PATH": str(cal_path)}, clear=False):
            rec = TokenUsageRecorder(self.tmp / "usage", "codex", "p1", model="gpt-5.6-luna", scenario="fibonacci")
            rec.add_estimate(input_tokens=1361, output_tokens=100, event="visible_stream")
            rec.observe_exact({"input_tokens": 300_000, "cached_input_tokens": 250_000, "output_tokens": 2000}, source="codex_live")
            self.assertFalse(cal_path.exists(), "intermediate exact samples must not train calibration")
            rec.observe_exact({"input_tokens": 891_543, "cached_input_tokens": 797_184, "output_tokens": 4358}, source="codex_live")
            self.assertFalse(cal_path.exists(), "only finalization may persist a calibration sample")
            final = rec.finalize()
            self.assertTrue(cal_path.exists())
            factors = final["calibration_update"]
            self.assertTrue(factors["available"])
            self.assertGreater(factors["context_factor"], 500.0)
            self.assertEqual(factors["samples"], 1)
            # Finalizing the same process again does not duplicate the sample.
            rec.finalize()
            self.assertEqual(UsageEstimatorCalibration(cal_path).factors("codex", "gpt-5.6-luna", "fibonacci")["samples"], 1)


    def test_legacy_50x_calibration_series_is_migrated_on_first_v2_final_sample(self):
        cal_path = self.tmp / "legacy-calibration.json"
        cal_path.write_text(json.dumps({
            "schema_version": 1,
            "series": {
                "codex|gpt-5.6-luna|fibonacci": {
                    "context_ratios": [50.0, 50.0, 50.0],
                    "generated_ratios": [10.0, 11.0, 12.0],
                    "samples": 3,
                    "context_factor": 50.0,
                    "generated_factor": 11.0,
                }
            }
        }), encoding="utf-8")
        cal = UsageEstimatorCalibration(cal_path)
        factors = cal.update(
            "codex", "gpt-5.6-luna",
            {"context_read_tokens": 1361, "generated_tokens": 100},
            {"context_read_tokens": 891_543, "generated_tokens": 4358},
            "fibonacci",
        )
        self.assertGreater(factors["context_factor"], 500.0)
        self.assertEqual(factors["samples"], 1)

    def test_predictive_memory_escalates_only_after_repeated_model_outcomes(self):
        root = self.tmp / "predict"
        memory = PredictiveRoutingMemory(root)
        for _ in range(5):
            memory.record("codex", "planning", "economy", success=False, counts_as_model_failure=True, failure_kind="test")
        assessment = memory.assess("codex", "planning", "economy", "normal")
        self.assertTrue(assessment["prediction_used"])
        self.assertEqual(assessment["recommended_tier"], "standard")
        ctl = ModelControlPlane(root)
        decision = ctl.decide("codex", "planning", usage={"phase_usage": []}, at_provider_boundary=False)
        self.assertEqual(decision.tier, "standard")
        self.assertIn("predictive_capability_history", decision.reasons)
        self.assertEqual(decision.prediction["action"], "predictive_capability_escalation")

    def test_predictive_memory_downshifts_complexity_only_route_when_economy_is_proven(self):
        root = self.tmp / "downshift"
        memory = PredictiveRoutingMemory(root)
        for _ in range(8):
            memory.record("codex", "implementation", "economy", success=True, counts_as_model_failure=False)
        ctl = ModelControlPlane(root)
        decision = ctl.decide(
            "codex", "implementation", usage={"phase_usage": []},
            extra_signals={"repository_files": 50, "task_count": 4, "requirement_count": 8},
        )
        self.assertEqual(decision.complexity.level, "normal")
        self.assertEqual(decision.prediction["action"], "predictive_cost_downshift")
        self.assertEqual(decision.tier, "economy")
        self.assertIn("predictive_cost_downshift", decision.reasons)

    def test_context_pressure_never_becomes_predictive_capability_signal(self):
        root = self.tmp / "context"
        ctl = ModelControlPlane(root)
        decision = ctl.decide("codex", "implementation", usage={"phase_usage": [{
            "phase": "implementation", "allocation_state": "live",
            "delta_context_read_tokens": 400_000, "delta_generated_tokens": 100,
        }]}, extra_signals={"repository_files": 0, "task_count": 1})
        self.assertEqual(decision.budget.status, "hard_exceeded")
        self.assertEqual(decision.context_strategy["mode"], "critical")
        self.assertTrue(decision.context_strategy["checkpoint_first"])
        self.assertFalse(decision.prediction["prediction_used"])
        self.assertFalse(decision.escalation["recommended"])
        self.assertEqual(decision.tier, "economy")

    def test_checkpoint_reference_is_smaller_and_sections_are_targeted(self):
        class DB:
            def query(self, sql, params):
                if "FROM decisions" in sql:
                    return [{"question": "DB?", "answer": "Postgres"}]
                if "FROM tasks" in sql:
                    return [{"id": "TASK-1", "title": "Implement", "state": "todo", "requirements": json.dumps([{"id":"R1","text":"x"*1000}]), "acceptance": json.dumps(["works"]), "files": json.dumps(["a.py"]), "depends_on": "[]"}]
                if "FROM validations" in sql:
                    return [{"command": "pytest", "status": "passed", "exit_code": 0}]
                if "FROM runs" in sql:
                    return [{"id":"RUN-1","status":"success","summary":"done","files_changed":json.dumps(["a.py"]),"verified_files":json.dumps(["a.py"]),"verification_status":"verified"}]
                if "FROM scope_requests" in sql:
                    return []
                return []
        class Artifacts:
            def artifact(self, work_id, kind):
                if kind == "spec":
                    return {"metadata": {"objective": "Do it", "requirements": [{"id":"R1","text":"x"*4000}], "acceptance_criteria": ["ok"]}}
                if kind == "plan":
                    return {"metadata": {"approach": "small", "files": [{"path":"a.py","action":"modify"}], "tasks": [{"id":"TASK-1"}]}}
        class Engine:
            db = DB(); artifacts = Artifacts()
            def get_work(self, work_id):
                return {"id":work_id,"title":"Feature","description":"Goal","state":"implementing","quality_score":100,"workspace_strategy":"interactive_branch"}

        store = ContextCheckpointStore(self.tmp / "cp")
        ref = store.capture(Engine(), "DYN-1")
        self.assertGreater(ref["estimated_tokens_full"], ref["estimated_tokens_reference"])
        self.assertGreater(ref["estimated_tokens_avoided"], 0)
        self.assertEqual(ref["reuse_policy"], "use_reference_first_load_only_required_sections")
        loaded = store.load_sections("DYN-1", ["tasks", "validations"])
        self.assertEqual(loaded["sections"], ["tasks", "validations"])
        self.assertNotIn("spec", loaded["data"])
        capsule = store.prompt_capsule("DYN-1", activity="implementation")
        self.assertIn("reuse before reread", capsule)
        self.assertIn("load_section", capsule)
        self.assertNotIn("x" * 500, capsule)

    def test_predictive_memory_deduplicates_same_work_success(self):
        root = self.tmp / "dedup-predict"
        memory = PredictiveRoutingMemory(root)
        first = memory.record("codex", "implementation", "economy", success=True, counts_as_model_failure=False, work_id="DYN-1")
        second = memory.record("codex", "implementation", "economy", success=True, counts_as_model_failure=False, work_id="DYN-1")
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        stats = memory.snapshot()["series"]["codex|implementation|economy"]
        self.assertEqual(stats["attempts"], 1)
        self.assertEqual(stats["successes"], 1)

    def test_checkpoint_reference_savings_are_counted_in_response_optimization(self):
        server = MCPServer.__new__(MCPServer)
        server.provider_session = {"id": "S-1"}
        server.session = None
        server._managed_response_cache = {}
        payload = {
            "work": {"id": "DYN-1"},
            "context_checkpoint": {
                "ref": "CTX-1",
                "estimated_bytes_avoided": 11170,
                "estimated_tokens_avoided": 2792,
            },
        }
        with patch.dict(os.environ, {"DYNOSAI_MANAGED_AGENT": "1"}, clear=False):
            compacted = server._compact_managed_result("dynosai_get_next_action", payload)
        optimization = compacted["response_optimization"]
        self.assertEqual(optimization["checkpoint_bytes_omitted"], 11170)
        self.assertGreaterEqual(optimization["estimated_bytes_omitted"], 11170)
        self.assertTrue(optimization["checkpoint_reference_used"])

    def test_acceptance_reports_checkpoint_and_predictive_optimization_metrics(self):
        project = self.tmp / "metrics-project"
        trace = self.tmp / "mcp-activity.jsonl"
        runtime = project / ".dynosai" / "runtime"
        runtime.mkdir(parents=True)
        trace.write_text("\n".join([
            json.dumps({"event":"mcp_tool_end","tool":"dynosai_checkpoint","status":"ok","result":{"response_optimization":{"mode":"delta","estimated_bytes_omitted":12000,"checkpoint_bytes_omitted":9000}}}),
            json.dumps({"event":"tool_surface_changed","tool_count":9}),
        ]) + "\n", encoding="utf-8")
        (runtime / "model-control.jsonl").write_text("\n".join([
            json.dumps({"event":"model_route_decision","budget":{"measurement_scope":"phase_live"},"context_strategy":{"mode":"lean"},"candidate_route":{"tier":"economy"},"prediction":{"prediction_used":True,"action":"predictive_cost_downshift"}}),
        ]) + "\n", encoding="utf-8")
        metrics = _context_optimization_metrics(trace, project)
        self.assertEqual(metrics["checkpoint_bytes_omitted"], 9000)
        self.assertEqual(metrics["response_bytes_omitted"], 12000)
        self.assertEqual(metrics["targeted_checkpoint_loads"], 1)
        self.assertEqual(metrics["predictive_decisions_used"], 1)
        self.assertEqual(metrics["predictive_cost_downshifts"], 1)
        self.assertEqual(metrics["context_strategy_modes"], {"lean": 1})

    def test_checkpoint_tool_is_progressive_not_added_to_initial_acceptance_superset(self):
        server = MCPServer.__new__(MCPServer)
        server.negotiated = "2025-11-25"
        server._last_advertised_activity = None
        server._current_activity = lambda: "discovery"
        with patch.dict(os.environ, {"DYNOSAI_PHASE_TOOL_DISCLOSURE": "adaptive", "DYNOSAI_MCP_TOOL_PROFILE": "acceptance"}, clear=False):
            first = server.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"})["result"]
        names = [x["name"] for x in first["tools"]]
        self.assertNotIn("dynosai_checkpoint", names)
        self.assertIn("dynosai_checkpoint", phase_tool_surface("implementation")["tools"])


if __name__ == "__main__":
    unittest.main()
