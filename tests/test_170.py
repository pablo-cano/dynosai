import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.team_scheduler import (
    build_team_plan,
    claim_allowed,
    detect_path_conflicts,
    fan_in_conflicts,
    follow_on_roles,
    lease_for_task,
)


def _task(task_id, files, depends_on=None, state="ready", evidence=None, validation=None):
    return {
        "id": task_id,
        "title": task_id,
        "state": state,
        "files": files,
        "depends_on": depends_on or [],
        "evidence_required": evidence or ["git_diff"],
        "validation_commands": validation or [],
    }


class DynosAI016TeamSchedulerTests(unittest.TestCase):
    def test_disjoint_ready_tasks_form_a_parallel_wave(self):
        plan = build_team_plan([
            _task("T01", ["src/a.py"]),
            _task("T02", ["src/b.py"]),
        ])
        self.assertFalse(plan["spawn_providers"])
        self.assertEqual(plan["parallelism"], "lease")
        self.assertEqual(plan["max_parallel"], 2)
        self.assertEqual(plan["current_wave"]["kind"], "parallel")
        self.assertEqual(plan["parallel_batches"], [["T01", "T02"]])

    def test_shared_files_are_serialized(self):
        plan = build_team_plan([
            _task("T01", ["src/shared.py"]),
            _task("T02", ["src/shared.py", "src/other.py"]),
        ])
        self.assertEqual(plan["current_wave"]["kind"], "serial")
        self.assertEqual(plan["parallel_batches"], [["T01"], ["T02"]])

    def test_dependencies_block_the_next_wave(self):
        plan = build_team_plan([
            _task("T01", ["src/a.py"]),
            _task("T02", ["src/b.py"], depends_on=["T01"]),
        ])
        self.assertEqual([wave["task_ids"] for wave in plan["waves"]], [["T01"], ["T02"]])
        self.assertEqual(claim_allowed(plan, "T02")["allowed"], False)

    def test_follow_on_roles_are_contracts_not_spawns(self):
        self.assertEqual(follow_on_roles(_task("T01", ["src/a.py"], evidence=["git_diff"])), [])
        self.assertIn("tester", follow_on_roles(_task("T01", ["tests/test_a.py"], evidence=["test_result"])))
        self.assertIn("reviewer", follow_on_roles(_task("T01", ["src/a.py"]), risk_level="high"))

    def test_claim_rejects_already_claimed_lease(self):
        plan = build_team_plan([_task("T01", ["src/a.py"], state="running")])
        plan["waves"][0]["leases"][0]["status"] = "claimed"
        plan["waves"][0]["leases"][0]["claimed_run"] = "RUN-1"
        decision = claim_allowed(plan, "T01")
        self.assertFalse(decision["allowed"])
        self.assertFalse(decision["spawn_provider"])

    def test_fan_in_blocks_overlapping_verified_scopes(self):
        conflicts = fan_in_conflicts([
            {"lease_id": "LEASE-T01", "status": "verified", "files": ["src/a.py", "src/shared.py"]},
            {"lease_id": "LEASE-T02", "status": "verified", "files": ["src/b.py", "src/shared.py"]},
        ])
        self.assertTrue(conflicts["blocked"])
        self.assertEqual(conflicts["conflicts"], ["src/shared.py"])
        self.assertEqual(detect_path_conflicts(["a"], ["b"]), [])

    def test_open_parallel_lease_can_be_claimed(self):
        plan = build_team_plan([
            _task("T01", ["src/a.py"]),
            _task("T02", ["src/b.py"]),
        ])
        self.assertTrue(claim_allowed(plan, "T01")["allowed"])
        self.assertTrue(claim_allowed(plan, "T02")["allowed"])
        self.assertEqual(lease_for_task(plan, "T02")["scope_ceiling"], ["src/b.py"])

    def test_engine_schedule_does_not_spawn_and_keeps_schema_v6_shape(self):
        tmp = Path(tempfile.mkdtemp(prefix="dynosai-170-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        app = DynosAIApplication(tmp)
        app.initialize_project(name="Team", allow_git_init=True)
        work = app.engine.start("Create Fibonacci")
        app.engine.continue_work(work["id"])
        app.engine.submit_spec(work["id"], demo_spec(), "test-170")
        app.engine.continue_work(work["id"])
        app.engine.review(work["id"], "spec")
        app.engine.submit_plan(work["id"], demo_plan(), "test-170")
        app.engine.review(work["id"], "plan")
        plan = app.engine.schedule(work["id"])
        self.assertEqual(plan["work_id"], work["id"])
        self.assertFalse(plan["spawn_providers"])
        self.assertTrue(plan["waves"])
        self.assertEqual(plan["waves"][0]["kind"], "serial")
        fan = app.engine.fan_in_report(work["id"])
        self.assertFalse(fan["blocked"])
        listed = app.list_work()
        item = next(row for row in listed if row["id"] == work["id"])
        self.assertIn("team", item)
        reviewer = app.engine.claim_lease(work["id"], plan["waves"][0]["task_ids"][0], "codex", role="reviewer")
        self.assertEqual(reviewer["status"], "human_gate")
        self.assertFalse(reviewer["spawn_provider"])


if __name__ == "__main__":
    unittest.main()
