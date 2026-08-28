import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.cli import _run_cursor_acp_turn
from dynosai_flow.managed_runtime import ManagedProviderRuntime


ACP_HOLDING_AGENT = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path

config = Path(os.environ["CURSOR_CONFIG_DIR"])
session_dir = config / "acp-sessions" / "held-session"
session_dir.mkdir(parents=True, exist_ok=True)
held = session_dir / "session.json"
held.write_text('{"id":"held-session"}', encoding="utf-8")
child = subprocess.Popen(
    [sys.executable, "-c", "import sys,time; from pathlib import Path; p=sys.argv[1]; f=open(p,'rb'); Path(p+'.ready').write_text('1'); time.sleep(120)", str(held)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
ready = Path(str(held) + ".ready")
for _ in range(200):
    if ready.exists():
        break
    time.sleep(0.05)
(config / "holder.pid").write_text(str(child.pid), encoding="utf-8")
(config / "agent.pid").write_text(str(os.getpid()), encoding="utf-8")
prompt_id = None

def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()

for raw in sys.stdin:
    msg = json.loads(raw)
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1}})
    elif method == "authenticate":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/new":
        send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "sess-held"}})
    elif method == "session/prompt":
        prompt_id = rid
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "sess-held", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "working"}}}})
        send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})
    elif method == "session/cancel":
        (config / "cancel.seen").write_text("1", encoding="utf-8")
    elif method == "session/close":
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
        (config / "close.seen").write_text("1", encoding="utf-8")

(config / "stdin-eof").write_text("1", encoding="utf-8")
'''


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_until(predicate, timeout: float = 8.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class DynosAI141WindowsAcpLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-153-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = self.tmp / "project"
        self.project.mkdir()
        AgentConfiguration(self.project).init()

    def _hold_file(self, path: Path) -> subprocess.Popen:
        ready = Path(str(path) + ".ready")
        proc = subprocess.Popen(
            [
                sys.executable, "-c",
                "import sys,time; from pathlib import Path; p=sys.argv[1]; f=open(p,'rb'); Path(p+'.ready').write_text('1'); time.sleep(60)",
                str(path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        self.assertTrue(_wait_until(lambda: proc.poll() is None and ready.exists()), f"holder failed to lock {path}")
        return proc

    def test_prepare_does_not_crash_when_acp_session_file_is_locked(self):
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="discovery")
        cfg = Path(runtime.env["CURSOR_CONFIG_DIR"])
        locked = cfg / "acp-sessions" / "sess-1" / "session.json"
        locked.parent.mkdir(parents=True)
        locked.write_text('{"id":"sess-1"}', encoding="utf-8")
        holder = self._hold_file(locked)
        self.assertIsNone(holder.poll())

        refreshed = ManagedProviderRuntime(self.project).prepare("cursor", activity="planning")
        new_cfg = Path(refreshed.env["CURSOR_CONFIG_DIR"])
        self.assertTrue((new_cfg / "mcp.json").exists())
        mcp = json.loads((new_cfg / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(set(mcp["mcpServers"]), {"dynosai"})
        self.assertEqual(refreshed.tool_profile, "design")
        self.assertTrue(holder.poll() is None, "prepare must not need to kill an unrelated locker to succeed")

    def test_prepare_prunes_stale_skills_and_unlocked_session_state(self):
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="discovery")
        cfg = Path(runtime.env["CURSOR_CONFIG_DIR"])
        stale = cfg / "skills" / "stale-skill"
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text("stale", encoding="utf-8")
        sessions = cfg / "acp-sessions" / "old"
        sessions.mkdir(parents=True)
        (sessions / "session.json").write_text("{}", encoding="utf-8")

        ManagedProviderRuntime(self.project).prepare("cursor", activity="specification")
        self.assertFalse(stale.exists())
        self.assertFalse(sessions.exists())

    def test_cursor_acp_shutdown_closes_stdin_and_kills_session_holders(self):
        cfg = self.tmp / "cursor-config"
        cfg.mkdir()
        fake = self.tmp / "agent.py"
        fake.write_text(ACP_HOLDING_AGENT, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        env = os.environ.copy()
        env["CURSOR_CONFIG_DIR"] = str(cfg)

        result = _run_cursor_acp_turn(str(fake), self.tmp, env, "continue after approval", None, max_runtime=12, idle_timeout=6)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["session_id"], "sess-held")

        holder_pid = int((cfg / "holder.pid").read_text(encoding="utf-8"))
        agent_pid = int((cfg / "agent.pid").read_text(encoding="utf-8"))
        self.addCleanup(lambda: subprocess.run(["taskkill", "/F", "/PID", str(holder_pid)], capture_output=True) if os.name == "nt" else None)
        self.assertTrue(_wait_until(lambda: not _pid_running(agent_pid)), f"ACP parent still running: {agent_pid}")
        self.assertTrue((cfg / "stdin-eof").exists(), "ACP shutdown must close stdin so the agent can exit")
        self.assertTrue(_wait_until(lambda: not _pid_running(holder_pid)), f"ACP child still holding session files: {holder_pid}")

        held = cfg / "acp-sessions" / "held-session" / "session.json"
        self.assertTrue(_wait_until(lambda: not held.exists() or os.access(str(held), os.W_OK)))
        # A later workflow turn must be able to rebuild the managed runtime.
        later = ManagedProviderRuntime(self.project).prepare("cursor", activity="planning")
        self.assertTrue((Path(later.env["CURSOR_CONFIG_DIR"]) / "mcp.json").exists())

    def test_resume_after_approval_rebuilds_cursor_runtime_once_acp_tree_exits(self):
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="specification")
        cfg = Path(runtime.env["CURSOR_CONFIG_DIR"])
        fake = self.tmp / "agent.py"
        fake.write_text(ACP_HOLDING_AGENT, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        env = os.environ.copy()
        env["CURSOR_CONFIG_DIR"] = str(cfg)

        result = _run_cursor_acp_turn(str(fake), self.project, env, "plan Fibonacci", None, max_runtime=12, idle_timeout=6)
        self.assertEqual(result["exit_code"], 0, result)
        holder_pid = int((cfg / "holder.pid").read_text(encoding="utf-8"))
        self.addCleanup(lambda: subprocess.run(["taskkill", "/F", "/PID", str(holder_pid)], capture_output=True) if os.name == "nt" else None)
        self.assertTrue(_wait_until(lambda: not _pid_running(holder_pid)), f"ACP child still holding session files: {holder_pid}")

        resumed = ManagedProviderRuntime(self.project).prepare("cursor", activity="planning")
        resumed_cfg = Path(resumed.env["CURSOR_CONFIG_DIR"])
        self.assertTrue((resumed_cfg / "mcp.json").exists())
        self.assertFalse((resumed_cfg / "acp-sessions" / "held-session" / "session.json").exists())


if __name__ == "__main__":
    unittest.main()
