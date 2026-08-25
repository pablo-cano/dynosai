# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Workflow quality scoring and validation result evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .db import Database
from .util import json_loads


@dataclass(slots=True)
class Finding:
    code: str
    severity: str
    entity_id: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PLACEHOLDERS = (
    "tbd", "por definir", "completar según", "según corresponda", "derivar módulos",
    "si estás conforme", "según tu respuesta", "req-001:", "ac-001:",
)


class QualityEngine:
    def __init__(self, db: Database):
        self.db = db

    def validate_work(self, work_id: str) -> list[Finding]:
        findings: list[Finding] = []
        artifacts = self.db.query("SELECT * FROM artifacts WHERE work_id=?", (work_id,))
        for artifact in artifacts:
            lowered = artifact["content"].lower()
            for placeholder in PLACEHOLDERS:
                if placeholder in lowered:
                    findings.append(Finding("ARTIFACT.PLACEHOLDER", "blocking", artifact["id"], f"Contiene texto no resuelto: {placeholder}"))
            if artifact["kind"] == "spec":
                metadata = json_loads(artifact.get("metadata"), {})
                requirements = metadata.get("requirements", [])
                acceptance = metadata.get("acceptance_criteria", metadata.get("acceptance", []))
                if not requirements:
                    findings.append(Finding("SPEC.NO_REQUIREMENTS", "blocking", artifact["id"], "La spec no contiene requisitos"))
                if not acceptance:
                    findings.append(Finding("SPEC.NO_ACCEPTANCE", "blocking", artifact["id"], "La spec no contiene criterios de aceptación"))
                for item in requirements:
                    if not item.get("text", "").strip():
                        findings.append(Finding("SPEC.EMPTY_REQUIREMENT", "blocking", item.get("id", artifact["id"]), "Requisito vacío"))
                for item in acceptance:
                    if len(item.get("text", "").strip()) < 30:
                        findings.append(Finding("SPEC.WEAK_ACCEPTANCE", "blocking", item.get("id", artifact["id"]), "Criterio no verificable"))

                unknowns = metadata.get("unknowns", [])
                for unknown in unknowns:
                    if str(unknown).strip():
                        findings.append(Finding("SPEC.UNRESOLVED_UNKNOWN", "blocking", artifact["id"], f"Ambigüedad material pendiente: {unknown}"))
                req_ids = {item.get("id") for item in requirements}
                linked = {item.get("requirement") for item in acceptance}
                for req_id in req_ids - linked:
                    findings.append(Finding("SPEC.REQ_WITHOUT_AC", "blocking", str(req_id), "Requisito sin criterio de aceptación"))
            if artifact["kind"] == "plan":
                required = ["Baseline", "Validación", "Constitution Check"]
                for section in required:
                    if section.lower() not in lowered:
                        findings.append(Finding("PLAN.MISSING_SECTION", "blocking", artifact["id"], f"Falta la sección {section}"))
                metadata = json_loads(artifact.get("metadata"), {})
                migration = metadata.get("data_migration") or {}
                planned_files = metadata.get("files") or []
                has_migration_file = any(
                    str(item.get("path", "")).lower().endswith(".sql")
                    or "/migrations/" in f"/{str(item.get('path','')).lower()}"
                    for item in planned_files
                )
                if has_migration_file and migration.get("required") is not True:
                    findings.append(Finding("PLAN.MIGRATION_GOVERNANCE", "blocking", artifact["id"], "El plan modifica esquema/migraciones sin gobierno data_migration explícito"))
                if migration.get("required") is True and "migración de datos" not in lowered:
                    findings.append(Finding("PLAN.MIGRATION_SECTION", "blocking", artifact["id"], "Falta la sección explícita de migración de datos"))

        tasks = self.db.query("SELECT * FROM tasks WHERE work_id=?", (work_id,))
        for task in tasks:
            if len(task["description"].strip()) < 25 or task["description"].lower().startswith("tarea de implementación"):
                findings.append(Finding("TASK.GENERIC", "blocking", task["id"], "La tarea no es ejecutable ni verificable"))
            if not json_loads(task.get("requirements"), []):
                findings.append(Finding("TASK.NO_REQUIREMENT", "warning", task["id"], "La tarea no está vinculada a requisitos"))
            if not json_loads(task.get("evidence_required"), []):
                findings.append(Finding("TASK.NO_EVIDENCE", "blocking", task["id"], "La tarea no define evidencias"))

        # Detect circular dependencies deterministically.
        graph = {task["id"]: json_loads(task.get("depends_on"), []) for task in tasks}
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(node: str) -> bool:
            if node in visiting: return True
            if node in visited: return False
            visiting.add(node)
            for dep in graph.get(node, []):
                if dep in graph and visit(dep): return True
            visiting.remove(node); visited.add(node); return False
        if any(visit(node) for node in graph):
            findings.append(Finding("TASK.CYCLE", "blocking", work_id, "Las dependencias de tareas contienen un ciclo"))

        # Unresolved scope is governance debt, not an informational detail.
        # Code approval and merge must not silently pass while a user decision
        # is still pending.
        for request in self.db.query("SELECT id,path,action FROM scope_requests WHERE work_id=? AND status='pending' ORDER BY created_at",(work_id,)):
            findings.append(Finding("SCOPE.PENDING", "blocking", request["id"], f"Extensión de scope pendiente para {request['action']} {request['path']}"))

        return findings

    def health_score(self, work_id: str) -> tuple[int, list[Finding]]:
        findings = self.validate_work(work_id)
        score = 100
        for finding in findings:
            score -= 20 if finding.severity == "blocking" else 7
        work = self.db.one("SELECT state FROM work_items WHERE id=?", (work_id,))
        if work and work["state"] in {"inbox", "discovery"}:
            score = min(score, 70)
        return max(0, score), findings
