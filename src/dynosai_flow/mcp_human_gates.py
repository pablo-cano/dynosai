# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Shared human-gate transport keys for legacy elicitation and MCP 2026 MRTR.

Application authority stays in `knowledge.db`. This module only names the
stable input-request key and shapes elicitation params. It does not trust
client `inputResponses` or `requestState`.
"""

from __future__ import annotations

from typing import Any

GATE_KEY_PREFIX = "dynosai-gate-"


def input_request_key(interaction_id: str) -> str:
    return f"{GATE_KEY_PREFIX}{interaction_id}"


def parse_input_request_key(key: str) -> str | None:
    text = str(key or "")
    if not text.startswith(GATE_KEY_PREFIX):
        return None
    iid = text[len(GATE_KEY_PREFIX):].strip()
    return iid or None


def elicitation_create_params(interaction: dict[str, Any]) -> dict[str, Any]:
    schema = interaction.get("requested_schema") or interaction.get("requestedSchema") or {}
    return {
        "mode": "form",
        "message": str(interaction.get("message") or "Human decision required."),
        "requestedSchema": schema if isinstance(schema, dict) else {},
    }


def normalize_elicitation_action(payload: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    body = payload if isinstance(payload, dict) else {}
    action = str(body.get("action") or "cancel").lower()
    if action not in {"accept", "decline", "cancel"}:
        action = "cancel"
    content = body.get("content") if isinstance(body.get("content"), dict) else {}
    return action, dict(content)
