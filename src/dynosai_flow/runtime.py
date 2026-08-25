# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Machine-local runtime broker for projects, provider sessions, and shared runtime state."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .util import utc_now

RUNTIME_SCHEMA = 1
CURSOR_CONFIG_VERSION = 1


def user_home() -> Path:
    override = os.environ.get("DYNOSAI_USER_HOME")
    return Path(override).expanduser().resolve() if override else Path.home().resolve()


def runtime_dir() -> Path:
    override = os.environ.get("DYNOSAI_RUNTIME_HOME")
    base = Path(override).expanduser().resolve() if override else user_home() / ".dynosai" / "runtime"
    base.mkdir(parents=True, exist_ok=True)
    return base


class RuntimeBroker:
    """Machine-local broker for projects and managed agent sessions.

    It contains no source code and no reusable bearer token. The authoritative
    workflow state remains in each project's knowledge.db. This registry only
    maps a local agent/worktree to the correct project/session.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or runtime_dir() / "runtime.db").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runtime_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS projects(
                    root TEXT PRIMARY KEY, name TEXT, db_path TEXT NOT NULL,
                    registered_at TEXT NOT NULL, last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions(
                    session_id TEXT PRIMARY KEY, provider TEXT NOT NULL,
                    project_root TEXT NOT NULL, worktree TEXT NOT NULL,
                    work_id TEXT, run_id TEXT, state TEXT NOT NULL,
                    expires_at TEXT NOT NULL, launcher_pid INTEGER,
                    created_at TEXT NOT NULL, last_seen TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_runtime_sessions_provider_state
                  ON sessions(provider,state,expires_at);
                CREATE TABLE IF NOT EXISTS connections(
                    provider TEXT PRIMARY KEY, config_version INTEGER NOT NULL,
                    global_mcp INTEGER NOT NULL DEFAULT 0,
                    connected_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL,
                    provider TEXT, project_root TEXT, session_id TEXT,
                    details TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                );
                """
            )
            con.execute("INSERT OR REPLACE INTO runtime_meta(key,value) VALUES('schema_version',?)", (str(RUNTIME_SCHEMA),))

    def register_project(self, root: str | Path, name: str = "") -> None:
        root = Path(root).resolve(); now = utc_now(); db = root / ".dynosai" / "knowledge.db"
        with self.connect() as con:
            con.execute(
                "INSERT INTO projects(root,name,db_path,registered_at,last_seen) VALUES(?,?,?,?,?) "
                "ON CONFLICT(root) DO UPDATE SET name=excluded.name,db_path=excluded.db_path,last_seen=excluded.last_seen",
                (str(root), name, str(db), now, now),
            )

    def mark_connection(self, provider: str, global_mcp: bool) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute(
                "INSERT INTO connections(provider,config_version,global_mcp,connected_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(provider) DO UPDATE SET config_version=excluded.config_version,global_mcp=excluded.global_mcp,updated_at=excluded.updated_at",
                (provider, CURSOR_CONFIG_VERSION, int(global_mcp), now, now),
            )

    def remove_connection(self, provider: str) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM connections WHERE provider=?", (provider,))

    def register_session(self, session: dict[str, Any], project_root: str | Path, worktree: str | Path, provider: str, work_id: str | None, run_id: str | None, expires_at: str, launcher_pid: int | None = None) -> None:
        now = utc_now(); root = Path(project_root).resolve(); wt = Path(worktree).resolve()
        self.register_project(root)
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO sessions(session_id,provider,project_root,worktree,work_id,run_id,state,expires_at,launcher_pid,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (session["session_id"], provider, str(root), str(wt), work_id, run_id, "active", expires_at, launcher_pid, now, now),
            )
            con.execute("INSERT INTO events(event,provider,project_root,session_id,details,created_at) VALUES(?,?,?,?,?,?)",
                        ("session_registered", provider, str(root), session["session_id"], json.dumps({"worktree": str(wt), "work_id": work_id, "run_id": run_id}), now))

    def close_session(self, session_id: str) -> None:
        now = utc_now()
        with self.connect() as con:
            con.execute("UPDATE sessions SET state='closed',last_seen=? WHERE session_id=?", (now, session_id))

    def _live(self, provider: str) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        with self.connect() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM sessions WHERE provider=? AND state='active' ORDER BY created_at DESC", (provider,))]
            out=[]
            for row in rows:
                try: exp=datetime.fromisoformat(row["expires_at"])
                except Exception: exp=now
                if exp <= now:
                    con.execute("UPDATE sessions SET state='expired',last_seen=? WHERE session_id=?", (utc_now(), row["session_id"]))
                else: out.append(row)
            return out

    def resolve(self, provider: str, cwd: str | Path | None = None) -> dict[str, Any] | None:
        live=self._live(provider)
        if not live: return None
        hint=Path(cwd or Path.cwd()).resolve()
        exact=[r for r in live if Path(r["worktree"]).resolve()==hint]
        if len(exact)==1: chosen=exact[0]
        else:
            containing=[r for r in live if hint == Path(r["project_root"]).resolve() or hint.is_relative_to(Path(r["project_root"]).resolve())]
            if len(containing)==1: chosen=containing[0]
            elif len(live)==1: chosen=live[0]
            else: return None
        with self.connect() as con:
            con.execute("UPDATE sessions SET last_seen=? WHERE session_id=?", (utc_now(), chosen["session_id"]))
        return chosen

    def project_for_path(self, cwd: str | Path | None = None, provider: str | None = None) -> Path | None:
        hint=Path(cwd or Path.cwd()).resolve()
        if provider:
            sess=self.resolve(provider,hint)
            if sess: return Path(sess["project_root"]).resolve()
        with self.connect() as con:
            rows=[dict(r) for r in con.execute("SELECT * FROM projects ORDER BY length(root) DESC")]
        matches=[]
        for row in rows:
            root=Path(row["root"]).resolve()
            if hint==root or hint.is_relative_to(root): matches.append(root)
        return matches[0] if matches else None

    def status(self) -> dict[str, Any]:
        with self.connect() as con:
            return {
                "path": str(self.path),
                "schema_version": int((con.execute("SELECT value FROM runtime_meta WHERE key='schema_version'").fetchone() or [0])[0]),
                "projects": [dict(r) for r in con.execute("SELECT * FROM projects ORDER BY last_seen DESC LIMIT 20")],
                "active_sessions": [dict(r) for r in con.execute("SELECT * FROM sessions WHERE state='active' ORDER BY created_at DESC")],
                "connections": [dict(r) for r in con.execute("SELECT * FROM connections ORDER BY provider")],
            }
