import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_spec
from dynosai_flow.engine import DynosAI


class _LaunchStateAware:
    def __init__(self, launch_state):
        self.starts = []
        self.launch_state = launch_state

    def available(self, provider):
        return True

    def start(self, root, provider, work_id, *, on_exit=None):
        self.starts.append({"root": str(root), "provider": provider, "work_id": work_id})
        return {"work_id": work_id, "provider": provider, "status": "running", "message": "Coding agent is working."}

    def status(self, root, work_id=None):
        return {"items": [{"work_id": work_id, "launch_state": self.launch_state, "status": "running"}]}


class DynosAI158PlanTurnAutoContinueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-158-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git_repo(self):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "tests@dynosai.local"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "DynosAI Tests"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("# demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.tmp, check=True, capture_output=True, text=True)

    def _plan_review_without_plan(self):
        self._git_repo()
        engine = DynosAI(self.tmp)
        engine.initialize("RC", "python", "true", None)
        work = engine.start("Crear una CLI Fibonacci que genere N términos")
        wid = work["id"]
        engine.continue_work(wid)
        engine.submit_spec(wid, demo_spec(), "rc")
        engine.continue_work(wid)
        app = DynosAIApplication(self.tmp)
        app.set_auto_approve(True)
        app.next_action("codex", wid, execute=False)
        self.assertEqual(engine.get_work(wid)["state"], "plan_review")
        self.assertIsNone(engine.artifacts.artifact(wid, "plan"))
        return app, engine, wid

    def test_valueerror_in_a_new_library_request_is_not_a_bug(self):
        self._git_repo()
        engine = DynosAI(self.tmp)
        engine.initialize("RC", "python", "true", None)
        work = engine.start(
            "Crea una pequeña librería Python de Fibonacci. Rechaza valores negativos con ValueError."
        )
        self.assertEqual(work["type"], "feature")

    def test_mid_turn_spec_approval_relaunches_agent_to_author_the_plan(self):
        _app, engine, wid = self._plan_review_without_plan()
        counting = _LaunchStateAware("discovery")
        api = StudioAPI(self.tmp, execution_manager=counting)
        outcome = api._on_execution_exit(self.tmp, "codex", wid, 0)
        self.assertEqual(outcome["status"], "running", outcome)
        self.assertEqual(engine.get_work(wid)["state"], "plan_review")
        self.assertEqual(len(counting.starts), 1)

    def test_same_phase_plan_review_exit_does_not_loop_without_a_plan(self):
        _app, engine, wid = self._plan_review_without_plan()
        counting = _LaunchStateAware("plan_review")
        api = StudioAPI(self.tmp, execution_manager=counting)
        outcome = api._on_execution_exit(self.tmp, "codex", wid, 0)
        self.assertEqual(outcome["status"], "stopped", outcome)
        self.assertEqual(len(counting.starts), 0)


if __name__ == "__main__":
    unittest.main()
