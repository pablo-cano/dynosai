# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Deterministic MCP authority prefix for cacheable structure, not cache-hit claims."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .version import DISPLAY_VERSION, __version__

from .mcp_protocol import CURRENT_PROTOCOL

PREFIX_SCHEMA = "PROMPT_PREFIX_1.0"
PREFIX_FILE = Path(".dynosai") / "runtime" / "prompt-prefix.json"
DEFAULT_PROTOCOL = CURRENT_PROTOCOL


def build_authority_prefix(*, schema_version: int = 6, mcp_tool_count: int = 31, protocol: str = DEFAULT_PROTOCOL) -> dict[str, Any]:
    """Stable identity prefix. Tool names stay in MCP tools/list, not in this text."""
    identity = {
        "schema": PREFIX_SCHEMA,
        "package_version": __version__,
        "display_version": DISPLAY_VERSION,
        "schema_version": int(schema_version),
        "mcp_protocol": protocol,
        "mcp_tool_count": int(mcp_tool_count),
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    text = (
        f"DynosAI authority prefix {PREFIX_SCHEMA}. "
        f"Package {__version__} ({DISPLAY_VERSION}). Schema v{schema_version}. "
        f"MCP protocol {protocol}. Unique MCP names: {mcp_tool_count}. "
        "Discover tools via MCP tools/list. "
        "This hash describes cacheable structure, not provider cache telemetry."
    )
    return {
        **identity,
        "hash": digest,
        "text": text,
        "tools_listed": False,
        "claims_cache_hit": False,
    }


def persist_prefix(root: str | Path, prefix: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(prefix or build_authority_prefix())
    payload["claims_cache_hit"] = False
    path = Path(root).expanduser().resolve() / PREFIX_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "hash": payload["hash"], **payload}


def load_prefix(root: str | Path) -> dict[str, Any] | None:
    path = Path(root).expanduser().resolve() / PREFIX_FILE
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_or_create_prefix(root: str | Path) -> dict[str, Any]:
    existing = load_prefix(root)
    if existing and existing.get("hash"):
        existing.setdefault("claims_cache_hit", False)
        existing.setdefault("schema", PREFIX_SCHEMA)
        return existing
    return persist_prefix(root)


def compose_prompt(prefix: dict[str, Any], suffix: str) -> dict[str, Any]:
    prefix_text = str(prefix.get("text") or "")
    suffix_text = str(suffix or "")
    suffix_hash = hashlib.sha256(suffix_text.encode("utf-8")).hexdigest()
    return {
        "prompt": prefix_text + "\n\n" + suffix_text,
        "prefix": prefix_text,
        "suffix": suffix_text,
        "prefix_hash": prefix.get("hash"),
        "suffix_hash": suffix_hash,
        "claims_cache_hit": False,
    }


def summarize_prefix(root: str | Path) -> dict[str, Any]:
    payload = load_or_create_prefix(root)
    return {
        "schema": payload.get("schema") or PREFIX_SCHEMA,
        "hash": payload.get("hash"),
        "claims_cache_hit": False,
        "tools_listed": False,
    }
