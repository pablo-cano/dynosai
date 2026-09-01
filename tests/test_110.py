import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.cli import parser
from dynosai_flow.context_control import EvidenceCache, ContextCheckpointStore
from dynosai_flow.db import Database
from dynosai_flow.cost_telemetry import estimate_list_price
from dynosai_flow.estimator_calibration import UsageEstimatorCalibration
from dynosai_flow.managed_runtime import ManagedProviderRuntime
from dynosai_flow.mcp import MCPServer, advertised_tools, phase_tool_surface
from dynosai_flow.model_benchmark import ModelBenchmarkMatrix
from dynosai_flow.model_control import ModelControlPlane, PHASE_TOKEN_BUDGETS, classify_validation_failure, retry_policy_for
from dynosai_flow.token_usage import TokenUsageRecorder
from dynosai_flow.version import __version__


class DynosAI110Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_version_is_0110(self):
        self.assertEqual(__version__, "0.19.0")

    def test_phase_aware_model_control_starts_cheap_and_escalates_complex_work(self):
        control = ModelControlPlane(self.tmp)
        simple = control.decide("cursor", "discovery", extra_signals={"repository_files": 2}, at_provider_boundary=True)
        self.assertEqual(simple.tier, "economy")
        self.assertEqual(simple.route.model, "gpt-5.6-luna")
        self.assertEqual(simple.route.effort, "low")
        normal_impl = control.decide("codex", "implementation", extra_signals={"repository_files": 80, "planned_file_count": 6}, at_provider_boundary=True)
        self.assertEqual(normal_impl.tier, "standard")
        self.assertEqual(normal_impl.route.model, "gpt-5.6-terra")
        complex_review = control.decide("codex", "code_review", extra_signals={"repository_files": 300, "planned_file_count": 15, "task_count": 5, "requirement_count": 10}, at_provider_boundary=True)
        self.assertEqual(complex_review.tier, "strong")
        self.assertEqual(complex_review.route.model, "gpt-5.6-sol")
        self.assertEqual(complex_review.route.effort, "high")

    def test_explicit_project_model_pin_wins_over_adaptive_control(self):
        cfg = self.tmp / ".dynosai" / "provider-models.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('[providers.cursor.default]\nmodel = "grok-4.6"\neffort = "medium"\n', encoding="utf-8")
        decision = ModelControlPlane(self.tmp).decide("cursor", "implementation", extra_signals={"repository_files": 500}, at_provider_boundary=True)
        self.assertEqual(decision.tier, "pinned")
        self.assertEqual(decision.route.model, "grok-4.6")
        self.assertIn("explicit_provider_model_route", decision.reasons)

    def test_token_budget_prefers_phase_allocated_usage_and_escalates_only_at_hard_limit(self):
        usage = {
            "context_read_tokens": 999999,
            "generated_tokens": 99999,
            "phase_usage": [
                {"phase": "implementation", "delta_context_read_tokens": 120000, "delta_generated_tokens": 10000},
                {"phase": "implementation", "delta_context_read_tokens": 10000, "delta_generated_tokens": 1000},
            ],
        }
        budget = ModelControlPlane.assess_budget("implementation", usage)
        self.assertEqual(budget.measurement_scope, "phase_allocated")
        self.assertEqual(budget.context_tokens, 130000)
        self.assertEqual(budget.status, "within_budget")
        hard = ModelControlPlane.assess_budget("discovery", {"context_read_tokens": PHASE_TOKEN_BUDGETS["discovery"]["context_hard"] + 1})
        self.assertEqual(hard.status, "unallocatable")
        self.assertEqual(hard.measurement_scope, "process_fallback")
        d = ModelControlPlane(self.tmp).decide("cursor", "discovery", usage={"context_read_tokens": 80000}, at_provider_boundary=False)
        self.assertFalse(d.escalation["recommended"])
        self.assertIn("telemetry_not_comparable_no_escalation", d.reasons)
        phase_hard={"phase_usage":[{"phase":"discovery","delta_context_read_tokens":80000,"delta_generated_tokens":1000}]}
        d2 = ModelControlPlane(self.tmp).decide("cursor", "discovery", usage=phase_hard, at_provider_boundary=False)
        self.assertFalse(d2.escalation["recommended"])
        self.assertIn("phase_token_pressure_compaction_first", d2.reasons)
        self.assertIn("reevaluate_after_compaction", d2.budget.actions)
        self.assertFalse(d2.apply_now)
        self.assertFalse(d2.safe_boundary)

    def test_model_control_reads_real_project_database_signals(self):
        root=self.tmp/"dbproj"; db=Database(root); db.initialize()
        now="2026-08-24T00:00:00+00:00"
        db.execute("INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,0,1,0,?,?)",("DYN-1","x","x","feature","implementing",now,now))
        db.execute("INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",("TASK-1","DYN-1","t","d","todo",'["R1"]','[]','["a.py","b.py"]',now,now))
        db.execute("INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",("DYN-1","pytest","failed",1,"AssertionError test failed",now))
        decision=ModelControlPlane(root).decide("cursor","implementation",work_id="DYN-1")
        self.assertEqual(decision.complexity.signals["planned_file_count"],2)
        self.assertEqual(decision.complexity.signals["consecutive_validation_failures"],1)
        self.assertEqual(decision.complexity.signals["failure_kind"],"test")

    def test_model_escalation_memory_resets_after_successful_gate(self):
        c=ModelControlPlane(self.tmp/"state")
        c.record_outcome("cursor","DYN-1",success=False,failure_kind="test",activity="implementation")
        failed=c.record_outcome("cursor","DYN-1",success=False,failure_kind="test",activity="implementation")
        self.assertEqual(failed["tier"],"standard")
        clean=c.record_outcome("cursor","DYN-1",success=True,activity="validation")
        self.assertEqual(clean["tier"],"economy")
        self.assertEqual(clean["same_tier_retries"],0)

    def test_validation_retry_policy_is_bounded_and_classified(self):
        self.assertEqual(classify_validation_failure("AssertionError: test failed", exit_code=1), "test")
        one = retry_policy_for("test", 1)
        two = retry_policy_for("test", 2)
        scope = retry_policy_for("scope", 1)
        self.assertTrue(one["same_tier_retry_allowed"])
        self.assertTrue(two["escalate_after_retry"])
        self.assertFalse(scope["retryable"])

    def test_cost_telemetry_separates_cursor_cache_and_codex_cached_subset(self):
        cursor = estimate_list_price("cursor", "gpt-5.6-luna", {
            "quality": "exact", "input_tokens": 60, "cached_input_tokens": 640123,
            "cache_write_input_tokens": 49889, "output_tokens": 4442,
        })
        self.assertIsNotNone(cursor)
        self.assertEqual(cursor["fresh_input_tokens"], 60)
        self.assertAlmostEqual(cursor["estimated_list_price_usd"], 0.03061711, places=8)
        codex = estimate_list_price("codex", "gpt-5.6-luna", {
            "quality": "exact", "input_tokens": 1000, "cached_input_tokens": 800,
            "cache_write_input_tokens": 0, "output_tokens": 100,
        })
        self.assertEqual(codex["fresh_input_tokens"], 200)

    def test_learned_estimator_calibration_is_robust_and_stays_estimated(self):
        path = self.tmp / "calibration.json"
        cal = UsageEstimatorCalibration(path)
        update = cal.update("cursor", "gpt-5.6-luna", {"context_read_tokens": 39332, "generated_tokens": 81}, {"context_read_tokens": 640183, "generated_tokens": 4442}, "fibonacci")
        self.assertTrue(update["available"])
        scaled = cal.apply("cursor", "gpt-5.6-luna", {"context_read_tokens": 1000, "generated_tokens": 100}, "fibonacci")
        self.assertEqual(scaled["quality"], "calibrated_estimate")
        self.assertGreater(scaled["context_read_tokens"], 15000)
        self.assertGreaterEqual(scaled["generated_tokens"], 5000)

    def test_evidence_cache_deduplicates_only_unchanged_content_within_session(self):
        cache = EvidenceCache()
        first, meta1 = cache.compact("session-a", "read:a.py:1:10", {"text": "abc" * 500})
        second, meta2 = cache.compact("session-a", "read:a.py:1:10", {"text": "abc" * 500})
        changed, meta3 = cache.compact("session-a", "read:a.py:1:10", {"text": "abcd" * 500})
        other, meta4 = cache.compact("session-b", "read:a.py:1:10", {"text": "abc" * 500})
        self.assertIn("_dynosai_evidence_ref", first)
        self.assertTrue(second["unchanged"])
        self.assertGreater(meta2["estimated_bytes_omitted"], 0)
        self.assertFalse(meta3["unchanged"])
        self.assertFalse(meta4["unchanged"])

    def test_dynamic_tool_disclosure_uses_safe_initial_superset_then_phase_subset(self):
        server = MCPServer.__new__(MCPServer)
        server.negotiated = "2025-11-25"
        server._last_advertised_activity = None
        server._current_activity = lambda: "discovery"
        with patch.dict(os.environ, {"DYNOSAI_PHASE_TOOL_DISCLOSURE": "adaptive", "DYNOSAI_MCP_TOOL_PROFILE": "acceptance"}, clear=False):
            first = server.handle({"jsonrpc":"2.0","id":1,"method":"tools/list"})["result"]
            second = server.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"})["result"]
        self.assertEqual(len(first["tools"]), 15)
        self.assertTrue(first["dynosaiToolSurface"]["initial_safe_superset"])
        self.assertEqual(len(second["tools"]), phase_tool_surface("discovery")["tool_count"])
        self.assertLess(len(second["tools"]), 15)
        self.assertIn("dynosai_retrieve_handle", [item["name"] for item in first["tools"]])

    def test_context_checkpoint_creates_bounded_authoritative_resume_capsule(self):
        class DB:
            def query(self, sql, params):
                if "FROM decisions" in sql: return [{"question":"DB?","answer":"Postgres"}]
                if "FROM tasks" in sql: return [{"id":"TASK-1","title":"Implement","state":"todo","requirements":"[]","acceptance":"[]","files":"[\"a.py\"]","depends_on":"[]"}]
                if "FROM validations" in sql: return [{"command":"pytest","status":"passed","exit_code":0}]
                if "FROM runs" in sql: return []
                if "FROM scope_requests" in sql: return []
                return []
        class Artifacts:
            def artifact(self, work_id, kind):
                if kind=="spec": return {"metadata":{"objective":"Do it","requirements":[{"id":"R1","text":"x"}]}}
                if kind=="plan": return {"metadata":{"approach":"small","files":[{"path":"a.py","action":"modify"}]}}
        class Engine:
            db=DB(); artifacts=Artifacts()
            def get_work(self, work_id): return {"id":work_id,"title":"Feature","description":"Goal","state":"ready","quality_score":100,"workspace_strategy":"interactive_branch"}
        store=ContextCheckpointStore(self.tmp/"cp")
        ref=store.capture(Engine(),"DYN-1",reason="phase_boundary")
        capsule=store.prompt_capsule("DYN-1")
        self.assertTrue(ref["ref"].startswith("CTX-"))
        self.assertIn("authoritative context checkpoint",capsule)
        self.assertIn("Postgres",capsule)
        self.assertIn("TASK-1",capsule)

    def test_managed_codex_runtime_passes_strict_and_control_env_into_mcp(self):
        project = self.tmp / "p"
        project.mkdir()
        AgentConfiguration(project).init()
        runtime = ManagedProviderRuntime(project).prepare("codex", activity="discovery", extra_env={"DYNOSAI_TOKEN_USAGE_FILE":"/tmp/usage.json"})
        config = (Path(runtime.root) / "home" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('DYNOSAI_STRICT_MANAGED_RUNTIME = "1"', config)
        self.assertIn('DYNOSAI_MODEL_CONTROL_POLICY = "adaptive"', config)
        self.assertIn('DYNOSAI_PHASE_TOOL_DISCLOSURE = "adaptive"', config)
        self.assertIn('DYNOSAI_TOKEN_USAGE_FILE = "/tmp/usage.json"', config)

    def test_token_usage_schema_4_contains_calibration_and_cost(self):
        calibration = self.tmp / "cal.json"
        with patch.dict(os.environ, {"DYNOSAI_TOKEN_CALIBRATION_PATH": str(calibration)}, clear=False):
            rec = TokenUsageRecorder(self.tmp / "usage", "cursor", "p", model="gpt-5.6-luna", scenario="fibonacci")
            rec.add_estimate(input_tokens=100, output_tokens=10)
            final = rec.observe_exact({"inputTokens":200,"cacheReadTokens":300,"outputTokens":20}, source="cursor_result")
        self.assertEqual(final["schema_version"], 4)
        self.assertIsNotNone(final["cost_estimate"])
        self.assertIsNotNone(final["calibration_update"])
        self.assertIn("calibrated_live_estimate", final)

    def test_model_benchmark_hard_gates_quality_then_ranks_cost(self):
        def child(model, cost, elapsed, passed=True, quality=100):
            return {"provider":"cursor","scenario":"fibonacci","status":"passed" if passed else "failed","quality_score":quality,
                    "model_route":{"model":model,"effort":"medium"},"oracle":{"passed":passed},"gates":{"strict_managed_runtime_verified":True},
                    "token_usage":{"context_read_tokens":100,"generated_tokens":10,"processed_tokens":110,"cost_estimate":{"estimated_list_price_usd":cost}},
                    "runtime_metrics":{"workflow_elapsed_seconds":elapsed,"mcp_calls":10,"reconnect_count":0}}
        a=self.tmp/"a.json"; b=self.tmp/"b.json"; c=self.tmp/"c.json"
        a.write_text(json.dumps({"dynosai_version":"0.11.0","children":[child("luna",.02,60)]}))
        b.write_text(json.dumps({"dynosai_version":"0.11.0","children":[child("terra",.05,40)]}))
        c.write_text(json.dumps({"dynosai_version":"0.11.0","children":[child("bad",.001,10,passed=False)]}))
        result=ModelBenchmarkMatrix.rank([a,b,c])
        self.assertEqual(result["successful_runs"],2)
        self.assertEqual(result["ranked"][0]["model"],"luna")
        self.assertNotIn("bad",[x["model"] for x in result["ranked"]])

    def test_new_cli_surfaces_are_parseable(self):
        p=parser()
        show=p.parse_args(["model-control","show","--provider","cursor","--policy","adaptive"])
        outcome=p.parse_args(["model-control","outcome","--provider","codex","--work-id","DYN-1","--failure-kind","test"])
        bench=p.parse_args(["model-benchmark","a.zip","b.zip"])
        self.assertEqual(show.command,"model-control")
        self.assertEqual(outcome.action,"outcome")
        self.assertEqual(bench.command,"model-benchmark")


if __name__ == "__main__":
    unittest.main()
