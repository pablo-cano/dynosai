import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dynosai_flow.app_server import StudioExecutionManager
from dynosai_flow.cli import _run_codex_headless_turn, open_agent
from dynosai_flow.managed_runtime import ManagedProviderRuntime
from dynosai_flow.application import DynosAIApplication


class DynosAI141WindowsUtf8StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-150-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_codex_app_server_transport_is_explicit_utf8_and_preserves_spanish_prompt(self):
        fake = self.tmp / "fake_codex.py"
        captured = self.tmp / "captured.json"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys
captured=sys.argv[-1]
def send(x):
    sys.stdout.write(json.dumps(x)+"\n"); sys.stdout.flush()
for raw in sys.stdin.buffer:
    text=raw.decode("utf-8", errors="strict")
    msg=json.loads(text)
    method=msg.get("method"); rid=msg.get("id")
    if method=="initialize": send({"id":rid,"result":{"userAgent":"fake"}})
    elif method=="initialized": pass
    elif method=="thread/start": send({"id":rid,"result":{"thread":{"id":"thr"}}})
    elif method=="turn/start":
        open(captured,"w",encoding="utf-8").write(json.dumps(msg,ensure_ascii=False))
        send({"id":rid,"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
        send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
        break
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        # executable_command passes additional args before our custom path, so use a wrapper
        wrapper = self.tmp / "wrapper.py"
        wrapper.write_text(
            "import runpy,sys\nsys.argv=[sys.argv[1],*sys.argv[2:]," + repr(str(captured)) + "]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n",
            encoding="utf-8",
        )
        # Simpler direct shim that ignores app-server args and knows capture path.
        fake2 = self.tmp / "codex.py"
        body=fake.read_text(encoding="utf-8").replace("captured=sys.argv[-1]", f"captured={str(captured)!r}")
        fake2.write_text(body,encoding="utf-8"); fake2.chmod(fake2.stat().st_mode | stat.S_IXUSR)
        prompt="Crea una pequeña librería. Añade Fibonacci y validación."
        result=_run_codex_headless_turn(str(fake2),self.tmp,os.environ.copy(),prompt,{"model":"gpt-test","effort":"medium"},max_runtime=10,idle_timeout=5)
        self.assertEqual(result["exit_code"],0,result)
        payload=json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(payload["params"]["input"][0]["text"],prompt)

    def test_managed_runtime_forces_utf8_for_mcp_child(self):
        project=self.tmp/"project"; project.mkdir()
        DynosAIApplication(project).initialize_project(name="UTF8")
        runtime=ManagedProviderRuntime(project).prepare("codex")
        self.assertEqual(runtime.env["PYTHONUTF8"],"1")
        self.assertEqual(runtime.env["PYTHONIOENCODING"],"utf-8")
        config=(Path(runtime.env["CODEX_HOME"])/"config.toml").read_text(encoding="utf-8")
        self.assertIn('PYTHONUTF8 = "1"',config)
        self.assertIn('PYTHONIOENCODING = "utf-8"',config)

    def test_studio_launcher_forces_utf8_and_no_color(self):
        project=self.tmp/"project"; project.mkdir()
        manager=StudioExecutionManager()
        proc=MagicMock(); proc.pid=123; proc.poll.return_value=None
        with patch.object(manager,"_provider_command",return_value="codex"), patch("dynosai_flow.app_server.subprocess.Popen",return_value=proc) as popen:
            manager.start(project,"codex","DYN-1")
        env=popen.call_args.kwargs["env"]
        self.assertEqual(env["PYTHONUTF8"],"1")
        self.assertEqual(env["PYTHONIOENCODING"],"utf-8")
        self.assertEqual(env["NO_COLOR"],"1")

    def test_diagnostic_tail_strips_ansi_sequences(self):
        log=self.tmp/"stderr.log"
        log.write_text("\x1b[31mERROR\x1b[0m readable\n",encoding="utf-8")
        self.assertEqual(StudioExecutionManager._tail(log),"ERROR readable")

    def test_headless_open_does_not_materialize_project_provider_config(self):
        root=Path(__file__).resolve().parents[1]
        source=(root/"src"/"dynosai_flow"/"cli.py").read_text(encoding="utf-8")
        self.assertIn("if not headless:",source)
        self.assertIn("engine.agents.connect(agent)",source)
        # The connect call is nested under the non-headless guard.
        block=source[source.index("# Interactive CLI launches refresh portable provider integration"):source.index("implementation_prepare=None")]
        self.assertIn("if not headless:",block)


if __name__ == "__main__":
    unittest.main()
