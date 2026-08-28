import shutil
import tempfile
import unittest
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication


class _FakeExecutionManager:
    def __init__(self, available=True):
        self.is_available = available
        self.started = []

    def available(self, provider):
        return self.is_available

    def start(self, root, provider, work_id, *, on_exit=None):
        row = {"work_id": work_id, "provider": provider, "status": "running", "pid": 1234}
        self.started.append(row)
        return row

    def status(self, root, work_id=None):
        rows = self.started
        if work_id is not None:
            rows = [row for row in rows if row["work_id"] == work_id]
        return {"items": list(rows)}


class DynosAI141ExecutionFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-148-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.registry = self.tmp / "registry" / "projects.json"

    def _asset(self, name: str) -> str:
        return resources.files("dynosai_flow.studio_assets").joinpath(name).read_text(encoding="utf-8")

    def _initialized_project(self):
        project = self.tmp / "project"
        project.mkdir()
        DynosAIApplication(project).initialize_project(name="Execution flow")
        return project

    def test_new_change_advances_out_of_inbox_and_launches_configured_agent(self):
        project = self._initialized_project()
        execution = _FakeExecutionManager()
        api = StudioAPI(project, registry_path=self.registry, execution_manager=execution)
        status, created = api.post("/api/work/start", {
            "description": "Create a tiny Fibonacci function",
            "provider": "codex",
            "workspace_strategy": "interactive_branch",
        })
        self.assertEqual(status, 200)
        self.assertEqual(created["workflow"]["work"]["state"], "discovery")
        self.assertEqual(created["execution"]["status"], "running")
        self.assertEqual(execution.started[0]["work_id"], created["id"])
        status, work = api.get("/api/work")
        self.assertEqual(status, 200)
        self.assertEqual(next(item for item in work["items"] if item["id"] != "PROJECT")["state"], "discovery")

    def test_unavailable_provider_fails_before_creating_orphan_work(self):
        project = self._initialized_project()
        api = StudioAPI(project, registry_path=self.registry, execution_manager=_FakeExecutionManager(False))
        status, payload = api.post("/api/work/start", {
            "description": "Create Fibonacci",
            "provider": "codex",
            "workspace_strategy": "interactive_branch",
        })
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "provider_unavailable")
        _, work = api.get("/api/work")
        self.assertEqual([item for item in work["items"] if item["id"] != "PROJECT"], [])

    def test_execution_status_is_observable_from_studio_api(self):
        project = self._initialized_project()
        execution = _FakeExecutionManager()
        api = StudioAPI(project, registry_path=self.registry, execution_manager=execution)
        _, created = api.post("/api/work/start", {
            "description": "Create Fibonacci",
            "provider": "codex",
            "workspace_strategy": "interactive_branch",
        })
        status, payload = api.get("/api/execution", {"work_id": [created["id"]]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"][0]["status"], "running")

    def test_project_paths_are_read_only_and_selected_with_folder_browser(self):
        page = self._asset("index.html")
        self.assertIn('id="new-project-parent" class="text-input path-readonly" type="text" autocomplete="off" readonly', page)
        self.assertIn('id="project-path-input" class="text-input path-readonly" type="text" autocomplete="off" readonly', page)
        app = self._asset("app.js")
        self.assertIn('$("project-path-input").value = path;', app)
        self.assertNotIn('project-path-input").addEventListener("keydown"', app)

    def test_work_view_explains_live_agent_execution_and_retry(self):
        app = self._asset("app.js")
        self.assertIn('/api/execution', app)
        self.assertIn('/api/execution/start', app)
        self.assertIn('data-execution-retry', app)
        self.assertIn('execution.running', app)
        self.assertIn('execution.interrupted', app)

    def test_tutorial_waits_until_work_is_done_instead_of_finishing_after_first_gate(self):
        app = self._asset("app.js")
        update = app.split('function updateTutorialFromState()', 1)[1].split('function applyLanguage', 1)[0]
        self.assertIn('item.state === "done"', update)
        self.assertNotIn('["plan_review","ready","implementing","code_review","validating","ready_to_merge","done"]', update)
        self.assertIn('tutorial.step5ContinueTitle', app)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
