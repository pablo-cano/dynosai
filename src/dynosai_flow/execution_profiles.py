# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Authoritative execution profiles outside the model.

Strict, Balanced and Autonomous select network, dependency and secret policy.
They never bypass human gates, never ship Docker/VM/remote runtimes, and never
claim OS-level network interception for local child processes. Schema stays v6.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .execution_runtime import LocalExecutionRuntime
from .policy import DependencyPolicy, NetworkPolicy, PolicyError
from .secrets import SecretBroker, vault_path_for

PROFILE_NAMES = ("strict", "balanced", "autonomous")
DEFAULT_PROFILE = "balanced"
UNSUPPORTED_RUNTIMES = ("docker", "container", "vm", "remote")

PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "strict": {
        "network": "offline",
        "dependencies": "governed",
        "secret_materialization": "refuse",
        "timeout_seconds": 60,
        "allowlist": (),
    },
    "balanced": {
        "network": "default_deny",
        "dependencies": "governed",
        "secret_materialization": "runtime_only",
        "timeout_seconds": 180,
        "allowlist": (),
    },
    "autonomous": {
        "network": "allowlist",
        "dependencies": "governed",
        "secret_materialization": "runtime_only",
        "timeout_seconds": 600,
        "allowlist": (
            "pypi.org",
            "files.pythonhosted.org",
            "registry.npmjs.org",
            "github.com",
            "api.github.com",
        ),
    },
}

POLICY = (
    "Execution profiles are host policy, not model policy. "
    "Human gates stay required. Local runtime network enforcement is decision-only. "
    "Docker, VM and remote runtimes are not shipped. Secrets never enter model context."
)


def normalize_profile(name: str | None) -> str:
    chosen = str(name or DEFAULT_PROFILE).strip().lower() or DEFAULT_PROFILE
    if chosen not in PROFILE_NAMES:
        raise ValueError(f"unknown execution profile: {chosen}")
    return chosen


def require_local_runtime(kind: str | None) -> str:
    """Refuse unimplemented execution backends rather than silently falling back."""
    runtime = str(kind or "local").strip().lower() or "local"
    if runtime == "local":
        return runtime
    if runtime in UNSUPPORTED_RUNTIMES:
        raise PolicyError(
            f"{runtime} execution runtime is not shipped in 0.18; local remains the default"
        )
    raise PolicyError(f"unknown execution runtime: {runtime}")


def profile_spec(name: str | None) -> dict[str, Any]:
    chosen = normalize_profile(name)
    return {"name": chosen, **PROFILE_SPECS[chosen]}


def policy_evidence(
    name: str | None,
    *,
    vault_configured: bool = False,
    runtime: str = "local",
) -> dict[str, Any]:
    spec = profile_spec(name)
    materialization = spec["secret_materialization"]
    if materialization == "runtime_only" and not vault_configured:
        materialization = "refuse"
    return {
        "profile": spec["name"],
        "runtime": runtime,
        "network": spec["network"],
        "dependencies": spec["dependencies"],
        "secret_materialization": materialization,
        "timeout_seconds": spec["timeout_seconds"],
        "os_network_enforcement": False,
        "enforcement": "decision_only",
        "human_gates": "required",
        "unsupported_runtimes": list(UNSUPPORTED_RUNTIMES),
        "vault_configured": bool(vault_configured),
        "policy": POLICY,
    }


def bind_local_runtime(root: str | Path, name: str | None = None) -> tuple[LocalExecutionRuntime, dict[str, Any]]:
    """Build the local hands runtime for a named profile. Never returns a container."""
    require_local_runtime("local")
    spec = profile_spec(name)
    root_path = Path(root).expanduser().resolve()
    vault = vault_path_for(root_path)
    vault_configured = vault.exists()
    allow_runtime = spec["secret_materialization"] == "runtime_only" and vault_configured
    runtime = LocalExecutionRuntime(
        root_path,
        network=NetworkPolicy(spec["network"], allowlist=spec["allowlist"]),
        dependencies=DependencyPolicy(spec["dependencies"]),
        secrets=SecretBroker(vault_path=vault if vault_configured else None, allow_runtime=allow_runtime),
    )
    evidence = policy_evidence(spec["name"], vault_configured=vault_configured, runtime=runtime.name)
    runtime.profile_name = spec["name"]
    return runtime, evidence
