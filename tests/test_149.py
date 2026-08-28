import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.app_server import StudioExecutionManager
from dynosai_flow.cli import _run_codex_headless_turn, parser
from dynosai_flow.mcp import MCPServer, ElicitationExchange
from dynosai_flow.application import DynosAIApplication


class DynosAI141HeadlessStudioExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-149-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_studio_execution_uses_hidden_headless_open_mode(self):
        source = Path(__file__).resolve().parents[1] / "src" / "dynosai_flow" / "app_server.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn('"open", "--agent", provider, "--headless"', text)
        args = parser().parse_args(["open", "--agent", "codex", "--headless"])
        self.assertTrue(args.headless)

    def test_studio_gate_transport_returns_human_interaction_in_tool_result(self):
        project = self.tmp / "project"
        project.mkdir()
        DynosAIApplication(project).initialize_project(name="Studio gate")
        server = MCPServer(project)
        server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "clientInfo": {"name": "Cursor", "version": "test"},
                "capabilities": {"elicitation": {"form": {}}},
            },
        })
        interaction = {
            "id": "INT-1", "message": "Review", "requested_schema": {"type": "object"}
        }
        original = os.environ.get("DYNOSAI_HUMAN_GATE_TRANSPORT")
        os.environ["DYNOSAI_HUMAN_GATE_TRANSPORT"] = "studio"
        try:
            with patch.object(server, "call_tool", return_value={"human_interaction": interaction, "state": "spec_review"}):
                response = server.handle({
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "dynosai_ask", "arguments": {"query": "status"}},
                })
        finally:
            if original is None:
                os.environ.pop("DYNOSAI_HUMAN_GATE_TRANSPORT", None)
            else:
                os.environ["DYNOSAI_HUMAN_GATE_TRANSPORT"] = original
        self.assertNotIsInstance(response, ElicitationExchange)
        self.assertEqual(response["result"]["structuredContent"]["human_interaction"]["id"], "INT-1")

    def test_codex_headless_app_server_completes_without_terminal_ui(self):
        fake = self.tmp / "fake_codex.py"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys

def send(x):
    print(json.dumps(x), flush=True)
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get("method"); rid=msg.get("id")
    if method=="initialize":
        assert msg["params"]["capabilities"]["mcpServerOpenaiFormElicitation"] is False
        send({"id":rid,"result":{"userAgent":"fake-codex"}})
    elif method=="initialized":
        pass
    elif method=="thread/start":
        send({"id":rid,"result":{"thread":{"id":"thr"}}})
    elif method=="turn/start":
        send({"id":rid,"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
        send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
        break
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = _run_codex_headless_turn(str(fake), self.tmp, os.environ.copy(), "hello", {"model":"gpt-test","effort":"medium"}, max_runtime=10, idle_timeout=5)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue(result["turn_completed"])
        self.assertEqual(result["sandbox"], "danger-full-access")

    def test_codex_headless_turn_sends_sandbox_policy(self):
        captured = self.tmp / "turn.json"
        fake = self.tmp / "fake_codex.py"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys
captured=sys.argv[-1]

def send(x):
    print(json.dumps(x), flush=True)
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get("method"); rid=msg.get("id")
    if method=="initialize":
        send({"id":rid,"result":{"userAgent":"fake-codex"}})
    elif method=="initialized":
        pass
    elif method=="thread/start":
        send({"id":rid,"result":{"thread":{"id":"thr"}}})
    elif method=="turn/start":
        open(captured,"w",encoding="utf-8").write(json.dumps(msg))
        send({"id":rid,"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
        send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
        break
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        # Shim receives captured path as extra argument because executable_command
        # routes Python shebang files through the interpreter.
        fake2 = self.tmp / "codex.py"
        body=fake.read_text(encoding="utf-8").replace("captured=sys.argv[-1]", f"captured={str(captured)!r}")
        fake2.write_text(body,encoding="utf-8"); fake2.chmod(fake2.stat().st_mode | stat.S_IXUSR)
        result = _run_codex_headless_turn(str(fake2), self.tmp, os.environ.copy(), "hello", {"model":"gpt-test","effort":"medium"}, max_runtime=10, idle_timeout=5)
        self.assertEqual(result["exit_code"], 0, result)
        payload = json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(payload["params"].get("sandboxPolicy"), {"type": "dangerFullAccess"})

    def test_codex_headless_turn_tolerates_transient_stream_disconnect(self):
        fake = self.tmp / "fake_codex_retry.py"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys

def send(x):
    print(json.dumps(x), flush=True)
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get("method"); rid=msg.get("id")
    if method=="initialize":
        send({"id":rid,"result":{"userAgent":"fake-codex"}})
    elif method=="initialized":
        pass
    elif method=="thread/start":
        send({"id":rid,"result":{"thread":{"id":"thr"}}})
    elif method=="turn/start":
        send({"id":rid,"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
        send({"method":"error","params":{"message":"Reconnecting... 1/5","willRetry":True,"codexErrorInfo":{"responseStreamDisconnected":{"httpStatusCode":None}},"threadId":"thr","turnId":"turn"}})
        send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
        break
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = _run_codex_headless_turn(str(fake), self.tmp, os.environ.copy(), "hello", None, max_runtime=10, idle_timeout=5)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue(result["turn_completed"])
        self.assertEqual(result["errors"], [])

    def test_codex_headless_turn_honours_sandbox_override(self):
        captured = self.tmp / "turn-override.json"
        fake = self.tmp / "fake_codex_override.py"
        body = r'''#!/usr/bin/env python3
import json,sys
captured=CAPTURED

def send(x):
    print(json.dumps(x), flush=True)
for line in sys.stdin:
    msg=json.loads(line)
    method=msg.get("method"); rid=msg.get("id")
    if method=="initialize":
        send({"id":rid,"result":{"userAgent":"fake-codex"}})
    elif method=="initialized":
        pass
    elif method=="thread/start":
        send({"id":rid,"result":{"thread":{"id":"thr"}}})
    elif method=="turn/start":
        open(captured,"w",encoding="utf-8").write(json.dumps(msg))
        send({"id":rid,"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
        send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
        break
'''.replace("CAPTURED", repr(str(captured)))
        fake.write_text(body, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        env = os.environ.copy()
        env["DYNOSAI_CODEX_SANDBOX"] = "workspace-write"
        result = _run_codex_headless_turn(str(fake), self.tmp, env, "hello", None, max_runtime=10, idle_timeout=5)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["sandbox"], "workspace-write")
        payload = json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(payload["params"].get("sandboxPolicy"), {"type": "workspaceWrite"})

    def test_execution_manager_exposes_provider_log_tail_after_exit(self):
        manager = StudioExecutionManager()
        stderr = self.tmp / "stderr.log"
        stderr.write_text("provider diagnostic line\n", encoding="utf-8")
        self.assertEqual(manager._tail(stderr), "provider diagnostic line")

    def test_studio_ui_localizes_failure_and_offers_diagnostics(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        i18n = (root / "apps" / "studio" / "i18n.js").read_text(encoding="utf-8")
        self.assertIn('run.stderr_tail || run.stdout_tail', app)
        self.assertIn('execution.failedText', app)
        self.assertIn('"execution.details": "Ver detalles del diagnóstico"', i18n)
        self.assertIn('"execution.details": "View diagnostic details"', i18n)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
