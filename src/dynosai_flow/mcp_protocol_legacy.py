# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""MCP 2025-11-25 / 2025-06-18 stateful initialize handshake shaping.

Cursor ACP and Codex app-server currently speak this era. Keep initialize,
initialized, tools/list without result envelopes, and existing error codes.
"""

from __future__ import annotations

from typing import Any

from .mcp_protocol import advertised_server_capabilities, server_info


def initialize_result(*, protocol: str, instructions: str) -> dict[str, Any]:
    return {
        "protocolVersion": protocol,
        "capabilities": advertised_server_capabilities(list_changed=True),
        "serverInfo": server_info(),
        "instructions": instructions,
    }


def tools_list_result(tools: list[dict[str, Any]], surface: dict[str, Any]) -> dict[str, Any]:
    return {"tools": list(tools), "dynosaiToolSurface": surface}


def wrap_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(payload)
