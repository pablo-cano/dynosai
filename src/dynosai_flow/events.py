# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Typed workflow event and audit-event primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import Database
from .util import json_dumps, json_loads, utc_now


@dataclass(slots=True)
class ApplicationEvent:
    id: int
    event_type: str
    project_id: str | None
    work_id: str | None
    session_id: str | None
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "event_type": self.event_type, "project_id": self.project_id,
            "work_id": self.work_id, "session_id": self.session_id,
            "payload": self.payload, "created_at": self.created_at,
        }


class ApplicationEventBus:
    """Persistent provider-neutral event stream for MCP/CLI/future GUI adapters."""
    def __init__(self, db: Database):
        self.db = db

    def emit(self, event_type: str, *, work_id: str | None = None, session_id: str | None = None,
             payload: dict[str, Any] | None = None) -> dict[str, Any]:
        created = utc_now()
        project_id = self.db.get_meta("project_name", None)
        with self.db.transaction(immediate=True) as con:
            cur = con.execute(
                "INSERT INTO application_events(event_type,project_id,work_id,session_id,payload,created_at) VALUES(?,?,?,?,?,?)",
                (event_type, project_id, work_id, session_id, json_dumps(payload or {}), created),
            )
            event_id = int(cur.lastrowid)
        event = ApplicationEvent(event_id, event_type, project_id, work_id, session_id, payload or {}, created)
        return event.to_dict()

    def list(self, *, after_id: int = 0, work_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if work_id:
            rows = self.db.query(
                "SELECT * FROM application_events WHERE id>? AND work_id=? ORDER BY id LIMIT ?",
                (after_id, work_id, limit),
            )
        else:
            rows = self.db.query("SELECT * FROM application_events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit))
        return [ApplicationEvent(
            int(r["id"]), r["event_type"], r.get("project_id"), r.get("work_id"), r.get("session_id"),
            json_loads(r.get("payload"), {}), r["created_at"]
        ).to_dict() for r in rows]
