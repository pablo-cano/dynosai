import os
import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.context_handles import ContextHandleStore
from dynosai_flow.db import Database
from dynosai_flow.harness_contracts import OPTIONAL_HARNESS_FEATURES, harness_enabled
from dynosai_flow.mcp import LEGACY_TOOLS, TOOLS, MCPServer
from dynosai_flow.team_scheduler import build_team_plan
from dynosai_flow.util import utc_now
from dynosai_flow.version import __version__


FROZEN_MCP_TOOL_NAMES = frozenset({
    "dynosai_project",
    "dynosai_work",
    "dynosai_resume",
    "dynosai_events",
    "dynosai_project_overview",
    "dynosai_get_next_action",
    "dynosai_start",
    "dynosai_continue",
    "dynosai_board",
    "dynosai_ask",
    "dynosai_context_preview",
    "dynosai_checkpoint",
    "dynosai_retrieve_handle",
    "dynosai_register_decision",
    "dynosai_submit_spec",
    "dynosai_submit_plan",
    "dynosai_register_result",
    "dynosai_request_scope_extension",
    "dynosai_find_symbol",
    "dynosai_read",
    "dynosai_git_status",
    "dynosai_git_diff",
    "dynosai_refresh_overlay",
    "dynosai_analyze_impact",
    "dynosai_schedule",
    "dynosai_semantic_status",
    "dynosai_sync",
    "dynosai_stats",
    "dynosai_run_validation",
    "dynosai_get_symbol",
    "dynosai_get_file_slice",
})

HARNESS_I18N_KEYS = (
    "harness.title",
    "harness.contextHandles",
    "harness.teamScheduler",
    "harness.evalIntelligence",
    "harness.on",
    "harness.off",
    "harness.saved",
    "harness.authorityNote",
)


def _current_mcp_names() -> set[str]:
    return {str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}


class DynosAI210Rc1ContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-210-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _isolate_env(self, **values: str | None):
        keys = {
            "DYNOSAI_HARNESS_CONTEXT_HANDLES",
            "DYNOSAI_CONTEXT_HANDLES",
            "DYNOSAI_HARNESS_TEAM_SCHEDULER",
            "DYNOSAI_HARNESS_EVAL_INTELLIGENCE",
            "DYNOSAI_HARNESS_SKILLS",
        }
        previous = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ.pop(key, None)
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        def restore():
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)

    def test_mcp_surface_is_frozen_at_31_unique_names(self):
        current = _current_mcp_names()
        self.assertEqual(current, FROZEN_MCP_TOOL_NAMES)
        self.assertEqual(len(current), 31)
        advertised = {str(item["name"]) for item in TOOLS}
        self.assertEqual(len(advertised), 29)
        self.assertEqual(advertised | {"dynosai_get_symbol", "dynosai_get_file_slice"}, current)

    def test_schema_remains_v6(self):
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Schema", allow_git_init=True)
        self.assertEqual(int(app.engine.db.get_meta("schema_version", "0")), 6)

    def test_stats_expose_frozen_mcp_surface(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Stats", allow_git_init=True)
        stats = app.engine.stats()
        self.assertTrue(stats["mcp_surface_frozen"])
        self.assertEqual(stats["mcp_tool_count"], 31)
        self.assertEqual(set(stats["harness"]["features"]), set(OPTIONAL_HARNESS_FEATURES))
        self.assertEqual(stats["harness"]["precedence"], ["environment", "project", "default"])

    def test_harness_precedence_env_overrides_project(self):
        self._isolate_env()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Flags", allow_git_init=True)
        app.set_harness_features({"context_handles": True, "team_scheduler": False})
        self.assertTrue(app.engine.harness_feature("context_handles"))
        self.assertFalse(app.engine.harness_feature("team_scheduler"))
        report = app.engine.harness_report()
        self.assertEqual(report["by_name"]["team_scheduler"]["source"], "project")
        self._isolate_env(DYNOSAI_HARNESS_CONTEXT_HANDLES="0", DYNOSAI_HARNESS_TEAM_SCHEDULER="1")
        self.assertFalse(harness_enabled("context_handles", project_enabled=True))
        self.assertFalse(app.engine.harness_feature("context_handles"))
        self.assertTrue(app.engine.harness_feature("team_scheduler"))
        self.assertEqual(app.engine.harness_report()["by_name"]["context_handles"]["source"], "environment")
        self.assertTrue(app.engine.harness_report()["by_name"]["context_handles"]["env_locked"])

    def test_context_handles_off_keep_inline_and_historical_reads(self):
        self._isolate_env()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Handles", allow_git_init=True)
        store = ContextHandleStore(self.tmp)
        blob = "diff --git a/x.py b/x.py\n" + ("+line\n" * 3000)
        created = store.wrap_tool_result(
            "dynosai_git_diff",
            {"files": ["x.py"], "diff": blob, "denied": [], "truncated": False},
            enabled=True,
            inline_limit=1024,
        )
        handle_id = created["context_handle"]["id"]
        self._isolate_env(DYNOSAI_HARNESS_CONTEXT_HANDLES="0")
        inline = store.wrap_tool_result(
            "dynosai_git_diff",
            {"files": ["x.py"], "diff": blob, "denied": [], "truncated": False},
            enabled=False,
            inline_limit=1024,
        )
        self.assertEqual(inline["diff"], blob)
        self.assertIsNone(inline["context_handle_metrics"]["handle_id"])
        self.assertNotIn("context_handle", inline)
        retrieved = store.retrieve(handle_id, offset=0, limit=12)
        self.assertEqual(retrieved["content"], blob[:12])
        server = MCPServer(self.tmp)
        payload = server.call_tool("dynosai_retrieve_handle", {"handle_id": handle_id, "offset": 0, "limit": 8})
        self.assertEqual(payload["content"], blob[:8])

    def test_scheduler_off_uses_serial_fallback_and_refuses_mcp_plan(self):
        self._isolate_env()
        parallel = build_team_plan([
            {"id": "T01", "state": "ready", "files": ["src/a.py"], "depends_on": []},
            {"id": "T02", "state": "ready", "files": ["src/b.py"], "depends_on": []},
        ])
        self.assertEqual(parallel["max_parallel"], 2)
        serial = build_team_plan([
            {"id": "T01", "state": "ready", "files": ["src/a.py"], "depends_on": []},
            {"id": "T02", "state": "ready", "files": ["src/b.py"], "depends_on": []},
        ], parallel=False)
        self.assertEqual(serial["parallelism"], "serial_fallback")
        self.assertEqual(serial["max_parallel"], 1)
        self.assertEqual(serial["current_wave"]["kind"], "serial")
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Schedule", allow_git_init=True)
        work = app.engine.start("Create Fibonacci")
        app.engine.continue_work(work["id"])
        app.engine.submit_spec(work["id"], demo_spec(), "test-210")
        app.engine.continue_work(work["id"])
        app.engine.review(work["id"], "spec")
        app.engine.submit_plan(work["id"], demo_plan(), "test-210")
        app.engine.review(work["id"], "plan")
        claimed = app.engine.schedule(work["id"])
        self.assertTrue(claimed["feature_enabled"])
        app.set_harness_features({"team_scheduler": False})
        fallback = app.engine.schedule(work["id"])
        self.assertFalse(fallback["feature_enabled"])
        self.assertEqual(fallback["parallelism"], "serial_fallback")
        self.assertLessEqual(fallback["max_parallel"], 1)
        server = MCPServer(self.tmp)
        refused = server.call_tool("dynosai_schedule", {"work_id": work["id"]})
        self.assertEqual(refused["error"], "feature_disabled")
        self.assertEqual(refused["feature"], "team_scheduler")
        self.assertTrue(refused["serial_fallback"])
        self.assertTrue(refused["claimed_leases_remain_finishable"])

    def test_eval_intelligence_off_hides_propose_and_keeps_history(self):
        self._isolate_env()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Eval", allow_git_init=True)
        work = app.engine.start("Track a failure")
        app.engine.db.execute(
            "INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",
            (work["id"], "pytest", "failed", 1, "1 failed", utc_now()),
        )
        mined = app.engine.eval_intelligence()
        case_id = mined["open_cases"][0]
        app.set_harness_features({"eval_intelligence": False})
        historical = app.engine.eval_intelligence()
        self.assertFalse(historical["enabled"])
        self.assertFalse(historical["propose"])
        self.assertTrue(any(case.get("case_id") == case_id for case in historical["cases"]))
        api = StudioAPI(self.tmp)
        status, payload = api.post("/api/eval/propose", {"case_id": case_id})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "feature_disabled")
        self.assertEqual(payload["feature"], "eval_intelligence")

    def test_mcp_cannot_alter_harness_or_execution_profile(self):
        self._isolate_env()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Authority", allow_git_init=True)
        app.set_execution_profile("strict")
        app.set_harness_features({"eval_intelligence": False})
        names = _current_mcp_names()
        self.assertTrue(names.isdisjoint({
            "dynosai_set_harness",
            "dynosai_set_execution_profile",
            "dynosai_project_settings",
        }))
        server = MCPServer(self.tmp)
        server.call_tool("dynosai_stats", {})
        self.assertEqual(app.engine.execution_policy()["profile"], "strict")
        self.assertEqual(app.engine.execution_policy()["human_gates"], "required")
        self.assertFalse(app.engine.harness_feature("eval_intelligence"))
        with self.assertRaises(ValueError):
            app.engine.set_harness_features({"execution_profiles": False})

    def test_app_server_and_studio_surfaces(self):
        self._isolate_env()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Studio", allow_git_init=True)
        api = StudioAPI(self.tmp)
        health_status, health = api.get("/api/health")
        self.assertEqual(health_status, 200)
        self.assertEqual(health["version"], "1.0.0rc3")
        self.assertEqual(health["display_version"], "1.0.0-rc.3")
        status, payload = api.get("/api/project-settings")
        self.assertEqual(status, 200)
        self.assertIn("harness", payload)
        self.assertEqual(payload["execution_policy"]["human_gates"], "required")
        status, saved = api.post("/api/project-settings", {"harness": {"context_handles": False}})
        self.assertEqual(status, 200)
        self.assertFalse(saved["harness"]["by_name"]["context_handles"]["enabled"])
        overview = app.project_overview()
        self.assertFalse(overview["harness"]["by_name"]["context_handles"]["enabled"])
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        script = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("harness-features", html)
        self.assertIn("data-harness=\"context_handles\"", html)
        self.assertIn("harnessStatusHtml", script)
        self.assertIn("intel.enabled !== false", script)
        for key in HARNESS_I18N_KEYS:
            self.assertIn(f'"{key}"', i18n)
        self.assertIn("Context optimization", i18n)
        self.assertIn("Optimización de contexto", i18n)
        self.assertIn("Governed team scheduling", i18n)
        self.assertIn("Planificación de equipo gobernada", i18n)
        self.assertIn("Eval intelligence", i18n)
        self.assertIn("Inteligencia de eval", i18n)
        self.assertNotIn("DYNOSAI_DISABLE_", script)
        self.assertNotIn("DYNOSAI_DISABLE_", i18n)

    def test_no_negative_disable_subsystem(self):
        root = Path(__file__).resolve().parents[1]
        hits = []
        for path in (root / "src" / "dynosai_flow").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "DYNOSAI_DISABLE_" in text:
                hits.append(path.name)
        self.assertEqual(hits, [])
        self.assertEqual(__version__, "1.0.0rc3")


if __name__ == "__main__":
    unittest.main()
