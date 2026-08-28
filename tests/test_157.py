import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.db import Database
from dynosai_flow.engine import DynosAI
from dynosai_flow.git_manager import GitError, GitManager
from dynosai_flow.util import utc_now


class _SilentLaunch:
    def available(self, provider):
        return True

    def start(self, root, provider, work_id, *, on_exit=None):
        return {"work_id": work_id, "provider": provider, "status": "running", "message": "Coding agent is working."}

    def status(self, root, work_id=None):
        return {"items": []}


class DynosAI157MergeUntrackedTestsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-157-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.tmp, check=check, capture_output=True, text=True)

    def _git_repo(self):
        self._git("init", "-b", "main")
        self._git("config", "user.email", "tests@dynosai.local")
        self._git("config", "user.name", "DynosAI Tests")
        (self.tmp / "README.md").write_text("# demo\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "initial")

    def _manager(self, work_id="DYN-0001"):
        self._git_repo()
        db = Database(self.tmp)
        db.initialize()
        db.set_meta("main_branch", "main")
        now = utc_now()
        db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (work_id, "Demo", "Demo", "feature", "implementing", 0, 1, 50, now, now),
        )
        return GitManager(self.tmp, db)

    def _ensure_clean(self):
        self._git("add", "-A")
        cached = self._git("diff", "--cached", "--quiet", check=False)
        if cached.returncode != 0:
            self._git("commit", "-m", "chore: test fixture")

    def _write_demo_source(self, worktree: Path):
        (worktree / "src").mkdir(exist_ok=True)
        (worktree / "tests").mkdir(exist_ok=True)
        (worktree / "src" / "__init__.py").write_text("", encoding="utf-8")
        (worktree / "src" / "fibonacci.py").write_text("def generate_fibonacci(n): return []\n", encoding="utf-8")
        (worktree / "src" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
        (worktree / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    def _interactive_to_code_review(self):
        self._git_repo()
        engine = DynosAI(self.tmp)
        engine.initialize("RC", "python", "true", None)
        self._ensure_clean()
        app = DynosAIApplication(self.tmp)
        work = app.start_feature("Crear una CLI Fibonacci que genere N términos", provider="cursor")
        wid = work["id"]
        engine.continue_work(wid)
        engine.submit_spec(wid, demo_spec(), "rc")
        engine.continue_work(wid)
        engine.review(wid, "spec")
        engine.submit_plan(wid, demo_plan(), "rc")
        engine.review(wid, "plan")
        prep = engine.continue_work(wid)
        worktree = Path(prep["worktree"])
        self._write_demo_source(worktree)
        engine._run_result_validation = lambda *args, **kwargs: {
            "passed": True,
            "results": [{"profile": "unit", "command": "true", "status": "passed", "exit_code": 0, "output": "ok"}],
            "scope": "task",
            "skipped": False,
            "task_ids": [f"{wid}-T01", f"{wid}-T02"],
            "evidence_required": ["git_diff"],
        }
        registered = engine.register_result(
            wid,
            "implemented",
            ["src/__init__.py", "src/fibonacci.py", "src/cli.py", "tests/test_fibonacci.py"],
            [],
            [f"{wid}-T01", f"{wid}-T02"],
            prep["run_id"],
        )
        self.assertEqual(registered.get("status"), "verified", registered)
        engine.continue_work(wid)
        self.assertEqual(engine.get_work(wid)["state"], "code_review")
        return app, engine, wid, worktree

    def test_status_expands_untracked_tests_directory(self):
        gm = self._manager()
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        collapsed = self._git("status", "--porcelain").stdout.splitlines()
        self.assertIn("?? tests/", collapsed)
        status = gm.status()
        self.assertTrue(any("tests/test_fibonacci.py" in line.replace("\\", "/") for line in status), status)
        self.assertNotIn("?? tests/", status)

    def test_checkpoint_extra_paths_commits_tests_omitted_by_diff_files(self):
        gm = self._manager()
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        real = gm.diff_files
        gm.diff_files = lambda cwd, base_commit=None: [path for path in real(cwd, base_commit) if not str(path).replace("\\", "/").startswith("tests/")]
        gm.checkpoint("DYN-0001", self.tmp, "feat(DYN-0001): verified implementation checkpoint", extra_paths=["tests/test_fibonacci.py"])
        tracked = self._git("ls-files", "tests/test_fibonacci.py").stdout.strip().replace("\\", "/")
        self.assertEqual(tracked, "tests/test_fibonacci.py")

    def test_merge_blockers_ignore_overlays_but_list_untracked_tests(self):
        gm = self._manager()
        overlay = self.tmp / "specs" / "dyn-0001-crea-una-pequeña-librería-python-de-fibonacci-añ"
        overlay.mkdir(parents=True)
        (overlay / "plan.md").write_text("# exported plan\n", encoding="utf-8")
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        blockers = gm.merge_blockers()
        self.assertIn("tests/test_fibonacci.py", blockers)
        self.assertFalse(any("plan.md" in path or path.startswith("specs/") for path in blockers), blockers)

    def test_merge_succeeds_after_scooping_verified_tests_and_ignoring_overlay(self):
        gm = self._manager()
        self._git("switch", "-c", "dyn/dyn-0001-demo")
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "fibonacci.py").write_text("def generate_fibonacci(n): return []\n", encoding="utf-8")
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        overlay = self.tmp / "specs" / "dyn-0001-demo"
        overlay.mkdir(parents=True)
        (overlay / "plan.md").write_text("# exported plan\n", encoding="utf-8")
        gm.checkpoint("DYN-0001", self.tmp, "feat(DYN-0001): remaining verified source", extra_paths=["src/fibonacci.py", "tests/test_fibonacci.py"])
        self.assertEqual(gm.merge_blockers(), [])
        commit = gm.merge("DYN-0001", "dyn/dyn-0001-demo", "Fibonacci tests")
        self.assertTrue(commit)
        tracked = self._git("ls-files", "tests/test_fibonacci.py").stdout.strip().replace("\\", "/")
        self.assertEqual(tracked, "tests/test_fibonacci.py")

    def test_merge_still_blocks_unmanaged_scratch_files(self):
        gm = self._manager()
        (self.tmp / "scratch.txt").write_text("do not absorb\n", encoding="utf-8")
        with self.assertRaises(GitError) as raised:
            gm.merge("DYN-0001", "dyn/missing", "blocked")
        self.assertIn("scratch.txt", str(raised.exception))

    def test_review_merge_scoops_untracked_verified_tests_on_interactive_branch(self):
        app, engine, wid, worktree = self._interactive_to_code_review()
        result = engine.review(wid, "code")
        self.assertTrue(result.get("approved"), result)
        cached = self._git("ls-files", "--error-unmatch", "tests/test_fibonacci.py", check=False)
        if cached.returncode == 0:
            self._git("rm", "--cached", "tests/test_fibonacci.py")
        self.assertTrue((worktree / "tests" / "test_fibonacci.py").exists())
        engine.run_validations = lambda *args, **kwargs: {
            "passed": True,
            "results": [{"profile": "unit", "status": "passed", "exit_code": 0, "output": "ok"}],
            "scope": "work",
            "skipped": False,
        }
        validation = engine.continue_work(wid)
        self.assertEqual(engine.get_work(wid)["state"], "ready_to_merge", validation)
        merged = engine.review(wid, "merge")
        self.assertEqual(merged.get("state"), "done", merged)
        tracked = self._git("ls-files", "tests/test_fibonacci.py").stdout.strip().replace("\\", "/")
        self.assertEqual(tracked, "tests/test_fibonacci.py")

    def test_execution_start_settles_ready_to_merge_without_pending_gate(self):
        app, engine, wid, worktree = self._interactive_to_code_review()
        engine.review(wid, "code")
        cached = self._git("ls-files", "--error-unmatch", "tests/test_fibonacci.py", check=False)
        if cached.returncode == 0:
            self._git("rm", "--cached", "tests/test_fibonacci.py")
        engine.run_validations = lambda *args, **kwargs: {
            "passed": True,
            "results": [{"profile": "unit", "status": "passed", "exit_code": 0, "output": "ok"}],
            "scope": "work",
            "skipped": False,
        }
        engine.continue_work(wid)
        self.assertEqual(engine.get_work(wid)["state"], "ready_to_merge")
        app.set_auto_approve(True)
        self.assertEqual(app.interactions.pending_for_work(wid), [])
        api = StudioAPI(self.tmp, execution_manager=_SilentLaunch())
        status, payload = api.post("/api/execution/start", {"work_id": wid})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload.get("status"), "done", payload)
        self.assertEqual(engine.get_work(wid)["state"], "done")

    def test_studio_work_offers_continue_on_final_review(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        i18n = (root / "apps" / "studio" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn("ready_to_merge", app)
        self.assertIn("execution.continueHost", app)
        self.assertIn("execution.finishStepText", app)
        self.assertIn("alerts.workFinished", app)
        self.assertIn('"execution.continueHost": "Continue"', i18n)
        self.assertIn('"execution.continueHost": "Continuar"', i18n)


if __name__ == "__main__":
    unittest.main()
