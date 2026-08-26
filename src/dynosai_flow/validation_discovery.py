# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Project-aware discovery and explicit approval of validation profiles."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .policy import ValidationProfilePolicy
from .util import json_dumps, utc_now


@dataclass(slots=True)
class ValidationCandidate:
    """A validation command inferred from repository configuration."""

    name: str
    command: list[str]
    reason: str
    source: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ValidationDiscovery:
    """Infer safe validation profiles without executing or approving them."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def discover(self) -> list[dict[str, Any]]:
        candidates: dict[str, ValidationCandidate] = {}
        self._python(candidates)
        self._node(candidates)
        self._rust(candidates)
        self._dotnet(candidates)
        self._java(candidates)
        return [candidates[name].to_dict() for name in ("unit", "lint", "typecheck", "build", "integration") if name in candidates]

    @staticmethod
    def _put(target: dict[str, ValidationCandidate], candidate: ValidationCandidate) -> None:
        current = target.get(candidate.name)
        if current is None or candidate.confidence > current.confidence:
            target[candidate.name] = candidate

    def _python(self, target: dict[str, ValidationCandidate]) -> None:
        pyproject = self.root / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8", errors="ignore") if pyproject.exists() else ""
        if pyproject.exists() or (self.root / "pytest.ini").exists() or (self.root / "tests").exists():
            self._put(target, ValidationCandidate("unit", ["python", "-m", "pytest"], "Python tests detected", "python", 0.95))
        if "ruff" in text or (self.root / "ruff.toml").exists() or (self.root / ".ruff.toml").exists():
            self._put(target, ValidationCandidate("lint", ["python", "-m", "ruff", "check", "."], "Ruff configuration/dependency detected", "python", 0.95))
        if "mypy" in text or (self.root / "mypy.ini").exists() or (self.root / ".mypy.ini").exists():
            self._put(target, ValidationCandidate("typecheck", ["python", "-m", "mypy", "."], "mypy configuration/dependency detected", "python", 0.92))

    def _node(self, target: dict[str, ValidationCandidate]) -> None:
        package = self.root / "package.json"
        if not package.exists():
            return
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        scripts = data.get("scripts") if isinstance(data, dict) else {}
        scripts = scripts if isinstance(scripts, dict) else {}
        mapping = {
            "test": ("unit", "JavaScript/TypeScript test script detected"),
            "lint": ("lint", "npm lint script detected"),
            "typecheck": ("typecheck", "npm typecheck script detected"),
            "build": ("build", "npm build script detected"),
            "test:integration": ("integration", "npm integration test script detected"),
            "integration": ("integration", "npm integration script detected"),
        }
        for script, (name, reason) in mapping.items():
            if script in scripts:
                self._put(target, ValidationCandidate(name, ["npm", "run", script], reason, "npm", 0.98))
        if "typecheck" not in target and (self.root / "tsconfig.json").exists():
            self._put(target, ValidationCandidate("typecheck", ["npx", "tsc", "--noEmit"], "tsconfig.json detected", "typescript", 0.75))

    def _rust(self, target: dict[str, ValidationCandidate]) -> None:
        if not (self.root / "Cargo.toml").exists():
            return
        self._put(target, ValidationCandidate("unit", ["cargo", "test"], "Cargo project detected", "rust", 0.95))
        self._put(target, ValidationCandidate("build", ["cargo", "build"], "Cargo project detected", "rust", 0.9))
        self._put(target, ValidationCandidate("lint", ["cargo", "clippy", "--", "-D", "warnings"], "Cargo project detected", "rust", 0.82))

    def _dotnet(self, target: dict[str, ValidationCandidate]) -> None:
        if list(self.root.glob("*.sln")) or list(self.root.glob("*.csproj")):
            self._put(target, ValidationCandidate("build", ["dotnet", "build"], ".NET solution/project detected", "dotnet", 0.95))
            if list(self.root.rglob("*Test*.csproj")) or list(self.root.rglob("*Tests*.csproj")):
                self._put(target, ValidationCandidate("unit", ["dotnet", "test"], ".NET test project detected", "dotnet", 0.92))

    def _java(self, target: dict[str, ValidationCandidate]) -> None:
        if (self.root / "pom.xml").exists():
            self._put(target, ValidationCandidate("unit", ["mvn", "test"], "Maven project detected", "maven", 0.9))
            self._put(target, ValidationCandidate("build", ["mvn", "package", "-DskipTests"], "Maven project detected", "maven", 0.85))
        elif (self.root / "gradlew").exists():
            self._put(target, ValidationCandidate("unit", ["./gradlew", "test"], "Gradle wrapper detected", "gradle", 0.9))
            self._put(target, ValidationCandidate("build", ["./gradlew", "build", "-x", "test"], "Gradle wrapper detected", "gradle", 0.85))

    @staticmethod
    def apply(db: Any, candidates: list[dict[str, Any]], names: list[str] | None = None, *, overwrite: bool = False) -> dict[str, Any]:
        """Approve selected discovered profiles in an initialized DynosAI database."""
        selected = set(names or [str(c.get("name")) for c in candidates])
        applied: list[str] = []
        skipped: list[dict[str, str]] = []
        now = utc_now()
        for candidate in candidates:
            name = str(candidate.get("name") or "")
            command = candidate.get("command") or []
            if name not in selected:
                continue
            if not name or not isinstance(command, list) or not all(isinstance(x, str) and x for x in command):
                skipped.append({"name": name or "unknown", "reason": "invalid candidate"})
                continue
            existing = db.one("SELECT name,source,command_json FROM validation_profiles WHERE name=?", (name,))
            encoded = ValidationProfilePolicy.encode(command)
            # Decode once through the policy so malformed commands cannot be persisted.
            ValidationProfilePolicy.decode(encoded)
            if existing and existing.get("command_json") != encoded and not overwrite:
                skipped.append({"name": name, "reason": f"existing profile from {existing.get('source') or 'unknown'} differs"})
                continue
            db.execute(
                "INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,source=excluded.source,approved=1,updated_at=excluded.updated_at",
                (name, encoded, f"auto-discovery:{candidate.get('source') or 'project'}", 1, now, now),
            )
            applied.append(name)
        if applied:
            db.audit("ValidationProfilesDiscovered", "PROJECT", {"applied": applied, "skipped": skipped})
        return {"applied": applied, "skipped": skipped, "approved": True}
