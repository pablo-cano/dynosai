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
INVALID_PARAMS_CODE = -32602

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
    """Legacy helper: per-request `_meta` first, then the 2025 session negotiation.

    MCP 2026 must not use this fallback. Stateless requests validate `_meta` on
    the current request via `validate_stateless_request_meta`.
    """
    meta = extract_params_meta(params)
    requested = protocol_from_meta(meta)
    if requested:
        return requested
    if negotiated and is_legacy_protocol(negotiated):
        return str(negotiated)
    return None


def invalid_params_error(reason: str, extra: dict[str, Any] | None = None) -> tuple[int, str, dict[str, Any]]:
    data: dict[str, Any] = {"reason": reason}
    if extra:
        data.update(extra)
    return INVALID_PARAMS_CODE, "Invalid params", data


def validate_stateless_request_meta(params: dict[str, Any] | None) -> tuple[int, str, dict[str, Any]] | None:
    """Validate MCP 2026-07-28 per-request `_meta` without inheriting prior requests.

    Every stateless request MUST include protocolVersion and clientCapabilities.
    clientInfo is optional. An unsupported protocolVersion is `-32022`.
    """
    if not isinstance(params, dict):
        return invalid_params_error("params_must_be_object")
    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return invalid_params_error(
            "missing_meta",
            {"required": [META_PROTOCOL_VERSION, META_CLIENT_CAPABILITIES]},
        )
    if META_PROTOCOL_VERSION not in meta:
        return invalid_params_error("missing_protocol_version", {"required": META_PROTOCOL_VERSION})
    requested = protocol_from_meta(meta)
    if not requested:
        return invalid_params_error("missing_protocol_version", {"required": META_PROTOCOL_VERSION})
    if requested not in SUPPORTED_PROTOCOLS:
        return unsupported_protocol_error(requested)
    if not is_stateless_protocol(requested):
        return invalid_params_error(
            "stateless_protocol_required",
            {"requested": requested, "required": PROTOCOL_2026},
        )
    if META_CLIENT_CAPABILITIES not in meta:
        return invalid_params_error("missing_client_capabilities", {"required": META_CLIENT_CAPABILITIES})
    caps = meta.get(META_CLIENT_CAPABILITIES)
    if not isinstance(caps, dict):
        return invalid_params_error("client_capabilities_must_be_object")
    info = meta.get(META_CLIENT_INFO)
    if info is not None and not isinstance(info, dict):
        return invalid_params_error("client_info_must_be_object")
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
