import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path

from dynosai_flow.acceptance import CursorAcceptanceDriver
from dynosai_flow.acceptance_logging import AcceptanceLogger, acceptance_status, bundle_acceptance_logs
from dynosai_flow.cli import parser
from dynosai_flow.version import __version__


class AcceptanceTelemetry092Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_version_is_092(self):
        self.assertEqual(__version__, "1.0.0rc8")

    def test_cli_exposes_persistent_acceptance_telemetry(self):
        args = parser().parse_args([
            "debug", "acceptance", "--providers", "cursor", "--scenario", "fibonacci",
            "--log-dir", str(self.tmp / "logs"), "--heartbeat", "3",
        ])
        self.assertEqual(args.log_dir, str(self.tmp / "logs"))
        self.assertEqual(args.heartbeat, 3)
        status = parser().parse_args(["debug", "acceptance-status", "--log-dir", str(self.tmp / "logs")])
        self.assertEqual(status.debug_command, "acceptance-status")
        bundle = parser().parse_args(["debug", "acceptance-logs", "--log-dir", str(self.tmp / "logs"), "--output", str(self.tmp / "x.zip")])
        self.assertEqual(bundle.debug_command, "acceptance-logs")

    def test_status_reports_last_mcp_activity_and_log_bundle_is_shareable(self):
        root = self.tmp / "persistent"
        workspace = self.tmp / "workspace"
        raw = workspace / "cursor" / "fibonacci" / "logs"
        raw.mkdir(parents=True)
        logger = AcceptanceLogger(root, run_id="ACCEPTANCE-test", console=False, scope="suite")
        logger.initialize(workspace=str(workspace), version="0.9.2")
        logger.emit("case_worker_start", phase="case", provider="cursor", scenario="fibonacci", message="running")
        case = root / "cases" / "cursor" / "fibonacci"
        case_logger = AcceptanceLogger(case, run_id="ACCEPTANCE-test", console=False, scope="case")
        case_logger.initialize(provider="cursor", scenario="fibonacci", raw_logs=str(raw), workspace=str(workspace / "cursor" / "fibonacci" / "project"))
        case_logger.emit("provider_run_start", phase="provider", provider="cursor", scenario="fibonacci", message="provider running")
        (raw / "mcp-activity.jsonl").write_text(json.dumps({"event":"mcp_tool_start","tool":"dynosai_get_next_action","work_id":"DYN-1"})+"\n", encoding="utf-8")
        (raw / "provider-stream.jsonl").write_text('{"type":"system","subtype":"init"}\n', encoding="utf-8")
        status = acceptance_status(root)
        self.assertEqual(status["active_case"]["last_mcp_activity"]["tool"], "dynosai_get_next_action")
        self.assertTrue(status["active_case"]["provider_stream"]["exists"])
        out = self.tmp / "share.zip"
        result = bundle_acceptance_logs(root, out)
        self.assertEqual(result["share_this_file"], str(out.resolve()))
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        self.assertIn("telemetry/acceptance.log", names)
        self.assertIn("workspace-evidence/cursor/fibonacci/logs/mcp-activity.jsonl", names)
        self.assertIn("workspace-evidence/cursor/fibonacci/logs/provider-stream.jsonl", names)

    def test_cursor_stream_is_persisted_while_provider_is_still_running(self):
        fake = self.tmp / "cursor-agent"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json,time\n"
            "print(json.dumps({'type':'system','subtype':'init','model':'fake-model'}), flush=True)\n"
            "time.sleep(1.5)\n"
            "print(json.dumps({'type':'result','subtype':'completed'}), flush=True)\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        project = self.tmp / "project"; project.mkdir()
        raw = self.tmp / "raw"
        persistent = self.tmp / "persistent-cursor"
        logger = AcceptanceLogger(persistent, run_id="stream-test", console=False, scope="case")
        logger.initialize(provider="cursor")
        box = {}
        def run():
            box["result"] = CursorAcceptanceDriver(str(fake), 10).run(project, "prompt", raw, interaction_mode="auto", logger=logger)
        t = threading.Thread(target=run)
        t.start()
        mirror = persistent / "provider-stream.jsonl"
        deadline = time.time() + 8.0
        while time.time() < deadline and (not mirror.exists() or "init" not in mirror.read_text(encoding="utf-8")):
            time.sleep(0.05)
        self.assertTrue(t.is_alive(), "provider should still be sleeping when first stream line is persisted")
        self.assertTrue(mirror.exists())
        self.assertIn("fake-model", mirror.read_text(encoding="utf-8"))
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(box["result"]["exit_code"], 0)

    def test_mcp_tool_activity_trace_records_start_and_end(self):
        project = self.tmp / "mcp-project"; project.mkdir()
        trace = self.tmp / "mcp-activity.jsonl"
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        env["DYNOSAI_AGENT_PROVIDER"] = "cursor"
        env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE"] = str(trace)
        proc = subprocess.Popen(
            [sys.executable, "-m", "dynosai_flow.mcp", "--project", str(project)],
            cwd=project, env=env, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"test","version":"1"},"capabilities":{}}})+"\n")
        proc.stdin.flush(); json.loads(proc.stdout.readline())
        proc.stdin.write(json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_project","arguments":{"action":"detect","root":str(project)}}})+"\n")
        proc.stdin.flush(); json.loads(proc.stdout.readline())
        proc.terminate(); proc.communicate(timeout=5)
        events = [json.loads(x) for x in trace.read_text(encoding="utf-8").splitlines()]
        starts = [x for x in events if x.get("event") == "mcp_tool_start"]
        ends = [x for x in events if x.get("event") == "mcp_tool_end"]
        self.assertEqual(starts[-1]["tool"], "dynosai_project")
        self.assertEqual(ends[-1]["tool"], "dynosai_project")
        self.assertEqual(ends[-1]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
