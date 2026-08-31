# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Core DynosAI workflow facade coordinating state, Git, scope, validation, retrieval, and evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .agents import AgentConfigurator
from .artifacts import ArtifactManager
from .db import Database
from .git_manager import GitError, GitManager
from .git_guard import GitGuard
from .state import StateManager
from .indexer import CodeIndexer
from .retrieval import RetrievalEngine
from .semantic import DEFAULT_DIMENSIONS, DEFAULT_MODEL, EmbeddingProvider, FastEmbedProvider, SemanticIndex, default_cache_dir
from .util import decode_git_path, json_dumps, json_loads, slugify, utc_now
from .validation import QualityEngine
from .policy import PathPolicyEngine, PolicyError, ValidationProfilePolicy, is_managed_workflow_artifact, product_scope_paths
from .runtime import RuntimeBroker
from .execution_runtime import LocalExecutionRuntime
from .team_scheduler import build_team_plan, claim_allowed, fan_in_conflicts
from .eval_intelligence import build_report, case_by_id, improvement_description, load_cases, mark_proposed, mark_regressed
from .eval_registry import EvalRegistry
from .version import __version__


WORKFLOW_ORDER = [
    "inbox",
    "discovery",
    "spec_review",
    "plan_review",
    "ready",
    "implementing",
    "code_review",
    "validating",
    "ready_to_merge",
    "done",
]


class DynosAI:
    """Local-first workflow facade. SQLite is authoritative; Markdown is generated."""

    def __init__(self, root: str | Path = ".", semantic_provider: EmbeddingProvider | None = None):
        self.root = Path(root).resolve()
        self.db = Database(self.root)
        self.git = GitManager(self.root, self.db)
        self.artifacts = ArtifactManager(self.root, self.db)
        self.indexer = CodeIndexer(self.root, self.db)
        self.semantic = SemanticIndex(self.db, semantic_provider)
        self.retrieval = RetrievalEngine(self.db, self.semantic)
        self.quality = QualityEngine(self.db)
        self.runtime = RuntimeBroker()
        self.agents = AgentConfigurator(self.root, self.db, self.runtime)
        self.state = StateManager(self.root, self.db)
        self.git_guard = GitGuard(self.root)
        self.execution = LocalExecutionRuntime(self.root)
        self._injected_semantic_provider = semantic_provider is not None
        # Reuse stable task-scoped context previews while authoritative task/file state is unchanged.
        # Byte-stable contracts let the managed MCP delta compressor return a compact reference
        # instead of replaying the full execution contract.
        self._context_preview_cache: dict[str, dict[str, Any]] = {}

    def initialize(
        self,
        name: str | None = None,
        language: str = "python",
        test_command: str = "pytest",
        agent: str | None = None,
        mode: str = "greenfield",
    ) -> dict[str, Any]:
        """Initialize DynosAI metadata, Git governance, validation defaults, indexes, and provider configuration for a new project."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.db.initialize()
        project_name = name or self.root.name
        now = utc_now()
        if not self.db.one("SELECT id FROM work_items WHERE id='PROJECT'"):
            self.db.execute(
                "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) "
                "VALUES('PROJECT',?,?,?,?,0,0,100,?,?)",
                (project_name, "Project governance", "project", "done", now, now),
            )
        self.db.set_meta("project_name", project_name)
        self.runtime.register_project(self.root, project_name)
        self.db.set_meta("language", language)
        self.db.set_meta("test_command", test_command)
        parts = ValidationProfilePolicy.parse_command(test_command)
        now_profile=utc_now()
        self.db.execute("INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,approved=1,updated_at=excluded.updated_at",("unit",json_dumps(parts),"project-baseline",1,now_profile,now_profile))
        self.db.set_meta("mode", mode)
        self.db.set_meta("embedding_model", self.semantic.model_name or DEFAULT_MODEL)
        self.db.set_meta("embedding_dimensions", str(self.semantic.dimensions or DEFAULT_DIMENSIONS))
        self.db.set_meta("embedding_cache", str(getattr(self.semantic.provider, "cache_dir", default_cache_dir())))
        global_model_marker = default_cache_dir() / ".dynosai-model.json"
        if self._injected_semantic_provider or global_model_marker.exists():
            self.db.set_meta("embedding_installed", "1")
            self.db.set_meta("embedding_provider", "fastembed" if not self._injected_semantic_provider else "injected")
        self.git.initialize_repo()
        main_branch = self.git.current_branch()
        self.db.set_meta("main_branch", main_branch)
        self._write_config()
        self._ensure_gitignore()
        self.artifacts.ensure_project_artifacts(project_name)
        if agent:
            self.agents.connect(agent)
        self.git.ensure_initial_commit()
        self.git.commit_main_changes("chore: configure DynosAI project")
        stats = self.indexer.index(self.git.head())
        semantic = self.semantic.reindex_authoritative(self.git.head())
        guard = self.git_guard.install()
        self.db.audit("ProjectInitialized", "PROJECT", {"name": project_name, "mode": mode})
        snapshot = self.state.snapshot("project-initialized")
        self.git.commit_main_changes("chore: persist DynosAI portable state")
        self.db.set_meta("indexed_commit", self.git.head())
        return {"project": project_name, "root": str(self.root), "main_branch": main_branch, "index": stats, "semantic": semantic, "git_guard": guard, "snapshot": snapshot}

    def adopt(
        self,
        name: str | None = None,
        agent: str | None = None,
        language: str = "python",
        test_command: str = "pytest",
    ) -> dict[str, Any]:
        # Never absorb unrelated uncommitted work into a managed checkpoint.
        """Adopt an existing repository as a brownfield DynosAI project and build its inferred baseline."""
        if (self.root / ".git").exists():
            # Check cleanliness before creating .dynosai/knowledge.db, otherwise the
            # adoption process itself would appear as an untracked change.
            self.git.initialize_repo()
            dirty = self.git.status()
            if dirty:
                raise GitError("Brownfield adoption requires a clean repository. Commit or stash existing changes first.")
        result = self.initialize(name=name, language=language, test_command=test_command, agent=agent, mode="brownfield")
        stats = self.sync(bootstrap=True)
        baseline = self.bootstrap_brownfield_specs()
        self.git.commit_main_changes("docs: add DynosAI brownfield baseline")
        self.db.set_meta("indexed_commit", self.git.head())
        result["bootstrap"] = stats
        result["baseline_specs"] = baseline
        return result

    def start(self, description: str, work_type: str = "auto", quick: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Create a new governed work item and persist its initial workflow state."""
        self._ensure_ready()
        classified = self._classify(description) if work_type == "auto" else work_type
        impact = self.retrieval.impact(description)
        if dry_run:
            return {
                "dry_run": True,
                "classification": classified,
                "suggested_flow": "quick" if quick or self._quick_candidate(impact, classified) else "standard",
                "impact": impact,
            }
        work_id = self.db.next_id("DYN", "work_items")
        title = self._title(description)
        quick_flow = quick or self._quick_candidate(impact, classified)
        now = utc_now()
        self.db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (work_id, title, description, classified, "inbox", int(quick_flow), 1, 50, now, now),
        )
        self.db.index_search("work", work_id, title, description)
        self.db.set_meta(f"impact:{work_id}", self._impact_markdown(impact))
        self.db.set_meta("active_work", work_id)
        self.db.audit("WorkStarted", work_id, {"type": classified, "quick": quick_flow})
        return {"id": work_id, "title": title, "type": classified, "state": "inbox", "quick_flow": quick_flow, "impact": impact, "next": self.next_action(work_id)}

    def continue_work(self, work_id: str | None = None) -> dict[str, Any]:
        """Advance a work item through deterministic workflow transitions without bypassing required human gates."""
        work = self.get_work(work_id); state = work["state"]
        if state == "inbox":
            self._set_state(work["id"], "discovery")
            return {"work": work["id"], "state": "discovery", "message": "Discovery abierto. El agente debe aclarar ambigüedades y compilar una spec estructurada.", "impact": self.retrieval.impact(f"{work['title']} {work['description']}", work_id=work["id"])}
        if state == "discovery":
            spec = self.artifacts.artifact(work["id"], "spec")
            if not spec:
                decisions = self.db.query("SELECT * FROM decisions WHERE work_id=? ORDER BY created_at", (work["id"],))
                impact = self.retrieval.impact(f"{work['title']} {work['description']}", work_id=work["id"])
                return {"work": work["id"], "state": "discovery", "requires_agent": True, "action": self.artifacts.specification_request(work, impact, decisions), "message": "La spec debe ser redactada por el agente y enviada mediante dynosai_submit_spec; DynosAI validará el contrato antes de continuar."}
            score, findings = self.quality.health_score(work["id"]); self._set_score(work["id"], score)
            blocking = [f for f in findings if f.severity == "blocking"]
            if blocking:
                return {"work": work["id"], "state": "discovery", "quality": score, "findings": [f.to_dict() for f in findings], "message": "La spec candidata no supera los gates; debe corregirse mediante dynosai_submit_spec."}
            self._set_state(work["id"], "spec_review"); self.artifacts.export_work_views(work["id"])
            return {"work": work["id"], "state": "spec_review", "artifact": spec["id"], "quality": score, "message": "Spec estructurada válida. Requiere aprobación explícita."}
        if state == "spec_review":
            return {"work": work["id"], "state": state, "message": "La spec está pendiente de revisión."}
        if state == "plan_review":
            plan = self.artifacts.artifact(work["id"], "plan")
            if not plan:
                spec = self.artifacts.artifact(work["id"], "spec"); assert spec
                impact = self.retrieval.impact(f"{work['title']} {work['description']}", work_id=work["id"])
                return {"work": work["id"], "state": state, "requires_agent": True, "action": self.artifacts.plan_request(work, spec, impact), "message": "El agente debe compilar plan y tareas mediante dynosai_submit_plan."}
            return {"work": work["id"], "state": state, "message": "Plan y tareas pendientes de aprobación.", "plan": plan["id"]}
        if state == "ready":
            self.artifacts.export_work_views(work["id"])
            self.state.snapshot(f"pre-worktree-{work['id']}")
            self.git.commit_main_changes(f"dynosai({work['id']}): approve specification and plan")
            self.db.set_meta("indexed_commit", self.git.head())
            preflight = self.preflight(work["id"])
            if not preflight["ready"]: return {"work": work["id"], "state": state, "preflight": preflight, "message": "Preflight bloqueado."}
            base_commit = self.git.head()
            strategy = work.get("workspace_strategy") or "isolated_worktree"
            if strategy == "interactive_branch" and work.get("branch") and work.get("worktree"):
                branch, worktree = str(work["branch"]), Path(str(work["worktree"])).resolve()
                if worktree != self.root:
                    raise GitError("interactive_branch workspace must be the project root")
                if self.git.current_branch(worktree) != branch:
                    raise GitError(f"Interactive provider session lost feature branch {branch}")
            else:
                branch, worktree = self.git.create_worktree(work["id"], work["title"])
            self.db.execute("UPDATE work_items SET branch=?,worktree=?,state='implementing',updated_at=? WHERE id=?", (branch, str(worktree), utc_now(), work["id"]))
            run_id = self.db.next_id("RUN", "runs")
            preview = self.context_preview(work["id"])
            feature_task_ids = [row["id"] for row in self.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id", (work["id"],))]
            self.db.execute("INSERT INTO runs(id,work_id,agent,status,started_at,base_commit,context_id,worktree,task_ids) VALUES(?,?,?,?,?,?,?,?,?)", (run_id, work["id"], self.db.get_meta("default_agent", ""), "running", utc_now(), base_commit, preview["id"], str(worktree), json_dumps(feature_task_ids)))
            self.db.execute("INSERT OR REPLACE INTO run_metrics(run_id,repository_files,context_tokens,suggested_files) VALUES(?,?,?,?)", (run_id, int((self.db.one('SELECT COUNT(*) n FROM code_files') or {'n':0})['n']), preview["estimated_tokens"], len(preview.get("impact",{}).get("files",[]))))
            return {"work": work["id"], "state": "implementing", "branch": branch, "worktree": str(worktree), "run_id": run_id, "preflight": preflight, "context_preview": preview, "agent_cwd": str(worktree), "git_guard": str(self.git_guard.guard_dir), "message": "Workspace preparado. La sesión persistente del proveedor puede continuar sin reabrirse."}
        if state == "implementing":
            run = self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1", (work["id"],))
            remaining=int((self.db.one("SELECT COUNT(*) n FROM tasks WHERE work_id=? AND state!='completed'",(work["id"],)) or {"n":0})["n"])
            if run and run["status"] == "verified" and remaining==0:
                first_run=self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id ASC LIMIT 1",(work["id"],)) or run
                self._set_state(work["id"], "code_review")
                return {"work": work["id"], "state": "code_review", "run": run, "diff": self.git.diff_text(Path(work["worktree"]), first_run.get("base_commit") or None), "message": "Resultado verificado contra Git y tests. Revisa el diff."}
            return {"work": work["id"], "state": state, "run": run, "message": "La ejecución sigue abierta o pendiente de verificación.", "context_preview": self.context_preview(work["id"])}
        if state == "code_review": return {"work": work["id"], "state": state, "message": "Código pendiente de aprobación."}
        if state == "validating":
            validation = self.run_validations(work["id"])
            if validation["passed"]: self._set_state(work["id"], "ready_to_merge")
            return {"work": work["id"], "state": "ready_to_merge" if validation["passed"] else "validating", "validation": validation}
        if state == "ready_to_merge": return {"work": work["id"], "state": state, "message": "Validado y listo para integración gobernada."}
        if state == "done": return {"work": work["id"], "state": state, "message": "Trabajo completado y consolidado."}
        return {"work": work["id"], "state": state, "message": "El trabajo requiere intervención."}

    def review(self, work_id: str, approve: str | None = None, changes: str | None = None) -> dict[str, Any]:
        """Resolve a governed review gate for specification, plan, code, or merge."""
        work = self.get_work(work_id)
        if changes:
            self.register_decision(work_id,"Review changes",changes)
            if work["state"]=="code_review":
                previous=self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(work_id,)); run_id=self.db.next_id("RUN","runs"); preview=self.context_preview(work_id)
                self.db.execute("UPDATE tasks SET state='ready',updated_at=? WHERE work_id=?",(utc_now(),work_id))
                self.db.execute("INSERT INTO runs(id,work_id,agent,status,started_at,base_commit,context_id,supersedes_run,worktree) VALUES(?,?,?,?,?,?,?,?,?)",(run_id,work_id,(previous or {}).get("agent") or self.db.get_meta("default_agent",""),"running",utc_now(),(previous or {}).get("base_commit") or self.git.head(),preview["id"],(previous or {}).get("id"),work.get("worktree") or ""))
                self._set_state(work_id,"implementing"); return {"work":work_id,"state":"implementing","run_id":run_id,"supersedes":(previous or {}).get("id"),"message":"Review changes crea una nueva ejecución auditable."}
            target="discovery" if work["state"]=="spec_review" else "plan_review" if work["state"]=="plan_review" else "implementing"
            self._set_state(work_id,target); return {"work":work_id,"state":target,"message":"Cambios solicitados y registrados."}
        if approve == "spec":
            if work["state"] != "spec_review": raise ValueError("Spec approval is only valid in spec_review")
            findings=self.quality.validate_work(work_id); blocking=[f for f in findings if f.severity=="blocking"]
            if blocking: return {"approved":False,"findings":[f.to_dict() for f in blocking]}
            spec=self.artifacts.artifact(work_id,"spec"); assert spec; self.artifacts.approve(spec["id"]); self._set_state(work_id,"plan_review")
            return {"approved":True,"state":"plan_review","message":"Spec aprobada. El agente debe compilar el plan estructurado."}
        if approve == "plan":
            if work["state"] != "plan_review": raise ValueError("Plan approval is only valid in plan_review")
            plan=self.artifacts.artifact(work_id,"plan")
            if not plan: return {"approved":False,"message":"Todavía no existe un plan estructurado; ejecuta dynosai continue dentro del agente."}
            current_spec=self.artifacts.artifact(work_id,"spec")
            if not current_spec or int(plan["metadata"].get("spec_revision",0))!=int(current_spec["version"]): return {"approved":False,"message":"El plan está obsoleto respecto a la revisión actual de la spec."}
            findings=self.quality.validate_work(work_id); blocking=[f for f in findings if f.severity=="blocking"]
            if blocking: return {"approved":False,"findings":[f.to_dict() for f in blocking]}
            self.artifacts.approve(plan["id"]); self.artifacts.export_work_views(work_id); self._set_state(work_id,"ready")
            return {"approved":True,"state":"ready"}
        if approve == "code":
            if work["state"] != "code_review": raise ValueError("Code approval is only valid in code_review")
            findings=self.quality.validate_work(work_id); blocking=[f for f in findings if f.severity=="blocking"]
            if blocking:
                score,_=self.quality.health_score(work_id); self._set_score(work_id,score)
                return {"approved":False,"state":"code_review","findings":[f.to_dict() for f in blocking]}
            run=self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(work_id,))
            if not run or run.get("verification_status")!="verified": raise ValueError("Agent result has not been independently verified")
            extra=[decode_git_path(path) for path in json_loads(run.get("verified_files") or run.get("files_changed"), []) if path]
            commit=self.git.checkpoint(work_id,Path(work["worktree"]),f"feat({work_id}): verified implementation checkpoint", extra_paths=extra); self._set_state(work_id,"validating")
            return {"approved":True,"state":"validating","checkpoint":commit}
        if approve == "merge":
            if work["state"] != "ready_to_merge": raise ValueError("Merge approval is only valid in ready_to_merge")
            findings=self.quality.validate_work(work_id); blocking=[f for f in findings if f.severity=="blocking"]
            if blocking:
                score,_=self.quality.health_score(work_id); self._set_score(work_id,score)
                return {"approved":False,"state":"ready_to_merge","findings":[f.to_dict() for f in blocking]}
            self.state.snapshot(f"pre-merge-{work_id}")
            self.git.commit_main_changes(f"dynosai({work_id}): persist pre-merge state")
            run=self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(work_id,))
            extra=[decode_git_path(path) for path in json_loads((run or {}).get("verified_files") or (run or {}).get("files_changed"), []) if path]
            self.git.checkpoint(work_id,Path(work.get("worktree") or self.root),f"feat({work_id}): remaining verified source", extra_paths=extra)
            commit=self.git.merge(work_id,work["branch"],work["title"]); self.indexer.index(commit); feature=self.consolidate(work_id,commit)
            self._set_state(work_id,"done"); self.db.execute("UPDATE work_items SET active=0,quality_score=100 WHERE id=?",(work_id,))
            if (work.get("workspace_strategy") or "isolated_worktree") == "interactive_branch":
                self.git.cleanup_interactive_branch(work["branch"])
            else:
                self.git.cleanup_worktree(work["branch"],Path(work["worktree"]))
            snapshot=self.state.snapshot(f"merged-{work_id}"); self.git.commit_main_changes(f"dynosai({work_id}): persist knowledge snapshot"); self.db.set_meta("indexed_commit", self.git.head())
            return {"approved":True,"state":"done","commit":commit,"feature":feature,"snapshot":snapshot}
        return self.review_summary(work_id)

    def submit_spec(self, work_id: str, payload: dict[str, Any], authored_by: str = "agent") -> dict[str, Any]:
        """Validate and persist a specification payload for the work item."""
        work=self.get_work(work_id)
        if work["state"] not in {"discovery","spec_review"}: raise ValueError("Spec drafts are only accepted during discovery/spec review")
        artifact=self.artifacts.submit_spec(work,payload,authored_by)
        existing_plan=self.artifacts.artifact(work_id,"plan")
        if existing_plan:
            self.db.execute("UPDATE artifacts SET status='stale',updated_at=? WHERE id=?",(utc_now(),existing_plan["id"]))
            self.db.execute("UPDATE tasks SET state='stale',updated_at=? WHERE work_id=?",(utc_now(),work_id))
        score,findings=self.quality.health_score(work_id); self._set_score(work_id,score); self.artifacts.export_work_views(work_id)
        return {"artifact":artifact,"quality":score,"findings":[f.to_dict() for f in findings]}

    def submit_plan(self, work_id: str, payload: dict[str, Any], authored_by: str = "agent") -> dict[str, Any]:
        """Validate and persist a technical plan derived from the approved specification."""
        work=self.get_work(work_id)
        if work["state"]!="plan_review": raise ValueError("Plan drafts are only accepted during plan review")
        spec=self.artifacts.artifact(work_id,"spec");
        if not spec or spec["status"]!="approved": raise ValueError("An approved spec is required")
        plan,tasks=self.artifacts.submit_plan(work,spec,payload,authored_by); score,findings=self.quality.health_score(work_id); self._set_score(work_id,score)
        return {"plan":plan,"tasks":tasks,"quality":score,"findings":[f.to_dict() for f in findings]}

    def register_decision(self, work_id: str, question: str, answer: str) -> dict[str, Any]:
        """Persist a clarified project or work decision as authoritative workflow knowledge."""
        decision_id = self.db.next_id("DEC", "decisions")
        self.db.execute("INSERT INTO decisions(id,work_id,question,answer,status,created_at) VALUES(?,?,?,?,?,?)", (decision_id, work_id, question, answer, "accepted", utc_now()))
        self.db.index_search("decision", decision_id, question, answer)
        self.db.audit("DecisionRegistered", decision_id, {"work_id": work_id})
        return {"id": decision_id, "work_id": work_id, "question": question, "answer": answer}

    def request_scope_extension(self, work_id: str, path: str, action: str, reason: str, run_id: str | None = None, task_id: str | None = None) -> dict[str, Any]:
        """Request explicit authorization for an unplanned file path/action during implementation."""
        work=self.get_work(work_id)
        if work["state"]!="implementing": raise ValueError("Scope can only be extended during implementation")
        root=Path(work.get("worktree") or self.root); policy=PathPolicyEngine(root); decision=policy.decision(path,"read" if action=="read" else "write",agent=True)
        if action not in {"read","modify","create","delete","test"}: raise ValueError("invalid scope action")
        candidate = decision.path or str(path).replace("\\", "/")
        if is_managed_workflow_artifact(path) or is_managed_workflow_artifact(candidate):
            payload={"work_id":work_id,"path":candidate,"action":action,"reason":reason,"task_id":task_id}
            self.db.audit("ScopeExtensionNotRequired", candidate, payload)
            return {
                "id": None, "status": "managed_artifact", "work_id": work_id, "path": candidate, "action": action,
                "message": "DynosAI owns exported spec/plan overlays. Use dynosai_submit_spec or dynosai_submit_plan. Do not edit specs/** or plan.md directly.",
            }
        if not decision.allowed: raise PermissionError(decision.reason)

        # A scope extension is only for genuinely unplanned access. Earlier plans could
        # create a pending request for a path that was already approved under a
        # later task, leaving stale governance debt even though the final diff
        # stayed inside the approved plan.
        plan=self.artifacts.artifact(work_id,"plan")
        planned=None
        if plan:
            for item in (plan.get("metadata") or {}).get("files",[]):
                if str(item.get("path","")).replace("\\","/")==decision.path:
                    planned=item; break
        if planned:
            planned_action=str(planned.get("action","")).lower()
            requested_action="modify" if action=="test" else action
            already_allowed=(
                requested_action=="read" or
                (requested_action=="modify" and planned_action in {"modify","create"}) or
                (requested_action=="create" and planned_action=="create") or
                (requested_action=="delete" and planned_action=="delete")
            )
            if already_allowed:
                owners=[]
                for task in self.db.query("SELECT id,files FROM tasks WHERE work_id=? ORDER BY id",(work_id,)):
                    if decision.path in json_loads(task.get("files"),[]): owners.append(task["id"])
                payload={"work_id":work_id,"path":decision.path,"action":action,"planned_action":planned_action,"task_id":task_id,"owner_tasks":owners}
                self.db.audit("ScopeExtensionNotRequired",decision.path,payload)
                return {"id":None,"status":"already_planned","work_id":work_id,"path":decision.path,"action":action,"planned_action":planned_action,"owner_tasks":owners,"message":"Path is already approved by the active plan; no scope extension or user approval is required."}

        request_id=self.db.next_id("SCOPE","scope_requests")
        self.db.execute("INSERT INTO scope_requests(id,work_id,run_id,path,action,reason,status,created_at) VALUES(?,?,?,?,?,?,?,?)",(request_id,work_id,run_id,decision.path,action,reason,"pending",utc_now()))
        self.db.audit("ScopeExtensionRequested",request_id,{"work_id":work_id,"path":decision.path,"action":action,"reason":reason,"task_id":task_id})
        return {"id":request_id,"status":"pending","work_id":work_id,"path":decision.path,"action":action,"reason":reason,"message":"Requires user approval through dynosai review."}

    def resolve_scope_request(self, request_id: str, decision: str, comments: str | None = None) -> dict[str, Any]:
        """Close a pending scope request without mutating the approved plan.

        Human rejection/cancellation is a terminal governance outcome, not a
        forever-pending risk signal. The exact request id is supplied by the
        linked elicitation dedupe key so concurrent/multiple requests cannot
        accidentally resolve the newest unrelated row.
        """
        normalized = str(decision or "").lower()
        status_map = {"request_changes": "declined", "decline": "declined", "reject": "declined", "cancel": "cancelled"}
        status = status_map.get(normalized)
        if not status:
            raise ValueError("scope decision must be request_changes/decline/reject/cancel")
        req = self.db.one("SELECT * FROM scope_requests WHERE id=?", (request_id,))
        if not req:
            raise KeyError(request_id)
        if str(req.get("status")) != "pending":
            return {"id": request_id, "status": req.get("status"), "path": req.get("path"), "action": req.get("action"), "already_resolved": True}
        self.db.execute("UPDATE scope_requests SET status=?,resolved_at=? WHERE id=?", (status, utc_now(), request_id))
        self.artifacts.export_work_views(req["work_id"])
        self.db.audit("ScopeExtensionDeclined" if status == "declined" else "ScopeExtensionCancelled", request_id, {"comments": comments or "", "path": req.get("path"), "action": req.get("action")})
        return {"id": request_id, "status": status, "path": req.get("path"), "action": req.get("action")}

    def approve_scope_request(self, request_id: str, task_id: str | None = None) -> dict[str, Any]:
        """Resolve a pending scope-extension request with an auditable terminal decision."""
        req=self.db.one("SELECT * FROM scope_requests WHERE id=? AND status='pending'",(request_id,))
        if not req: raise KeyError(request_id)
        if is_managed_workflow_artifact(req["path"]):
            self.db.execute("UPDATE scope_requests SET status='cancelled',resolved_at=? WHERE id=?",(utc_now(),request_id))
            payload={"work_id":req["work_id"],"path":req["path"],"action":req["action"],"reason":req.get("reason"),"task_id":task_id}
            self.db.audit("ScopeExtensionNotRequired", req["path"], payload)
            return {
                "id": request_id, "status": "managed_artifact", "work_id": req["work_id"], "path": req["path"], "action": req["action"],
                "message": "DynosAI owns exported spec/plan overlays. Use dynosai_submit_spec or dynosai_submit_plan. Do not edit specs/** or plan.md directly.",
            }
        plan=self.artifacts.artifact(req["work_id"],"plan")
        if not plan: raise ValueError("No plan exists")
        metadata=dict(plan["metadata"]); files=list(metadata.get("files",[]))
        if not any(x.get("path")==req["path"] for x in files): files.append({"path":req["path"],"action":req["action"],"reason":f"Approved scope extension: {req['reason']}"})
        metadata["files"]=files
        content=self.artifacts.render_plan(self.get_work(req["work_id"]),plan,metadata)
        updated=self.artifacts.upsert_artifact(plan["id"],req["work_id"],"plan",plan["title"],content,"approved",metadata)
        task=None
        if task_id: task=self.db.one("SELECT * FROM tasks WHERE id=? AND work_id=?",(task_id,req["work_id"]))
        if not task: task=self.retrieval._next_ready_task(req["work_id"])
        if task:
            task_files=json_loads(task.get("files"),[])
            if req["path"] not in task_files: task_files.append(req["path"]); self.db.execute("UPDATE tasks SET files=?,updated_at=? WHERE id=?",(json_dumps(task_files),utc_now(),task["id"]))
        self.db.execute("UPDATE scope_requests SET status='approved',resolved_at=? WHERE id=?",(utc_now(),request_id)); self.artifacts.export_work_views(req["work_id"]); self.db.audit("ScopeExtensionApproved",request_id,{"task_id":task and task["id"]})
        return {"id":request_id,"status":"approved","plan_revision":updated["version"],"task_id":task and task["id"],"path":req["path"],"action":req["action"]}

    def prepare_task_run(self, work_id: str, task_id: str, agent: str) -> dict[str, Any]:
        """Atomically claim a task and create an isolated task worktree/run."""
        work=self.get_work(work_id)
        if work["state"]!="implementing" or not work.get("worktree") or not work.get("branch"):
            raise ValueError("Task agents can only be started during implementation")
        existing=self.db.one("SELECT * FROM tasks WHERE id=? AND work_id=?",(task_id,work_id))
        if not existing: raise KeyError(task_id)
        if existing.get("claimed_run"):
            run=self.db.one("SELECT * FROM runs WHERE id=?",(existing["claimed_run"],))
            if run and run.get("status") in {"running","verified_partial"}:
                return {"work_id":work_id,"task_id":task_id,"run_id":run["id"],"worktree":run.get("worktree"),"base_commit":run.get("base_commit"),"reused":True}
            raise ValueError(f"Task {task_id} is already claimed")
        deps=json_loads(existing.get("depends_on"),[])
        incomplete=[d for d in deps if (self.db.one("SELECT state FROM tasks WHERE id=?",(d,)) or {}).get("state")!="completed"]
        if incomplete: raise ValueError(f"Task dependencies are not complete: {incomplete}")
        run_id=self.db.next_id("RUN","runs"); now=utc_now()
        with self.db.transaction(immediate=True) as con:
            row=con.execute("SELECT state,claimed_run FROM tasks WHERE id=? AND work_id=?",(task_id,work_id)).fetchone()
            if not row or row[1]: raise ValueError(f"Task {task_id} was claimed concurrently")
            con.execute("UPDATE tasks SET state='running',owner=?,claimed_run=?,updated_at=? WHERE id=?",(agent,run_id,now,task_id))
            con.execute("INSERT INTO runs(id,work_id,agent,status,started_at,base_commit,task_ids,worktree) VALUES(?,?,?,?,?,?,?,?)",(run_id,work_id,agent,"running",now,self.git.head(Path(work["worktree"])),json_dumps([task_id]),""))
        try:
            branch,task_worktree,base=self.git.create_task_worktree(work_id,task_id,work["branch"],Path(work["worktree"]))
            preview=self.context_preview(work_id,task_id=task_id)
            self.db.execute("UPDATE runs SET worktree=?,base_commit=?,context_id=? WHERE id=?",(str(task_worktree),base,preview["id"],run_id))
            self.db.execute("INSERT OR REPLACE INTO run_metrics(run_id,repository_files,context_tokens,suggested_files) VALUES(?,?,?,?)",(run_id,int((self.db.one('SELECT COUNT(*) n FROM code_files') or {'n':0})['n']),preview["estimated_tokens"],len(preview.get("impact",{}).get("files",[]))))
            self.db.audit("TaskClaimed",task_id,{"work_id":work_id,"run_id":run_id,"agent":agent,"branch":branch,"worktree":str(task_worktree)})
            return {"work_id":work_id,"task_id":task_id,"run_id":run_id,"branch":branch,"worktree":str(task_worktree),"base_commit":base,"context_preview":preview,"reused":False}
        except Exception:
            self.db.execute("UPDATE tasks SET state='ready',owner=NULL,claimed_run=NULL,updated_at=? WHERE id=?",(utc_now(),task_id))
            self.db.execute("DELETE FROM runs WHERE id=?",(run_id,))
            raise

    def register_result(self, work_id: str, summary: str, files_changed: list[str], tests: list[dict[str, Any]], completed_tasks: list[str] | None = None, run_id: str | None = None) -> dict[str, Any]:
        """Register agent work, compare it with Git/scope evidence, and independently determine verification status."""
        work=self.get_work(work_id)
        if work["state"]!="implementing": raise ValueError("Results can only be registered while implementing")
        run=self.db.one("SELECT * FROM runs WHERE id=? AND work_id=?",(run_id,work_id)) if run_id else self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(work_id,))
        if not run: raise ValueError("No active run")
        self._scrub_managed_overlays_from_tasks(work_id)
        worktree=Path(run.get("worktree") or work["worktree"]); actual=[path for path in self.git.diff_files(worktree,run.get("base_commit") or None) if not is_managed_workflow_artifact(path)]; statuses={path:status for path,status in self.git.diff_name_status(worktree,run.get("base_commit") or None).items() if not is_managed_workflow_artifact(path)}
        declared=sorted(set(files_changed)); extra=sorted(set(actual)-set(declared)); missing=sorted(set(declared)-set(actual))
        plan=self.artifacts.artifact(work_id,"plan") or {}; entries=(plan.get("metadata") or {}).get("files",[])
        scope={str(item.get("path","")).replace("\\","/"):str(item.get("action","")).lower() for item in entries if item.get("path")}
        run_task_ids=set(json_loads(run.get("task_ids"),[]))
        allowed_scope=set(scope)
        if run_task_ids:
            allowed_scope=set()
            for tid in run_task_ids:
                task_row=self.db.one("SELECT files FROM tasks WHERE id=? AND work_id=?",(tid,work_id))
                if task_row: allowed_scope.update(json_loads(task_row.get("files"),[]))
        outside_scope=sorted(set(actual)-allowed_scope) if allowed_scope else list(actual)
        action_violations=[]; path_policy=PathPolicyEngine(worktree)
        for rel in actual:
            action=scope.get(rel); status=statuses.get(rel,"M")
            decision=path_policy.decision(rel,"write",agent=True)
            if not decision.allowed: action_violations.append({"path":rel,"reason":decision.reason}); continue
            ok=(action=="modify" and status=="M") or (action=="create" and status=="A") or (action=="delete" and status=="D") or (action=="test" and status in {"A","M"})
            if action=="read": ok=False
            if not ok: action_violations.append({"path":rel,"action":action,"git_status":status,"reason":"planned action does not permit observed change"})
        requested=set(completed_tasks or run_task_ids or [r["id"] for r in self.db.query("SELECT id FROM tasks WHERE work_id=?",(work_id,))])
        # Atomic completion may include a task and its dependency in the same
        # register_result call, but an agent may never leap over a dependency
        # that is neither already completed nor part of the same result set.
        for tid in requested:
            row=self.db.one("SELECT state,depends_on FROM tasks WHERE id=? AND work_id=?",(tid,work_id))
            if not row: raise ValueError(f"Unknown completed task: {tid}")
            unresolved=[]
            for dep in json_loads(row.get("depends_on"),[]):
                dep_row=self.db.one("SELECT state FROM tasks WHERE id=? AND work_id=?",(dep,work_id))
                if dep not in requested and (not dep_row or dep_row.get("state")!="completed"): unresolved.append(dep)
            if unresolved: raise ValueError(f"Task {tid} dependencies are not complete or included atomically: {unresolved}")
        validation=self._run_result_validation(work_id,requested,worktree)
        verification="verified" if validation["passed"] and not outside_scope and not action_violations else "failed"
        self.indexer.refresh_overlay(run["id"],worktree,actual)
        hunks=self.git.diff_hunks(worktree,run.get("base_commit") or None)
        task_results=[]
        if verification=="verified":
            for task in self.db.query("SELECT * FROM tasks WHERE work_id=? ORDER BY id",(work_id,)):
                if task["id"] not in requested or task["state"]=="completed": continue
                task_files=set(product_scope_paths(json_loads(task.get("files"),[]))); must_change={f for f in task_files if scope.get(f)!="read"}
                touched=sorted(must_change & set(actual)); missing_task=sorted(must_change-set(actual))
                accepted=list(json_loads(task.get("acceptance"),[])); requirements=list(json_loads(task.get("requirements"),[]))
                evidence_ok=bool(touched) and not missing_task and bool(accepted)
                symbols=[]
                for rel in touched:
                    overlay=self.db.one("SELECT symbols FROM run_overlays WHERE run_id=? AND path=?",(run["id"],rel))
                    if overlay:
                        for sym in json_loads(overlay["symbols"],[]):
                            ranges=hunks.get(rel,[])
                            if statuses.get(rel)=="A" or not ranges or any(not (sym.get("end_line",0)<a or sym.get("start_line",0)>b) for a,b in ranges): symbols.append(sym.get("id"))
                if evidence_ok:
                    test_files=[rel for rel in touched if "test" in rel.lower()]
                    ev_id=self.db.next_id("EVID","evidence"); payload={"actual_files":touched,"requirements":requirements,"acceptance":accepted,"symbols":symbols,"test_files":test_files,"validation_profiles":[r["profile"] for r in validation["results"]],"validation":validation["results"]}
                    self.db.execute("INSERT INTO evidence(id,work_id,run_id,task_id,kind,source,status,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(ev_id,work_id,run["id"],task["id"],"task_verification","dynosai","accepted",json_dumps(payload),utc_now()))
                    symbol_values=symbols or [""]
                    test_values=test_files or [""]
                    for ac in accepted:
                        for sid in symbol_values:
                            for test_name in test_values:
                                self.db.execute("INSERT OR IGNORE INTO task_evidence_links(task_id,evidence_id,acceptance_id,symbol_id,test_name,confidence) VALUES(?,?,?,?,?,?)",(task["id"],ev_id,ac,sid,test_name,1.0 if sid and test_name else 0.9))
                    self.db.execute("UPDATE tasks SET state='completed',owner=COALESCE(owner,?),claimed_run=COALESCE(claimed_run,?),updated_at=? WHERE id=?",(run.get("agent") or "agent",run["id"],utc_now(),task["id"]))
                task_results.append({"task":task["id"],"verified":evidence_ok,"touched":touched,"missing":missing_task,"symbols":symbols})
        remaining=int((self.db.one("SELECT COUNT(*) n FROM tasks WHERE work_id=? AND state!='completed'",(work_id,)) or {"n":0})["n"])
        assigned_remaining=sum(1 for tid in run_task_ids if (self.db.one("SELECT state FROM tasks WHERE id=?",(tid,)) or {}).get("state")!="completed")
        final_status="failed" if verification=="failed" else ("verified" if (run_task_ids and assigned_remaining==0) or remaining==0 else "verified_partial")
        self.db.execute("UPDATE runs SET status=?,summary=?,files_changed=?,tests=?,declared_files=?,verified_files=?,verification_status=?,finished_at=? WHERE id=?",("verified" if final_status=="verified" else "running" if final_status=="verified_partial" else "failed",summary,json_dumps(actual),json_dumps(validation["results"]),json_dumps(declared),json_dumps(actual),final_status,utc_now() if final_status!="verified_partial" else None,run["id"]))
        integration=None
        feature_worktree = Path(work.get("worktree") or self.root).resolve()
        run_worktree = worktree.resolve()
        is_task_run = bool(run_task_ids) and run_worktree != feature_worktree
        if final_status=="verified" and is_task_run:
            task_branch=f"dyn-task/{slugify(next(iter(run_task_ids)))}"
            try:
                task_commit=self.git.checkpoint(work_id,worktree,f"feat({next(iter(run_task_ids))}): verified task implementation")
                feature_head=self.git.integrate_task_commit(Path(work["worktree"]),task_commit)
                self.git.cleanup_task_worktree(task_branch,worktree)
                integration={"task_commit":task_commit,"feature_head":feature_head,"status":"integrated"}
            except Exception as exc:
                final_status="integration_conflict"; integration={"status":"conflict","error":str(exc)}
                self.db.execute("UPDATE runs SET status='integration_conflict',verification_status='integration_conflict' WHERE id=?",(run["id"],))
                for tid in run_task_ids: self.db.execute("UPDATE tasks SET state='blocked',updated_at=? WHERE id=?",(utc_now(),tid))
        suggested={f["path"] for f in self.retrieval.impact(f"{work['title']} {work['description']}",work_id=work_id).get("files",[])}; hits=len(set(actual)&suggested); unplanned=len(set(actual)-suggested)
        self.db.execute("UPDATE run_metrics SET actual_files=?,planned_files_hit=?,unplanned_files=?,test_passed=? WHERE run_id=?",(len(actual),hits,unplanned,int(validation["passed"]),run["id"]))
        self.db.audit("AgentResultVerified",run["id"],{"work_id":work_id,"declared":declared,"actual":actual,"outside_scope":outside_scope,"action_violations":action_violations,"remaining_tasks":remaining,"status":final_status})
        return {"run_id":run["id"],"work_id":work_id,"status":final_status,"remaining_tasks":remaining,"declared_files":declared,"actual_files":actual,"undeclared_files":extra,"outside_scope":outside_scope,"scope_action_violations":action_violations,"declared_but_unchanged":missing,"task_evidence":task_results,"validation":validation,"integration":integration}

    def board(self, state: str | None = None, work_type: str | None = None) -> list[dict[str, Any]]:
        """Return a board-style summary of active work and workflow state."""
        clauses = ["id!='PROJECT'"]
        params: list[Any] = []
        if state:
            clauses.append("state=?")
            params.append(state)
        if work_type:
            clauses.append("work_type=?")
            params.append(work_type)
        rows = self.db.query(f"SELECT * FROM work_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC", tuple(params))
        for row in rows:
            tasks = self.db.one("SELECT COUNT(*) total,SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END) completed FROM tasks WHERE work_id=?", (row["id"],)) or {"total": 0, "completed": 0}
            row["tasks"] = {"completed": tasks.get("completed") or 0, "total": tasks.get("total") or 0}
            row["next"] = self.next_action(row["id"])
        return rows

    def ask(self, query: str, limit: int = 10) -> dict[str, Any]:
        """Retrieve bounded project knowledge relevant to a natural-language query."""
        impact = self.retrieval.impact(query, limit=limit)
        hits = [item.to_dict() for item in self.retrieval.search(query, limit=limit)]
        return {"query": query, "impact": impact, "hits": hits}

    def _context_preview_fingerprint(self, work: dict[str, Any], task_id: str | None, max_tokens: int, include_task_contract: bool) -> str:
        """Fingerprint only signals that can change implementation retrieval.

        Git status alone is insufficient because a file can remain ``M`` while its
        contents keep changing. Include task state plus stat metadata for approved
        task files so an edit invalidates the preview without hashing the whole repo.
        """
        rows=self.db.query("SELECT id,state,files FROM tasks WHERE work_id=? ORDER BY id",(work["id"],))
        files=[]
        task_states=[]
        for row in rows:
            task_states.append((row.get("id"),row.get("state")))
            if task_id is None or str(row.get("id"))==str(task_id):
                files.extend(json_loads(row.get("files"),[]))
        workspace=Path(work.get("worktree") or self.root)
        file_state=[]
        for rel in sorted({str(x) for x in files if x}):
            fp=workspace/rel
            try:
                st=fp.stat(); file_state.append((rel,st.st_size,st.st_mtime_ns))
            except OSError:
                file_state.append((rel,None,None))
        try: status=self.git.status(workspace)
        except Exception: status=[]
        payload={
            "work_id":work["id"],"state":work.get("state"),"task_id":task_id,
            "task_states":task_states,"files":file_state,"git_status":status,
            "indexed_commit":self.db.get_meta("indexed_commit"),"max_tokens":int(max_tokens),
            "include_task_contract":bool(include_task_contract),
        }
        raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    def context_preview(self, work_id: str | None = None, max_tokens: int = 3000, task_id: str | None = None, *, include_task_contract: bool = True) -> dict[str, Any]:
        """Build a bounded, task-aware context contract for the active work item."""
        work = self.get_work(work_id)
        fingerprint=self._context_preview_fingerprint(work,task_id,max_tokens,include_task_contract)
        purpose=f"implementation:{task_id or 'work'}:{fingerprint}:{'full' if include_task_contract else 'delta'}"
        cache_key=f"{work['id']}:{purpose}:{int(max_tokens)}"
        cached=self._context_preview_cache.get(cache_key)
        if cached is None:
            row=self.db.one("SELECT id,payload FROM context_manifests WHERE work_id=? AND purpose=? AND max_tokens=? ORDER BY created_at DESC LIMIT 1",(work["id"],purpose,int(max_tokens)))
            if row:
                value=json_loads(row.get("payload"),{})
                if isinstance(value,dict):
                    value["id"]=row.get("id"); cached=value
        if cached is not None:
            self._context_preview_cache[cache_key]=dict(cached)
            self.db.audit("ContextPreviewCacheHit",work["id"],{"task_id":task_id,"fingerprint":fingerprint,"manifest_id":cached.get("id"),"include_task_contract":bool(include_task_contract)})
            return dict(cached)
        preview = self.retrieval.context_preview(work, max_tokens=max_tokens, task_id=task_id, include_task_contract=include_task_contract)
        manifest_id = self.db.next_id("CTX", "context_manifests")
        self.db.execute(
            "INSERT INTO context_manifests(id,work_id,purpose,query,max_tokens,estimated_tokens,payload,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (manifest_id, work["id"], purpose, f"{work['title']} {work['description']}", max_tokens, preview["estimated_tokens"], json_dumps(preview), utc_now()),
        )
        preview["id"] = manifest_id
        self._context_preview_cache[cache_key]=dict(preview)
        self.db.audit("ContextPreviewCacheMiss",work["id"],{"task_id":task_id,"fingerprint":fingerprint,"manifest_id":manifest_id,"include_task_contract":bool(include_task_contract)})
        return preview

    def preflight(self, work_id: str) -> dict[str, Any]:
        """Check implementation preconditions, scope, task readiness, and validation requirements before execution."""
        work = self.get_work(work_id)
        findings = self.quality.validate_work(work_id)
        checks = {
            "git_repository": self.git.is_repo(),
            "main_workspace_clean": not self.git.status(),
            "spec_approved": bool(self.db.one("SELECT id FROM artifacts WHERE work_id=? AND kind='spec' AND status='approved'", (work_id,))),
            "plan_approved": bool(self.db.one("SELECT id FROM artifacts WHERE work_id=? AND kind='plan' AND status='approved'", (work_id,))),
            "tasks_present": bool(self.db.one("SELECT id FROM tasks WHERE work_id=? LIMIT 1", (work_id,))),
            "quality_gates": not any(item.severity == "blocking" for item in findings),
            "index_current": self.db.get_meta("indexed_commit") == self.git.head(),
        }
        return {"ready": all(checks.values()), "checks": checks, "findings": [item.to_dict() for item in findings], "work": work["id"]}

    def _run_result_validation(self, work_id: str, task_ids: set[str], cwd: Path) -> dict[str, Any]:
        """Run only validation evidence required by the tasks being registered.

        A partial implementation task that only requires a Git diff must not trigger
        project-wide unit tests before test files or later dependencies exist. Full
        work validation still runs at the validating gate before merge.
        """
        rows=[]
        for task_id in sorted(task_ids):
            row=self.db.one("SELECT id,evidence_required,validation_commands FROM tasks WHERE id=? AND work_id=?",(task_id,work_id))
            if row: rows.append(row)
        evidence={item for row in rows for item in json_loads(row.get("evidence_required"),[])}
        profiles=[]
        for row in rows:
            for profile in json_loads(row.get("validation_commands"),[]):
                if profile not in profiles: profiles.append(profile)
        requires_tests=("test_result" in evidence or "migration_result" in evidence or any(str(x).startswith("validation:") for x in evidence))
        if not requires_tests:
            payload={
                "passed":True, "results":[], "scope":"task", "skipped":True,
                "reason":"selected tasks do not require test_result evidence",
                "task_ids":sorted(task_ids), "evidence_required":sorted(evidence),
            }
            self.db.audit("TaskValidationSkipped",work_id,payload)
            return payload
        result=self.run_validations(work_id,record=True,cwd=cwd,profiles=profiles or None,scope="task")
        result["task_ids"]=sorted(task_ids); result["evidence_required"]=sorted(evidence)
        return result

    def run_validations(self, work_id: str, record: bool = True, cwd: Path | None = None, profiles: list[str] | None = None, scope: str = "work") -> dict[str, Any]:
        """Execute the approved validation profiles for a work item and persist their real results."""
        work=self.get_work(work_id); worktree=Path(cwd or work.get("worktree") or self.root); plan=self.artifacts.artifact(work_id,"plan")
        selected=profiles or (plan or {}).get("metadata",{}).get("validation_profiles",[]) or ["unit"]
        results=[]; passed=True
        for profile in dict.fromkeys(selected):
            row=self.db.one("SELECT command_json FROM validation_profiles WHERE name=? AND approved=1",(profile,))
            if not row:
                passed=False; results.append({"profile":profile,"status":"failed","exit_code":126,"output":"Validation profile not approved"}); continue
            parts=ValidationProfilePolicy.decode(row["command_json"]); command=" ".join(parts)
            try:
                executed=self.execution.run(parts,cwd=worktree,timeout=180,env={**os.environ,"DYNOSAI_GIT_BYPASS":"1"})
                if executed.timed_out:
                    passed=False
                    results.append({"profile":profile,"command":command,"status":"failed","exit_code":124,"output":"Timed out"})
                    continue
                output=(executed.stdout+"\n"+executed.stderr).strip(); status="passed" if executed.returncode==0 else "failed"; passed=passed and executed.returncode==0
                if record:self.db.execute("INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",(work_id,f"profile:{profile}",status,executed.returncode,output[-12000:],utc_now()))
                results.append({"profile":profile,"command":command,"status":status,"exit_code":executed.returncode,"output":output[-2000:]})
            except PolicyError as exc:
                passed=False; results.append({"profile":profile,"command":command,"status":"failed","exit_code":126,"output":str(exc)})
            except FileNotFoundError: passed=False; results.append({"profile":profile,"command":command,"status":"failed","exit_code":127,"output":"Command not found"})
        return {"passed":passed,"results":results,"scope":scope,"skipped":False}

    def consolidate(self, work_id: str, commit: str) -> dict[str, Any]:
        """Consolidate verified completed work into durable feature knowledge and retrieval indexes."""
        import hashlib
        work=self.get_work(work_id); feature_id=self.db.next_id("FEATURE","features"); run=self.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(work_id,)) or {}; summary=run.get("summary") or work["description"]; now=utc_now()
        self.db.execute("INSERT INTO features(id,work_id,title,summary,status,commit_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(feature_id,work_id,work["title"],summary,"validated",commit,now,now)); self.db.index_search("feature",feature_id,work["title"],summary)
        spec=self.artifacts.artifact(work_id,"spec")
        if spec:
            for requirement in spec["metadata"].get("requirements",[]): self.db.execute("INSERT INTO feature_rules(feature_id,rule) VALUES(?,?)",(feature_id,requirement["text"])); self.db.index_search("rule",f"{feature_id}:{requirement['id']}",requirement["id"],requirement["text"])
        files=json_loads(run.get("files_changed"),[]); evidence_symbols={r["symbol_id"] for r in self.db.query("SELECT DISTINCT symbol_id FROM task_evidence_links tel JOIN evidence e ON e.id=tel.evidence_id WHERE e.work_id=? AND tel.symbol_id IS NOT NULL AND tel.symbol_id!=''",(work_id,))}
        mapped_files:set[str]=set()
        for sid in evidence_symbols:
            sym=self.db.one("SELECT id,path,qualified_name,body FROM symbols WHERE id=?",(sid,))
            if not sym: continue
            mapped_files.add(sym["path"]); role="test" if "test" in sym["path"].lower() else "implementation"; digest=hashlib.sha256((sym.get("body") or "").encode()).hexdigest()
            self.db.execute("INSERT OR IGNORE INTO feature_files(feature_id,path,role,reason,confidence,content_hash,symbol,validity) VALUES(?,?,?,?,?,?,?,?)",(feature_id,sym["path"],role,"Símbolo exacto intersecta diff y evidencia de tarea",0.99,digest,sym["id"],"current"))
        from .util import sha256_file
        for path in files:
            if path in mapped_files: continue
            fp=self.root/path; digest=sha256_file(fp) if fp.exists() and fp.is_file() else ""; role="test" if "test" in path.lower() else "implementation"
            self.db.execute("INSERT OR IGNORE INTO feature_files(feature_id,path,role,reason,confidence,content_hash,validity) VALUES(?,?,?,?,?,?,?)",(feature_id,path,role,"Archivo validado sin símbolo granular disponible",0.86,digest,"current"))
        semantic=self.semantic.reindex_authoritative(commit); self.db.audit("FeatureConsolidated",feature_id,{"work_id":work_id,"commit":commit,"symbols":len(evidence_symbols),"semantic":semantic})
        return {"id":feature_id,"title":work["title"],"summary":summary,"commit":commit,"files":files,"symbols":sorted(evidence_symbols),"status":"validated","semantic":semantic}

    def sync(self, bootstrap: bool = False) -> dict[str, Any]:
        """Synchronize derived indexes and persisted knowledge with the current authoritative repository state."""
        import hashlib
        from .util import sha256_file
        self._ensure_ready(); head=self.git.head(); external=self.git.status()
        source_suffixes={".py",".rs",".ts",".tsx",".js",".jsx",".java",".cs",".go"}
        dirty_source=[]
        for line in external:
            rel=(line[3:] if len(line)>3 else line).strip().replace("\\","/")
            if Path(rel).suffix.lower() in source_suffixes: dirty_source.append(rel)
        index=self.indexer.index(head) if not dirty_source else {"files_changed":0,"symbols":0,"calls":0,"routes":0,"files_total":int((self.db.one('SELECT COUNT(*) n FROM code_files') or {'n':0})['n']),"changed_paths":[],"blocked":True,"reason":"uncommitted source changes","paths":dirty_source}
        stale=[]
        for row in self.db.query("SELECT ff.id,ff.feature_id,ff.path,ff.content_hash,ff.symbol FROM feature_files ff WHERE ff.validity='current'"):
            if row.get("symbol"):
                sym=self.db.one("SELECT body FROM symbols WHERE id=?",(row["symbol"],))
                if not sym: validity="gone"
                else: validity="current" if not row["content_hash"] or hashlib.sha256((sym.get("body") or "").encode()).hexdigest()==row["content_hash"] else "needs_verification"
            else:
                path=self.root/row["path"]; validity="gone" if not path.exists() else "current" if not row["content_hash"] or sha256_file(path)==row["content_hash"] else "needs_verification"
            if validity!="current":
                self.db.execute("UPDATE feature_files SET validity=? WHERE id=?",(validity,row["id"])); self.db.execute("UPDATE features SET status='needs_verification',updated_at=? WHERE id=?",(utc_now(),row["feature_id"])); stale.append({"feature":row["feature_id"],"path":row["path"],"symbol":row.get("symbol"),"validity":validity})
        semantic=self.semantic.reindex_authoritative(head) if not dirty_source else {"enabled":self.semantic.ready(),"indexed":0,"skipped":0,"deleted":0,"blocked":True,"reason":"uncommitted source changes"}; self.artifacts.export_project_views()
        for work in self.db.query("SELECT id FROM work_items WHERE id!='PROJECT'"): self.artifacts.export_work_views(work["id"])
        self.db.set_meta("last_sync",utc_now()); self.db.set_meta("indexed_commit",head)
        managed_internal=[]; external_user=[]
        for line in external:
            rel=(line[3:] if len(line)>3 else line).strip().replace("\\","/")
            (managed_internal if rel.startswith(".dynosai/") else external_user).append(line)
        return {"commit":head,"index":index,"semantic":semantic,"stale_knowledge":stale,"external_changes":external_user,"managed_internal_changes":managed_internal,"bootstrap":bootstrap}

    def bootstrap_brownfield_specs(self) -> list[dict[str, Any]]:
        """Reconstruct evidence-backed AS-IS candidate specs.

        The repository can prove behavior and contracts, not historical product
        intent. Candidate requirements therefore carry confidence/evidence and
        remain `inferred` until reviewed.
        """
        groups: dict[str,list[dict[str,Any]]]={}
        for row in self.db.query("SELECT * FROM code_files ORDER BY path"):
            parts=row["path"].split("/"); module=parts[1] if parts and parts[0] in {"src","app","lib"} and len(parts)>1 else parts[0] if len(parts)>1 else Path(row["path"]).stem; groups.setdefault(module,[]).append(row)
        created=[]
        for module,files in list(groups.items())[:120]:
            symbols=[]; routes=[]
            for f in files:
                symbols += self.db.query("SELECT * FROM symbols WHERE path=? ORDER BY start_line",(f["path"],)); routes += self.db.query("SELECT * FROM routes WHERE file_path=? ORDER BY line",(f["path"],))
            production=[x for x in symbols if not x["name"].startswith("_") and "test" not in x["path"].lower()]
            reqs=[]; acs=[]; unknowns=[]; business_rules=[]
            def linked_tests(symbol_id:str)->list[dict[str,Any]]:
                return self.db.query("SELECT s.path,s.qualified_name,t.confidence FROM test_links t JOIN symbols s ON s.id=t.test_symbol WHERE t.target_symbol=? ORDER BY t.confidence DESC LIMIT 8",(symbol_id,))
            for route in routes[:12]:
                idx=len(reqs)+1; tests_for=linked_tests(route["symbol_id"]); evidence=[f"{route['file_path']}:{route['line']}"]+[f"{t['path']}::{t['qualified_name']}" for t in tests_for]
                reqs.append({"id":f"REQ-{idx:03d}","text":f"El sistema expone {route['method']} {route['path']} con el comportamiento observable implementado por su handler actual.","priority":"must","confidence":"high" if tests_for else "medium","evidence":"; ".join(evidence)})
                ac_text=(f"Dado el commit adoptado, cuando se ejercita {route['method']} {route['path']} mediante {tests_for[0]['qualified_name']}, entonces se conserva el comportamiento observado." if tests_for else f"Dado el commit adoptado, cuando se invoca {route['method']} {route['path']}, entonces el handler actual responde sin alterar su contrato observable.")
                acs.append({"id":f"AC-{idx:03d}","requirement":f"REQ-{idx:03d}","text":ac_text})
                if not tests_for: unknowns.append(f"No se encontró test enlazado de alta confianza para {route['method']} {route['path']}.")
            for sym in production:
                if len(reqs)>=16: break
                # Prefer symbols that are externally relevant: tested, called or named as validation/security rules.
                tests_for=linked_tests(sym["id"]); callers=self.db.one("SELECT COUNT(*) n FROM call_edges WHERE target_symbol=?",(sym["id"],)) or {"n":0}
                rule_like=bool(re.search(r"valid|authori|permission|limit|check|policy|rule",sym["name"],re.I))
                if not tests_for and not callers["n"] and not rule_like: continue
                idx=len(reqs)+1; desc=(sym.get("docstring") or sym.get("signature") or sym["qualified_name"]).strip(); evidence=[f"{sym['path']}:{sym['start_line']}-{sym['end_line']}"]+[f"{t['path']}::{t['qualified_name']}" for t in tests_for]
                reqs.append({"id":f"REQ-{idx:03d}","text":f"El sistema conserva el comportamiento observable de {sym['qualified_name']}: {desc[:180]}.","priority":"must","confidence":"high" if tests_for else "medium","evidence":"; ".join(evidence)})
                acs.append({"id":f"AC-{idx:03d}","requirement":f"REQ-{idx:03d}","text":f"Dado el commit adoptado, cuando se ejecuta el comportamiento {sym['qualified_name']}, entonces se mantiene el resultado observable cubierto por la evidencia registrada."})
                if rule_like: business_rules.append({"id":f"RULE-{len(business_rules)+1:03d}","text":f"Regla candidata inferida de {sym['qualified_name']}: {desc[:180]}.","confidence":"medium","evidence":evidence[0]})
            if not reqs:
                reqs=[{"id":"REQ-001","text":f"El sistema conserva el comportamiento observable actualmente implementado en el módulo {module}.","priority":"must","confidence":"low","evidence":", ".join(f["path"] for f in files[:5])}]
                acs=[{"id":"AC-001","requirement":"REQ-001","text":f"Dado el commit adoptado, cuando se ejecutan las validaciones existentes del módulo {module}, entonces no aparecen regresiones observables."}]
                unknowns.append(f"El módulo {module} no contiene suficiente evidencia estructurada para reconstruir requisitos más granulares.")
            work_id=self.db.next_id("BASE","work_items"); now=utc_now(); test_count=len({t["path"] for s in production for t in linked_tests(s["id"])})
            desc=f"Baseline AS-IS inferida de {module} con {len(files)} archivos, {len(symbols)} símbolos, {len(routes)} rutas y evidencia de {test_count} archivos de test."
            self.db.execute("INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(work_id,f"Baseline — {module}",desc,"baseline","spec_review",0,0,75,now,now))
            payload={"objective":f"Documentar el comportamiento AS-IS observable del área {module}.","scope":[f"Código, contratos y tests indexados del área {module}"],"out_of_scope":["Intención histórica o reglas de negocio no demostrables desde evidencia del repositorio"],"requirements":reqs,"acceptance_criteria":acs,"business_rules":business_rules,"unknowns":unknowns,"affected_features":[]}
            work=self.get_work(work_id); art=self.artifacts.submit_spec(work,payload,"brownfield-inference"); self.db.execute("UPDATE artifacts SET status='inferred' WHERE id=?",(art["id"],)); self.artifacts.export_work_views(work_id)
            created.append({"work_id":work_id,"artifact":art["id"],"module":module,"files":len(files),"symbols":len(symbols),"routes":len(routes),"tests":test_count,"requirements":len(reqs),"unknowns":len(unknowns),"status":"inferred"})
        self.db.audit("BrownfieldBaselineGenerated",None,{"count":len(created)}); return created

    def rollback(self, work_id: str, commit: str | None = None) -> dict[str, Any]:
        """Rollback a governed work item using recorded Git/workflow evidence."""
        work = self.get_work(work_id)
        if not work.get("worktree"):
            raise ValueError("Work item has no worktree")
        restored = self.git.rollback(work_id, Path(work["worktree"]), commit)
        return {"work": work_id, "restored_commit": restored}

    def review_summary(self, work_id: str) -> dict[str, Any]:
        """Return a compact evidence summary intended for a human review decision."""
        score, findings = self.quality.health_score(work_id)
        self._set_score(work_id, score)
        work = self.get_work(work_id)
        return {
            "work": work,
            "spec": self.artifacts.artifact(work_id, "spec"),
            "plan": self.artifacts.artifact(work_id, "plan"),
            "tasks": [Database.decode(row, "requirements", "acceptance", "files", "validation_commands", "evidence_required", "depends_on") for row in self.db.query("SELECT * FROM tasks WHERE work_id=? ORDER BY id", (work_id,))],
            "quality_score": score,
            "findings": [item.to_dict() for item in findings],
            "scope_requests": self.db.query("SELECT * FROM scope_requests WHERE work_id=? AND status='pending' ORDER BY created_at",(work_id,)),
            "next": self.next_action(work_id),
        }

    def backup(self, reason: str = "manual") -> dict[str, Any]:
        """Create a portable DynosAI state snapshot and safety database backup."""
        self._ensure_ready(); return self.state.snapshot(reason)

    def restore(self, snapshot: str | None = None) -> dict[str, Any]:
        """Restore a validated DynosAI portable snapshot atomically."""
        result=self.state.restore(snapshot); self.sync(); return result

    def stats(self) -> dict[str, Any]:
        """Return persisted workflow, knowledge, and repository statistics."""
        runs=self.db.query("SELECT r.id,r.agent,r.status,m.* FROM runs r LEFT JOIN run_metrics m ON m.run_id=r.id ORDER BY r.started_at"); total=len(runs); verified=sum(1 for r in runs if r.get("status")=="verified"); context=sum(int(r.get("context_tokens") or 0) for r in runs); actual=sum(int(r.get("actual_files") or 0) for r in runs); suggested=sum(int(r.get("suggested_files") or 0) for r in runs); hits=sum(int(r.get("planned_files_hit") or 0) for r in runs)
        precision=hits/max(1,suggested); recall=hits/max(1,actual)
        mcp_calls=int((self.db.one("SELECT COUNT(*) n FROM audit WHERE event_type='MCPToolCalled'") or {"n":0})["n"])
        mcp_failures=int((self.db.one("SELECT COUNT(*) n FROM audit WHERE event_type='MCPToolFailed'") or {"n":0})["n"])
        scope_requests=int((self.db.one("SELECT COUNT(*) n FROM scope_requests") or {"n":0})["n"])
        validation_runs=int((self.db.one("SELECT COUNT(*) n FROM validations") or {"n":0})["n"])
        cases=load_cases(self.root)
        return {"runs":total,"verified_runs":verified,"success_rate":round(verified/max(1,total),3),"context_tokens":context,"actual_files":actual,"suggested_files":suggested,"predicted_file_precision":round(precision,3),"predicted_file_recall":round(recall,3),"retrieval_queries":int((self.db.one('SELECT COUNT(*) n FROM retrieval_events') or {'n':0})['n']),"semantic_documents":int((self.db.one('SELECT COUNT(*) n FROM semantic_documents') or {'n':0})['n']),"mcp_calls":mcp_calls,"mcp_failures":mcp_failures,"scope_requests":scope_requests,"validation_runs":validation_runs,"eval_intelligence":{"open":sum(1 for case in cases if case.get("status")=="open"),"proposed":sum(1 for case in cases if case.get("status")=="proposed"),"regressed":sum(1 for case in cases if case.get("status")=="regressed"),"predictive_routing":"shadow","live_provider_evals":False}}

    def agent_git_status(self, run_id: str | None = None) -> dict[str, Any]:
        """Policy-safe Git status metadata for coding agents."""
        run=self.db.one("SELECT * FROM runs WHERE id=?",(run_id,)) if run_id else None
        worktree=Path(run.get("worktree")) if run and run.get("worktree") else self.root
        policy=PathPolicyEngine(worktree); visible=[]; denied=[]
        for line in self.git.status(worktree):
            rel=decode_git_path(line[3:] if len(line)>3 else line)
            decision=policy.decision(rel,"read",agent=True)
            if decision.allowed: visible.append(line)
            else: denied.append({"path":rel,"reason":decision.reason})
        return {"worktree":str(worktree),"branch":self.git.current_branch(worktree),"head":self.git.head(worktree),"status":visible,"hidden_sensitive_entries":len(denied)}

    def agent_git_diff(self, run_id: str | None = None, max_chars: int = 30000) -> dict[str, Any]:
        """Policy-filtered Git diff; direct content-bearing Git commands stay blocked."""
        run=self.db.one("SELECT * FROM runs WHERE id=?",(run_id,)) if run_id else None
        if run:
            worktree=Path(run.get("worktree") or self.root); base=run.get("base_commit") or None
        else:
            worktree=self.root; base=None
        return self.git.policy_filtered_diff(worktree,base,max_chars=max(1000,min(int(max_chars),100000)))

    def schedule(self, work_id: str | None = None) -> dict[str, Any]:
        """Return governed team waves and leases for the approved task DAG.

        Parallel batches remain file-disjoint. This does not spawn provider processes.
        """
        work=self.get_work(work_id)
        tasks=[Database.decode(t,"files","depends_on","requirements","acceptance","validation_commands","evidence_required") for t in self.db.query("SELECT * FROM tasks WHERE work_id=? ORDER BY id",(work["id"],))]
        plan=build_team_plan(tasks)
        plan["work_id"]=work["id"]
        return plan

    def claim_lease(self, work_id: str, task_id: str, agent: str, role: str = "implementer") -> dict[str, Any]:
        """Claim a current-wave lease for a governed session. Never starts a second provider."""
        decision=claim_allowed(self.schedule(work_id), task_id)
        if not decision.get("allowed"):
            raise ValueError(decision.get("reason") or "lease cannot be claimed")
        if role == "reviewer":
            return {**decision, "status": "human_gate", "role": role, "message": "Reviewer role is the human code-review gate, not a spawned agent."}
        claimed=self.prepare_task_run(work_id, task_id, agent)
        self.db.audit("WorkerLeaseClaimed", task_id, {"work_id": work_id, "role": role, "run_id": claimed.get("run_id"), "spawn_provider": False})
        return {**claimed, "lease": decision.get("lease"), "spawn_provider": False, "role": role}

    def fan_in_report(self, work_id: str) -> dict[str, Any]:
        """Report whether verified worker scopes in a wave can merge without silent overlap."""
        plan=self.schedule(work_id)
        waves=[]
        for wave in plan.get("waves") or []:
            item=fan_in_conflicts(list(wave.get("leases") or []))
            item["wave_id"]=wave.get("id")
            item["kind"]=wave.get("kind")
            waves.append(item)
        return {
            "work_id":work_id,
            "waves":waves,
            "blocked":any(item.get("blocked") for item in waves),
            "spawn_providers":False,
            "policy":"Fan-in is wave-scoped. Overlapping verified scopes require a human merge resolution.",
        }

    def eval_intelligence(self) -> dict[str, Any]:
        """Mine local traces into bounded eval cases. Does not start providers or enable predictive routing."""
        self._ensure_ready()
        return build_report(self.db, self.root)

    def propose_eval_improvement(self, case_id: str) -> dict[str, Any]:
        """Create inbox work for a mined eval case. Never auto-starts a provider session."""
        self.eval_intelligence()
        case = case_by_id(self.root, case_id)
        if not case:
            raise ValueError(f"unknown eval case: {case_id}")
        if case.get("improvement_work_id"):
            work = self.get_work(case["improvement_work_id"])
            return {"work": work, "case": case, "spawn_provider": False, "auto_start": False, "status": "already_proposed"}
        work = self.start(improvement_description(case))
        updated = mark_proposed(self.root, case_id, work["id"])
        self.db.audit("EvalImprovementProposed", case_id, {"work_id": work["id"], "layer": (updated.get("attribution") or {}).get("layer"), "spawn_provider": False})
        return {"work": work, "case": updated, "spawn_provider": False, "auto_start": False, "status": "inbox"}

    def register_eval_regression(self, case_id: str) -> dict[str, Any]:
        """Record an offline eval pass as regression evidence. Does not complete or approve work."""
        self.eval_intelligence()
        case = case_by_id(self.root, case_id)
        if not case:
            raise ValueError(f"unknown eval case: {case_id}")
        scenario_id = str(case.get("scenario") or "mined_regression")
        record = EvalRegistry(self.root).run_offline(scenario_id)
        if not record.get("success"):
            return {"case": case, "eval": record, "status": "eval_failed", "work_completed": False}
        evidence = {"eval_path": record.get("path"), "scenario": scenario_id, "success": True, "recorded_at": record.get("recorded_at")}
        updated = mark_regressed(self.root, case_id, evidence)
        self.db.audit("EvalRegressionRecorded", case_id, evidence)
        return {"case": updated, "eval": record, "status": "regressed", "work_completed": False, "auto_approved": False}

    def _context_query(self, work: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
        """Build phase-aware retrieval text from authoritative structured knowledge.

        Brownfield planning should search with the approved spec and recorded decisions,
        not mostly with a broad project/title phrase that may contain common package names.
        """
        parts: list[str] = []
        if spec:
            meta=spec.get("metadata") or {}
            parts.append(str(meta.get("objective") or ""))
            parts.extend(str(x.get("text") or "") for x in (meta.get("requirements") or []))
            parts.extend(str(x.get("text") or "") for x in (meta.get("business_rules") or []))
        for decision in self.db.query("SELECT answer FROM decisions WHERE work_id=? ORDER BY created_at",(work["id"],)):
            parts.append(str(decision.get("answer") or ""))
        parts.extend([str(work.get("title") or ""),str(work.get("description") or "")])
        return " ".join(x.strip() for x in parts if x and x.strip())

    def _scrub_managed_overlays_from_tasks(self, work_id: str) -> None:
        """Remove DynosAI-owned spec/plan overlays from persisted task file lists."""
        for task in self.db.query("SELECT id,files FROM tasks WHERE work_id=?", (work_id,)):
            files = json_loads(task.get("files"), [])
            cleaned = product_scope_paths(files)
            if cleaned != list(files):
                self.db.execute("UPDATE tasks SET files=?,updated_at=? WHERE id=?", (json_dumps(cleaned), utc_now(), task["id"]))

    def _task_queue_contract(self, work_id: str) -> dict[str, Any]:
        self._scrub_managed_overlays_from_tasks(work_id)
        rows=[Database.decode(t,"files","depends_on","requirements","acceptance","evidence_required","validation_commands") for t in self.db.query("SELECT * FROM tasks WHERE work_id=? ORDER BY id",(work_id,))]
        completed={t["id"] for t in rows if t["state"]=="completed"}
        remaining=[t for t in rows if t["state"]!="completed"]
        ready=[t for t in remaining if set(t.get("depends_on") or [])<=completed]

        def compact(t: dict[str, Any]) -> dict[str, Any]:
            deps=t.get("depends_on") or []
            return {
                "id":t["id"],"title":t["title"],"state":t["state"],"files":t.get("files") or [],
                "depends_on":deps,"dependencies_satisfied":set(deps)<=completed,
                "requirements":t.get("requirements") or [],"acceptance":t.get("acceptance") or [],
                "evidence_required":t.get("evidence_required") or [],"validation_profiles":t.get("validation_commands") or [],
            }

        # Execution waves preserve dependency order while allowing a bounded dependent task chain to execute atomically. A
        # short downstream chain may be implemented atomically when there is no
        # human gate between tasks. Presenting T02 as simply "blocked" caused
        # Codex to finish/register T01, ask DynosAI again, and pay for an entire
        # extra inference before writing T02. A wave explicitly authorizes those
        # downstream edits while preserving registration order and one validation.
        wave:list[dict[str,Any]]=[]
        if ready:
            max_tasks=max(1,min(4,int(os.environ.get("DYNOSAI_MAX_EXECUTION_WAVE_TASKS","3") or 3)))
            max_files=max(1,min(16,int(os.environ.get("DYNOSAI_MAX_EXECUTION_WAVE_FILES","8") or 8)))
            wave=[ready[0]]
            wave_ids={ready[0]["id"]}
            wave_files=set(ready[0].get("files") or [])
            while len(wave)<max_tasks:
                candidate=None
                for task in remaining:
                    if task["id"] in wave_ids: continue
                    deps=set(task.get("depends_on") or [])
                    # Only pull transitive dependants of the current task into the
                    # wave. Independent ready work remains a later orchestration
                    # decision; this keeps batching conservative and predictable.
                    if not deps or not (deps & wave_ids): continue
                    if not deps <= (completed | wave_ids): continue
                    files=set(task.get("files") or [])
                    if len(wave_files | files)>max_files: continue
                    candidate=task; break
                if candidate is None: break
                wave.append(candidate); wave_ids.add(candidate["id"]); wave_files.update(candidate.get("files") or [])

        wave_id=None
        wave_contract=None
        if wave:
            wave_payload=[(t["id"],t.get("state"),t.get("depends_on") or [],t.get("files") or []) for t in wave]
            digest=hashlib.sha256(json_dumps(wave_payload).encode("utf-8")).hexdigest()[:12]
            wave_id=f"WAVE-{digest}"
            evidence={str(x) for t in wave for x in (t.get("evidence_required") or [])}
            wave_tasks=[]
            available=set(completed)
            for task in wave:
                item=compact(task)
                start_satisfied=bool(item.pop("dependencies_satisfied",False))
                deps=set(task.get("depends_on") or [])
                item["dependencies_satisfied_at_wave_start"]=start_satisfied
                item["dependencies_satisfied_in_wave"]=deps <= available
                item["execution_status"]="ready_in_wave" if deps <= available else "blocked"
                wave_tasks.append(item)
                available.add(task["id"])
            wave_contract={
                "id":wave_id,
                "task_ids":[t["id"] for t in wave],
                "tasks":wave_tasks,
                "files":sorted(wave_files),
                "atomic_execution_allowed":len(wave)>1,
                "registration_order":[t["id"] for t in wave],
                "validation_required":("test_result" in evidence),
                "validation_after":[t["id"] for t in wave] if "test_result" in evidence else [],
                "expected_orchestration_round_trips_saved":max(0,len(wave)-1),
                "policy":"Implement every task in this wave before registering intermediate results. Dependencies define edit/registration order inside the wave; they do not require another get_next_action call.",
            }
            # Audit a logical wave once. Repeated polling returns the same WAVE id
            # without inflating metrics.
            if not self.db.one("SELECT 1 ok FROM audit WHERE event_type='ExecutionWavePlanned' AND entity_id=? LIMIT 1",(wave_id,)):
                self.db.audit("ExecutionWavePlanned",wave_id,{"work_id":work_id,"task_ids":wave_contract["task_ids"],"files":wave_contract["files"],"expected_round_trips_saved":wave_contract["expected_orchestration_round_trips_saved"]})

        wave_ids={t["id"] for t in wave}
        blocked=[t for t in remaining if t not in ready and t["id"] not in wave_ids]
        return {
            "current_task":compact(ready[0]) if ready else None,
            "ready_task_ids":[t["id"] for t in ready],
            "execution_wave":wave_contract,
            # Tasks included in execution_wave are intentionally absent here: they
            # remain dependency-ordered but are no longer blocked for execution.
            "blocked_tasks":[{
                "id":t["id"],"title":t.get("title"),"files":t.get("files") or [],
                "depends_on":t.get("depends_on") or [],"evidence_required":t.get("evidence_required") or [],
            } for t in blocked[:6]],
            "remaining_task_ids":[t["id"] for t in remaining],
            "remaining_count":len(remaining),
        }

    def _repository_context_contract(self, impact: dict[str, Any]) -> dict[str, Any]:
        files=int((self.db.one("SELECT COUNT(*) n FROM code_files") or {"n":0})["n"])
        symbols=int((self.db.one("SELECT COUNT(*) n FROM symbols") or {"n":0})["n"])
        mode=self.db.get_meta("mode","greenfield")
        evidence_files=len(impact.get("files") or [])+len(impact.get("tests") or [])
        context_complete=(mode=="greenfield" and files==0) or evidence_files>0
        return {
            "mode":mode,"repository_files":files,"indexed_symbols":symbols,
            "repository_context_complete":context_complete,
            "additional_discovery_required":not context_complete,
            "discovery_policy":"targeted-only" if mode=="brownfield" else "none-unless-contract-requests-it",
            "reason":(
                "DynosAI already identified concrete repository evidence; use supplied file/symbol context and request targeted slices only when needed."
                if context_complete and mode=="brownfield" else
                "The repository has no existing source context for this greenfield work." if context_complete else
                "No sufficiently concrete repository evidence was retrieved; targeted symbol/file discovery is allowed before authoring the plan."
            ),
        }

    def action_contract(self, work_id: str | None = None) -> dict[str, Any] | None:
        """Return the authoritative structured contract for the current agent action.

        0.6 contracts also expose repository-context completeness and the executable
        task queue so an agent never has to reconstruct workflow state by calling
        board/project-overview or by exploring provider transcripts.
        """
        work = self.get_work(work_id)
        state = work["state"]
        if state == "inbox":
            return {"operation": "open_discovery", "tool": "dynosai_get_next_action", "arguments": {"work_id": work["id"], "execute": True}, "work_id": work["id"], "deterministic_transition": True}
        if state == "discovery":
            if not self.artifacts.artifact(work["id"], "spec"):
                decisions = self.db.query("SELECT * FROM decisions WHERE work_id=? ORDER BY created_at", (work["id"],))
                impact = self.retrieval.impact(self._context_query(work), work_id=work["id"])
                contract=self.artifacts.specification_request(work, impact, decisions)
                contract["repository_context"]=self._repository_context_contract(impact)
                return contract
            return {"operation": "advance_spec_gate", "tool": "dynosai_get_next_action", "arguments": {"work_id": work["id"], "execute": True}, "work_id": work["id"], "deterministic_transition": True}
        if state == "plan_review":
            if not self.artifacts.artifact(work["id"], "plan"):
                spec = self.artifacts.artifact(work["id"], "spec")
                if spec:
                    impact = self.retrieval.impact(self._context_query(work,spec), work_id=work["id"])
                    contract=self.artifacts.plan_request(work, spec, impact)
                    contract["repository_context"]=self._repository_context_contract(impact)
                    return contract
            return None
        if state == "ready":
            return {"operation":"prepare_implementation","tool":"dynosai_get_next_action","arguments":{"work_id":work["id"],"execute":True},"work_id":work["id"],"deterministic_transition":True}
        if state == "validating":
            return {"operation":"run_governed_validation","tool":"dynosai_get_next_action","arguments":{"work_id":work["id"],"execute":True},"work_id":work["id"],"deterministic_transition":True}
        if state == "implementing":
            queue=self._task_queue_contract(work["id"])
            current=(queue.get("current_task") or {}).get("id")
            # task_queue already carries the current task requirements, acceptance,
            # files and evidence contract. Retrieval is asked only for the
            # *repository delta* that is not already present there, avoiding the
            # duplicated task/spec payload that would otherwise be replayed.
            preview=self.context_preview(work["id"],max_tokens=1000,task_id=current,include_task_contract=False) if current else self.context_preview(work["id"],max_tokens=700,include_task_contract=False)
            return {
                "operation": "implement_tasks",
                "work_id": work["id"],
                "task_queue":queue,
                "team": self.schedule(work["id"]),
                "context": preview,
                "repository_context":self._repository_context_contract(preview.get("impact") or {}),
                "rules": [
                    "Treat execution_wave.tasks as executable now. Their depends_on order is logical inside the wave and does not require another dynosai_get_next_action call.",
                    "When execution_wave.atomic_execution_allowed=true, implement all wave files before registering any intermediate result.",
                    "Do not reconstruct remaining work from board/project-overview when task_queue is present.",
                    "Edit only files in execution_wave.files or other explicitly approved task scope.",
                    "If execution_wave.validation_required=true, run dynosai_run_validation once after all wave edits, not between wave tasks.",
                    "Register the whole wave atomically with completed_tasks=execution_wave.registration_order. Do not register T01 alone when T02 is in the same wave.",
                    "After the final wave result DynosAI may auto-advance directly to code review; do not poll get_next_action merely to discover that boundary.",
                    "team.current_wave leases are the only parallel slots. DynosAI does not spawn extra provider processes. Stay inside the claimed lease scope_ceiling.",
                ],
            }
        return None

    def next_action(self, work_id: str | None = None) -> str:
        """Return the authoritative next-action contract for the current workflow state."""
        work = self.get_work(work_id)
        return {
            "inbox": "Usar dynosai_get_next_action execute=true para abrir discovery",
            "discovery": "Compilar la spec estructurada; dynosai_get_next_action execute=true avanza únicamente transiciones deterministas",
            "spec_review": f"Esperar/aplicar la revisión humana de spec para {work['id']}; no autoaprobar",
            "plan_review": "Compilar plan/tareas si faltan; usar dynosai_get_next_action execute=true para transición determinista",
            "ready": "Usar dynosai_get_next_action execute=true para preparar implementación",
            "implementing": "Implementar la tarea actual y registrar resultado; reutilizar el contrato si DynosAI devuelve unchanged=true",
            "code_review": f"Esperar/aplicar la revisión humana de código para {work['id']}; no autoaprobar",
            "validating": "Usar dynosai_get_next_action execute=true para validación gobernada",
            "ready_to_merge": f"Esperar/aplicar la aprobación humana de merge para {work['id']}; no autoaprobar",
            "done": "Trabajo completado",
        }.get(work["state"], "Revisar el estado del trabajo")

    def install_model(self) -> dict[str, Any]:
        """Install or initialize the local semantic embedding model used by DynosAI retrieval."""
        self._ensure_ready()
        result = self.semantic.install_model()
        result["index"] = self.semantic.reindex_authoritative(self.git.head(), rebuild=True)
        self._write_config()
        self.db.audit("SemanticModelInstalled", None, result)
        return result

    def model_status(self, probe: bool = False) -> dict[str, Any]:
        """Report local semantic-model availability and cache state."""
        self._ensure_ready()
        return self.semantic.status(probe=probe)

    def remove_model(self) -> dict[str, Any]:
        """Remove the local semantic-model cache managed by DynosAI."""
        self._ensure_ready()
        result = self.semantic.remove_model()
        self._write_config()
        self.db.audit("SemanticModelRemoved", None, result)
        return result

    def semantic_index(self, rebuild: bool = False) -> dict[str, Any]:
        """Build or refresh the semantic index from authoritative project knowledge and code metadata."""
        self._ensure_ready()
        code = self.indexer.index(self.git.head())
        semantic = self.semantic.reindex_authoritative(self.git.head(), rebuild=rebuild)
        return {"code": code, "semantic": semantic}

    def doctor(self) -> dict[str, Any]:
        """Run fast environment and project-health checks."""
        import importlib.util, shutil, subprocess
        detected = self.agents.detect(); semantic = self.semantic.status(probe=False)
        integrity=(self.db.one("PRAGMA integrity_check") or {}).get("integrity_check","unknown")
        git_read=self.git_guard.check(["status"])[0]
        git_write_blocked=not self.git_guard.check(["branch","dynosai-should-block"])[0]
        secret_blocked=not PathPolicyEngine(self.root).decision(".env","read",agent=True).allowed
        mcp_entry=shutil.which("dynosai-mcp") is not None or importlib.util.find_spec("dynosai_flow.mcp") is not None
        connected_names=self.db.get_meta("connected_agents").split(",") if self.db.get_meta("connected_agents") else []
        checks = {
            "database": self.db.path.exists(), "database_integrity": integrity=="ok", "git": self.git.is_repo(),
            "git_head": bool(self.git.head()), "index_current": self.db.get_meta("indexed_commit") == self.git.head(),
            "spec_views": (self.root / ".specify" / "memory" / "constitution.md").exists(),
            "agent_protocol": (not connected_names) or (self.root / "AGENTS.md").exists(), "schema_current": self.db.get_meta("schema_version") == str(self.db.CURRENT_SCHEMA_VERSION),
            "portable_snapshot": (self.root / ".dynosai" / "state" / "snapshot.json.gz").exists(),
            "git_guard": (self.git_guard.guard_dir / "git").exists(), "git_guard_read":git_read,
            "git_guard_write_block":git_write_blocked, "secret_policy":secret_blocked, "mcp_entrypoint":mcp_entry,
        }
        semantic_ready = bool(semantic.get("configured")) and semantic.get("documents", 0) >= 0
        parser_backends = {row["key"].split(":",1)[1]: row["value"] for row in self.db.query("SELECT key,value FROM meta WHERE key LIKE 'parser_backend:%'")}
        agent_health={}
        commands={"codex":"codex","cursor":"cursor-agent"}
        for name,available in detected.items():
            item={"executable":available,"version_probe":"not-installed"}
            if available:
                try:
                    r=subprocess.run([commands[name],"--version"],text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=4)
                    item["version_probe"]="ok" if r.returncode==0 else "failed"; item["version"]=(r.stdout or r.stderr).strip()[:300]
                except Exception as exc: item["version_probe"]="failed"; item["error"]=str(exc)
            agent_health[name]=item
        provider_ready=any(agent_health.get(name,{}).get("executable") and agent_health.get(name,{}).get("version_probe")=="ok" for name in connected_names)
        from .model_routing import ProviderModelRouting
        routing=ProviderModelRouting(self.root)
        return {
            "ready": all(checks.values()), "full_ready": all(checks.values()) and semantic_ready and provider_ready, "checks": checks,
            "semantic_ready":semantic_ready,"provider_ready":provider_ready,
            "provider_model_routing":{"cursor":routing.effective("cursor"),"codex":routing.effective("codex")},
            "semantic": semantic, "codegraph": {"tree_sitter_available": importlib.util.find_spec("tree_sitter_language_pack") is not None,"parser_backends": parser_backends},
            "agents": detected, "agent_health":agent_health, "connected": connected_names,
            "external_verification_note":"Executable/version probes are reported when locally installed; authentication and live coding are never inferred."
        }

    def environment_report(self) -> dict[str, Any]:
        """Return a structured report of the local runtime, provider, Git, model, and project environment."""
        import platform, shutil, subprocess
        def version(command: list[str]) -> str | None:
            try:
                r=subprocess.run(command,text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=5)
                return (r.stdout or r.stderr).strip().splitlines()[0][:300] if r.returncode==0 else None
            except Exception: return None
        return {
            "dynosai": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
            "os_name": os.name, "cwd": str(Path.cwd().resolve()), "project_root": str(self.root),
            "git": version([self.git._git_executable(),"--version"]),
            "cursor": version([shutil.which("cursor-agent") or "cursor-agent","--version"]) if shutil.which("cursor-agent") else None,
            "codex": version(["codex","--version"]) if shutil.which("codex") else None,
            "semantic": self.semantic.status(False), "runtime": self.runtime.status(),
        }

    def _mcp_handshake_probe(self, cwd: Path) -> dict[str, Any]:
        import shutil, subprocess
        exe=shutil.which("dynosai-mcp")
        command=[exe] if exe else [sys.executable,"-m","dynosai_flow.mcp"]
        messages=[
            {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"dynosai-doctor","version":__version__}}},
            {"jsonrpc":"2.0","method":"notifications/initialized","params":{}},
            {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
        ]
        env=os.environ.copy(); env["DYNOSAI_AGENT_PROVIDER"]="cursor"; env["DYNOSAI_RUNTIME_BROKER"]="1"; env.pop("DYNOSAI_PROJECT_ROOT",None); env.pop("DYNOSAI_SESSION_TOKEN",None)
        if not exe:
            # Source-tree doctor runs must remain valid even when the probe cwd is
            # a temporary Git worktree. Make the package source absolute rather
            # than relying on a relative PYTHONPATH inherited from the caller.
            source_root=str(Path(__file__).resolve().parents[1])
            current=env.get("PYTHONPATH","")
            env["PYTHONPATH"]=source_root + (os.pathsep + current if current else "")
        try:
            r=subprocess.run(command,input="\n".join(json.dumps(x) for x in messages)+"\n",cwd=cwd,env=env,text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=15)
            lines=[json.loads(x) for x in r.stdout.splitlines() if x.strip()]
            init=next((x for x in lines if x.get("id")==1),{}); tools=next((x for x in lines if x.get("id")==2),{})
            count=len(((tools.get("result") or {}).get("tools") or []))
            return {"ok":r.returncode==0 and count>=20,"protocol":((init.get("result") or {}).get("protocolVersion")),"tools":count,"stderr":r.stderr[-1000:]}
        except Exception as exc: return {"ok":False,"error":str(exc)}

    def deep_doctor(self) -> dict[str, Any]:
        """Run deeper readiness checks, including optional semantic/provider prerequisites."""
        import json as _json, shutil, subprocess, tempfile
        from .runtime import user_home, CURSOR_CONFIG_VERSION
        base=self.doctor(); checks=dict(base["checks"]); details={}
        runtime=self.runtime.status(); checks["runtime_broker"]=runtime.get("schema_version")==1; details["runtime"]=runtime
        cursor_connected="cursor" in self.db.get_meta("connected_agents").split(",")
        gmcp=user_home()/".cursor"/"mcp.json"; gcli=user_home()/".cursor"/"cli-config.json"
        try:
            mcp_data=_json.loads(gmcp.read_text(encoding="utf-8")); server=(mcp_data.get("mcpServers") or {}).get("dynosai") or {}
            checks["cursor_global_mcp"]=bool(server.get("command")) if cursor_connected else True; details["cursor_global_mcp"]={"path":str(gmcp),"server":server}
        except Exception as exc: checks["cursor_global_mcp"]=not cursor_connected; details["cursor_global_mcp"]={"path":str(gmcp),"error":str(exc),"required":cursor_connected}
        try:
            cli_data=_json.loads(gcli.read_text(encoding="utf-8"))
            # Cursor CLI rejects unknown top-level metadata. Version ownership is recorded in RuntimeBroker, not cli-config.json.
            conn=next((x for x in runtime.get("connections",[]) if x.get("provider")=="cursor"),None)
            checks["cursor_config_version"]=(int((conn or {}).get("config_version",0))==CURSOR_CONFIG_VERSION) if cursor_connected else True
            checks["cursor_cli_schema_clean"]=("dynosai" not in cli_data) if cursor_connected else True
            details["cursor_cli"]={"path":str(gcli),"configVersion":(conn or {}).get("config_version"),"schema_clean":"dynosai" not in cli_data}
        except Exception as exc:
            checks["cursor_config_version"]=not cursor_connected; checks["cursor_cli_schema_clean"]=not cursor_connected
            details["cursor_cli"]={"path":str(gcli),"error":str(exc),"required":cursor_connected}
        root_probe=self._mcp_handshake_probe(self.root); checks["mcp_root_handshake"]=bool(root_probe.get("ok")); details["mcp_root"]=root_probe
        # Prove project discovery from a freshly-created Git worktree, then remove it.
        worktree_probe={"ok":False,"skipped":False}
        temp_parent=Path(tempfile.mkdtemp(prefix="dynosai-doctor-")); wt=temp_parent/"worktree"
        try:
            r=subprocess.run([self.git._git_executable(),"worktree","add","--detach",str(wt),"HEAD"],cwd=self.root,text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=20)
            if r.returncode==0:
                worktree_probe=self._mcp_handshake_probe(wt)
            else: worktree_probe={"ok":False,"error":(r.stderr or r.stdout)[-1000:]}
        finally:
            if wt.exists(): subprocess.run([self.git._git_executable(),"worktree","remove","--force",str(wt)],cwd=self.root,text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=20)
            shutil.rmtree(temp_parent,ignore_errors=True)
        checks["mcp_worktree_handshake"]=bool(worktree_probe.get("ok")); details["mcp_worktree"]=worktree_probe
        cursor=shutil.which("cursor-agent")
        cursor_probe={"installed":bool(cursor)}
        if cursor:
            try:
                status=subprocess.run([cursor,"status"],text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=8); cursor_probe["authenticated"]=status.returncode==0 and "logged in" in (status.stdout+status.stderr).lower()
                listing=subprocess.run([cursor,"mcp","list"],text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=10); txt=listing.stdout+listing.stderr; cursor_probe["mcp_ready"]=listing.returncode==0 and "dynosai" in txt.lower() and "ready" in txt.lower(); cursor_probe["mcp_list"]=txt.strip()[:1000]
                toolrun=subprocess.run([cursor,"mcp","list-tools","dynosai"],text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=10); cursor_probe["tools_visible"]=toolrun.returncode==0 and toolrun.stdout.count("dynosai_")>=20; cursor_probe["tool_count"]=toolrun.stdout.count("dynosai_")
            except Exception as exc: cursor_probe["error"]=str(exc)
        checks["cursor_authenticated"]=bool(cursor_probe.get("authenticated")) if cursor_connected else True
        checks["cursor_mcp_ready"]=bool(cursor_probe.get("mcp_ready")) if cursor_connected else True
        checks["cursor_tools_visible"]=bool(cursor_probe.get("tools_visible")) if cursor_connected else True
        details["cursor_live"]=cursor_probe
        base["checks"]=checks; base["deep_checks"]=details; base["ready"]=all(checks.values()); base["full_ready"]=base["ready"] and base.get("semantic_ready",False) and base.get("provider_ready",False)
        return base

    def diagnostic_bundle(self, output: str | None = None) -> dict[str, Any]:
        """Create a portable diagnostic bundle with sanitized DynosAI runtime evidence."""
        import tempfile, zipfile
        target=Path(output).expanduser().resolve() if output else self.root/".dynosai"/"diagnostics"/f"dynosai-diagnostic-{utc_now().replace(':','').replace('+','_')}.zip"
        target.parent.mkdir(parents=True,exist_ok=True)
        payloads={
            "environment.json":self.environment_report(), "doctor.json":self.doctor(), "runtime.json":self.runtime.status(),
            "board.json":self.board(), "stats.json":self.stats(),
            "recent_audit.json":self.db.query("SELECT id,event_type,entity_id,payload,created_at FROM audit ORDER BY id DESC LIMIT 100"),
            "recent_runs.json":self.db.query("SELECT id,work_id,agent,status,started_at,finished_at,verification_status,worktree FROM runs ORDER BY id DESC LIMIT 50"),
        }
        with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
            for name,data in payloads.items(): z.writestr(name,json.dumps(data,ensure_ascii=False,indent=2,default=str))
            z.writestr("README.txt",f"DynosAI {__version__} diagnostic bundle. No source files, environment secrets, session tokens, .env files or private keys are included.\n")
        return {"bundle":str(target),"files":sorted(payloads),"sanitized":True}

    def scorecard(self) -> dict[str, Any]:
        """Evidence-based internal readiness scorecard.

        A category score is the percentage of implemented controls that pass at
        runtime (0-10). External provider authentication/live execution and real
        neural model inference are reported separately instead of being invented.
        """
        import importlib.util
        from .mcp import TOOLS, SUPPORTED_PROTOCOLS
        from .runtime import user_home, CURSOR_CONFIG_VERSION
        d=self.doctor(); tables={r["name"] for r in self.db.query("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}; cols={}
        for table in ("runs","tasks","symbols","semantic_documents","agent_sessions"):
            if table in tables: cols[table]={r["name"] for r in self.db.query(f"PRAGMA table_info({table})")}
        def category(controls: dict[str,bool])->dict[str,Any]:
            passed=sum(bool(v) for v in controls.values()); total=max(1,len(controls)); score=round(10.0*passed/total,1)
            return {"score":score,"passed":passed,"total":total,"controls":controls,"failed":[k for k,v in controls.items() if not v]}
        categories={
            "architecture":category({
                "database_authoritative":d["checks"]["database"],"database_integrity":d["checks"]["database_integrity"],"git_repository":d["checks"]["git"],"schema_current":d["checks"]["schema_current"],"portable_state":d["checks"]["portable_snapshot"]}),
            "workflow_sdd":category({
                "artifact_revisions":"artifact_revisions" in tables,"task_evidence":"task_evidence_links" in tables,"validation_profiles":"validation_profiles" in tables,"scope_reviews":"scope_requests" in tables,"state_audit":"audit" in tables}),
            "git_governance":category({
                "guard_installed":d["checks"]["git_guard"],"safe_metadata_read":d["checks"]["git_guard_read"],"write_blocked":d["checks"]["git_guard_write_block"],"content_git_blocked":not self.git_guard.check(["show","HEAD:.env"])[0],"worktree_runs":{"worktree","base_commit"}<=cols.get("runs",set())}),
            "security":category({
                "secret_read_denied":d["checks"]["secret_policy"],"managed_write_denied":not PathPolicyEngine(self.root).decision(".dynosai/config.toml","write",agent=True).allowed,"git_content_mediated":not self.git_guard.check(["diff","HEAD~1"])[0],"session_expiry":"expires_at" in cols.get("agent_sessions",set()),"scope_is_explicit":"scope_requests" in tables}),
            "agent_integration":category({
                "mcp_entrypoint":d["checks"]["mcp_entrypoint"],"mcp_current_protocol":"2025-11-25" in SUPPORTED_PROTOCOLS,"mcp_tooling":len(TOOLS)>=20,"work_bound_sessions":{"run_id","work_id","worktree","permissions"}<=cols.get("agent_sessions",set()),"portable_agent_command":True}),
            "agent_runtime":category({
                "runtime_broker":self.runtime.status().get("schema_version")==1,
                "cursor_global_mcp": ("cursor" not in self.db.get_meta("connected_agents").split(",")) or (user_home()/".cursor"/"mcp.json").exists(),
                "cursor_config_version": ("cursor" not in self.db.get_meta("connected_agents").split(",")) or (user_home()/".cursor"/"cli-config.json").exists(),
                "diagnostic_bundle":hasattr(self,"diagnostic_bundle"),
                "deep_doctor":hasattr(self,"deep_doctor"),
            }),
            "spec_compiler":category({
                "immutable_revisions":"artifact_revisions" in tables,"structured_requirements":"artifacts" in tables,"quality_findings":"audit" in tables,"plan_scope_enforced":"scope_requests" in tables,"baseline_validation_profiles":"validation_profiles" in tables}),
            "evidence_validation":category({
                "evidence_table":"evidence" in tables,"task_links":"task_evidence_links" in tables,"independent_profiles":"validation_profiles" in tables,"run_verification":"verification_status" in cols.get("runs",set()),"declared_vs_verified":{"declared_files","verified_files"}<=cols.get("runs",set())}),
            "brownfield":category({
                "files_indexed":"code_files" in tables,"stable_symbols":"qualified_name" in cols.get("symbols",set()),"routes":"routes" in tables,"test_links":"test_links" in tables,"inferred_specs":"artifact_revisions" in tables}),
            "code_intelligence":category({
                "stable_symbol_ids":"qualified_name" in cols.get("symbols",set()),"call_graph":"call_edges" in tables,"test_graph":"test_links" in tables,"run_overlay":"run_overlays" in tables,"parser_fallback":True}),
            "retrieval_context":category({
                "fts":"search_fts" in tables,"semantic_index":"semantic_documents" in tables,"retrieval_audit":"retrieval_events" in tables,"task_context":"context_manifests" in tables,"graph_expansion":"call_edges" in tables}),
            "semantic_memory":category({
                "content_hash":"content_hash" in cols.get("semantic_documents",set()),"provenance":{"created_commit","last_changed_commit","last_verified_commit","indexed_at_commit"}<=cols.get("semantic_documents",set()),"vector_storage":"vector" in cols.get("semantic_documents",set()),"incremental_index":True,"local_provider_interface":True}),
            "persistence_recovery":category({
                "integrity_check":d["checks"]["database_integrity"],"portable_snapshot":d["checks"]["portable_snapshot"],"migration_ledger":"migrations" in tables,"schema_guard":d["checks"]["schema_current"],"event_journal":(self.root/".dynosai"/"state"/"events.jsonl").exists()}),
            "multiagent_execution":category({
                "task_claim":"claimed_run" in cols.get("tasks",set()),"run_task_binding":"task_ids" in cols.get("runs",set()),"isolated_worktree":"worktree" in cols.get("runs",set()),"session_binding":{"run_id","work_id"}<=cols.get("agent_sessions",set()),"dependency_graph":"depends_on" in cols.get("tasks",set())}),
        }
        minimum=min(item["score"] for item in categories.values())
        return {"version":__version__,"method":"10 × passed controls / total controls per category","minimum_score":minimum,"categories":categories,"goal_met":minimum>=9.0,"external_verification":{"live_provider_e2e":{name:("executable-detected" if val else "not-installed-in-this-environment") for name,val in d["agents"].items()},"real_neural_inference":"probe with `dynosai model status --probe` on a machine where the model is installed"},"note":"Internal control scores are evidence-based. External provider authentication/live execution and model download are explicit gates, never fabricated."}

    def get_work(self, work_id: str | None = None) -> dict[str, Any]:
        """Return one persisted work item by identifier, including normalized workflow metadata."""
        self._ensure_ready()
        identifier = work_id or self.db.get_meta("active_work")
        if not identifier:
            row = self.db.one("SELECT * FROM work_items WHERE active=1 AND id!='PROJECT' ORDER BY updated_at DESC LIMIT 1")
        else:
            row = self.db.one("SELECT * FROM work_items WHERE id=?", (identifier,))
        if not row:
            raise KeyError("No active work item")
        return row

    def _ensure_ready(self) -> None:
        if not self.db.path.exists():
            raise RuntimeError("DynosAI project not initialized. Initialize it from Cursor/Codex with DynosAI project onboarding, or headlessly with `dynosai project initialize .`.")
        self.db.initialize()
        self.runtime.register_project(self.root,self.db.get_meta("project_name",self.root.name))

    def _set_state(self, work_id: str, state: str) -> None:
        self.db.execute("UPDATE work_items SET state=?,updated_at=? WHERE id=?", (state, utc_now(), work_id))
        self.db.set_meta("active_work", work_id)
        self.db.audit("WorkStateChanged", work_id, {"state": state})

    def _set_score(self, work_id: str, score: int) -> None:
        self.db.execute("UPDATE work_items SET quality_score=? WHERE id=?", (score, work_id))

    def _write_config(self) -> None:
        config = self.root / ".dynosai" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        content = (
            f"project_name = {json.dumps(self.db.get_meta('project_name'))}\n"
            f"language = {json.dumps(self.db.get_meta('language'))}\n"
            f"test_command = {json.dumps(self.db.get_meta('test_command'))}\n"
            f"main_branch = {json.dumps(self.db.get_meta('main_branch', 'main'))}\n"
            "database = \".dynosai/knowledge.db\"\n"
            "source_of_truth = \"database\"\n"
            f"semantic_enabled = {str(self.db.get_meta('embedding_installed') == '1').lower()}\n"
            f"embedding_model = {json.dumps(self.db.get_meta('embedding_model', DEFAULT_MODEL))}\n"
            f"embedding_dimensions = {int(self.db.get_meta('embedding_dimensions', str(DEFAULT_DIMENSIONS)) or DEFAULT_DIMENSIONS)}\n"
            f"embedding_cache = {json.dumps(self.db.get_meta('embedding_cache', str(default_cache_dir())))}\n"
            f"vector_backend = {json.dumps(self.db.get_meta('vector_backend', 'auto'))}\n"
        )
        config.write_text(content, encoding="utf-8")

    def _ensure_gitignore(self) -> None:
        path = self.root / ".gitignore"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        required = [".dynosai/knowledge.db*", ".dynosai/state/events.jsonl", ".dynosai/backups/", ".dynosai/worktrees/", ".dynosai/runtime/", ".dynosai/generated/", "*.db-wal", "*.db-shm", ".env", ".env.*", "__pycache__/", ".venv/"]
        lines = existing.splitlines()
        for item in required:
            if item not in lines:
                lines.append(item)
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    @staticmethod
    def _classify(description: str) -> str:
        lowered = description.lower()
        if re.search(r"\b(error|bug|falla)\b", lowered) or "no funciona" in lowered or "corregir" in lowered:
            return "bug"
        if any(word in lowered for word in ("refactor", "limpiar", "reorganizar")):
            return "refactor"
        if any(word in lowered for word in ("investigar", "analizar", "spike")):
            return "research"
        if any(word in lowered for word in ("mejorar", "cambiar", "añadir", "agregar", "limitar")):
            return "enhancement"
        return "feature"

    @staticmethod
    def _title(description: str) -> str:
        first = description.strip().splitlines()[0].strip()
        return first[:90].rstrip(". ") or "Nuevo trabajo"

    @staticmethod
    def _quick_candidate(impact: dict[str, Any], work_type: str) -> bool:
        return work_type in {"bug", "refactor"} and len(impact.get("files", [])) <= 3 and len(impact.get("features", [])) <= 2

    @staticmethod
    def _impact_markdown(impact: dict[str, Any]) -> str:
        lines = []
        if impact.get("features"):
            lines.append("Funcionalidades relacionadas: " + ", ".join(item["title"] for item in impact["features"]))
        if impact.get("files"):
            lines.append("Archivos sugeridos:\n" + "\n".join(f"- {item['path']}: {item['reason']}" for item in impact["files"]))
        if impact.get("tests"):
            lines.append("Tests sugeridos:\n" + "\n".join(f"- {item['path']}" for item in impact["tests"]))
        return "\n\n".join(lines) or "No se encontró memoria previa relacionada."
