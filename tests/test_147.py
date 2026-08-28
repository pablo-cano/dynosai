import json
import os
import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.studio_projects import StudioProjectRegistry


class DynosAI141RecentProjectsAndTutorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-147-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.registry_file = self.tmp / "state" / "projects.json"

    def _asset(self, name: str) -> str:
        return resources.files("dynosai_flow.studio_assets").joinpath(name).read_text(encoding="utf-8")

    def test_recent_registry_prunes_missing_duplicates_and_caps_at_ten(self):
        projects = self.tmp / "projects"
        projects.mkdir()
        valid = []
        for index in range(12):
            root = projects / f"p-{index:02d}"
            root.mkdir()
            valid.append(root)
        missing = projects / "missing"
        payload = {
            "version": 1,
            "projects": [
                *[
                    {"path": str(root), "name": root.name, "last_opened": f"2026-08-26T10:{index:02d}:00+00:00"}
                    for index, root in enumerate(valid)
                ],
                {"path": str(valid[-1]), "name": "duplicate", "last_opened": "2026-08-26T11:59:00+00:00"},
                {"path": str(missing), "name": "missing", "last_opened": "2026-08-26T12:00:00+00:00"},
            ],
        }
        self.registry_file.parent.mkdir(parents=True)
        self.registry_file.write_text(json.dumps(payload), encoding="utf-8")
        registry = StudioProjectRegistry(self.registry_file)
        items = registry.list()
        self.assertEqual(len(items), 10)
        self.assertTrue(all(item["exists"] for item in items))
        self.assertNotIn(str(missing), [item["path"] for item in items])
        self.assertEqual(len({os.path.normcase(item["path"]) for item in items}), len(items))
        persisted = json.loads(self.registry_file.read_text(encoding="utf-8"))["projects"]
        self.assertEqual(len(persisted), 10)

    def test_legacy_014_test_temp_projects_are_pruned_even_if_folder_still_exists(self):
        artifact = Path(tempfile.gettempdir()) / "dynosai-141-old-studio-artifact"
        artifact.mkdir(exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(artifact, ignore_errors=True))
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.registry_file.write_text(json.dumps({
            "version": 1,
            "projects": [{"path": str(artifact), "name": artifact.name, "last_opened": "2026-08-26T12:00:00+00:00"}],
        }), encoding="utf-8")
        self.assertEqual(StudioProjectRegistry(self.registry_file).list(), [])

    def test_successful_open_is_added_and_failed_open_is_not(self):
        project = self.tmp / "open-me"
        project.mkdir()
        api = StudioAPI(None, registry_path=self.registry_file)
        status, _ = api.post("/api/projects/open", {"path": str(project)})
        self.assertEqual(status, 200)
        api.post("/api/projects/close", {})
        status, _ = api.post("/api/projects/open", {"path": str(self.tmp / "does-not-exist")})
        self.assertEqual(status, 400)
        _, projects = api.get("/api/projects")
        self.assertEqual([Path(item["path"]) for item in projects["items"]], [project.resolve()])

    def test_touch_moves_existing_project_to_front_without_duplicates(self):
        registry = StudioProjectRegistry(self.registry_file)
        first = self.tmp / "first"; first.mkdir()
        second = self.tmp / "second"; second.mkdir()
        registry.touch(first)
        registry.touch(second)
        registry.touch(first)
        items = registry.list()
        self.assertEqual(Path(items[0]["path"]), first.resolve())
        self.assertEqual(len(items), 2)

    def test_project_creation_ui_has_no_technology_template_selector(self):
        page = self._asset("index.html")
        home = page.split('<section id="home"', 1)[1].split('</section>', 1)[0]
        self.assertNotIn('data-combobox="create-template"', home)
        self.assertNotIn('id="create-template"', home)
        app = self._asset("app.js")
        self.assertNotIn('comboValue("create-template")', app)
        self.assertIn('template:"empty"', app)

    def test_fibonacci_walkthrough_is_six_steps_and_skips_project_checks(self):
        page = self._asset("index.html")
        app = self._asset("app.js")
        self.assertIn('1 / 6', page)
        self.assertIn('Math.min(6', app)
        config = app.split('function tutorialConfig(step)', 1)[1].split('function clearHighlight', 1)[0]
        self.assertNotIn('view:"checks"', config)
        self.assertIn('3:{view:"new-task"', config)
        self.assertIn('4:{view:"work"', config)
        self.assertIn('5:{view:"approvals"', config)
        self.assertIn('6:{view:"work"', config)

    def test_tutorial_initialization_continues_to_new_change(self):
        app = self._asset("app.js")
        initialize = app.split('async function initializeProject()', 1)[1].split('async function approveSelectedValidations()', 1)[0]
        self.assertIn('if (tutorialMatchesCurrent()) { setTutorialStep(3); navigate("new-task"); }', initialize)

    def test_project_checks_are_not_a_tutorial_progress_requirement(self):
        app = self._asset("app.js")
        update = app.split('function updateTutorialFromState()', 1)[1].split('function applyLanguage', 1)[0]
        self.assertNotIn('approvedChecks', update)
        self.assertNotIn('validations?.approved', update)

    def test_recent_project_copy_explains_that_only_available_projects_are_kept(self):
        i18n = self._asset("i18n.js")
        self.assertIn('"hub.recentText": "Only available projects you opened successfully are kept here."', i18n)
        self.assertIn('"hub.recentText": "Aquí solo se conservan proyectos disponibles que se hayan abierto correctamente."', i18n)

    def test_pytest_default_registry_does_not_target_user_home(self):
        old = os.environ.get("PYTEST_CURRENT_TEST")
        os.environ["PYTEST_CURRENT_TEST"] = "test isolation"
        try:
            registry = StudioProjectRegistry()
        finally:
            if old is None:
                os.environ.pop("PYTEST_CURRENT_TEST", None)
            else:
                os.environ["PYTEST_CURRENT_TEST"] = old
        self.assertNotEqual(registry.state_path, (Path.home() / ".dynosai" / "studio" / "projects.json").resolve())
        self.assertIn("dynosai-studio-tests-", str(registry.state_path))

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
