# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""User-local persistent paths for provider runtimes and certification workspaces.

Managed Codex homes must not live under the OS temporary directory. Current Codex
refuses helper-binary creation from temporary CODEX_HOME locations.
"""

from __future__ import annotations

import os
import platform
import tempfile
from pathlib import Path


UNSAFE_CODEX_RUNTIME_MESSAGE = (
    "Unsafe Codex managed runtime.\n"
    "CODEX_HOME would be located under the OS temporary directory.\n"
    "Current Codex refuses helper-binary creation from temporary CODEX_HOME locations.\n"
    "Provider execution was not started."
)
HELPER_BINARY_REFUSAL_TEXT = "Refusing to create helper binaries under temporary dir"


class UnsafeCodexRuntime(RuntimeError):
    """CODEX_HOME would be under the OS temp root. Provider must not start."""


def os_temp_root() -> Path:
    return Path(tempfile.gettempdir()).expanduser().resolve()


def path_is_under(path: Path, root: Path) -> bool:
    """Return True when path is root or a descendant. Comparison is resolved."""
    target = Path(path).expanduser()
    base = Path(root).expanduser()
    try:
        resolved_target = target.resolve()
        resolved_base = base.resolve()
    except OSError:
        resolved_target = target
        resolved_base = base
    if resolved_target == resolved_base:
        return True
    try:
        resolved_target.relative_to(resolved_base)
        return True
    except ValueError:
        return False


def path_is_under_temp(path: Path, *, temp_root: Path | None = None) -> bool:
    return path_is_under(path, temp_root or os_temp_root())


def path_is_under_repo(path: Path, repo_root: Path) -> bool:
    return path_is_under(path, repo_root)


def user_local_state_root(
    *,
    environ: dict[str, str] | None = None,
    system: str | None = None,
    home: str | Path | None = None,
) -> Path:
    """Platform-local persistent DynosAI state. Never the OS temp directory."""
    env = os.environ if environ is None else environ
    kind = (system or platform.system()).lower()
    home_path = Path(home).expanduser() if home is not None else Path.home()
    if kind.startswith("win"):
        local = str(env.get("LOCALAPPDATA") or "").strip()
        if local:
            return Path(local) / "DynosAI"
        profile = str(env.get("USERPROFILE") or "").strip()
        return Path(profile or home_path) / ".dynosai"
    if kind == "darwin":
        return home_path / "Library" / "Application Support" / "DynosAI"
    xdg = str(env.get("XDG_STATE_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "dynosai"
    return home_path / ".local" / "state" / "dynosai"


def certification_workspace_root(
    *,
    environ: dict[str, str] | None = None,
    system: str | None = None,
    home: str | Path | None = None,
) -> Path:
    return user_local_state_root(environ=environ, system=system, home=home) / "certification" / "matrix-1.0"


def user_local_scratch(*parts: str) -> Path:
    """Persistent scratch under user-local state. Never the OS temp directory."""
    return user_local_state_root().joinpath("scratch", *(str(part) for part in parts))


def default_acceptance_workspace(
    run_id: str,
    *,
    environ: dict[str, str] | None = None,
    system: str | None = None,
    home: str | Path | None = None,
) -> Path:
    return user_local_state_root(environ=environ, system=system, home=home) / "acceptance" / str(run_id)


def default_matrix_workspace(
    repo_root: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
    system: str | None = None,
    home: str | Path | None = None,
) -> Path:
    """Certification workspace in user-local state, never repo and never OS temp."""
    repo = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    workspace = certification_workspace_root(environ=environ, system=system, home=home)
    resolved = workspace.expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    if path_is_under_repo(resolved, repo):
        raise RuntimeError("MATRIX default workspace resolved inside the repository")
    if path_is_under_temp(resolved):
        raise RuntimeError("MATRIX default workspace resolved under the OS temporary directory")
    return resolved


def managed_codex_home(project: str | Path) -> Path:
    return Path(project).expanduser() / ".dynosai" / "runtime" / "managed-agents" / "codex" / "home"


def acceptance_project_dir(workspace: str | Path, provider: str, scenario: str) -> Path:
    return Path(workspace).expanduser() / provider / scenario / "project"


def helper_binary_refusal_detected(text: str | None) -> bool:
    return HELPER_BINARY_REFUSAL_TEXT in str(text or "")


def assert_codex_home_safe(codex_home: str | Path, *, temp_root: Path | None = None) -> Path:
    home = Path(codex_home).expanduser()
    if path_is_under_temp(home, temp_root=temp_root):
        raise UnsafeCodexRuntime(UNSAFE_CODEX_RUNTIME_MESSAGE)
    return home


def portable_codex_preflight(report: dict[str, object]) -> dict[str, object]:
    """Drop absolute paths so MATRIX public JSON stays portable."""
    return {
        "status": report.get("status"),
        "codex_version": report.get("codex_version"),
        "codex_home_safe": bool(report.get("codex_home_safe")),
        "helper_binary_refusal_detected": bool(report.get("helper_binary_refusal_detected")),
        "provider_started": bool(report.get("provider_started")),
        "exit_code": report.get("exit_code"),
    }
