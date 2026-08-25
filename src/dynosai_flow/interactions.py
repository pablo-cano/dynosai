# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Human-gate and elicitation lifecycle primitives."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

from .db import Database
from .util import json_dumps, json_loads, utc_now


@dataclass(slots=True)
class HumanInteractionRequest:
    id: str
    work_id: str | None
    session_id: str | None
    kind: str
    gate: str | None
    message: str
    requested_schema: dict[str, Any]
    status: str
    provider: str | None = None
    response: dict[str, Any] | None = None
    dedupe_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "work_id": self.work_id,
            "session_id": self.session_id,
            "kind": self.kind,
            "gate": self.gate,
            "message": self.message,
            "requested_schema": self.requested_schema,
            "status": self.status,
            "provider": self.provider,
            "response": self.response,
            "dedupe_key": self.dedupe_key,
        }


class HumanInteractionService:
    """Provider-neutral human-in-the-loop requests.

    MCP adapters translate these requests to `elicitation/create`; a future GUI can
    render the exact same request as a dialog without importing MCP concepts into
    the workflow core.
    """

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _decision_schema(include_comments: bool = True) -> dict[str, Any]:
        props: dict[str, Any] = {
            "decision": {
                "type": "string",
                "title": "Decision",
                "enum": ["approve", "request_changes", "cancel"],
            }
        }
        if include_comments:
            props["comments"] = {"type": "string", "title": "Comments", "default": ""}
        return {"type": "object", "properties": props, "required": ["decision"]}

    def create(
        self,
        *,
        work_id: str | None,
        kind: str,
        message: str,
        requested_schema: dict[str, Any],
        gate: str | None = None,
        session_id: str | None = None,
        provider: str | None = None,
        dedupe_key: str | None = None,
    ) -> HumanInteractionRequest:
        if dedupe_key:
            row = self.db.one(
                "SELECT * FROM human_interactions WHERE dedupe_key=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                (dedupe_key,),
            )
            if row:
                return self._decode(row)
        iid = self.db.next_id("ELIC", "human_interactions")
        now = utc_now()
        try:
            self.db.execute(
                "INSERT INTO human_interactions(id,work_id,session_id,kind,gate,message,schema_json,response_json,status,provider,dedupe_key,created_at,resolved_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (iid, work_id, session_id, kind, gate, message, json_dumps(requested_schema), "{}", "pending", provider, dedupe_key, now),
            )
        except sqlite3.IntegrityError:
            # Schema v6 enforces one pending interaction per logical dedupe key.
            # A concurrent poll may win the insert race; return that request
            # instead of surfacing an MCP failure or creating a second gate.
            if dedupe_key:
                row = self.db.one(
                    "SELECT * FROM human_interactions WHERE dedupe_key=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
                    (dedupe_key,),
                )
                if row:
                    return self._decode(row)
            raise
        self.db.audit("HumanInteractionRequested", iid, {"work_id": work_id, "kind": kind, "gate": gate, "provider": provider})
        return HumanInteractionRequest(iid, work_id, session_id, kind, gate, message, requested_schema, "pending", provider, None, dedupe_key)

    def gate_request(self, work: dict[str, Any], session_id: str | None = None, provider: str | None = None) -> HumanInteractionRequest | None:
        state = str(work.get("state") or "")
        gate_map = {
            "spec_review": ("spec", "Specification ready. Review the requirements and acceptance criteria before continuing."),
            "plan_review": ("plan", "Implementation plan ready. Review scope, architecture, risks, tasks and migration strategy before continuing."),
            "code_review": ("code", "Implementation verified. Review the diff and evidence before final validation."),
            "ready_to_merge": ("merge", "Validation passed. Review the final evidence before merging the feature."),
        }
        if state not in gate_map:
            return None
        gate, message = gate_map[state]
        # `plan_review` is also the state in which the agent is asked to author
        # the first plan. Do not interrupt that action with a human approval until
        # a plan artifact actually exists. The same defensive rule applies to spec.
        artifact_kind = "spec" if gate == "spec" else "plan" if gate == "plan" else None
        if artifact_kind:
            artifact = self.db.one(
                "SELECT id,status FROM artifacts WHERE work_id=? AND kind=? ORDER BY version DESC LIMIT 1",
                (work["id"], artifact_kind),
            )
            if not artifact:
                return None
        return self.create(
            work_id=work["id"], kind="gate_approval", gate=gate, message=message,
            requested_schema=self._decision_schema(), session_id=session_id, provider=provider,
            dedupe_key=f"gate:{work['id']}:{gate}",
        )

    def clarification(self, work_id: str, question: str, *, session_id: str | None = None, provider: str | None = None) -> HumanInteractionRequest:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string", "title": "Answer"}},
            "required": ["answer"],
        }
        return self.create(
            work_id=work_id, kind="clarification", message=question, requested_schema=schema,
            session_id=session_id, provider=provider,
        )

    def scope_request(self, scope: dict[str, Any], *, session_id: str | None = None, provider: str | None = None) -> HumanInteractionRequest:
        message = f"The agent requests {scope.get('action')} access to {scope.get('path')}. Reason: {scope.get('reason')}"
        return self.create(
            work_id=scope.get("work_id"), kind="scope_extension", gate="scope", message=message,
            requested_schema=self._decision_schema(), session_id=session_id, provider=provider,
            dedupe_key=f"scope:{scope.get('id')}",
        )

    def get(self, interaction_id: str) -> HumanInteractionRequest:
        row = self.db.one("SELECT * FROM human_interactions WHERE id=?", (interaction_id,))
        if not row:
            raise KeyError(interaction_id)
        return self._decode(row)

    def pending_for_work(self, work_id: str) -> list[HumanInteractionRequest]:
        return [self._decode(row) for row in self.db.query(
            "SELECT * FROM human_interactions WHERE work_id=? AND status='pending' ORDER BY created_at", (work_id,)
        )]

    def resolve(self, interaction_id: str, action: str, content: dict[str, Any] | None) -> HumanInteractionRequest:
        row = self.db.one("SELECT * FROM human_interactions WHERE id=? AND status='pending'", (interaction_id,))
        if not row:
            existing = self.db.one("SELECT * FROM human_interactions WHERE id=?", (interaction_id,))
            if existing:
                return self._decode(existing)
            raise KeyError(interaction_id)
        normalized = str(action or "").lower()
        if normalized not in {"accept", "decline", "cancel"}:
            raise ValueError("elicitation action must be accept, decline or cancel")
        status = "accepted" if normalized == "accept" else "declined" if normalized == "decline" else "cancelled"
        response = content or {}
        self.db.execute(
            "UPDATE human_interactions SET status=?,response_json=?,resolved_at=? WHERE id=?",
            (status, json_dumps(response), utc_now(), interaction_id),
        )
        self.db.audit("HumanInteractionResolved", interaction_id, {"status": status, "response": response})
        return self.get(interaction_id)

    @staticmethod
    def _decode(row: dict[str, Any]) -> HumanInteractionRequest:
        return HumanInteractionRequest(
            id=row["id"], work_id=row.get("work_id"), session_id=row.get("session_id"), kind=row["kind"],
            gate=row.get("gate"), message=row["message"], requested_schema=json_loads(row.get("schema_json"), {}),
            status=row["status"], provider=row.get("provider"), response=json_loads(row.get("response_json"), {}) or None,
            dedupe_key=row.get("dedupe_key"),
        )
