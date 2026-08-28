import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI, create_server
from dynosai_flow.application import DynosAIApplication, ProjectDetector
from dynosai_flow.cli import parser
from dynosai_flow.risk import RiskAssessment
from dynosai_flow.validation_discovery import ValidationDiscovery
from dynosai_flow.version import __version__


class _FakeArtifacts:
    def artifact(self, work_id, kind):
        if kind != "plan":
            return None
        return {
            "metadata": {
                "files": [
                    {"path": "src/auth/session.py", "action": "modify"},
                    {"path": "migrations/002_tokens.sql", "action": "create"},
                    {"path": "pyproject.toml", "action": "modify"},
                ]
            }
        }


class _FakeDB:
    def query(self, sql, params=()):
        if "scope_requests" in sql:
            return [{"id": "SCOPE-1", "path": "src/security.py", "action": "modify"}]
        if "human_interactions" in sql:
            return []
        return []


class _FakeEngine:
    artifacts = _FakeArtifacts()
    db = _FakeDB()

    def get_work(self, work_id=None):
        return {"id": work_id or "DYN-0001", "state": "implementing"}


class DynosAI140Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-140-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_version_is_at_least_0140(self):
        self.assertGreaterEqual(tuple(int(part) for part in __version__.split(".")), (0, 14, 0))

    def test_project_detector_identifies_typescript_and_suggests_npm_test(self):
        (self.tmp / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run", "build": "next build"}}), encoding="utf-8")
        (self.tmp / "tsconfig.json").write_text("{}", encoding="utf-8")
        info = ProjectDetector().detect(self.tmp)
        self.assertIn("typescript", info["stacks"])
        self.assertEqual(info["primary_language"], "typescript")
        self.assertEqual(info["suggested_test_command"], "npm run test")
        self.assertTrue(any(item["name"] == "build" for item in info["validation_candidates"]))

    def test_validation_discovery_finds_project_scripts_without_executing_them(self):
        (self.tmp / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run", "lint": "eslint .", "typecheck": "tsc --noEmit", "build": "next build"}}), encoding="utf-8")
        candidates = ValidationDiscovery(self.tmp).discover()
        by_name = {item["name"]: item for item in candidates}
        self.assertEqual(by_name["unit"]["command"], ["npm", "run", "test"])
        self.assertEqual(by_name["lint"]["command"], ["npm", "run", "lint"])
        self.assertEqual(by_name["typecheck"]["command"], ["npm", "run", "typecheck"])
        self.assertEqual(by_name["build"]["command"], ["npm", "run", "build"])

    def test_discovered_validations_require_explicit_approval(self):
        (self.tmp / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run", "lint": "eslint ."}}), encoding="utf-8")
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Studio test", allow_git_init=True)
        before = app.validation_discovery()
        self.assertTrue(before["candidates"])
        result = app.approve_discovered_validations(["lint"])
        self.assertIn("lint", result["applied"])
        self.assertTrue(result["profiles"]["approved"]["lint"]["approved"])

    def test_risk_assessment_uses_plan_scope_and_pending_scope(self):
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "auth.py").write_text("# auth\n", encoding="utf-8")
        risk = RiskAssessment(self.tmp).assess(_FakeEngine(), "DYN-0001")
        codes = {item["code"] for item in risk["signals"]}
        self.assertEqual(risk["level"], "high")
        self.assertIn("security_change", codes)
        self.assertIn("data_change", codes)
        self.assertIn("dependency_change", codes)
        self.assertIn("pending_scope", codes)

    def test_uninitialized_project_has_explainable_blocker(self):
        app = DynosAIApplication(self.tmp)
        result = app.explain_blockers()
        self.assertTrue(result["blocked"])
        self.assertEqual(result["items"][0]["code"], "PROJECT.NOT_INITIALIZED")

    def test_project_overview_is_bounded_read_model(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Overview")
        overview = app.project_overview()
        self.assertTrue(overview["initialized"])
        self.assertEqual(overview["project"], "Overview")
        self.assertIn("risk", overview)
        self.assertIn("validations", overview)
        self.assertIn("blockers", overview)

    def test_studio_api_health_and_detection_do_not_require_provider(self):
        api = StudioAPI(self.tmp)
        status, health = api.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["version"], __version__)
        status, detection = api.get("/api/project/detect")
        self.assertEqual(status, 200)
        self.assertEqual(Path(detection["root"]), self.tmp.resolve())

    def test_studio_assets_are_packaged(self):
        package = resources.files("dynosai_flow.studio_assets")
        self.assertIn("DynosAI Studio", package.joinpath("index.html").read_text(encoding="utf-8"))
        self.assertGreater(len(package.joinpath("app.js").read_bytes()), 1000)

    def test_studio_and_app_server_cli_are_public_commands(self):
        studio = parser().parse_args(["--project", str(self.tmp), "studio", "--no-browser", "--port", "0"])
        self.assertEqual(studio.command, "studio")
        self.assertTrue(studio.no_browser)
        app_server = parser().parse_args(["app-server", "--port", "0"])
        self.assertEqual(app_server.command, "app-server")

    def test_server_rejects_non_loopback_binding(self):
        with self.assertRaises(ValueError):
            create_server(self.tmp, host="0.0.0.0", port=0)

    def test_server_serves_health_and_studio_over_loopback(self):
        server = create_server(self.tmp, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        host, port = server.server_address[:2]
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=3) as response:
                health = json.load(response)
            self.assertEqual(health["version"], __version__)
            with urllib.request.urlopen(f"http://{host}:{port}/", timeout=3) as response:
                page = response.read().decode("utf-8")
            self.assertIn("DynosAI Studio", page)
        finally:
            server.shutdown()
            thread.join(timeout=3)

    def test_studio_source_and_packaged_assets_are_identical(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual((root / "apps" / "studio" / name).read_bytes(), (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
