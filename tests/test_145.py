import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.model_routing import ProviderModelRouting
from dynosai_flow.studio_projects import create_directory


class DynosAI141StudioFinalPolishTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-141-final-polish-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.registry = self.tmp / "state" / "projects.json"

    def _asset(self, name: str) -> str:
        return resources.files("dynosai_flow.studio_assets").joinpath(name).read_text(encoding="utf-8")

    def test_folder_browser_can_create_child_directory(self):
        parent = self.tmp / "parent"
        parent.mkdir()
        api = StudioAPI(None, registry_path=self.registry)
        status, payload = api.post("/api/filesystem/mkdir", {"parent": str(parent), "name": "new-projects"})
        self.assertEqual(status, 200)
        created = parent / "new-projects"
        self.assertTrue(created.is_dir())
        self.assertEqual(Path(payload["path"]), created.resolve())
        self.assertIn("new-projects", [item["name"] for item in payload["listing"]["directories"]])

    def test_create_directory_rejects_traversal_and_existing_folder(self):
        parent = self.tmp / "parent"
        parent.mkdir()
        with self.assertRaises(ValueError):
            create_directory(parent, "../escape")
        (parent / "exists").mkdir()
        with self.assertRaises(ValueError):
            create_directory(parent, "exists")

    def test_studio_uses_custom_dialogs_and_no_native_confirm_or_select(self):
        page = self._asset("index.html")
        app = self._asset("app.js")
        self.assertIn('id="confirm-dialog"', page)
        self.assertIn('id="new-folder-dialog"', page)
        self.assertIn('id="route-dialog"', page)
        self.assertNotIn("window.confirm", app)
        self.assertNotIn("window.prompt", app)
        self.assertNotIn("<select", page)

    def test_recent_projects_are_vertically_bounded(self):
        page = self._asset("index.html")
        css = self._asset("styles.css")
        self.assertIn('class="project-list recent-project-list"', page)
        self.assertIn(".recent-project-list{max-height:340px;overflow-y:auto;overflow-x:hidden", css)

    def test_refresh_button_is_removed_and_background_refresh_is_present(self):
        page = self._asset("index.html")
        app = self._asset("app.js")
        self.assertNotIn('id="refresh"', page)
        self.assertIn("setInterval(() => { if (!document.hidden) refresh({preserveView:true}); }, 2500);", app)

    def test_project_settings_exposes_model_routing_by_phase(self):
        page = self._asset("index.html")
        app = self._asset("app.js")
        self.assertIn('data-view="project-settings"', page)
        self.assertIn('id="model-routing-list"', page)
        self.assertIn('id="routing-provider-label"', page)
        self.assertNotIn('data-routing-provider="codex"', page)
        self.assertNotIn('data-routing-provider="cursor"', page)
        self.assertIn('/api/model-routing/set', app)
        self.assertIn('/api/model-routing/reset', app)

    def test_project_model_routing_api_updates_and_resets_project_override(self):
        project = self.tmp / "project"
        project.mkdir()
        api = StudioAPI(project, registry_path=self.registry)
        status, before = api.get("/api/model-routing", {"provider": ["codex"]})
        self.assertEqual(status, 200)
        self.assertEqual(before["providers"]["codex"]["activities"]["specification"]["model"], "gpt-5.6-sol")
        status, changed = api.post("/api/model-routing/set", {
            "provider": "codex", "model": "gpt-5.6-luna", "effort": "low", "activity": "specification"
        })
        self.assertEqual(status, 200)
        route = changed["effective"]["providers"]["codex"]["activities"]["specification"]
        self.assertEqual(route["model"], "gpt-5.6-luna")
        self.assertEqual(route["scope"], "project")
        status, reset = api.post("/api/model-routing/reset", {"provider": "codex", "activity": "specification"})
        self.assertEqual(status, 200)
        route = reset["effective"]["providers"]["codex"]["activities"]["specification"]
        self.assertEqual(route["model"], "gpt-5.6-sol")
        self.assertNotEqual(route["scope"], "project")

    def test_global_settings_no_longer_mix_project_execution_defaults(self):
        page = self._asset("index.html")
        settings = page.split('<section id="settings"', 1)[1].split('</section>', 1)[0]
        project_settings = page.split('<section id="project-settings"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('id="default-provider"', settings)
        self.assertNotIn('id="default-workspace"', settings)
        self.assertIn('id="project-provider"', project_settings)
        self.assertIn('id="project-workspace"', project_settings)

    def test_duplicate_page_intro_is_visually_removed_and_new_change_is_focused(self):
        css = self._asset("styles.css")
        page = self._asset("index.html")
        self.assertIn(".view > .view-intro{display:none}", css)
        new_change = page.split('<section id="new-task"', 1)[1].split('</section>', 1)[0]
        self.assertIn('id="work-description"', new_change)
        self.assertNotIn('execution-summary-copy', new_change)
        self.assertNotIn('data-combobox="provider"', new_change)
        self.assertNotIn('data-combobox="workspace"', new_change)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
