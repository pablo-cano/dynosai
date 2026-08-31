import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.db import Database
from dynosai_flow.interactions import HumanInteractionService
from dynosai_flow.model_control import ModelControlPlane, classify_validation_failure
from dynosai_flow.predictive_routing import PredictiveRoutingMemory
from dynosai_flow.token_usage import TokenUsageRecorder
from dynosai_flow.estimator_calibration import UsageEstimatorCalibration
from dynosai_flow.util import utc_now
from dynosai_flow.version import __version__


class DynosAI121AcceptanceRegressionTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def _db_with_work(self, state="implementing"):
        db = Database(self.tmp)
        db.initialize()
        now = utc_now()
        db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001", "Fibonacci", "Build fibonacci CLI", "feature", state, 0, 1, 100, now, now),
        )
        return db

    def test_version_is_0121(self):
        self.assertEqual(__version__, "0.16.0")

    def test_unittest_missing_start_dir_is_precondition_when_test_task_is_pending(self):
        output = "ImportError: Start directory is not importable: 'tests'"
        kind = classify_validation_failure(output, exit_code=1, pending_planned_files=["tests/test_cli.py"])
        self.assertEqual(kind, "validation_precondition")
        # The same error without a planned pending test artifact remains a real environment/test signal.
        self.assertNotEqual(classify_validation_failure(output, exit_code=1, pending_planned_files=[]), "validation_precondition")

    def test_precondition_never_trains_predictor(self):
        root = self.tmp / "predict"
        ctl = ModelControlPlane(root)
        state = ctl.record_outcome(
            "codex", "DYN-1", success=False, failure_kind="validation_precondition",
            failure_fingerprint="precondition-1", activity="implementation",
        )
        event = state["history"][-1]
        self.assertFalse(event["counts_as_model_failure"])
        self.assertFalse(event["eligible_for_learning"])
        stats = PredictiveRoutingMemory(root).stats("codex", "implementation", "economy")
        self.assertEqual(stats["samples"], 0)
        self.assertEqual(stats["model_failures"], 0)
        self.assertEqual(stats["excluded_observations"], 1)

    def test_scope_failure_is_not_predictive_learning_or_capability_escalation(self):
        root = self.tmp / "scope-routing"
        ctl = ModelControlPlane(root)
        state = ctl.record_outcome(
            "codex", "DYN-1", success=False, failure_kind="scope",
            failure_fingerprint="scope-1", activity="implementation",
        )
        self.assertFalse(state["history"][-1]["counts_as_model_failure"])
        self.assertFalse(state["history"][-1]["eligible_for_learning"])
        self.assertEqual(state["tier"], "economy")
        decision = ctl.decide(
            "codex", "implementation", usage={"phase_usage": []},
            extra_signals={"pending_scope_requests": 3, "repository_files": 0, "task_count": 1},
        )
        self.assertNotIn("scope_risk", decision.reasons)
        self.assertIn("scope_governance_pending_no_capability_escalation", decision.reasons)
        self.assertFalse(decision.escalation["recommended"])
        self.assertEqual(decision.tier, "economy")

    def test_scope_request_changes_closes_exact_linked_scope_row(self):
        app = DynosAIApplication(self.tmp / "scope-app")
        app.engine.db.initialize()
        now = utc_now()
        app.engine.db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001", "Fibonacci", "Build fibonacci CLI", "feature", "implementing", 0, 1, 100, now, now),
        )
        app.engine.db.execute(
            "INSERT INTO scope_requests(id,work_id,run_id,path,action,reason,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            ("SCOPE-0001", "DYN-0001", None, "tests/__init__.py", "create", "unnecessary importability workaround", "pending", now),
        )
        interaction = app.interactions.scope_request({
            "id": "SCOPE-0001", "work_id": "DYN-0001", "path": "tests/__init__.py",
            "action": "create", "reason": "unnecessary importability workaround",
        }, provider="codex")
        result = app.resolve_interaction(
            interaction.id, "accept", {"decision": "request_changes", "comments": "stay inside approved plan"}
        )
        row = app.engine.db.one("SELECT status,resolved_at FROM scope_requests WHERE id='SCOPE-0001'")
        self.assertEqual(row["status"], "declined")
        self.assertIsNotNone(row["resolved_at"])
        self.assertEqual(result["result"]["status"], "declined")
        self.assertEqual(app.engine.review_summary("DYN-0001")["scope_requests"], [])

    def test_scope_cancel_closes_scope_without_cancelling_work(self):
        app = DynosAIApplication(self.tmp / "scope-cancel")
        app.engine.db.initialize()
        now = utc_now()
        app.engine.db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001", "Feature", "desc", "feature", "implementing", 0, 1, 100, now, now),
        )
        app.engine.db.execute(
            "INSERT INTO scope_requests(id,work_id,path,action,reason,status,created_at) VALUES(?,?,?,?,?,?,?)",
            ("SCOPE-0001", "DYN-0001", "extra.py", "create", "extra", "pending", now),
        )
        interaction = app.interactions.scope_request({"id":"SCOPE-0001","work_id":"DYN-0001","path":"extra.py","action":"create","reason":"extra"}, provider="codex")
        app.resolve_interaction(interaction.id, "cancel", {})
        self.assertEqual(app.engine.db.one("SELECT status FROM scope_requests WHERE id='SCOPE-0001'")["status"], "cancelled")
        self.assertEqual(app.engine.db.one("SELECT active FROM work_items WHERE id='DYN-0001'")["active"], 1)

    def test_schema_v6_enforces_single_pending_gate_per_dedupe_key(self):
        db = self._db_with_work(state="code_review")
        service = HumanInteractionService(db)
        first = service.create(
            work_id="DYN-0001", kind="gate_approval", gate="code", message="review",
            requested_schema={"type":"object"}, provider="codex", dedupe_key="gate:DYN-0001:code",
        )
        second = service.create(
            work_id="DYN-0001", kind="gate_approval", gate="code", message="review",
            requested_schema={"type":"object"}, provider="codex", dedupe_key="gate:DYN-0001:code",
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM human_interactions WHERE dedupe_key='gate:DYN-0001:code' AND status='pending'")["n"], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO human_interactions(id,work_id,kind,gate,message,schema_json,response_json,status,provider,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("ELIC-9999","DYN-0001","gate_approval","code","race","{}","{}","pending","codex","gate:DYN-0001:code",utc_now()),
            )

    def test_successful_code_gate_is_consumed_once(self):
        app = DynosAIApplication(self.tmp / "gate-app")
        app.engine.db.initialize()
        now = utc_now()
        app.engine.db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001", "Feature", "desc", "feature", "code_review", 0, 1, 100, now, now),
        )
        interaction = app.interactions.gate_request(app.engine.get_work("DYN-0001"), provider="codex")
        self.assertIsNotNone(interaction)
        original_review = app.engine.review
        def fake_review(work_id, approve=None, changes=None):
            self.assertEqual(approve, "code")
            app.engine.db.execute("UPDATE work_items SET state='validating',updated_at=? WHERE id=?", (utc_now(), work_id))
            return {"approved": True, "state": "validating"}
        app.engine.review = fake_review
        try:
            resolved = app.resolve_interaction(interaction.id, "accept", {"decision":"approve","comments":"ok"})
        finally:
            app.engine.review = original_review
        self.assertEqual(resolved["work"]["state"], "validating")
        self.assertIsNone(app.interactions.gate_request(app.engine.get_work("DYN-0001"), provider="codex"))
        self.assertEqual(app.engine.db.one("SELECT COUNT(*) n FROM human_interactions WHERE dedupe_key='gate:DYN-0001:code'")["n"], 1)

    def test_calibration_update_is_reserved_for_next_run_no_data_leakage(self):
        cal_path = self.tmp / "calibration.json"
        with patch.dict(os.environ, {"DYNOSAI_TOKEN_CALIBRATION_PATH": str(cal_path)}, clear=False):
            rec1 = TokenUsageRecorder(self.tmp / "usage1", "codex", "p1", model="gpt-5.6-luna", scenario="fibonacci")
            rec1.add_estimate(input_tokens=1000, output_tokens=100)
            rec1.observe_exact({"input_tokens": 600_000, "cached_input_tokens": 500_000, "output_tokens": 5000}, source="codex")
            final1 = rec1.finalize()
            self.assertFalse(final1["calibration_before_run"]["available"])
            self.assertEqual(final1["calibrated_live_estimate"]["quality"], "raw_estimate")
            self.assertGreater(final1["calibration_update"]["context_factor"], 500)
            # A new process may use the factor learned by the previous run.
            rec2 = TokenUsageRecorder(self.tmp / "usage2", "codex", "p2", model="gpt-5.6-luna", scenario="fibonacci")
            current2 = rec2.current()
            self.assertTrue(current2["calibration_before_run"]["available"])
            self.assertGreater(current2["calibration_before_run"]["context_factor"], 500)


if __name__ == "__main__":
    unittest.main()
