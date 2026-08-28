import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from dynosai_flow.app_server import StudioExecutionManager
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import _run_cursor_acp_turn


class DynosAI141ReviewExecutionUXTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-152-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _git_repo(self):
        subprocess.run(["git", "init"], cwd=self.tmp, check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "tests@dynosai.local"], cwd=self.tmp, check=True)
        subprocess.run(["git", "config", "user.name", "DynosAI Tests"], cwd=self.tmp, check=True)
        (self.tmp / "README.md").write_text("# demo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.tmp, check=True, capture_output=True, text=True)

    def test_spec_review_exposes_the_actual_requirements_and_acceptance_criteria(self):
        self._git_repo()
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Review detail", agent="cursor")
        work = app.start_feature("Añadir Fibonacci", provider="cursor")
        app.next_action("cursor", work["id"], execute=True)
        spec = {
            "objective": "Crear Fibonacci iterativo",
            "scope": ["library"],
            "out_of_scope": [],
            "requirements": [
                {"id": "REQ-001", "text": "fibonacci(n) devuelve los primeros n números", "priority": "must"},
                {"id": "REQ-002", "text": "Los negativos producen ValueError", "priority": "must"},
            ],
            "acceptance_criteria": [
                {"id": "AC-001", "requirement": "REQ-001", "text": "Dado n=5, cuando se llama fibonacci(5), entonces devuelve [0, 1, 1, 2, 3]."},
                {"id": "AC-002", "requirement": "REQ-002", "text": "Dado n=-1, cuando se llama fibonacci(-1), entonces lanza ValueError."},
            ],
            "business_rules": [],
            "unknowns": [],
            "affected_features": [],
        }
        app.engine.submit_spec(work["id"], spec, "test")
        app.next_action("cursor", work["id"], execute=True)
        reviews = app.list_reviews()
        self.assertEqual(len(reviews), 1)
        detail = reviews[0]["detail"]["spec"]
        self.assertEqual(detail["objective"], "Crear Fibonacci iterativo")
        self.assertEqual([row["id"] for row in detail["requirements"]], ["REQ-001", "REQ-002"])
        self.assertEqual([row["id"] for row in detail["acceptance_criteria"]], ["AC-001", "AC-002"])

    def test_execution_status_exposes_only_bounded_studio_progress_as_activity(self):
        manager = StudioExecutionManager()
        stderr = self.tmp / "stderr.log"
        stdout = self.tmp / "stdout.log"
        stderr.write_text(
            "provider-noise\nDYNOSAI_PROGRESS|Cursor connected\nDYNOSAI_PROGRESS|Planning task 1\n",
            encoding="utf-8",
        )
        stdout.write_text("", encoding="utf-8")
        root = str(self.tmp.resolve())
        manager._runs[(root, "DYN-001")] = {
            "work_id": "DYN-001", "provider": "cursor", "status": "running",
            "started_at": "2026-08-26T00:00:00+00:00", "stderr": str(stderr), "stdout": str(stdout),
        }
        item = manager.status(self.tmp, "DYN-001")["items"][0]
        self.assertEqual(item["activity"], ["Cursor connected", "Planning task 1"])
        self.assertIn("provider-noise", item["stderr_tail"])
        self.assertIn("last_activity_at", item)

    def test_cursor_provider_plan_gate_is_transport_acknowledged_without_bypassing_dynosai(self):
        fake = self.tmp / "agent.py"
        fake.write_text(
            """#!/usr/bin/env python3
import json,sys
prompt_id=None

def send(x):
    print(json.dumps(x), flush=True)

for raw in sys.stdin:
    msg=json.loads(raw); method=msg.get('method'); rid=msg.get('id')
    if method=='initialize': send({'jsonrpc':'2.0','id':rid,'result':{'protocolVersion':1}})
    elif method=='authenticate': send({'jsonrpc':'2.0','id':rid,'result':{}})
    elif method=='session/new': send({'jsonrpc':'2.0','id':rid,'result':{'sessionId':'s1'}})
    elif method=='session/prompt':
        prompt_id=rid
        send({'jsonrpc':'2.0','id':700,'method':'cursor/create_plan','params':{'name':'Fibonacci plan','plan':'Do it','todos':[]}})
    elif rid==700:
        assert msg['result']['outcome']['outcome']=='accepted'
        send({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'s1','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'Preparing governed plan'}}}})
        send({'jsonrpc':'2.0','id':prompt_id,'result':{'stopReason':'end_turn'}})
        break
""",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        err = StringIO()
        with redirect_stderr(err):
            result = _run_cursor_acp_turn(str(fake), self.tmp, os.environ.copy(), "plan", None, max_runtime=10, idle_timeout=5)
        self.assertEqual(result["exit_code"], 0, result)
        progress = err.getvalue()
        self.assertIn("DYNOSAI_PROGRESS|", progress)
        self.assertIn("Cursor prepared Fibonacci plan", progress)
        self.assertIn("Preparing governed plan", progress)

    def test_frontend_keeps_change_request_open_and_updates_tutorial_after_refresh(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        self.assertIn("expandedChangeRequests", app)
        self.assertIn("reviewDrafts", app)
        self.assertIn("updateTutorialFromState();\n    syncTutorialNavigation()", app)
        self.assertIn('navigate("approvals")', app)
        self.assertIn("executionActivityHtml", app)

    def test_approval_ui_renders_full_contract_instead_of_only_counts(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        self.assertIn('reviewItems("approvals.requirementsTitle", detail.spec.requirements)', app)
        self.assertIn('reviewItems("approvals.acceptanceTitle", detail.spec.acceptance_criteria)', app)
        self.assertIn('reviewItems("approvals.tasksTitle", tasks)', app)
        self.assertIn("reviewIntro(review)", app)

    def test_spanish_contract_and_agent_activity_copy_are_present(self):
        root = Path(__file__).resolve().parents[1]
        i18n = (root / "apps" / "studio" / "i18n.js").read_text(encoding="utf-8")
        for phrase in (
            "Comprueba el objetivo, los requisitos y los criterios de aceptación",
            "Criterios de aceptación",
            "Actividad del agente",
            "Un registro breve y en directo",
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
