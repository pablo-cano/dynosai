# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Deterministic project and work risk assessment for governed execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import json_loads


class RiskAssessment:
    """Score engineering risk from repository and authoritative work signals."""

    EXCLUDED_DIRS = {".git", ".dynosai", ".venv", "venv", "node_modules", "dist", "build", ".next", "out"}

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def assess(self, engine: Any | None = None, work_id: str | None = None) -> dict[str, Any]:
        signals: list[dict[str, Any]] = []
        paths = self._repository_paths(limit=5000)
        lower_paths = [p.lower() for p in paths]
        if any("migration" in p or p.endswith(".sql") for p in lower_paths):
            self._signal(signals, "data_surface", 8, "Repository contains database migration/SQL surfaces")
        if any(any(token in p for token in ("auth", "security", "permission", "oauth", "session")) for p in lower_paths):
            self._signal(signals, "security_surface", 8, "Repository contains authentication/security-sensitive surfaces")
        if any(p in {"pyproject.toml", "package.json", "cargo.toml", "pom.xml"} or p.endswith((".csproj", ".sln")) for p in lower_paths):
            self._signal(signals, "dependency_surface", 4, "Project dependency/build manifests are present")
        if any(p.startswith(".github/workflows/") or "pipeline" in p or "ci/" in p for p in lower_paths):
            self._signal(signals, "delivery_surface", 4, "CI/CD configuration is part of the repository")

        work = None
        if engine is not None:
            try:
                work = engine.get_work(work_id)
            except Exception:
                work = None
        if work:
            plan = engine.artifacts.artifact(work["id"], "plan") or {}
            metadata = plan.get("metadata") or {}
            files = [str(item.get("path") or "").replace("\\", "/") for item in metadata.get("files", []) if item.get("path")]
            lowered = [p.lower() for p in files]
            if len(files) >= 12:
                self._signal(signals, "blast_radius", 18, f"Plan touches {len(files)} files")
            elif len(files) >= 6:
                self._signal(signals, "blast_radius", 10, f"Plan touches {len(files)} files")
            elif files:
                self._signal(signals, "blast_radius", 4, f"Plan touches {len(files)} files")
            if any("migration" in p or p.endswith(".sql") for p in lowered):
                self._signal(signals, "data_change", 24, "Planned change touches schema/migration files")
            if any(any(token in p for token in ("auth", "security", "permission", "oauth", "session", "secret")) for p in lowered):
                self._signal(signals, "security_change", 24, "Planned change touches authentication/security-sensitive files")
            if any(Path(p).name.lower() in {"pyproject.toml", "package.json", "package-lock.json", "cargo.toml", "pom.xml"} for p in lowered):
                self._signal(signals, "dependency_change", 14, "Planned change modifies dependency/build manifests")
            if any(p.startswith(".github/workflows/") for p in lowered):
                self._signal(signals, "ci_change", 10, "Planned change modifies CI workflows")
            pending_scope = engine.db.query("SELECT id,path,action FROM scope_requests WHERE work_id=? AND status='pending'", (work["id"],))
            if pending_scope:
                self._signal(signals, "pending_scope", 25, f"{len(pending_scope)} scope extension request(s) are unresolved")
            pending_gates = engine.db.query("SELECT id,kind,gate FROM human_interactions WHERE work_id=? AND status='pending'", (work["id"],))
            if pending_gates:
                self._signal(signals, "pending_gate", 8, f"{len(pending_gates)} human decision(s) are pending")

        score = min(100, sum(int(item["weight"]) for item in signals))
        level = "low" if score < 25 else "medium" if score < 55 else "high"
        recommendations: list[str] = []
        codes = {item["code"] for item in signals}
        if {"data_change", "security_change"} & codes:
            recommendations.append("Require focused independent review before merge.")
        if "dependency_change" in codes:
            recommendations.append("Run build, tests and dependency/security validation after the change.")
        if "blast_radius" in codes and score >= 55:
            recommendations.append("Consider splitting the plan into smaller governed work items.")
        if "pending_scope" in codes:
            recommendations.append("Resolve scope extensions before code approval.")
        if not recommendations:
            recommendations.append("Use the normal governed workflow and configured project validations.")
        return {
            "score": score,
            "level": level,
            "signals": signals,
            "recommendations": recommendations,
            "work_id": work.get("id") if work else None,
            "policy": "Deterministic advisory risk score; it does not bypass DynosAI gates or validation.",
        }

    def _repository_paths(self, *, limit: int) -> list[str]:
        paths: list[str] = []
        if not self.root.exists():
            return paths
        for path in self.root.rglob("*"):
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                continue
            if any(part in self.EXCLUDED_DIRS for part in rel.parts):
                continue
            if path.is_file():
                paths.append(str(rel).replace("\\", "/"))
                if len(paths) >= limit:
                    break
        return paths

    @staticmethod
    def _signal(target: list[dict[str, Any]], code: str, weight: int, message: str) -> None:
        if any(item["code"] == code for item in target):
            return
        target.append({"code": code, "weight": weight, "message": message})
