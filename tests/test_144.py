import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.studio_projects import browse_directory


class DynosAI141ProjectHubPolishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-141-hub-polish-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.registry = self.tmp / "state" / "projects.json"

    def _asset(self, name: str) -> str:
        return resources.files("dynosai_flow.studio_assets").joinpath(name).read_text(encoding="utf-8")

    def test_folder_browser_lists_directories_only_without_selected_project(self):
        root = self.tmp / "browse"
        root.mkdir()
        (root / "alpha").mkdir()
        (root / "beta").mkdir()
        (root / "note.txt").write_text("not a directory", encoding="utf-8")
        api = StudioAPI(None, registry_path=self.registry)
        status, payload = api.post("/api/filesystem/list", {"path": str(root)})
        self.assertEqual(status, 200)
        self.assertEqual(Path(payload["path"]), root.resolve())
        self.assertEqual([item["name"] for item in payload["directories"]], ["alpha", "beta"])
        self.assertIn("home", payload)
        self.assertIn("roots", payload)

    def test_folder_browser_rejects_non_directory_path(self):
        file_path = self.tmp / "file.txt"
        file_path.write_text("x", encoding="utf-8")
        api = StudioAPI(None, registry_path=self.registry)
        status, payload = api.post("/api/filesystem/list", {"path": str(file_path)})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "validation")

    def test_browse_directory_defaults_to_home_and_has_parent_contract(self):
        payload = browse_directory(None)
        self.assertEqual(Path(payload["path"]), Path.home().resolve())
        self.assertIsInstance(payload["directories"], list)
        self.assertIn("writable", payload)

    def test_studio_uses_contextual_folder_dialog_not_native_picker_routes(self):
        page = self._asset("index.html")
        app = self._asset("app.js")
        self.assertIn('id="folder-dialog"', page)
        self.assertIn('id="folder-dialog-list"', page)
        self.assertIn('/api/filesystem/list', app)
        self.assertNotIn('/api/projects/pick', app)
        self.assertNotIn('/api/projects/pick-parent', app)

    def test_create_and_open_browse_actions_use_different_dialog_modes(self):
        app = self._asset("app.js")
        self.assertIn('openFolderDialog("open")', app)
        self.assertIn('openFolderDialog("create")', app)
        self.assertIn('folders.projectWillBeCreated', app)
        self.assertIn('folders.selectedProject', app)

    def test_home_navigation_closes_active_project_context(self):
        app = self._asset("app.js")
        self.assertIn('async function goHome()', app)
        self.assertIn('if (hasProject()) {\n    await closeProject();', app)
        self.assertIn('if (target.dataset.view === "home") { goHome(); return; }', app)

    def test_sidebar_prevents_horizontal_scrolling_and_hides_long_path(self):
        css = self._asset("styles.css")
        app = self._asset("app.js")
        self.assertIn('overflow-x:hidden', css)
        self.assertIn('$("sidebar-project-path").textContent = t("project.openContext")', app)
        self.assertNotIn('$("sidebar-project-path").textContent = health.root', app)

    def test_folder_dialog_is_localized_in_english_and_spanish(self):
        i18n = self._asset("i18n.js")
        self.assertIn('"folders.createTitle": "Choose where to create the project"', i18n)
        self.assertIn('"folders.createTitle": "Elige dónde crear el proyecto"', i18n)
        self.assertIn('"folders.openProject": "Open this project"', i18n)
        self.assertIn('"folders.openProject": "Abrir este proyecto"', i18n)

    def test_native_picker_contract_is_removed_from_server(self):
        source = Path(__file__).resolve().parents[1].joinpath("src", "dynosai_flow", "app_server.py").read_text(encoding="utf-8")
        self.assertNotIn('"/api/projects/pick"', source)
        self.assertNotIn('"/api/projects/pick-parent"', source)
        self.assertIn('"/api/filesystem/list"', source)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
