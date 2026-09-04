import json
import os
import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.capability_manifests import capability_report
from dynosai_flow.certification_matrix import (
    CELL_KEYS,
    HISTORICAL_MATRIX_PATH,
    MATRIX_SCHEMA,
    load_live_matrix,
    validate_live_matrix,
)
from dynosai_flow.cli import demo_spec
from dynosai_flow.db import Database
from dynosai_flow.mcp import LEGACY_TOOLS, TOOLS, MCPServer
from dynosai_flow.version import __version__


def _disjoint_plan() -> dict:
    return {
        "approach": "Two file-disjoint modules claimed by separate governed sessions.",
        "architecture_delta": [],
        "files": [
            {"path": "src/alpha.py", "action": "create", "reason": "alpha"},
            {"path": "src/beta.py", "action": "create", "reason": "beta"},
        ],
        "risks": [],
        "validation_profiles": ["unit"],
        "tasks": [
            {
                "title": "Alpha module",
                "description": "Create alpha without touching beta.",
                "requirements": ["REQ-001"],
                "acceptance": ["AC-001"],
                "files": ["src/alpha.py"],
                "depends_on": [],
                "evidence_required": ["git_diff"],
            },
            {
                "title": "Beta module",
                "description": "Create beta without touching alpha.",
                "requirements": ["REQ-002"],
                "acceptance": ["AC-002"],
                "files": ["src/beta.py"],
                "depends_on": [],
                "evidence_required": ["git_diff"],
            },
        ],
    }


def _overlap_plan() -> dict:
    plan = _disjoint_plan()
    plan["tasks"][1]["files"] = ["src/alpha.py", "src/beta.py"]
    return plan


class DynosAI220Rc2MatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-220-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self._isolate_env()

    def _isolate_env(self):
        keys = ("DYNOSAI_HARNESS_TEAM_SCHEDULER",)
        previous = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ.pop(key, None)

        def restore():
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)

    def _ready_work(self, plan: dict) -> tuple[DynosAIApplication, dict]:
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="RC2", allow_git_init=True)
        work = app.engine.start("Split alpha and beta")
        app.engine.continue_work(work["id"])
        app.engine.submit_spec(work["id"], demo_spec(), "test-220")
        app.engine.continue_work(work["id"])
        app.engine.review(work["id"], "spec")
        app.engine.submit_plan(work["id"], plan, "test-220")
        app.engine.review(work["id"], "plan")
        app.engine.continue_work(work["id"])
        return app, app.engine.get_work(work["id"])

    def test_matrix_1_0_placeholder_is_not_run_and_does_not_copy_0_13(self):
        matrix = load_live_matrix()
        self.assertEqual(matrix["schema"], MATRIX_SCHEMA)
        self.assertEqual(matrix["schema_version"], 1)
        self.assertFalse(matrix["copied_from_historical"])
        self.assertFalse(matrix["all_passed"])
        cells = {(cell["provider"], cell["mode"]): cell for cell in matrix["cells"]}
        self.assertEqual(set(cells), set(CELL_KEYS))
        for cell in cells.values():
            self.assertIn(cell["status"], {"not_run", "run", "pass", "fail"})
            self.assertNotEqual(cell.get("evidence"), HISTORICAL_MATRIX_PATH)
            if cell["status"] != "pass":
                self.assertNotEqual(cell.get("quality_score"), 100)
        dumped = json.dumps(matrix)
        self.assertNotIn('"quality_score": 100', dumped)
        validate_live_matrix(matrix)
        repo = Path(__file__).resolve().parents[1]
        historical = json.loads((repo / HISTORICAL_MATRIX_PATH).read_text(encoding="utf-8"))
        self.assertEqual(historical["release"], "0.13.0")
        self.assertTrue(historical["matrix_passed"])
        self.assertNotEqual(historical["release"], matrix.get("release_line"))
        checked = json.loads((repo / "docs/validation/matrix-1.0.json").read_text(encoding="utf-8"))
        validate_live_matrix(checked)
        self.assertEqual(len(checked["cells"]), 4)
        self.assertFalse(checked["copied_from_historical"])

    def test_stats_and_capabilities_are_provider_aware_not_globally_live(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Matrix stats", allow_git_init=True)
        stats = app.engine.stats()
        live = stats["live_matrix"]
        self.assertEqual(live["schema"], MATRIX_SCHEMA)
        self.assertEqual(set(live["cells"]), {f"{provider}.{mode}" for provider, mode in CELL_KEYS})
        self.assertFalse(live["all_passed"])
        self.assertNotIn("certified", live)
        report = capability_report()
        self.assertEqual(report["certification"], "0.13.0-matrix")
        self.assertEqual(set(report["live_matrix"]["cells"]), set(live["cells"]))
        self.assertFalse(report["live_matrix"]["all_passed"])
        overview = app.project_overview()
        self.assertEqual(overview["live_matrix"]["schema"], MATRIX_SCHEMA)
        bundle = app.engine.diagnostic_bundle()
        self.assertIn("live_matrix.json", bundle["files"])
        api = StudioAPI(self.tmp)
        status, payload = api.get("/api/overview")
        self.assertEqual(status, 200)
        self.assertIn(payload["live_matrix"]["cells"]["cursor.brownfield"], {"not_run", "run", "pass", "fail"})

    def test_two_host_sessions_claim_file_disjoint_leases_without_spawning(self):
        app, work = self._ready_work(_disjoint_plan())
        self.assertEqual(work["state"], "implementing")
        plan = app.engine.schedule(work["id"])
        self.assertTrue(plan["feature_enabled"])
        self.assertEqual(plan["current_wave"]["kind"], "parallel")
        self.assertEqual(plan["max_parallel"], 2)
        self.assertFalse(plan["spawn_providers"])
        first = app.engine.claim_lease(
            work["id"], plan["waves"][0]["task_ids"][0], "session-a",
            role="implementer", session_id="host-session-a",
        )
        second = app.engine.claim_lease(
            work["id"], plan["waves"][0]["task_ids"][1], "session-b",
            role="implementer", session_id="host-session-b",
        )
        self.assertFalse(first["spawn_provider"])
        self.assertFalse(second["spawn_provider"])
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["session_id"], "host-session-a")
        self.assertEqual(second["session_id"], "host-session-b")
        self.assertFalse(set(first["lease"]["files"]) & set(second["lease"]["files"]))
        refreshed = app.engine.schedule(work["id"])
        self.assertEqual(set(refreshed["claimed_leases"]), {"LEASE-" + plan["waves"][0]["task_ids"][0], "LEASE-" + plan["waves"][0]["task_ids"][1]})
        fan = app.engine.fan_in_report(work["id"])
        self.assertFalse(fan["spawn_providers"])

    def test_overlapping_scopes_serialize_and_fan_in_conflicts(self):
        app, work = self._ready_work(_overlap_plan())
        plan = app.engine.schedule(work["id"])
        self.assertEqual(plan["current_wave"]["kind"], "serial")
        self.assertEqual(plan["max_parallel"], 1)
        first_id = plan["waves"][0]["task_ids"][0]
        later_id = plan["waves"][1]["task_ids"][0]
        claimed = app.engine.claim_lease(work["id"], first_id, "session-a", session_id="host-session-a")
        self.assertFalse(claimed["spawn_provider"])
        with self.assertRaises(ValueError):
            app.engine.claim_lease(work["id"], later_id, "session-b", session_id="host-session-b")
        from dynosai_flow.team_scheduler import fan_in_conflicts
        conflicts = fan_in_conflicts([
            {"lease_id": "LEASE-T01", "status": "verified", "files": ["src/alpha.py"]},
            {"lease_id": "LEASE-T02", "status": "verified", "files": ["src/alpha.py", "src/beta.py"]},
        ])
        self.assertTrue(conflicts["blocked"])
        self.assertIn("src/alpha.py", conflicts["conflicts"])

    def test_scheduler_off_keeps_serial_two_session_behavior(self):
        app, work = self._ready_work(_disjoint_plan())
        app.set_harness_features({"team_scheduler": False})
        plan = app.engine.schedule(work["id"])
        self.assertFalse(plan["feature_enabled"])
        self.assertEqual(plan["parallelism"], "serial_fallback")
        self.assertEqual(plan["max_parallel"], 1)
        first_id = plan["current_wave"]["task_ids"][0]
        other_id = next(task_id for wave in plan["waves"] for task_id in wave["task_ids"] if task_id != first_id)
        first = app.engine.claim_lease(work["id"], first_id, "session-a", session_id="host-session-a")
        self.assertFalse(first["spawn_provider"])
        with self.assertRaises(ValueError):
            app.engine.claim_lease(work["id"], other_id, "session-b", session_id="host-session-b")
        server = MCPServer(self.tmp)
        refused = server.call_tool("dynosai_schedule", {"work_id": work["id"]})
        self.assertEqual(refused["error"], "feature_disabled")
        self.assertTrue(refused["claimed_leases_remain_finishable"])

    def test_mcp_surface_schema_and_studio_copy(self):
        names = {str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}
        self.assertEqual(len(names), 31)
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)
        self.assertTrue(__version__.startswith("1.0.0rc"))
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        script = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("live-matrix", html)
        self.assertIn("liveMatrixHtml", script)
        self.assertIn("matrix.title", i18n)
        self.assertIn("1.0 live certification", i18n)
        self.assertIn("Certificación viva 1.0", i18n)
        self.assertIn("Not run", i18n)
        self.assertIn("Sin ejecutar", i18n)


if __name__ == "__main__":
    unittest.main()
