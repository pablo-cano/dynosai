import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI, create_server
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.version import __version__


class DynosAI141Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-141-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git_repo(self, *, dirty=False):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "tests@dynosai.local"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "DynosAI Tests"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("# test\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.tmp, check=True, capture_output=True, text=True)
        if dirty:
            (self.tmp / "README.md").write_text("# changed\n", encoding="utf-8")

    def test_version_is_0141(self):
        self.assertEqual(__version__, "1.0.0rc2")

    def test_setup_status_guides_clean_uninitialized_git_project(self):
        self._git_repo()
        (self.tmp / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
        subprocess.run(["git", "add", "pyproject.toml"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "python"], cwd=self.tmp, check=True, capture_output=True, text=True)
        setup = DynosAIApplication(self.tmp).setup_status()
        self.assertFalse(setup["ready"])
        self.assertEqual(setup["primary_action"]["code"], "initialize")
        self.assertTrue(any(step["id"] == "git" and step["state"] == "complete" for step in setup["steps"]))

    def test_setup_status_explains_dirty_git_before_adoption(self):
        self._git_repo(dirty=True)
        setup = DynosAIApplication(self.tmp).setup_status()
        self.assertEqual(setup["primary_action"]["code"], "clean_git")
        self.assertGreater(setup["dirty_count"], 0)
        self.assertTrue(any(step["id"] == "git" and step["state"] == "blocked" for step in setup["steps"]))

    def test_setup_status_becomes_ready_after_initialization(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Ready project")
        setup = app.setup_status()
        self.assertTrue(setup["initialized"])
        self.assertTrue(setup["ready"])
        self.assertEqual(setup["primary_action"]["code"], "new_task")

    def test_review_center_lists_pending_human_interactions(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Reviews")
        request = app.interactions.create(
            work_id=None,
            kind="clarification",
            message="Which API should be preserved?",
            requested_schema={"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]},
        )
        reviews = app.list_reviews()
        self.assertEqual([item["id"] for item in reviews], [request.id])
        self.assertEqual(reviews[0]["kind"], "clarification")
        self.assertEqual(reviews[0]["message"], "Which API should be preserved?")

    def test_studio_api_exposes_setup_and_reviews(self):
        api = StudioAPI(self.tmp)
        status, setup = api.get("/api/setup")
        self.assertEqual(status, 200)
        self.assertIn("primary_action", setup)
        app = api.app
        app.initialize_project(name="API")
        app.interactions.create(work_id=None, kind="clarification", message="Question", requested_schema={"type": "object"})
        status, review_payload = api.get("/api/reviews")
        self.assertEqual(status, 200)
        self.assertEqual(len(review_payload["items"]), 1)

    def test_studio_uses_web_brand_icon_and_favicon(self):
        package = resources.files("dynosai_flow.studio_assets")
        page = package.joinpath("index.html").read_text(encoding="utf-8")
        icon = package.joinpath("icon.svg").read_text(encoding="utf-8")
        self.assertIn('rel="icon" href="/icon.svg"', page)
        self.assertIn('class="brand-logo" src="/icon.svg"', page)
        self.assertIn('viewBox="0 0 64 64"', icon)
        self.assertIn("#8b7cff", icon)

    def test_studio_has_project_scoped_nontechnical_navigation(self):
        page = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        for label in ("Home", "Overview", "New change", "Work", "Approvals", "Project checks", "Settings", "Help"):
            self.assertIn(f">{label}<", page)
        self.assertIn('id="project-nav" class="project-navigation hidden"', page)
        self.assertNotIn('data-view="tutorial"', page)
        self.assertNotIn("Advanced mode", page)
        self.assertIn("Show technical details", page)

    def test_theme_supports_system_light_dark_and_persists_preference(self):
        package = resources.files("dynosai_flow.studio_assets")
        page = package.joinpath("index.html").read_text(encoding="utf-8")
        script = package.joinpath("theme-init.js").read_text(encoding="utf-8")
        self.assertIn('data-theme-value="system"', page)
        self.assertIn('data-theme-value="light"', page)
        self.assertIn('data-theme-value="dark"', page)
        self.assertIn("dynosai.studio.theme", script)

    def test_studio_contains_setup_task_review_and_help_flows(self):
        page = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        for phrase in (
            "Create a project",
            "Checking your project",
            "What would you like to change?",
            "Approvals",
            "How Studio works",
        ):
            self.assertIn(phrase, page)

    def test_server_serves_icon_and_theme_bootstrap_over_loopback(self):
        server = create_server(self.tmp, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/icon.svg", timeout=3) as response:
                icon = response.read().decode("utf-8")
                content_type = response.headers.get("Content-Type", "")
            self.assertIn("image/svg+xml", content_type)
            self.assertIn("<svg", icon)
            with urllib.request.urlopen(f"http://{host}:{port}/theme-init.js", timeout=3) as response:
                theme = response.read().decode("utf-8")
            self.assertIn("dynosai.studio.theme", theme)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_studio_source_and_packaged_assets_include_0141_files(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual((root / "apps" / "studio" / name).read_bytes(), (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes())

    def test_technical_views_are_opt_in_in_frontend_contract(self):
        page = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        self.assertIn('id="technical-nav" class="technical-nav hidden"', page)
        self.assertIn("dynosai.studio.technical", app)
        self.assertIn("Risk & governance", page)
        self.assertIn("Activity log", page)


if __name__ == "__main__":
    unittest.main()
