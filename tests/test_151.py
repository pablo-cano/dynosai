import json
import os
import shutil
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.app_server import StudioExecutionManager
from dynosai_flow.cli import _run_cursor_acp_turn, main


class DynosAI141CursorACPStudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-151-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_cursor_acp_injects_dynosai_mcp_and_handles_permission_requests(self):
        captured = self.tmp / "session.json"
        fake = self.tmp / "agent.py"
        fake.write_text(
            """#!/usr/bin/env python3
import json,sys
capture = %r
prompt_id = None

def send(x):
    sys.stdout.write(json.dumps(x, ensure_ascii=False) + "\\n")
    sys.stdout.flush()

for raw in sys.stdin:
    msg=json.loads(raw)
    method=msg.get("method"); rid=msg.get("id")
    if method=="initialize":
        assert msg.get("jsonrpc")=="2.0"
        send({"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":1,"authMethods":[{"id":"cursor_login"}]}})
    elif method=="authenticate":
        assert msg["params"]["methodId"]=="cursor_login"
        send({"jsonrpc":"2.0","id":rid,"result":{}})
    elif method=="session/new":
        params=msg["params"]
        server=params["mcpServers"][0]
        assert server["name"]=="dynosai"
        assert server["args"]==["-m","dynosai_flow.mcp"]
        env={item["name"]:item["value"] for item in server["env"]}
        assert env["DYNOSAI_HUMAN_GATE_TRANSPORT"]=="studio"
        open(capture,"w",encoding="utf-8").write(json.dumps(params,ensure_ascii=False))
        send({"jsonrpc":"2.0","id":rid,"result":{"sessionId":"sess-1"}})
    elif method=="session/prompt":
        assert msg["params"]["prompt"][0]["text"].startswith("Crea una pequeña")
        prompt_id=rid
        send({"jsonrpc":"2.0","id":900,"method":"session/request_permission","params":{"options":[{"optionId":"reject-once","kind":"reject_once"},{"optionId":"allow-once","kind":"allow_once"}]}})
    elif rid==900:
        assert msg["result"]["outcome"]["outcome"]=="selected"
        assert msg["result"]["outcome"]["optionId"]=="allow-once"
        send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"sess-1","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"ok"}}}})
        send({"jsonrpc":"2.0","id":prompt_id,"result":{"stopReason":"end_turn"}})
        break
""" % str(captured),
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        env = os.environ.copy()
        env.update({"DYNOSAI_HUMAN_GATE_TRANSPORT":"studio", "DYNOSAI_SESSION_TOKEN":"token", "PYTHONUTF8":"1"})
        result = _run_cursor_acp_turn(
            str(fake), self.tmp, env,
            "Crea una pequeña librería de Fibonacci con validación.",
            {"provider_selector":"grok-test"}, max_runtime=10, idle_timeout=5,
        )
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["protocol"], "cursor-acp")
        self.assertEqual(result["session_id"], "sess-1")
        self.assertTrue(result["mcp_injected"])
        params = json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(params["cwd"], str(self.tmp))

    def test_cursor_acp_returns_actionable_auth_error(self):
        fake = self.tmp / "agent_auth.py"
        fake.write_text(
            """#!/usr/bin/env python3
import json,sys
for raw in sys.stdin:
    msg=json.loads(raw); method=msg.get('method'); rid=msg.get('id')
    if method=='initialize': print(json.dumps({'jsonrpc':'2.0','id':rid,'result':{'protocolVersion':1}}),flush=True)
    elif method=='authenticate': print(json.dumps({'jsonrpc':'2.0','id':rid,'error':{'code':401,'message':'not logged in'}}),flush=True); break
""",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        result = _run_cursor_acp_turn(str(fake), self.tmp, os.environ.copy(), "prompt", None, max_runtime=10, idle_timeout=5)
        self.assertEqual(result["exit_code"], 1)
        self.assertIn("authentication failed", json.dumps(result["errors"]).lower())

    def test_headless_cli_propagates_provider_exit_code_without_dumping_runtime(self):
        stdout = StringIO()
        with patch("dynosai_flow.cli.open_agent", return_value={"exit_code":7,"managed_runtime":{"very":"large"}}):
            with redirect_stdout(stdout):
                code = main(["--project", str(self.tmp), "open", "--agent", "cursor", "--headless"])
        self.assertEqual(code, 7)
        self.assertEqual(stdout.getvalue(), "")

    def test_studio_cursor_discovery_supports_current_agent_entrypoint(self):
        manager = StudioExecutionManager()
        def which(name):
            return "C:/cursor/agent.exe" if name == "agent" else None
        with patch("dynosai_flow.app_server.shutil.which", side_effect=which):
            self.assertEqual(manager._provider_command("cursor"), "C:/cursor/agent.exe")

    def test_studio_diagnostics_use_persistent_custom_disclosure(self):
        root = Path(__file__).resolve().parents[1]
        app = (root / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
        css = (root / "apps" / "studio" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("expandedExecutionDiagnostics", app)
        self.assertIn("data-diagnostic-toggle", app)
        self.assertNotIn('<details class="runtime-details">', app)
        self.assertIn("runtime-diagnostic-panel", css)

    def test_studio_assets_remain_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg"):
            self.assertEqual(
                (root / "apps" / "studio" / name).read_bytes(),
                (root / "src" / "dynosai_flow" / "studio_assets" / name).read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
