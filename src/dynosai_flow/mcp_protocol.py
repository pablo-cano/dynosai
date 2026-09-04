# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Shared MCP protocol-version constants and per-request metadata helpers.

DynosAI speaks three protocol revisions on stdio. This module does not own
tool dispatch, elicitation, or workflow authority.
"""

from __future__ import annotations

from typing import Any

from .version import __version__

PROTOCOL_2026 = "2026-07-28"
PROTOCOL_2025_11 = "2025-11-25"
PROTOCOL_2025_06 = "2025-06-18"
SUPPORTED_PROTOCOLS = (PROTOCOL_2026, PROTOCOL_2025_11, PROTOCOL_2025_06)
CURRENT_PROTOCOL = PROTOCOL_2026
LEGACY_PROTOCOLS = (PROTOCOL_2025_11, PROTOCOL_2025_06)

META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

UNSUPPORTED_PROTOCOL_CODE = -32022
NOT_INITIALIZED_CODE = -32002
METHOD_NOT_FOUND_CODE = -32601

MCP_SERVER_NAME = "dynosai-flow"


def server_info() -> dict[str, str]:
    return {"name": MCP_SERVER_NAME, "version": __version__}


def is_legacy_protocol(version: str | None) -> bool:
    return str(version or "") in LEGACY_PROTOCOLS


def is_stateless_protocol(version: str | None) -> bool:
    return str(version or "") == PROTOCOL_2026


def extract_params_meta(params: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    meta = params.get("_meta")
    return dict(meta) if isinstance(meta, dict) else {}


def protocol_from_meta(meta: dict[str, Any]) -> str | None:
    value = meta.get(META_PROTOCOL_VERSION)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_protocol_version(params: dict[str, Any] | None, negotiated: str | None) -> str | None:
    """Prefer per-request `_meta` protocol version, then the session negotiation."""
    meta = extract_params_meta(params)
    requested = protocol_from_meta(meta)
    if requested:
        return requested
    if negotiated:
        return str(negotiated)
    return None


def client_identity_from_params(params: dict[str, Any] | None) -> dict[str, Any]:
    raw = params if isinstance(params, dict) else {}
    meta = extract_params_meta(raw)
    info = meta.get(META_CLIENT_INFO) if isinstance(meta.get(META_CLIENT_INFO), dict) else raw.get("clientInfo")
    caps = meta.get(META_CLIENT_CAPABILITIES) if isinstance(meta.get(META_CLIENT_CAPABILITIES), dict) else raw.get("capabilities")
    protocol = protocol_from_meta(meta) or (str(raw.get("protocolVersion")).strip() if raw.get("protocolVersion") else None)
    return {
        "clientInfo": dict(info) if isinstance(info, dict) else {},
        "capabilities": dict(caps) if isinstance(caps, dict) else {},
        "protocolVersion": protocol or "",
    }


def result_meta(*, protocol: str) -> dict[str, Any]:
    return {
        META_PROTOCOL_VERSION: protocol,
        META_SERVER_INFO: server_info(),
    }


def unsupported_protocol_error(requested: str, supported: tuple[str, ...] = SUPPORTED_PROTOCOLS) -> tuple[int, str, dict[str, Any]]:
    return (
        UNSUPPORTED_PROTOCOL_CODE,
        "Unsupported protocol version",
        {"supported": list(supported), "requested": requested},
    )


def advertised_server_capabilities(*, list_changed: bool = True) -> dict[str, Any]:
    """Advertise only capabilities DynosAI actually implements."""
    tools: dict[str, Any] = {"listChanged": True} if list_changed else {}
    return {"tools": tools}


def sort_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tools, key=lambda item: str(item.get("name") or ""))
