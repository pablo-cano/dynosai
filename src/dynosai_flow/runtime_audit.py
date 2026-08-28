# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Audit of managed-runtime isolation, provider process behavior, and external tool boundaries."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


def _relative_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file() or p.is_symlink())


def _trace_paths(logs: Path) -> tuple[Path, ...]:
    return tuple(logs / name for name in ("codex-app-server.jsonl", "provider-stream.jsonl", "provider-stderr.log"))


def _trace_text(logs: Path) -> str:
    chunks: list[str] = []
    for p in _trace_paths(logs):
        if p.exists():
            chunks.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _jsonl_records(logs: Path) -> Iterable[tuple[str, dict[str, Any]]]:
    """Yield structured trace records without interpreting arbitrary text.

    Runtime isolation decisions must be based on protocol semantics, not on a
    regex hit somewhere in a provider message. In particular Codex emits
    `remoteControl/status/changed` with a host `serverName`; that is not an MCP
    server and must never be counted as one.
    """
    for p in _trace_paths(logs):
        if not p.exists() or p.suffix != ".jsonl":
            continue
        for line_no, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                yield f"{p.name}:{line_no}", item


def _add_server(evidence: list[dict[str, str]], server: Any, *, source: str, kind: str) -> None:
    if server is None:
        return
    value = str(server).strip()
    if not value:
        return
    evidence.append({"server": value, "source": source, "kind": kind})


def _semantic_mcp_evidence(provider: str, logs: Path) -> list[dict[str, str]]:
    """Extract only protocol events that semantically identify MCP activity.

    Accepted evidence shapes intentionally include actual tool calls,
    MCP-server elicitation requests, and Cursor MCP namespace/tool calls. We do
    *not* treat arbitrary fields called `serverName` as MCP evidence.
    """
    evidence: list[dict[str, str]] = []

    for source, record in _jsonl_records(logs):
        # Compatibility for explicit audit fixtures / normalized telemetry.
        if "mcpServerName" in record:
            _add_server(evidence, record.get("mcpServerName"), source=source, kind="explicit_mcp_server")
        if "mcp_server" in record:
            _add_server(evidence, record.get("mcp_server"), source=source, kind="explicit_mcp_server")

        payload = record.get("payload")
        if isinstance(payload, dict):
            method = str(payload.get("method") or "")
            params = payload.get("params")
            params = params if isinstance(params, dict) else {}

            # Codex app-server actual MCP tool invocation.
            if method in {"item/started", "item/completed"}:
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "mcpToolCall":
                    _add_server(evidence, item.get("server"), source=source, kind="codex_mcp_tool_call")

            # A server-originated MCP Elicitation is also real MCP interaction.
            # This is deliberately method-scoped so remoteControl.serverName is ignored.
            if method.startswith("mcpServer/elicitation/"):
                _add_server(evidence, params.get("serverName") or params.get("name"), source=source, kind="codex_mcp_elicitation")

        # Cursor stream MCP calls. Cursor nests the provider/server identity in
        # tool_call.mcpToolCall.args rather than in a generic serverName field.
        tool_call = record.get("tool_call")
        if isinstance(tool_call, dict):
            mcp = tool_call.get("mcpToolCall")
            if isinstance(mcp, dict):
                args = mcp.get("args")
                args = args if isinstance(args, dict) else {}
                _add_server(
                    evidence,
                    args.get("serverIdentifier") or args.get("providerIdentifier") or mcp.get("server"),
                    source=source,
                    kind="cursor_mcp_tool_call",
                )
            discovery = tool_call.get("getMcpToolsToolCall")
            if isinstance(discovery, dict):
                args = discovery.get("args")
                args = args if isinstance(args, dict) else {}
                _add_server(evidence, args.get("server"), source=source, kind="cursor_mcp_namespace_access")

        # Generic normalized MCP tool-call records, if a provider adapter emits
        # them directly rather than inside a provider-specific envelope.
        if record.get("type") == "mcpToolCall":
            _add_server(evidence, record.get("server"), source=source, kind="normalized_mcp_tool_call")

    # Stable deduplication keeps the audit compact while retaining evidence type.
    unique: dict[tuple[str, str, str], dict[str, str]] = {}
    for item in evidence:
        unique[(item["server"], item["source"], item["kind"])] = item
    return list(unique.values())


def _semantic_plugin_invocations(logs: Path) -> list[str]:
    """Extract explicit plugin IDs from structured records only."""
    found: set[str] = set()
    for _source, record in _jsonl_records(logs):
        direct = record.get("pluginId") or record.get("plugin_id")
        if direct:
            found.add(str(direct))
        payload = record.get("payload")
        if isinstance(payload, dict):
            params = payload.get("params")
            if isinstance(params, dict):
                item = params.get("item")
                if isinstance(item, dict) and item.get("pluginId"):
                    found.add(str(item.get("pluginId")))
    return sorted(x for x in found if x and x.lower() != "dynosai")


def audit_managed_runtime(provider: str, managed: dict[str, Any] | None, logs: str | Path) -> dict[str, Any]:
    """Audit whether a managed session stayed under DynosAI configuration authority.

    This intentionally distinguishes *materialized provider extensions* from
    *observed invocations*. Strict 0.9.5 acceptance requires both to be zero.
    """
    provider = str(provider).lower()
    managed = managed or {}
    root = Path(str(managed.get("root") or "")) if managed.get("root") else Path("/__dynosai_missing_runtime__")
    logs = Path(logs)
    effective = managed.get("effective") or {}
    manifest_path = root / "runtime.json"
    if not effective and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            effective = manifest.get("effective") or {}
        except Exception:
            effective = {}
    selected = {str(x.get("id")) for x in (effective.get("skills") or []) if x.get("id")}
    # The runtime may materialize the complete DynosAI-owned provider-native skill
    # catalogue so later workflow phases can activate skills without importing
    # user/provider defaults or restarting the provider. Those catalogued skills
    # are authoritative DynosAI artifacts, not external extensions.
    selected.update(str(x) for x in (managed.get("skill_catalog") or []) if x)
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            selected.update(str(x) for x in (manifest.get("skill_catalog") or []) if x)
        except Exception:
            pass
    files = _relative_files(root)

    provider_system_skills: list[str] = []
    provider_plugin_artifacts: list[str] = []
    external_skill_artifacts: list[str] = []
    if provider == "codex":
        provider_system_skills = [x for x in files if x.startswith("home/skills/.system/") and x.endswith("SKILL.md")]
        provider_plugin_artifacts = [x for x in files if x.startswith("home/plugins/")]
        for x in files:
            m = re.match(r"home/skills/([^/]+)/SKILL\.md$", x)
            if m and m.group(1) not in selected and m.group(1) != ".system":
                external_skill_artifacts.append(x)
    elif provider == "cursor":
        for x in files:
            m = re.match(r"config/skills/([^/]+)/SKILL\.md$", x)
            if m and m.group(1) not in selected:
                external_skill_artifacts.append(x)

    text = _trace_text(logs)
    low = text.lower()
    external_plugin_invocations = _semantic_plugin_invocations(logs)

    mcp_evidence = _semantic_mcp_evidence(provider, logs)
    mcp_servers = sorted({x["server"] for x in mcp_evidence})
    external_mcp_invocations = sorted(x for x in mcp_servers if x.lower() != "dynosai")

    external_skill_invocations: list[str] = []
    candidates = []
    for path in provider_system_skills + external_skill_artifacts:
        try:
            sid = Path(path).parent.name
            if sid and sid not in selected:
                candidates.append(sid)
        except Exception:
            pass
    for sid in sorted(set(candidates)):
        escaped = re.escape(sid.lower())
        if re.search(rf"(?:skill|skill\.md|skills/)[^\n]{{0,120}}{escaped}|{escaped}[^\n]{{0,120}}(?:skill|skill\.md|skills/)", low):
            external_skill_invocations.append(sid)

    config_authority_verified = bool(managed) and managed.get("external_defaults_policy") == "ignore" and managed.get("provider_extensions_policy") == "deny" and bool(managed.get("strict_managed_runtime"))
    zero_provider_extension_artifacts = not provider_system_skills and not provider_plugin_artifacts and not external_skill_artifacts
    zero_external_skill_invocations = not external_skill_invocations
    zero_external_plugin_invocations = not external_plugin_invocations
    zero_external_mcp_invocations = not external_mcp_invocations
    strict_verified = all((
        config_authority_verified,
        zero_provider_extension_artifacts,
        zero_external_skill_invocations,
        zero_external_plugin_invocations,
        zero_external_mcp_invocations,
    ))
    return {
        "provider": provider,
        "policy": "strict_managed_runtime",
        "audit_version": 2,
        "mcp_detection": "semantic_protocol_events",
        "managed_root": str(root),
        "selected_dynosai_skills": sorted(selected),
        "config_authority_verified": config_authority_verified,
        "provider_system_skill_artifacts": provider_system_skills,
        "provider_plugin_artifacts": provider_plugin_artifacts,
        "external_skill_artifacts": external_skill_artifacts,
        "external_skill_invocations": external_skill_invocations,
        "external_plugin_invocations": external_plugin_invocations,
        "mcp_invocation_evidence": mcp_evidence,
        "observed_mcp_servers": mcp_servers,
        "external_mcp_invocations": external_mcp_invocations,
        "provider_extension_artifacts": len(provider_system_skills) + len(provider_plugin_artifacts) + len(external_skill_artifacts),
        "external_skill_invocation_count": len(external_skill_invocations),
        "external_plugin_invocation_count": len(external_plugin_invocations),
        "external_mcp_invocation_count": len(external_mcp_invocations),
        "zero_provider_extension_artifacts": zero_provider_extension_artifacts,
        "zero_external_skill_invocations": zero_external_skill_invocations,
        "zero_external_plugin_invocations": zero_external_plugin_invocations,
        "zero_external_mcp_invocations": zero_external_mcp_invocations,
        "strict_managed_runtime_verified": strict_verified,
    }
