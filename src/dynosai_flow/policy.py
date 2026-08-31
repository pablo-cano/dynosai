# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Path scope, validation-profile, network and dependency policy enforcement."""

from __future__ import annotations

import fnmatch
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .util import decode_git_path


SENSITIVE_PATTERNS = (
    ".env", ".env.*", "**/.env", "**/.env.*", "**/secrets/**", "secrets/**",
    "**/*.pem", "**/*.key", "**/*.p12", "**/*.pfx", "**/*.jks", "**/*.keystore",
    "**/credentials*", "credentials*", "**/.aws/**", "**/.ssh/**", "**/.gnupg/**",
    ".npmrc", "**/.npmrc", ".pypirc", "**/.pypirc", ".netrc", "**/.netrc",
    "**/.docker/config.json", "**/.kube/config", "**/id_rsa", "**/id_ed25519",
    ".git/**", "**/.git/**", ".dynosai/knowledge.db*", ".dynosai/backups/**", ".dynosai/runtime/**",
)

SAFE_SECRET_TEMPLATES = (
    ".env.example", ".env.sample", ".env.template",
    "**/.env.example", "**/.env.sample", "**/.env.template",
)

AGENT_WRITE_DENY = (
    ".dynosai/**", ".git/**", "specs/**", ".specify/**",
    "AGENTS.md", "CLAUDE.md", ".mcp.json", ".codex/**", ".cursor/**", ".agents/**",
)

MANAGED_WORKFLOW_ARTIFACT_PATTERNS = (
    "specs/**",
    ".specify/**",
    "plan.md",
    "spec.md",
    "tasks.md",
    "**/plan.md",
    "**/spec.md",
    "**/tasks.md",
)


class PolicyError(PermissionError):
    pass


def _norm(value: str | Path) -> str:
    return decode_git_path(value)


def _matches(path: str, pattern: str) -> bool:
    p = PurePosixPath(path)
    # PurePath.match handles ** better than fnmatch for nested paths; keep both for root patterns.
    return p.match(pattern) or fnmatch.fnmatch(path, pattern)


def is_managed_workflow_artifact(path: str | Path) -> bool:
    """Return True for DynosAI-exported spec/plan overlays, not product source."""
    rel = _norm(path)
    return any(_matches(rel, pattern) for pattern in MANAGED_WORKFLOW_ARTIFACT_PATTERNS)


def product_scope_paths(paths: Iterable[str | Path]) -> list[str]:
    """Drop DynosAI-owned spec/plan overlays from a task or plan file list."""
    return [str(path) for path in paths if not is_managed_workflow_artifact(path)]


@dataclass(slots=True)
class PathDecision:
    allowed: bool
    path: str
    operation: str
    reason: str


class PathPolicyEngine:
    """Single authority for repository file access.

    All agent-facing reads/writes must pass this policy. It protects secrets and
    DynosAI/Git control files independently of any provider-specific rule file.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def decision(self, path: str | Path, operation: str = "read", *, agent: bool = True) -> PathDecision:
        candidate = Path(path)
        full = candidate.resolve() if candidate.is_absolute() else (self.root / candidate).resolve()
        if full != self.root and self.root not in full.parents:
            return PathDecision(False, _norm(path), operation, "path escapes project root")
        rel = _norm(full.relative_to(self.root)) if full != self.root else "."
        safe_template = any(_matches(rel, pattern) for pattern in SAFE_SECRET_TEMPLATES)
        if not safe_template and any(_matches(rel, pattern) for pattern in SENSITIVE_PATTERNS):
            return PathDecision(False, rel, operation, "sensitive path is denied")
        if agent and operation in {"write", "modify", "create", "delete"} and any(_matches(rel, pattern) for pattern in AGENT_WRITE_DENY):
            return PathDecision(False, rel, operation, "DynosAI/Git managed path is not agent-writable")
        return PathDecision(True, rel, operation, "allowed")

    def require(self, path: str | Path, operation: str = "read", *, agent: bool = True) -> Path:
        decision = self.decision(path, operation, agent=agent)
        if not decision.allowed:
            raise PolicyError(f"{decision.operation} denied for {decision.path}: {decision.reason}")
        return (self.root / decision.path).resolve() if decision.path != "." else self.root

    def filter_paths(self, paths: list[str], operation: str = "read", *, agent: bool = True) -> list[str]:
        result: list[str] = []
        for path in paths:
            decision = self.decision(path, operation, agent=agent)
            if decision.allowed:
                result.append(decision.path)
        return result


class GitCommandPolicy:
    """Permit only Git metadata forms that cannot expose arbitrary file contents.

    Content-bearing operations (diff/show/log/grep/blame/cat-file) are deliberately
    denied to agents because they can bypass PathPolicy and reveal committed secrets
    or external files. DynosAI exposes policy-filtered status/diff through MCP instead.
    """

    GLOBAL_ALLOWED = {"--no-pager"}

    @staticmethod
    def _safe_ref(value: str) -> bool:
        return bool(value) and not value.startswith("-") and all(ch.isalnum() or ch in "._/@{}^-~:" for ch in value)

    @classmethod
    def allowed(cls, args: list[str]) -> tuple[bool, str]:
        if not args:
            return False, "missing git subcommand"
        pos = 0
        while pos < len(args) and args[pos].startswith("-"):
            if args[pos] not in cls.GLOBAL_ALLOWED:
                return False, f"global git option is not allowed: {args[pos]}"
            pos += 1
        if pos >= len(args):
            return False, "missing git subcommand"
        cmd, rest = args[pos], args[pos + 1 :]
        if cmd == "status":
            allowed={"--short","-s","--porcelain","--porcelain=v1","--porcelain=v2","--branch","-b","--untracked-files=no","--untracked-files=normal","--untracked-files=all"}
            if all(x in allowed for x in rest): return True, "safe repository status metadata"
            return False, "unsupported git status form"
        if cmd == "rev-parse":
            exact={"HEAD","--show-toplevel","--show-prefix","--show-cdup","--git-dir","--git-common-dir","--is-inside-work-tree","--abbrev-ref"}
            if not rest: return False, "rev-parse requires an approved argument"
            if rest == ["--abbrev-ref","HEAD"] or (len(rest)==1 and rest[0] in exact): return True, "safe repository identity metadata"
            if len(rest)==1 and cls._safe_ref(rest[0]) and ":" not in rest[0]: return True, "safe revision resolution"
            return False, "unsupported rev-parse form"
        if cmd == "branch":
            if not rest or rest == ["--show-current"] or all(x in {"--list","-l","-a","--all","-r","--remotes"} for x in rest):
                return True, "safe branch metadata"
            return False, "git branch write/complex form blocked"
        if cmd == "tag":
            if not rest or rest in (["--list"],["-l"]): return True, "safe tag metadata"
            return False, "git tag write/complex form blocked"
        if cmd == "ls-files":
            allowed={"--cached","-c","--modified","-m","--deleted","-d","--others","-o","--exclude-standard","--stage","-s","--unmerged","-u"}
            if all(x in allowed for x in rest): return True, "safe tracked-file metadata"
            return False, "unsupported ls-files form"
        if cmd in {"diff","show","log","grep","blame","cat-file","remote","config","describe","merge-base","name-rev"}:
            return False, f"git {cmd} content/config access is mediated by DynosAI"
        return False, f"git subcommand blocked: {cmd}"


class ValidationProfilePolicy:
    """Validation commands are baseline-owned profiles, never free-form agent shell."""

    @staticmethod
    def parse_command(command: str) -> list[str]:
        parts = shlex.split(command, posix=os.name != "nt")
        if not parts:
            raise ValueError("empty validation command")
        # No shell metacharacters; commands are executed with shell=False but reject for clarity/audit.
        bad = {";", "&&", "||", "|", ">", ">>", "<", "2>", "&"}
        if any(part in bad for part in parts):
            raise ValueError("shell operators are not allowed in validation profiles")
        return parts

    @staticmethod
    def encode(parts: list[str]) -> str:
        return json.dumps(parts, ensure_ascii=False)

    @staticmethod
    def decode(value: str) -> list[str]:
        data = json.loads(value)
        if not isinstance(data, list) or not data or not all(isinstance(x, str) and x for x in data):
            raise ValueError("invalid validation profile command")
        return data


NETWORK_PROFILES = ("unrestricted", "allowlist", "default_deny", "offline")
DEPENDENCY_PROFILES = ("compatibility", "governed")

_INSTALL_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("pip", "install"),
    ("python", "-m", "pip", "install"),
    ("py", "-m", "pip", "install"),
    ("npm", "install"),
    ("npm", "i"),
    ("npx", "npm", "install"),
    ("pnpm", "add"),
    ("pnpm", "install"),
    ("yarn", "add"),
    ("yarn", "install"),
    ("cargo", "add"),
    ("cargo", "install"),
    ("go", "get"),
    ("composer", "require"),
    ("gem", "install"),
    ("dotnet", "add"),
    ("pipx", "install"),
    ("uv", "add"),
    ("uv", "pip", "install"),
    ("poetry", "add"),
)


@dataclass(slots=True)
class NetworkDecision:
    allowed: bool
    profile: str
    target: str
    reason: str


class NetworkPolicy:
    """Authoritative network decision for DynosAI-mediated operations.

    LocalExecutionRuntime does not intercept child-process sockets; OS-level
    enforcement is not shipped in 0.18. Evidence records enforcement as
    decision_only. The model still cannot grant itself network access.
    """

    def __init__(self, profile: str | None = None, allowlist: Iterable[str] | None = None):
        env = (os.environ.get("DYNOSAI_NETWORK_PROFILE") or "").strip().lower()
        chosen = (profile or env or "unrestricted").strip().lower()
        if chosen not in NETWORK_PROFILES:
            raise ValueError(f"unknown network profile: {chosen}")
        self.profile = chosen
        self.allowlist = {str(item).strip().lower() for item in (allowlist or ()) if str(item).strip()}

    def decide(self, host: str, port: int | None = None) -> NetworkDecision:
        target = str(host or "").strip().lower()
        if port is not None:
            target = f"{target}:{int(port)}"
        if not str(host or "").strip():
            return NetworkDecision(False, self.profile, target, "missing network target")
        if self.profile == "unrestricted":
            return NetworkDecision(True, self.profile, target, "compatibility unrestricted")
        if self.profile == "offline":
            return NetworkDecision(False, self.profile, target, "offline sandbox denies network")
        host_only = str(host).strip().lower()
        allowed = host_only in self.allowlist or target in self.allowlist
        if self.profile == "allowlist":
            return NetworkDecision(allowed, self.profile, target, "allowlist match" if allowed else "host not in allowlist")
        # default_deny
        return NetworkDecision(allowed, self.profile, target, "explicit allowlist exception" if allowed else "default deny")


@dataclass(slots=True)
class DependencyDecision:
    kind: str
    allowed: bool
    reason: str
    argv: list[str]


class DependencyPolicy:
    """Distinguish using installed dependencies from installing new ones."""

    def __init__(self, profile: str | None = None, install_allowlist: Iterable[tuple[str, ...]] | None = None):
        env = (os.environ.get("DYNOSAI_DEPENDENCY_PROFILE") or "").strip().lower()
        chosen = (profile or env or "compatibility").strip().lower()
        if chosen not in DEPENDENCY_PROFILES:
            raise ValueError(f"unknown dependency profile: {chosen}")
        self.profile = chosen
        self.install_allowlist = [tuple(item) for item in (install_allowlist or ())]

    @staticmethod
    def classify(argv: list[str]) -> str:
        parts = [str(item).strip() for item in argv if str(item).strip()]
        lowered = tuple(part.lower() for part in parts)
        for prefix in _INSTALL_PREFIXES:
            if lowered[: len(prefix)] == prefix:
                return "install"
        return "use"

    def decide(self, argv: list[str]) -> DependencyDecision:
        parts = [str(item) for item in argv]
        kind = self.classify(parts)
        if kind == "use":
            return DependencyDecision("use", True, "using installed dependencies", parts)
        if self.profile == "compatibility":
            return DependencyDecision("install", True, "install allowed in compatibility profile; record for future policy", parts)
        lowered = tuple(part.lower() for part in parts)
        if any(lowered[: len(prefix)] == prefix for prefix in self.install_allowlist):
            return DependencyDecision("install", True, "install allowlisted", parts)
        return DependencyDecision("install", False, "autonomous dependency installation requires explicit policy", parts)


def validate_scope_entry(root: Path, entry: dict[str, Any], policy: PathPolicyEngine) -> str | None:
    path = _norm(str(entry.get("path", "")))
    action = str(entry.get("action", "")).lower()
    if not path or action not in {"read", "modify", "create", "delete", "test"}:
        return "invalid path/action"
    op = "read" if action == "read" else "write"
    decision = policy.decision(path, op, agent=True)
    if not decision.allowed:
        return decision.reason
    full = root / path
    if action in {"modify", "read", "test", "delete"} and not full.exists():
        return f"{action} path does not exist"
    if action == "create" and full.exists():
        return "create path already exists"
    return None
