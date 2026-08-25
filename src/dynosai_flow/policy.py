# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Path scope and validation-profile policy enforcement."""

from __future__ import annotations

import fnmatch
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


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


class PolicyError(PermissionError):
    pass


def _norm(value: str | Path) -> str:
    text=str(value).replace("\\", "/")
    while text.startswith("./"):
        text=text[2:]
    return text


def _matches(path: str, pattern: str) -> bool:
    p = PurePosixPath(path)
    # PurePath.match handles ** better than fnmatch for nested paths; keep both for root patterns.
    return p.match(pattern) or fnmatch.fnmatch(path, pattern)


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
