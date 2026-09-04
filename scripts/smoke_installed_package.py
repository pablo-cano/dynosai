#!/usr/bin/env python3
"""Smoke-test an installed DynosAI wheel.

This is not a checkout test. It refuses a non-empty PYTHONPATH and refuses
imports that resolve to src/dynosai_flow in a source tree.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


SENTINEL_WORK = "DYN-0190"


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(2)


def _write_019_state(project: Path) -> None:
    dynosai = project / ".dynosai"
    dynosai.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(dynosai / "knowledge.db")
    try:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE work_items (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              description TEXT NOT NULL,
              work_type TEXT NOT NULL,
              state TEXT NOT NULL,
              quick_flow INTEGER NOT NULL DEFAULT 0,
              active INTEGER NOT NULL DEFAULT 1,
              branch TEXT,
              worktree TEXT,
              quality_score INTEGER NOT NULL DEFAULT 0,
              workspace_strategy TEXT NOT NULL DEFAULT 'isolated_worktree',
              provider_session_id TEXT,
              original_branch TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            INSERT INTO meta(key,value) VALUES('schema_version','6');
            INSERT INTO meta(key,value) VALUES('project_name','Legacy019');
            INSERT INTO meta(key,value) VALUES('origin_release','0.19.0');
            INSERT INTO work_items(
              id,title,description,work_type,state,quick_flow,active,branch,worktree,
              quality_score,workspace_strategy,provider_session_id,original_branch,created_at,updated_at
            ) VALUES(
              'DYN-0190','Preserve me','upgrade sentinel','feature','inbox',0,1,NULL,NULL,
              0,'interactive_branch',NULL,NULL,'2026-08-31T00:00:00Z','2026-08-31T00:00:00Z'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _assert_installed_import() -> None:
    if os.environ.get("PYTHONPATH", "").strip():
        fail("installed-package smoke refuses a non-empty PYTHONPATH (checkout contamination)")
    import dynosai_flow

    location = Path(dynosai_flow.__file__).resolve().as_posix()
    if "/src/dynosai_flow/" in location or location.endswith("/src/dynosai_flow/__init__.py"):
        fail("refusing checkout src import; this is not an installed-package test")
    print(f"import dynosai_flow -> {location}")


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    return subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    cwd = Path.cwd().resolve()
    print(f"smoke cwd={cwd}")
    _assert_installed_import()

    from importlib import resources

    from dynosai_flow.db import Database
    from dynosai_flow.version import DISPLAY_VERSION, __version__

    print(f"version {__version__} display {DISPLAY_VERSION}")
    html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
    if 'id="doctor"' not in html:
        fail("packaged Studio assets missing doctor surface")

    exe = [sys.executable, "-m", "dynosai_flow.cli"]
    version = _run(exe + ["--version"], cwd=cwd)
    if version.returncode != 0 or __version__ not in (version.stdout + version.stderr):
        fail(f"dynosai --version failed: {version.stdout}{version.stderr}")
    help_text = _run(exe + ["--help"], cwd=cwd)
    if help_text.returncode != 0 or "doctor" not in help_text.stdout.lower():
        fail(f"dynosai --help failed: {help_text.stdout}{help_text.stderr}")
    doctor = _run(exe + ["doctor"], cwd=cwd)
    if doctor.returncode != 0:
        fail(f"dynosai doctor failed: {doctor.stdout}{doctor.stderr}")
    payload = json.loads(doctor.stdout)
    if "checks" not in payload:
        fail("dynosai doctor did not return checks")

    project = Path(tempfile.mkdtemp(prefix="dynosai-upgrade-019-"))
    _write_019_state(project)
    db = Database(project)
    db.initialize()
    if db.get_meta("schema_version") != "6":
        fail("upgrade wiped or changed schema v6")
    if db.get_meta("origin_release") != "0.19.0":
        fail("upgrade wiped 0.19.0 origin marker")
    row = db.one("SELECT id,title FROM work_items WHERE id=?", (SENTINEL_WORK,))
    if not row or row["title"] != "Preserve me":
        fail("upgrade wiped representative 0.19.0 work")

    port = _free_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    proc = subprocess.Popen(
        exe + ["app-server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        health = None
        for _ in range(40):
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                fail(f"app-server exited early: {out}")
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    health = json.loads(response.read().decode("utf-8"))
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.25)
        if not health or not health.get("ok"):
            fail("loopback App Server health check failed")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=3) as response:
            page = response.read().decode("utf-8")
        if 'id="doctor"' not in page:
            fail("loopback Studio index is missing doctor")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/doctor", timeout=10) as response:
            doctor_api = json.loads(response.read().decode("utf-8"))
        if "checks" not in doctor_api:
            fail("loopback /api/doctor missing checks")
        print(f"app-server health ok on 127.0.0.1:{port}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    print("installed-package smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
