import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI, create_server
from dynosai_flow.cli import parser
from dynosai_flow.studio_projects import StudioProjectRegistry


class DynosAI141ProjectHubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-141-hub-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.registry_file = self.tmp / "state" / "projects.json"

    def test_studio_cli_does_not_select_cwd_when_project_is_omitted(self):
        args = parser().parse_args(["studio", "--no-browser", "--port", "0"])
        self.assertIsNone(args.project)
        explicit = parser().parse_args(["--project", str(self.tmp), "studio", "--no-browser"])
        self.assertEqual(Path(explicit.project), self.tmp)

    def test_studio_api_can_start_without_project(self):
        api = StudioAPI(None, registry_path=self.registry_file)
        status, health = api.get("/api/health")
        self.assertEqual(status, 200)
        self.assertFalse(health["project_selected"])
        self.assertIsNone(health["root"])
        status, projects = api.get("/api/projects")
        self.assertEqual(status, 200)
        self.assertIsNone(projects["current"])
        status, payload = api.get("/api/overview")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "no_project")

    def test_create_project_supports_empty_and_python_starters(self):
        parent = self.tmp / "projects"
        parent.mkdir()
        registry = StudioProjectRegistry(self.registry_file)
        empty = registry.create_project(parent=parent, name="small-app", template="empty")
        empty_root = Path(empty["path"])
        self.assertTrue((empty_root / "README.md").exists())
        self.assertFalse((empty_root / "pyproject.toml").exists())
        python_project = registry.create_project(parent=parent, name="fibonacci-demo", template="python")
        python_root = Path(python_project["path"])
        self.assertTrue((python_root / "pyproject.toml").exists())
        self.assertIn("pytest", (python_root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertFalse((python_root / ".dynosai").exists())

    def test_create_project_rejects_unsafe_names_and_existing_folder(self):
        parent = self.tmp / "projects"
        parent.mkdir()
        registry = StudioProjectRegistry(self.registry_file)
        with self.assertRaises(ValueError):
            registry.create_project(parent=parent, name="../escape", template="empty")
        (parent / "exists").mkdir()
        with self.assertRaises(ValueError):
            registry.create_project(parent=parent, name="exists", template="empty")

    def test_project_can_be_closed_back_to_hub_without_stopping_server(self):
        project = self.tmp / "project"
        project.mkdir()
        api = StudioAPI(project, registry_path=self.registry_file)
        status, result = api.post("/api/projects/close", {})
        self.assertEqual(status, 200)
        self.assertIsNone(result["current"])
        status, health = api.get("/api/health")
        self.assertFalse(health["project_selected"])
        self.assertIsNone(api.root)
        self.assertIsNone(api.app)

    def test_project_hub_is_global_and_project_navigation_is_nested(self):
        page = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        self.assertIn('id="home" class="view active"', page)
        self.assertIn('id="create-project-card"', page)
        self.assertIn('id="open-project-card"', page)
        self.assertIn('id="project-nav" class="project-navigation hidden"', page)
        self.assertIn('class="nav-item nested" data-view="project-overview"', page)
        self.assertIn('class="nav-item nested requires-initialized" data-view="new-task"', page)
        self.assertNotIn('data-view="projects"', page)

    def test_tutorial_is_contextual_walkthrough_not_a_page_or_automatic_creator(self):
        package = resources.files("dynosai_flow.studio_assets")
        page = package.joinpath("index.html").read_text(encoding="utf-8")
        app = package.joinpath("app.js").read_text(encoding="utf-8")
        self.assertIn('id="tutorial-coach"', page)
        self.assertNotIn('data-view="tutorial"', page)
        self.assertIn("Show me where", page)
        self.assertIn("tutorial-target", app)
        self.assertIn("createProject", app)
        self.assertNotIn("createDemoProject", app)
        self.assertNotIn("/api/demo/fibonacci", app)

    def test_normal_ui_does_not_expose_local_server_language(self):
        package = resources.files("dynosai_flow.studio_assets")
        surface = "\n".join(package.joinpath(name).read_text(encoding="utf-8") for name in ("index.html", "app.js"))
        lowered = surface.lower()
        self.assertNotIn("local server", lowered)
        self.assertNotIn("local only", lowered)
        self.assertNotIn("servidor local", lowered)
        self.assertNotIn("solo local", lowered)

    def test_server_without_project_serves_hub_and_can_open_project_later(self):
        project = self.tmp / "project"
        project.mkdir()
        server = create_server(None, port=0, registry_path=self.registry_file)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=3) as response:
                health = json.load(response)
            self.assertFalse(health["project_selected"])
            request = urllib.request.Request(
                f"http://{host}:{port}/api/projects/open",
                data=json.dumps({"path": str(project)}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                opened = json.load(response)
            self.assertEqual(Path(opened["root"]), project.resolve())
            with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=3) as response:
                selected = json.load(response)
            self.assertTrue(selected["project_selected"])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual((root / "apps" / "studio" / name).read_bytes(), (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
