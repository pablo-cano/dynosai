import inspect
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.app_server import StudioAPI, StudioExecutionManager
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.util import run_command


SPEC = {
    "objective": "Crear Fibonacci iterativo",
    "scope": ["library"],
    "out_of_scope": [],
    "requirements": [
        {"id": "REQ-001", "text": "fibonacci(n) devuelve los primeros n números", "priority": "must"},
    ],
    "acceptance_criteria": [
        {"id": "AC-001", "requirement": "REQ-001", "text": "Dado n=5, entonces devuelve [0, 1, 1, 2, 3]."},
    ],
    "business_rules": [],
    "unknowns": [],
    "affected_features": [],
}


class _NoLaunch:
    def available(self, provider):
        return False

    def start(self, root, provider, work_id, *, on_exit=None):
        return {"work_id": work_id, "provider": provider, "status": "unavailable"}

    def status(self, root, work_id=None):
        return {"items": []}


class DynosAI154StudioReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-154-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git_repo(self):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "tests@dynosai.local"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "DynosAI Tests"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("# demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.tmp, check=True, capture_output=True, text=True)

    def _app_with_spec_review(self):
        self._git_repo()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Auto mode", agent="cursor")
        work = app.start_feature("Añadir Fibonacci", provider="cursor")
        app.next_action("cursor", work["id"], execute=True)
        app.engine.submit_spec(work["id"], SPEC, "test")
        return app, work

    def test_run_command_survives_invalid_utf8_from_child_output(self):
        script = "import sys; sys.stdout.buffer.write(b'hello \\xe0 world\\n')"
        result = run_command([sys.executable, "-c", script], self.tmp, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)
        self.assertIn("world", result.stdout)
        source = inspect.getsource(run_command)
        self.assertIn('encoding="utf-8"', source)
        self.assertIn('errors="replace"', source)

    def test_headless_provider_stdio_replaces_invalid_utf8(self):
        cli = (Path(__file__).resolve().parents[1] / "src" / "dynosai_flow" / "cli.py").read_text(encoding="utf-8")
        self.assertIn('errors="replace"', cli)
        self.assertNotIn('errors="strict"', cli)
        process = (Path(__file__).resolve().parents[1] / "src" / "dynosai_flow" / "provider_process.py").read_text(encoding="utf-8")
        self.assertIn('encoding="utf-8"', process)
        self.assertIn('errors="replace"', process)

    def test_progress_lines_keep_text_and_expose_timestamps(self):
        manager = StudioExecutionManager()
        stderr = self.tmp / "stderr.log"
        stderr.write_text(
            "noise\n"
            "DYNOSAI_PROGRESS|Cursor connected\n"
            "DYNOSAI_PROGRESS|2026-08-26T18:01:00+00:00|Planning task 1\n",
            encoding="utf-8",
        )
        stdout = self.tmp / "stdout.log"
        stdout.write_text("", encoding="utf-8")
        root = str(self.tmp.resolve())
        manager._runs[(root, "DYN-001")] = {
            "work_id": "DYN-001", "provider": "cursor", "status": "running",
            "started_at": "2026-08-26T00:00:00+00:00", "stderr": str(stderr), "stdout": str(stdout),
        }
        item = manager.status(self.tmp, "DYN-001")["items"][0]
        self.assertEqual(item["activity"], ["Cursor connected", "Planning task 1"])
        self.assertEqual(
            [row["text"] for row in item["activity_log"]],
            ["Cursor connected", "Planning task 1"],
        )
        self.assertIsNone(item["activity_log"][0]["at"])
        self.assertEqual(item["activity_log"][1]["at"], "2026-08-26T18:01:00+00:00")

    def test_auto_approve_records_studio_auto_and_clears_the_spec_gate(self):
        app, work = self._app_with_spec_review()
        app.set_auto_approve(True)
        result = app.next_action("cursor", work["id"], execute=True)
        self.assertIsNone(result.get("human_interaction"))
        self.assertEqual(app.list_reviews(), [])
        history = app.list_review_history()
        self.assertTrue(history)
        self.assertEqual(history[0]["gate"], "spec")
        self.assertEqual(history[0]["status"], "accepted")
        self.assertEqual(history[0]["source"], "studio_auto")
        self.assertEqual(history[0]["decision"], "approve")

    def test_auto_approve_does_not_answer_clarifications(self):
        app, work = self._app_with_spec_review()
        app.interactions.clarification(work["id"], "¿Qué representa n?")
        app.set_auto_approve(True)
        pending = app.interactions.pending_for_work(work["id"])
        self.assertTrue(any(item.kind == "clarification" for item in pending))

    def test_manual_approval_history_is_not_marked_auto(self):
        app, work = self._app_with_spec_review()
        app.next_action("cursor", work["id"], execute=True)
        reviews = app.list_reviews()
        self.assertEqual(len(reviews), 1)
        app.resolve_interaction(reviews[0]["id"], "accept", {"decision": "approve"})
        history = app.list_review_history()
        self.assertEqual(history[0]["source"], "human")
        self.assertEqual(history[0]["gate"], "spec")

    def test_reviews_api_exposes_history_and_project_settings_persist_auto_mode(self):
        app, work = self._app_with_spec_review()
        app.next_action("cursor", work["id"], execute=True)
        api = StudioAPI(self.tmp, execution_manager=_NoLaunch())
        status, payload = api.get("/api/reviews")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["history"], [])
        self.assertFalse(payload["auto_approve"])
        status, saved = api.post("/api/project-settings", {"auto_approve": True})
        self.assertEqual(status, 200)
        self.assertTrue(saved["auto_approve"])
        status, payload = api.get("/api/reviews")
        self.assertEqual(status, 200)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["history"][0]["source"], "studio_auto")
        status, overview = api.get("/api/overview")
        self.assertEqual(status, 200)
        self.assertTrue(overview["auto_approve"])

    def test_work_ui_pins_newest_activity_and_does_not_jump_scroll_on_refresh(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        self.assertIn("navigate(view, {scroll", app)
        self.assertIn("scroll:false", app)
        self.assertIn("window.scrollY", app)
        self.assertIn("newest first", app)
        self.assertIn("formatStamp", app)
        self.assertIn("data-work-id", app)
        self.assertIn("review-history", app)
        self.assertIn("studio_auto", app)
        self.assertIn("auto-approve-toggle", app)
        html = (root / "apps" / "studio" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="review-history"', html)
        self.assertIn('id="auto-approve-toggle"', html)

    def test_i18n_covers_auto_mode_history_and_project_checks(self):
        root = Path(__file__).resolve().parents[1]
        i18n = (root / "apps" / "studio" / "i18n.js").read_text(encoding="utf-8")
        for phrase in (
            "Auto-approve workflow gates",
            "Approved automatically",
            "Approval history",
            "These commands are not extra work now",
            "Aprobar automáticamente las puertas del flujo",
            "Aprobada automáticamente",
            "Historial de aprobaciones",
            "Estos comandos no son trabajo extra ahora",
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
