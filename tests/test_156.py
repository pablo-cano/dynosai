import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.engine import DynosAI
from dynosai_flow import policy as path_policy
from dynosai_flow.git_manager import GitManager
from dynosai_flow.db import Database


QUOTED_SPANISH_PLAN = (
    '"specs/dyn-0001-crea-una-peque\\303\\261a-librer\\303\\255a-python-de-fibonacci-a\\303\\261/plan.md"'
)
SLASH_OCTAL_PLAN = (
    '"specs/dyn-0001-crea-una-peque/303/261a-librer/303/255a-python-de-fibonacci-a/303/261/plan.md"'
)
UNICODE_SPANISH_PLAN = "specs/dyn-0001-crea-una-pequeña-librería-python-de-fibonacci-añ/plan.md"


class DynosAI156GitPathEncodingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-156-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git_repo(self):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "tests@dynosai.local"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "DynosAI Tests"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("# demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.tmp, check=True, capture_output=True, text=True)

    def test_decode_git_path_unescapes_c_quoted_spanish_overlay(self):
        self.assertEqual(path_policy.decode_git_path(QUOTED_SPANISH_PLAN), UNICODE_SPANISH_PLAN)
        self.assertEqual(path_policy.decode_git_path(UNICODE_SPANISH_PLAN), UNICODE_SPANISH_PLAN)

    def test_quoted_spanish_plan_overlay_is_a_managed_artifact(self):
        self.assertTrue(path_policy.is_managed_workflow_artifact(QUOTED_SPANISH_PLAN))
        self.assertTrue(path_policy.is_managed_workflow_artifact(UNICODE_SPANISH_PLAN))
        self.assertTrue(path_policy.is_managed_workflow_artifact(SLASH_OCTAL_PLAN))
        self.assertEqual(path_policy.product_scope_paths([UNICODE_SPANISH_PLAN, "fibonacci/core.py", SLASH_OCTAL_PLAN]), ["fibonacci/core.py"])

    def test_diff_files_returns_unicode_not_c_quoted_untracked_overlay(self):
        self._git_repo()
        folder = self.tmp / "specs" / "dyn-0001-crea-una-pequeña-librería-python-de-fibonacci-añ"
        folder.mkdir(parents=True)
        (folder / "plan.md").write_text("# exported plan\n", encoding="utf-8")
        quoted = subprocess.run(
            ["git", "-c", "core.quotepath=true", "ls-files", "--others", "--exclude-standard"],
            cwd=self.tmp, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.strip()
        self.assertTrue(quoted.startswith('"') and quoted.endswith('"'), quoted)
        db = Database(self.tmp)
        db.initialize()
        files = GitManager(self.tmp, db).diff_files(self.tmp)
        self.assertIn(UNICODE_SPANISH_PLAN, files)
        self.assertFalse(any(path.startswith('"') for path in files), files)

    def test_register_result_ignores_spanish_exported_plan_overlay(self):
        self._git_repo()
        engine = DynosAI(self.tmp)
        engine.initialize("RC", "python", "true", None)
        work = engine.start("Crear una CLI Fibonacci que genere N términos")
        wid = work["id"]
        engine.continue_work(wid)
        engine.submit_spec(wid, demo_spec(), "rc")
        engine.continue_work(wid)
        engine.review(wid, "spec")
        engine.submit_plan(wid, demo_plan(), "rc")
        engine.review(wid, "plan")
        prep = engine.continue_work(wid)
        worktree = Path(prep["worktree"])
        (worktree / "src").mkdir(exist_ok=True)
        (worktree / "tests").mkdir(exist_ok=True)
        (worktree / "src" / "__init__.py").write_text("", encoding="utf-8")
        (worktree / "src" / "fibonacci.py").write_text("def generate_fibonacci(n): return []\n", encoding="utf-8")
        (worktree / "src" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
        (worktree / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        overlay = worktree / "specs" / "dyn-0001-crea-una-pequeña-librería-python-de-fibonacci-añ"
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / "plan.md").write_text("# exported plan\n", encoding="utf-8")
        result = engine.register_result(
            wid,
            "implemented",
            ["src/__init__.py", "src/fibonacci.py", "src/cli.py", "tests/test_fibonacci.py"],
            [],
            [f"{wid}-T01", f"{wid}-T02"],
            prep["run_id"],
        )
        self.assertEqual(result["outside_scope"], [], result)
        self.assertFalse(any("plan.md" in path.replace("\\", "/") for path in result["actual_files"]))

    def test_register_result_completes_task_with_slash_octal_overlay_in_task_files(self):
        self._git_repo()
        engine = DynosAI(self.tmp)
        engine.initialize("RC", "python", "true", None)
        work = engine.start("Crear una CLI Fibonacci que genere N términos")
        wid = work["id"]
        engine.continue_work(wid)
        engine.submit_spec(wid, demo_spec(), "rc")
        engine.continue_work(wid)
        engine.review(wid, "spec")
        engine.submit_plan(wid, demo_plan(), "rc")
        engine.review(wid, "plan")
        prep = engine.continue_work(wid)
        worktree = Path(prep["worktree"])
        (worktree / "src").mkdir(exist_ok=True)
        (worktree / "tests").mkdir(exist_ok=True)
        (worktree / "src" / "__init__.py").write_text("", encoding="utf-8")
        (worktree / "src" / "fibonacci.py").write_text("def generate_fibonacci(n): return []\n", encoding="utf-8")
        (worktree / "src" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
        (worktree / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        t01 = engine.db.one("SELECT id,files FROM tasks WHERE work_id=? ORDER BY id LIMIT 1", (wid,))
        poisoned = json.loads(t01["files"])
        poisoned.extend([UNICODE_SPANISH_PLAN, SLASH_OCTAL_PLAN])
        engine.db.execute("UPDATE tasks SET files=? WHERE id=?", (json.dumps(poisoned), t01["id"]))
        engine._run_result_validation = lambda *args, **kwargs: {
            "passed": True, "results": [{"profile": "unit", "command": "true", "status": "passed", "exit_code": 0, "output": "ok"}],
            "scope": "task", "skipped": False, "task_ids": [f"{wid}-T01", f"{wid}-T02"], "evidence_required": ["git_diff"],
        }
        result = engine.register_result(
            wid,
            "implemented",
            ["src/__init__.py", "src/fibonacci.py", "src/cli.py", "tests/test_fibonacci.py"],
            [],
            [f"{wid}-T01", f"{wid}-T02"],
            prep["run_id"],
        )
        self.assertEqual(result["status"], "verified", result)
        self.assertEqual(result["remaining_tasks"], 0, result)
        self.assertEqual(engine.db.one("SELECT state FROM tasks WHERE id=?", (t01["id"],))["state"], "completed")
        cleaned = json.loads(engine.db.one("SELECT files FROM tasks WHERE id=?", (t01["id"],))["files"])
        self.assertFalse(any("plan.md" in str(path) or "303" in str(path) for path in cleaned), cleaned)


if __name__ == "__main__":
    unittest.main()
