import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.db import Database
from dynosai_flow.eval_intelligence import attribute_failure
from dynosai_flow.eval_registry import EvalRegistry
from dynosai_flow.util import utc_now
from dynosai_flow.version import __version__


class DynosAI017EvalIntelligenceTests(unittest.TestCase):
    def test_attribution_keeps_non_model_failures_out_of_predictive_learning(self):
        gate = attribute_failure("human_gate")
        self.assertEqual(gate["layer"], "governance")
        self.assertFalse(gate["eligible_for_predictive_learning"])
        self.assertEqual(gate["predictive_routing"], "shadow")
        provider = attribute_failure("provider")
        self.assertEqual(provider["layer"], "provider")
        self.assertFalse(provider["eligible_for_predictive_learning"])
        validation = attribute_failure("validation")
        self.assertEqual(validation["layer"], "project")

    def test_mining_propose_and_regression_stay_in_inbox(self):
        tmp = Path(tempfile.mkdtemp(prefix="dynosai-180-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        app = DynosAIApplication(tmp)
        app.initialize_project(name="EvalIntel", allow_git_init=True)
        work = app.engine.start("Track a failure")
        app.engine.db.execute(
            "INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",
            (work["id"], "pytest", "failed", 1, "1 failed", utc_now()),
        )
        report = app.engine.eval_intelligence()
        self.assertEqual(report["predictive_routing"], "shadow")
        self.assertFalse(report["live_provider_evals"])
        self.assertFalse(report["auto_start_provider"])
        self.assertTrue(report["open_cases"])
        case_id = report["open_cases"][0]
        proposed = app.propose_eval_improvement(case_id)
        self.assertEqual(proposed["work"]["state"], "inbox")
        self.assertFalse(proposed["spawn_provider"])
        self.assertFalse(proposed["auto_start"])
        listed = app.list_work()
        self.assertTrue(any(row["id"] == proposed["work"]["id"] for row in listed))
        overview = app.project_overview()
        self.assertTrue(overview["eval_intelligence"]["proposed_cases"])
        recorded = app.engine.register_eval_regression(case_id)
        self.assertEqual(recorded["status"], "regressed")
        self.assertFalse(recorded["work_completed"])
        self.assertFalse(recorded["auto_approved"])
        self.assertEqual(app.engine.get_work(proposed["work"]["id"])["state"], "inbox")

    def test_offline_team_overlap_fixture_does_not_call_providers(self):
        tmp = Path(tempfile.mkdtemp(prefix="dynosai-180-eval-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        record = EvalRegistry(tmp).run_offline("team_overlap")
        self.assertTrue(record["success"])
        self.assertEqual(record["metrics"]["conflicts"], ["src/shared.py"])
        self.assertIn("offline fixture", str(record["metrics"].get("note") or "").lower())

    def test_schema_and_version_remain_governed(self):
        self.assertEqual(__version__, "0.17.0")
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
