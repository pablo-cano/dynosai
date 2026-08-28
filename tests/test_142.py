import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from importlib import resources
from pathlib import Path

from dynosai_flow.agent_config import PROVIDERS
from dynosai_flow.app_server import StudioAPI, create_server
from dynosai_flow.cli import parser
from dynosai_flow.studio_projects import StudioProjectRegistry


class DynosAI141StudioRefinementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-141-refine-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project_a = self.tmp / "project-a"
        self.project_b = self.tmp / "project-b"
        self.project_a.mkdir()
        self.project_b.mkdir()
        self.registry_file = self.tmp / "studio-state" / "projects.json"

    def test_studio_has_no_native_select_and_uses_combobox_contract(self):
        page = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        self.assertNotIn("<select", page.lower())
        self.assertIn('role="combobox"', page)
        self.assertIn('class="combobox-trigger"', page)
        self.assertIn('data-combobox="project-provider"', page)
        self.assertIn('data-combobox="language"', page)
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        self.assertIn('aria-controls', app)
        self.assertIn('ArrowDown', app)

    def test_claude_is_not_exposed_by_studio_or_public_cli(self):
        package = resources.files("dynosai_flow.studio_assets")
        surface = "\n".join(package.joinpath(name).read_text(encoding="utf-8") for name in ("index.html", "app.js", "i18n.js"))
        self.assertNotIn("Claude", surface)
        self.assertNotIn("claude", surface.lower())
        api = StudioAPI(self.project_a, registry_path=self.registry_file)
        status, payload = api.post("/api/work/start", {"description": "test", "provider": "claude"})
        self.assertEqual(status, 400)
        self.assertIn("cursor or codex", payload["message"])
        with self.assertRaises(SystemExit):
            parser().parse_args(["setup", "--provider", "claude"])
        self.assertEqual(PROVIDERS, ("cursor", "codex"))

    def test_project_registry_switches_without_restarting_server(self):
        api = StudioAPI(self.project_a, registry_path=self.registry_file)
        status, opened = api.post("/api/projects/open", {"path": str(self.project_b)})
        self.assertEqual(status, 200)
        self.assertEqual(Path(opened["root"]), self.project_b.resolve())
        status, health = api.get("/api/health")
        self.assertEqual(Path(health["root"]), self.project_b.resolve())
        status, projects = api.get("/api/projects")
        self.assertTrue(any(Path(item["path"]) == self.project_a.resolve() for item in projects["items"]))
        self.assertTrue(any(item["current"] and Path(item["path"]) == self.project_b.resolve() for item in projects["items"]))

    def test_project_registry_remove_never_deletes_files(self):
        registry = StudioProjectRegistry(self.registry_file)
        registry.touch(self.project_a)
        registry.touch(self.project_b)
        self.assertTrue(registry.remove(self.project_a))
        self.assertTrue(self.project_a.exists())
        self.assertFalse(any(Path(item["path"]) == self.project_a.resolve() for item in registry.list()))

    def test_invalid_project_path_is_rejected(self):
        api = StudioAPI(self.project_a, registry_path=self.registry_file)
        status, payload = api.post("/api/projects/open", {"path": str(self.tmp / "missing")})
        self.assertEqual(status, 400)
        self.assertIn("does not exist", payload["message"])

    def test_greenfield_project_creation_is_explicit_and_switches_current_project(self):
        parent = self.tmp / "greenfield"
        parent.mkdir()
        api = StudioAPI(self.project_a, registry_path=self.registry_file)
        status, result = api.post("/api/projects/create", {"parent": str(parent), "name": "fibonacci-demo", "template": "python"})
        self.assertEqual(status, 200)
        demo = Path(result["root"])
        self.assertTrue((demo / "README.md").exists())
        self.assertTrue((demo / "pyproject.toml").exists())
        self.assertFalse((demo / ".dynosai").exists())
        if (demo / ".git").exists():
            status_run = subprocess.run(["git", "status", "--porcelain"], cwd=demo, check=True, capture_output=True, text=True)
            self.assertEqual(status_run.stdout.strip(), "")
        self.assertEqual(api.root, demo.resolve())

    def test_studio_tutorial_is_contextual_and_never_creates_demo_automatically(self):
        page = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        app = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        self.assertNotIn('id="tutorial" class="view', page)
        self.assertIn('id="tutorial-coach"', page)
        self.assertIn('data-start-tutorial="1"', page)
        self.assertIn("tutorial-target", app)
        self.assertNotIn("/api/demo/fibonacci", app)

    def test_studio_i18n_has_english_and_spanish_with_english_default(self):
        script = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("en: {", script)
        self.assertIn("es: {", script)
        self.assertIn('const fallbackLanguage = "en"', script)
        self.assertIn('"settings.language": "Language"', script)
        self.assertIn('"settings.language": "Idioma"', script)
        self.assertIn('"tutorial.title": "Construye Fibonacci paso a paso"', script)

    def test_server_serves_i18n_and_projects_api(self):
        server = create_server(self.project_a, port=0, registry_path=self.registry_file)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/i18n.js", timeout=3) as response:
                script = response.read().decode("utf-8")
            self.assertIn("DynosAII18n", script)
            with urllib.request.urlopen(f"http://{host}:{port}/api/projects", timeout=3) as response:
                payload = json.load(response)
            self.assertEqual(Path(payload["current"]), self.project_a.resolve())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_studio_source_and_package_include_i18n_asset(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual((root / "apps" / "studio" / name).read_bytes(), (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
