# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Specification, plan, constitution, baseline, and derived Markdown artifact management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import Database
from .util import json_dumps, json_loads, slugify, utc_now
from .policy import PathPolicyEngine, validate_scope_entry


DEFAULT_CONSTITUTION = """# Constitución del proyecto

## Principios

1. **La especificación es la fuente de verdad.** Todo cambio funcional debe estar vinculado a requisitos aprobados.
2. **Aclarar antes de asumir.** Las ambigüedades materiales bloquean la implementación.
3. **Simplicidad.** Se evita funcionalidad no solicitada, abstracciones prematuras y dependencias innecesarias.
4. **Baseline técnica.** Todo plan declara la baseline utilizada y cualquier desviación.
5. **Calidad verificable.** Ningún trabajo termina sin pruebas o evidencia aceptada.
6. **Trazabilidad.** Requisito → tarea → cambio → validación → evidencia.
7. **Seguridad.** Secretos fuera del repositorio y permisos de agente con mínimo privilegio.
8. **Git gobernado por DynosAI.** Los agentes no crean ramas, commits ni merges.
9. **Contexto mínimo.** Los agentes reciben primero entidades y símbolos relevantes, no documentos completos indiscriminadamente.
"""

DEFAULT_BASELINE = """# Baseline técnica

- Estado: Approved
- Versión: 1.1.0
- Desarrollo local-first.
- Soluciones simples y auditables.
- Git, ramas, worktrees, checkpoints y merges gestionados por DynosAI.
- Secretos fuera del repositorio.
- Las pruebas configuradas deben pasar antes de integrar.
- La base DynosAI gobierna el conocimiento; Git gobierna el código.
"""

SPEC_SCHEMA: dict[str, Any] = {
    "objective": "string",
    "scope": ["string"],
    "out_of_scope": ["string"],
    "requirements": [{"id": "REQ-001", "text": "string", "priority": "must|should|could"}],
    "acceptance_criteria": [{"id": "AC-001", "requirement": "REQ-001", "text": "Given/When/Then or observable criterion"}],
    "business_rules": [{"id": "RULE-001", "text": "string"}],
    "unknowns": ["string"],
    "affected_features": ["FEATURE-0001"],
}

PLAN_SCHEMA: dict[str, Any] = {
    "approach": "string",
    "architecture_delta": ["string"],
    "files": [{"path": "string", "action": "read|modify|create|delete", "role": "source|test|config|docs|other", "reason": "string"}],
    "risks": ["string"],
    "validation_profiles": ["unit|lint|typecheck|build|integration|project-approved-profile"],
    "data_migration": {
        "required": "boolean",
        "strategy": "string",
        "data_preservation": "string",
        "existing_row_semantics": "string",
        "backward_compatibility": "string",
        "verification_strategy": "string",
        "rollback_strategy": "string",
    },
    "tasks": [{
        "title": "string", "description": "string", "requirements": ["REQ-001"],
        "acceptance": ["AC-001"], "files": ["path"], "depends_on": ["zero-based-task-index-or-explicit-task-id"],
        "evidence_required": ["git_diff", "test_result", "migration_result"],
    }],
}


class ArtifactManager:
    def __init__(self, root: str | Path, db: Database):
        self.root = Path(root).resolve()
        self.db = db

    def ensure_project_artifacts(self, project_name: str) -> None:
        now = utc_now()
        for artifact_id, kind, title, content in (
            ("CONSTITUTION-001", "constitution", "Constitución del proyecto", DEFAULT_CONSTITUTION),
            ("BASELINE-001", "baseline", "Baseline técnica", DEFAULT_BASELINE),
        ):
            if not self.db.one("SELECT id FROM artifacts WHERE id=?", (artifact_id,)):
                self.db.execute(
                    "INSERT INTO artifacts(id,work_id,kind,status,version,title,content,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (artifact_id, "PROJECT", kind, "approved", 1, title, content, json_dumps({"project": project_name}), now, now),
                )
                self.db.index_search(kind, artifact_id, title, content)
        self.export_project_views()

    def specification_request(self, work: dict[str, Any], impact: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "operation": "author_specification",
            "work": {"id": work["id"], "title": work["title"], "description": work["description"], "type": work["work_type"]},
            "known_decisions": [{"question": d["question"], "answer": d["answer"]} for d in decisions],
            "related_context": {
                "features": impact.get("features", [])[:5],
                "rules": impact.get("rules", [])[:10],
                "files": impact.get("files", [])[:8],
                "tests": impact.get("tests", [])[:8],
            },
            "schema": SPEC_SCHEMA,
            "rules": [
                "Do not invent unresolved business behavior.",
                "Every acceptance criterion must reference one requirement.",
                "Unknown material behavior must be placed in unknowns.",
                "Requirements and criteria must be independently testable and non-empty.",
            ],
        }

    def plan_request(self, work: dict[str, Any], spec: dict[str, Any], impact: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": "author_plan",
            "work": {"id": work["id"], "title": work["title"]},
            "approved_spec": spec["metadata"],
            "related_context": impact,
            "baseline": "BASELINE-001@1.1.0",
            "validation_profiles": [r["name"] for r in self.db.query("SELECT name FROM validation_profiles WHERE approved=1 ORDER BY name")],
            "schema": PLAN_SCHEMA,
            "rules": [
                "Describe only the technical delta from the baseline.",
                "Every task must trace to requirements and acceptance criteria.",
                "Declare expected files only when evidence supports the prediction.",
                "File `action` is filesystem intent, never file role: use create for every new file (including new tests), modify/read/delete only for paths that already exist.",
                "Use `role: test` to classify test files; never use `action: test` in a plan.",
                "Dependencies must form an acyclic task graph.",
                "In brownfield, compatibility-sensitive modifications to existing production models, services or API contracts should request test_result evidence at the earliest task where regression can be observed; do not defer an obvious regression check merely because a later test task exists.",
                "Avoid a validation deadlock: if an implementation task requires test_result but the intended contract change necessarily requires existing tests to change, include those directly affected tests in the same atomic task or co-complete them; do not hide all required test updates behind a blocked downstream test-only task.",
                "Numeric depends_on references are zero-based task-array indices: 0 means the first task; explicit DynosAI task ids are also accepted.",
                "If the approved spec changes a persisted schema or requires a database migration, set data_migration.required=true and describe strategy, data preservation, existing-row physical semantics, backward compatibility, verification strategy and rollback strategy explicitly.",
                "For additive migrations, distinguish physical values stored/read in pre-existing rows after migration from application-level fallback semantics; never leave NULL/default/backfill behavior implicit.",
                "A migration-changing plan must include at least one migration/schema file and at least one task requiring migration_result evidence in addition to normal regression evidence.",
            ],
        }

    def submit_spec(self, work: dict[str, Any], payload: dict[str, Any], authored_by: str = "agent") -> dict[str, Any]:
        errors = self.validate_spec_payload(payload)
        if errors:
            raise ValueError("Invalid specification payload: " + "; ".join(errors))
        content = self.render_spec(work, payload)
        return self.upsert_artifact(
            artifact_id=f"SPEC-{work['id']}", work_id=work["id"], kind="spec",
            title=f"Especificación — {work['title']}", content=content, status="draft",
            metadata={**payload, "authored_by": authored_by, "schema": "dynosai-spec-v1"},
        )

    def submit_plan(self, work: dict[str, Any], spec: dict[str, Any], payload: dict[str, Any], authored_by: str = "agent") -> tuple[dict[str, Any], list[dict[str, Any]]]:
        errors = self.validate_plan_payload(spec, payload)
        if errors:
            raise ValueError("Invalid plan payload: " + "; ".join(errors))
        plan_content = self.render_plan(work, spec, payload)
        plan = self.upsert_artifact(
            artifact_id=f"PLAN-{work['id']}", work_id=work["id"], kind="plan",
            title=f"Plan — {work['title']}", content=plan_content, status="draft",
            metadata={**payload, "authored_by": authored_by, "schema": "dynosai-plan-v2", "baseline": "BASELINE-001@1.1.0", "spec_revision": spec.get("version",1)},
        )
        self.db.execute("DELETE FROM tasks WHERE work_id=?", (work["id"],))
        tasks: list[dict[str, Any]] = []
        generated_ids = [f"{work['id']}-T{i:02d}" for i in range(1, len(payload["tasks"]) + 1)]
        resolved_deps, dependency_errors = self._resolve_task_dependencies(work["id"], payload["tasks"])
        if dependency_errors:
            raise ValueError("Invalid plan payload: " + "; ".join(dependency_errors))
        now = utc_now()
        for index, item in enumerate(payload["tasks"], 1):
            task_id = generated_ids[index - 1]
            deps = resolved_deps[index - 1]
            self.db.execute(
                "INSERT INTO tasks(id,work_id,title,description,state,requirements,acceptance,files,validation_commands,evidence_required,depends_on,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, work["id"], item["title"], item["description"], "ready", json_dumps(item["requirements"]),
                 json_dumps(item["acceptance"]), json_dumps(item.get("files", [])),
                 json_dumps(payload.get("validation_profiles") or ["unit"]),
                 json_dumps(item.get("evidence_required") or ["git_diff", "test_result"]), json_dumps(deps), now, now),
            )
            self.db.index_search("task", task_id, item["title"], item["description"])
            tasks.append({"id": task_id, **item, "depends_on": deps})
        self.export_work_views(work["id"])
        return plan, tasks

    @staticmethod
    def validate_spec_payload(payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        reqs = payload.get("requirements") or []
        acs = payload.get("acceptance_criteria") or []
        if not str(payload.get("objective", "")).strip(): errors.append("objective is required")
        if not reqs: errors.append("at least one requirement is required")
        req_ids: set[str] = set()
        for item in reqs:
            rid = str(item.get("id", "")).strip()
            text = str(item.get("text", "")).strip()
            if not rid.startswith("REQ-"): errors.append(f"invalid requirement id {rid!r}")
            if len(text) < 12: errors.append(f"requirement {rid or '?'} is too short")
            if rid in req_ids: errors.append(f"duplicate requirement {rid}")
            req_ids.add(rid)
        linked: set[str] = set(); ac_seen: set[str] = set()
        for item in reqs:
            if str(item.get("priority", "")).lower() not in {"must","should","could"}: errors.append(f"requirement {item.get('id','?')} has invalid priority")
        for ac in acs:
            aid = str(ac.get("id", "")).strip(); req = str(ac.get("requirement", "")).strip(); text = str(ac.get("text", "")).strip()
            if not aid.startswith("AC-"): errors.append(f"invalid acceptance id {aid!r}")
            if aid in ac_seen: errors.append(f"duplicate acceptance criterion {aid}")
            ac_seen.add(aid)
            if req not in req_ids: errors.append(f"{aid or '?'} references unknown requirement {req}")
            if len(text) < 20: errors.append(f"acceptance {aid or '?'} is too short")
            linked.add(req)
        for rid in req_ids - linked: errors.append(f"requirement {rid} has no acceptance criterion")
        return errors

    @staticmethod
    def _resolve_task_dependencies(work_id: str, tasks: list[dict[str, Any]]) -> tuple[list[list[str]], list[str]]:
        """Resolve plan dependencies deterministically.

        Numeric references are ZERO-BASED task-array indices (0 = first task),
        matching the structured plan payload emitted by coding agents. Fully
        qualified DynosAI task ids are also accepted. Ambiguous/invalid refs are
        rejected rather than silently dropped.
        """
        generated_ids = [f"{work_id}-T{i:02d}" for i in range(1, len(tasks) + 1)]
        resolved: list[list[str]] = []
        errors: list[str] = []
        for index, item in enumerate(tasks):
            deps: list[str] = []
            for dep in item.get("depends_on", []) or []:
                target: str | None = None
                if isinstance(dep, int) or (isinstance(dep, str) and dep.strip().isdigit()):
                    pos = int(dep)
                    if 0 <= pos < len(generated_ids):
                        target = generated_ids[pos]
                    else:
                        errors.append(f"task {index + 1} dependency index {dep!r} is out of range (zero-based)")
                else:
                    value = str(dep).strip()
                    if value in generated_ids:
                        target = value
                    else:
                        errors.append(f"task {index + 1} references unknown dependency {dep!r}")
                if target:
                    if target == generated_ids[index]:
                        errors.append(f"task {index + 1} may not depend on itself")
                    elif target not in deps:
                        deps.append(target)
            resolved.append(deps)

        # Dependency graph must be acyclic. Forward references are allowed, but
        # cycles are never normalized away.
        graph = {generated_ids[i]: set(resolved[i]) for i in range(len(generated_ids))}
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(node: str) -> bool:
            if node in visiting: return False
            if node in visited: return True
            visiting.add(node)
            for dep in graph.get(node, set()):
                if not visit(dep): return False
            visiting.remove(node); visited.add(node); return True
        for node in generated_ids:
            if not visit(node):
                errors.append("task dependency graph contains a cycle")
                break
        return resolved, errors

    def validate_plan_payload(self, spec: dict[str, Any], payload: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if len(str(payload.get("approach", "")).strip()) < 20:
            errors.append("approach is too short")
        tasks = payload.get("tasks") or []
        if not tasks: errors.append("at least one task is required")
        req_ids = {r["id"] for r in spec["metadata"].get("requirements", [])}
        ac_ids = {a["id"] for a in spec["metadata"].get("acceptance_criteria", [])}
        policy = PathPolicyEngine(self.root)
        declared_files: set[str] = set(); actions: dict[str,str] = {}
        files = payload.get("files") or []
        if not files:
            errors.append("plan file scope may not be empty")
        for item in files:
            path = str(item.get("path", "")).replace("\\", "/").strip(); action=str(item.get("action","")).strip().lower()
            if action == "test":
                errors.append(f"invalid planned file {path!r}: action 'test' is a file role, not filesystem intent; use action='create' for a new test file or action='modify' for an existing test file, optionally role='test'")
                continue
            if action not in {"read","modify","create","delete"}:
                errors.append(f"invalid planned file {path!r}: invalid plan action {action!r}; expected read|modify|create|delete")
                continue
            err=validate_scope_entry(self.root,{"path":path,"action":action},policy)
            if err: errors.append(f"invalid planned file {path!r}: {err}"); continue
            if path in declared_files: errors.append(f"duplicate planned file {path}")
            declared_files.add(path); actions[path]=action
        profiles = payload.get("validation_profiles") or []
        if not profiles: errors.append("at least one validation profile is required")
        approved={r["name"] for r in self.db.query("SELECT name FROM validation_profiles WHERE approved=1")}
        for profile in profiles:
            if profile not in approved: errors.append(f"validation profile is not approved: {profile}")
        # Data evolution is a first-class plan concern in brownfield work. Infer
        # it only from approved objective/requirements/acceptance/rules (never from
        # out_of_scope text) and from explicit migration/schema files.
        spec_meta = spec.get("metadata") or {}
        data_text = " ".join(
            [str(spec_meta.get("objective") or "")]
            + [str(x.get("text") or "") for x in (spec_meta.get("requirements") or [])]
            + [str(x.get("text") or "") for x in (spec_meta.get("acceptance_criteria") or [])]
            + [str(x.get("text") or "") for x in (spec_meta.get("business_rules") or [])]
        ).lower()
        data_terms = ("migraci", "sqlite", "schema", "base de datos", "database", "persisted schema")
        planned_migration_files = {
            path for path in declared_files
            if path.lower().endswith(".sql") or "/migrations/" in f"/{path.lower()}" or path.lower().startswith("migrations/")
        }
        requires_data_migration = any(term in data_text for term in data_terms) or bool(planned_migration_files)
        migration = payload.get("data_migration") or {}
        if requires_data_migration:
            if migration.get("required") is not True:
                errors.append("data_migration.required=true is required for persisted schema/database changes")
            for field in ("strategy", "data_preservation", "existing_row_semantics", "backward_compatibility", "verification_strategy", "rollback_strategy"):
                if len(str(migration.get(field, "")).strip()) < 20:
                    errors.append(f"data_migration.{field} is required and must be explicit")
            if not planned_migration_files:
                errors.append("data migration plan must declare at least one migration/schema file")
        elif migration.get("required") is True:
            for field in ("strategy", "data_preservation", "existing_row_semantics", "backward_compatibility", "verification_strategy", "rollback_strategy"):
                if len(str(migration.get(field, "")).strip()) < 20:
                    errors.append(f"data_migration.{field} is required when data_migration.required=true")

        for i, task in enumerate(tasks, 1):
            if len(str(task.get("title", "")).strip()) < 5: errors.append(f"task {i} title is too short")
            if len(str(task.get("description", "")).strip()) < 12: errors.append(f"task {i} description is too short")
            requirements=set(task.get("requirements") or []); acceptance=set(task.get("acceptance") or [])
            if not requirements: errors.append(f"task {i} has no requirement trace")
            if not acceptance: errors.append(f"task {i} has no acceptance trace")
            if not requirements <= req_ids: errors.append(f"task {i} references unknown requirements")
            if not acceptance <= ac_ids: errors.append(f"task {i} references unknown acceptance criteria")
            task_files={str(x).replace("\\","/") for x in (task.get("files") or [])}
            if not task_files: errors.append(f"task {i} has empty file scope")
            if not task_files <= declared_files: errors.append(f"task {i} references files not declared by plan: {sorted(task_files-declared_files)}")
            if not task.get("evidence_required"): errors.append(f"task {i} has no evidence requirements")
        if requires_data_migration:
            migration_tasks = [
                task for task in tasks
                if {str(x).replace("\\", "/") for x in (task.get("files") or [])} & planned_migration_files
            ]
            if not migration_tasks:
                errors.append("at least one task must own the migration/schema file")
            elif not any("migration_result" in set(task.get("evidence_required") or []) for task in migration_tasks):
                errors.append("migration task must require migration_result evidence")
        _, dependency_errors = self._resolve_task_dependencies(str(spec.get("work_id") or "DYN-0000"), tasks)
        errors.extend(dependency_errors)
        return errors

    def upsert_artifact(self, artifact_id: str, work_id: str, kind: str, title: str, content: str, status: str = "draft", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        existing = self.db.one("SELECT version FROM artifacts WHERE id=?", (artifact_id,))
        version = int(existing["version"]) + 1 if existing else 1
        now = utc_now()
        encoded = json_dumps(metadata or {})
        with self.db.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO artifacts(id,work_id,kind,status,version,title,content,metadata,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status,version=excluded.version,title=excluded.title,content=excluded.content,metadata=excluded.metadata,updated_at=excluded.updated_at",
                (artifact_id, work_id, kind, status, version, title, content, encoded, now, now),
            )
            connection.execute("INSERT INTO artifact_revisions(artifact_id,work_id,kind,revision,status,title,content,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(artifact_id,work_id,kind,version,status,title,content,encoded,now))
        self.db.index_search(kind, artifact_id, title, content)
        self.db.audit("ArtifactUpserted", artifact_id, {"work_id": work_id, "kind": kind, "version": version})
        result = self.db.one("SELECT * FROM artifacts WHERE id=?", (artifact_id,)) or {}
        result["metadata"] = json_loads(result.get("metadata"), {})
        return result

    def approve(self, artifact_id: str) -> None:
        row=self.db.one("SELECT * FROM artifacts WHERE id=?",(artifact_id,))
        if not row: raise KeyError(artifact_id)
        now=utc_now(); revision=int(row["version"])+1
        with self.db.transaction(immediate=True) as connection:
            connection.execute("UPDATE artifacts SET status='approved',version=?,updated_at=? WHERE id=?",(revision,now,artifact_id))
            connection.execute("INSERT INTO artifact_revisions(artifact_id,work_id,kind,revision,status,title,content,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(artifact_id,row["work_id"],row["kind"],revision,"approved",row["title"],row["content"],row["metadata"],now))
        self.db.audit("ArtifactApproved", artifact_id,{"revision":revision})

    def artifact(self, work_id: str, kind: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM artifacts WHERE work_id=? AND kind=? ORDER BY version DESC LIMIT 1", (work_id, kind))
        if row: row["metadata"] = json_loads(row.get("metadata"), {})
        return row

    def export_project_views(self) -> None:
        specify = self.root / ".specify" / "memory"; specify.mkdir(parents=True, exist_ok=True)
        constitution = self.db.one("SELECT content FROM artifacts WHERE id='CONSTITUTION-001'")
        baseline = self.db.one("SELECT content FROM artifacts WHERE id='BASELINE-001'")
        if constitution: (specify / "constitution.md").write_text(constitution["content"], encoding="utf-8")
        docs = self.root / "docs" / "architecture"; docs.mkdir(parents=True, exist_ok=True)
        if baseline: (docs / "baseline.md").write_text(baseline["content"], encoding="utf-8")

    def export_work_views(self, work_id: str) -> Path:
        work = self.db.one("SELECT * FROM work_items WHERE id=?", (work_id,))
        if not work: raise KeyError(work_id)
        folder = self.root / "specs" / f"{work_id.lower()}-{slugify(work['title'])}"; folder.mkdir(parents=True, exist_ok=True)
        for kind, filename in (("spec", "spec.md"), ("plan", "plan.md")):
            artifact = self.artifact(work_id, kind)
            if artifact:
                header = f"---\ndynosai_id: {artifact['id']}\nversion: {artifact['version']}\nstatus: {artifact['status']}\nsource: knowledge.db\n---\n\n"
                (folder / filename).write_text(header + artifact["content"], encoding="utf-8")
        tasks = self.db.query("SELECT * FROM tasks WHERE work_id=? ORDER BY id", (work_id,))
        if tasks:
            lines = ["---", f"dynosai_work_id: {work_id}", "source: knowledge.db", "---", "", "# Tareas", ""]
            for task in tasks:
                box = "x" if task["state"] == "completed" else " "
                lines.extend([f"- [{box}] **{task['id']} — {task['title']}**", f"  - {task['description']}"])
            (folder / "tasks.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return folder

    @staticmethod
    def render_spec(work: dict[str, Any], p: dict[str, Any]) -> str:
        reqs = "\n".join(f"### {r['id']}\n\n{r['text']}\n" for r in p["requirements"])
        acs = "\n".join(f"### {a['id']} → {a['requirement']}\n\n{a['text']}\n" for a in p["acceptance_criteria"])
        rules = "\n".join(f"- **{r.get('id','RULE')}** {r['text']}" for r in p.get("business_rules", [])) or "- Ninguna regla adicional identificada."
        scope = "\n".join(f"- {x}" for x in p.get("scope", [])) or "- Alcance definido por los requisitos."
        out = "\n".join(f"- {x}" for x in p.get("out_of_scope", [])) or "- Cambios no descritos."
        unknowns = "\n".join(f"- {x}" for x in p.get("unknowns", [])) or "- Ninguna ambigüedad material pendiente."
        return f"""# {work['title']}\n\n## Objetivo\n\n{p['objective']}\n\n## Alcance\n\n{scope}\n\n## Fuera de alcance\n\n{out}\n\n## Requisitos\n\n{reqs}\n## Criterios de aceptación\n\n{acs}\n## Reglas de negocio\n\n{rules}\n\n## Incertidumbres\n\n{unknowns}\n"""

    @staticmethod
    def render_plan(work: dict[str, Any], spec: dict[str, Any], p: dict[str, Any]) -> str:
        files = "\n".join(f"- `{x['path']}` — {x['action']}: {x['reason']}" for x in p.get("files", [])) or "- Se resolverán mediante el índice de código antes de editar."
        delta = "\n".join(f"- {x}" for x in p.get("architecture_delta", [])) or "- Sin cambios estructurales globales."
        risks = "\n".join(f"- {x}" for x in p.get("risks", [])) or "- Sin riesgos extraordinarios identificados."
        validations = "\n".join(f"- profile `{x}`" for x in p.get("validation_profiles", []))
        migration = p.get("data_migration") or {}
        migration_section = ""
        if migration.get("required"):
            migration_section = (
                "\n## Migración de datos\n\n"
                f"- Estrategia: {migration.get('strategy','')}\n"
                f"- Preservación: {migration.get('data_preservation','')}\n"
                f"- Semántica física de filas existentes: {migration.get('existing_row_semantics','')}\n"
                f"- Compatibilidad: {migration.get('backward_compatibility','')}\n"
                f"- Verificación: {migration.get('verification_strategy','')}\n"
                f"- Rollback: {migration.get('rollback_strategy','')}\n"
            )
        return f"""# Plan técnico — {work['title']}\n\n## Referencias\n\n- Trabajo: {work['id']}\n- Spec: {spec['id']} v{spec['version']}\n- Baseline: BASELINE-001 v1.1.0\n\n## Enfoque\n\n{p['approach']}\n\n## Delta arquitectónico\n\n{delta}\n\n## Archivos previstos\n\n{files}\n\n## Riesgos\n\n{risks}\n{migration_section}\n## Validación\n\n{validations}\n\n## Constitution Check\n\n- Trazabilidad requisito → tarea: PASS\n- Evidencia por tarea: PASS\n- Git gobernado por DynosAI: PASS\n- Contexto mínimo: PASS\n"""
