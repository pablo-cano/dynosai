import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.engine import DynosAI
from dynosai_flow import policy as path_policy


class _CountingLaunch:
    def __init__(self):
        self.starts = []

    def available(self, provider):
        return True

    def start(self, root, provider, work_id, *, on_exit=None):
        self.starts.append({"root": str(root), "provider": provider, "work_id": work_id})
        return {"work_id": work_id, "provider": provider, "status": "running", "message": "Coding agent is working."}

    def status(self, root, work_id=None):
        return {"items": []}


class DynosAI155ImplementStopAndScrollTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-155-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git_repo(self):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "tests@dynosai.local"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "DynosAI Tests"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("# demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.tmp, check=True, capture_output=True, text=True)

    def _engine_implementing(self):
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
        self.assertEqual(engine.get_work(wid)["state"], "implementing")
        return engine, wid, prep

    def test_policy_treats_exported_plan_overlay_as_managed(self):
        fn = getattr(path_policy, "is_managed_workflow_artifact", None)
        self.assertTrue(callable(fn), "policy.is_managed_workflow_artifact is required")
        self.assertTrue(fn("plan.md"))
        self.assertTrue(fn("specs/dyn-0001-demo/plan.md"))
        self.assertTrue(fn("specs/dyn-0001-demo/spec.md"))
        self.assertFalse(fn("fibonacci/__init__.py"))
        self.assertFalse(fn("README.md"))

    def test_scope_extension_for_plan_overlay_is_not_a_human_gate(self):
        engine, wid, prep = self._engine_implementing()
        result = engine.request_scope_extension(
            wid, "plan.md", "modify", "Need to edit the exported plan overlay",
            run_id=prep["run_id"],
        )
        self.assertEqual(result["status"], "managed_artifact", result)
        self.assertIsNone(result.get("id"))
        nested = engine.request_scope_extension(
            wid, "specs/dyn-0001-demo/plan.md", "modify", "Need to patch specs plan.md",
            run_id=prep["run_id"],
        )
        self.assertEqual(nested["status"], "managed_artifact", nested)
        self.assertEqual(engine.db.one("SELECT COUNT(*) AS n FROM scope_requests WHERE work_id=?", (wid,))["n"], 0)

    def test_register_result_ignores_exported_plan_overlay_in_git_diff(self):
        engine, wid, prep = self._engine_implementing()
        worktree = Path(prep["worktree"])
        (worktree / "src").mkdir(exist_ok=True)
        (worktree / "tests").mkdir(exist_ok=True)
        (worktree / "src" / "__init__.py").write_text("", encoding="utf-8")
        (worktree / "src" / "fibonacci.py").write_text("def generate_fibonacci(n): return []\n", encoding="utf-8")
        (worktree / "src" / "cli.py").write_text("def main(): return 0\n", encoding="utf-8")
        (worktree / "tests" / "test_fibonacci.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        overlay = worktree / "specs" / "dyn-0001-demo"
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
        overlay_hits = [path for path in result["actual_files"] if "plan.md" in path.replace("\\", "/") or path.replace("\\", "/").startswith("specs/")]
        self.assertEqual(overlay_hits, [])
        self.assertEqual(result["outside_scope"], [])
        self.assertFalse(any("plan.md" in str(item.get("path") or "") for item in result.get("scope_action_violations") or []))

    def test_auto_approve_records_scope_extension_and_relanches_same_step(self):
        engine, wid, prep = self._engine_implementing()
        pending = engine.request_scope_extension(wid, "docs/notes.md", "create", "Need a short usage note", run_id=prep["run_id"])
        self.assertEqual(pending["status"], "pending")
        app = DynosAIApplication(self.tmp)
        app.interactions.scope_request(pending, provider="cursor")
        app.set_auto_approve(True)
        self.assertEqual(app.interactions.pending_for_work(wid), [])
        history = app.list_review_history()
        self.assertTrue(any(item["kind"] == "scope_extension" and item["source"] == "studio_auto" for item in history), history)
        counting = _CountingLaunch()
        api = StudioAPI(self.tmp, execution_manager=counting)
        extra = engine.request_scope_extension(wid, "docs/changelog.md", "create", "Need changelog", run_id=prep["run_id"])
        self.assertEqual(extra["status"], "pending")
        app.interactions.scope_request(extra, provider="cursor")
        outcome = api._on_execution_exit(self.tmp, "cursor", wid, 0)
        self.assertEqual(outcome["status"], "running", outcome)
        self.assertEqual(len(counting.starts), 1)

    def test_retry_is_blocked_while_a_human_question_is_pending(self):
        engine, wid, _prep = self._engine_implementing()
        app = DynosAIApplication(self.tmp)
        app.interactions.clarification(wid, "What does n represent?")
        api = StudioAPI(self.tmp, execution_manager=_CountingLaunch())
        status, payload = api.post("/api/execution/start", {"work_id": wid})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "human_gate")

    def test_settle_explains_unverified_implementation(self):
        engine, wid, _prep = self._engine_implementing()
        outcome = StudioAPI._settle_work(self.tmp, "cursor", wid)
        self.assertEqual(outcome["status"], "stopped")
        self.assertIn("verified", outcome["message"].lower())

    def test_studio_refresh_preserves_window_and_diagnostic_scroll(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        self.assertIn("lastWindowScrollY", app)
        self.assertIn('window.addEventListener("scroll"', app)
        self.assertIn("requestAnimationFrame", app)
        self.assertIn("data-diagnostic-work", app)
        self.assertIn("lastWindowScrollY = window.scrollY", app)
        start = app.index("async function refresh")
        chunk = app[start:start + 1800]
        self.assertNotIn("const pageY = window.scrollY;\n  const viewBefore", chunk)
        self.assertIn("window.scrollY || lastWindowScrollY", chunk)

    def test_cursor_progress_does_not_flush_tiny_fragments(self):
        cli = (Path(__file__).resolve().parents[1] / "src" / "dynosai_flow" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("def emit_agent_chunk", cli)
        self.assertIn("len(message_buffer) >= 40", cli)

    def test_i18n_and_auto_copy_cover_managed_overlays(self):
        root = Path(__file__).resolve().parents[1]
        i18n = (root / "apps" / "studio" / "i18n.js").read_text(encoding="utf-8")
        for phrase in (
            "DynosAI-owned spec and plan files do not need a scope decision",
            "Los ficheros de especificación y plan de DynosAI no piden una decisión de alcance",
        ):
            self.assertIn(phrase, i18n)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
