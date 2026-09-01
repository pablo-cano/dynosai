# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Host-owned provider capability manifests and adapter registry.

Cursor ACP and Codex app-server are already shipped. 0.19 publishes those
contracts as manifests so additional clients cannot be assumed certified.
Unknown adapters and project extension packs refuse closed. Schema stays v6.
"""

from __future__ import annotations

from typing import Any

SHIPPED_PROVIDERS = ("cursor", "codex")
PROVIDER_ALIASES = {"openai": "codex"}
SHIPPED_ADAPTERS = ("cursor-acp", "codex-app-server")
UNSUPPORTED_CLIENTS = (
    "vscode",
    "windsurf",
    "claude-code",
    "copilot",
    "gemini-cli",
    "aider",
    "continue",
    "zed",
)
CERTIFICATION = "0.13.0-matrix"

POLICY = (
    "Provider capability is a host contract, not a model claim. "
    "Cursor ACP and Codex app-server are the certified Studio transports. "
    "Additional clients, uncertified runtimes and project extension packs are not shipped."
)

MANIFESTS: dict[str, dict[str, Any]] = {
    "cursor": {
        "provider": "cursor",
        "adapter": "cursor-acp",
        "transport": "acp",
        "studio_launch": "agent acp",
        "mcp_injection": "session/new",
        "mcp_result_transport": "full-text",
        "elicitation": "studio_owned",
        "process_tree_shutdown": True,
        "certified": True,
        "certification": CERTIFICATION,
        "human_gates": "required",
        "spawn_extra_providers": False,
        "notes": "Cursor ACP shipped in 0.14.1 Studio. Structured MCP content is not assumed.",
    },
    "codex": {
        "provider": "codex",
        "adapter": "codex-app-server",
        "transport": "app-server",
        "studio_launch": "app-server stdio",
        "mcp_injection": "managed runtime",
        "mcp_result_transport": "structured-primary",
        "elicitation": "wire",
        "process_tree_shutdown": True,
        "certified": True,
        "certification": CERTIFICATION,
        "human_gates": "required",
        "spawn_extra_providers": False,
        "notes": "Codex app-server is the Studio and acceptance transport. Do not use `codex exec` for governed gates.",
    },
}


def normalize_provider_name(name: str | None) -> str:
    chosen = str(name or "").strip().lower()
    return PROVIDER_ALIASES.get(chosen, chosen)


def provider_manifest(name: str | None) -> dict[str, Any]:
    """Return the shipped manifest for a certified provider. Unknown names refuse."""
    chosen = normalize_provider_name(name)
    if chosen in UNSUPPORTED_CLIENTS:
        raise ValueError(
            f"{chosen} adapter is not shipped in 0.19; cursor ACP and Codex app-server remain the certified transports"
        )
    if chosen not in MANIFESTS:
        raise ValueError(f"unknown provider: {chosen or '(empty)'}")
    return dict(MANIFESTS[chosen])


def require_adapter(kind: str | None) -> str:
    """Refuse unimplemented client adapters rather than silently using Cursor/Codex."""
    adapter = str(kind or "").strip().lower()
    if adapter in SHIPPED_ADAPTERS:
        return adapter
    raise ValueError(
        f"{adapter or 'unknown'} adapter is not shipped in 0.19; cursor ACP and Codex app-server remain the certified transports"
    )


def load_extension_pack(name: str | None) -> None:
    """Project packs are a 1.0 concern. 0.19 refuses them instead of executing them."""
    slug = str(name or "").strip() or "unnamed"
    raise ValueError(
        f"project extension pack {slug!r} is not shipped in 0.19; quality and policy stay in Core"
    )


def list_manifests() -> list[dict[str, Any]]:
    return [dict(MANIFESTS[name]) for name in SHIPPED_PROVIDERS]


def capability_report() -> dict[str, Any]:
    """Host-visible adapter inventory. Safe for Studio, stats and diagnostic bundles."""
    return {
        "shipped_providers": list(SHIPPED_PROVIDERS),
        "shipped_adapters": list(SHIPPED_ADAPTERS),
        "unsupported_clients": list(UNSUPPORTED_CLIENTS),
        "extension_packs": False,
        "human_gates": "required",
        "spawn_extra_providers": False,
        "certification": CERTIFICATION,
        "policy": POLICY,
        "manifests": list_manifests(),
    }
