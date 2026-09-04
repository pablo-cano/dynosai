# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""MCP 2026-07-28 stateless request shaping.

This revision removes the initialize handshake. DynosAI implements
server/discover, per-request `_meta` protocol version, deterministic tools/list
ordering, list cache metadata, and resultType on list/call. Tasks, Apps, and
Streamable HTTP are not implemented.
"""

from __future__ import annotations

from typing import Any

from .mcp_protocol import (
    PROTOCOL_2026,
    SUPPORTED_PROTOCOLS,
    advertised_server_capabilities,
    result_meta,
    server_info,
    sort_tools,
)


DISCOVER_TTL_MS = 3_600_000
LIST_TTL_MS = 0


def discover_result(*, instructions: str) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_PROTOCOLS),
        "capabilities": advertised_server_capabilities(list_changed=True),
        "instructions": instructions,
        "ttlMs": DISCOVER_TTL_MS,
        "cacheScope": "public",
        "_meta": {**result_meta(protocol=PROTOCOL_2026), "io.modelcontextprotocol/serverInfo": server_info()},
    }


def tools_list_result(tools: list[dict[str, Any]], surface: dict[str, Any]) -> dict[str, Any]:
    ordered = sort_tools(list(tools))
    return {
        "resultType": "complete",
        "tools": ordered,
        "ttlMs": LIST_TTL_MS,
        "cacheScope": "private",
        "dynosaiToolSurface": surface,
        "_meta": result_meta(protocol=PROTOCOL_2026),
    }


def wrap_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out.setdefault("resultType", "complete")
    meta = dict(out.get("_meta") or {})
    meta.update(result_meta(protocol=PROTOCOL_2026))
    out["_meta"] = meta
    return out
