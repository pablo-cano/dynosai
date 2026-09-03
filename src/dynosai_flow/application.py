# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Application-layer adapters that expose DynosAI workflows without binding to a specific provider."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from .engine import DynosAI
from .db import Database
from .git_manager import GitError
from .interactions import HumanInteractionRequest, HumanInteractionService
from .events import ApplicationEventBus
from .util import json_dumps, json_loads, utc_now
from .model_routing import ProviderModelRouting, activity_for_state
from .model_control import ModelControlPlane
from .token_usage import align_usage_to_activity
from .context_control import ContextCheckpointStore
from .agent_config import AgentConfiguration
from .validation_discovery import ValidationDiscovery
from .risk import RiskAssessment
from .validation_integrity import evaluate_integrity
from .cost_telemetry import aggregate_governed_change_cost, empty_governed_change_aggregate, governed_change_cost
from .secrets import redact_text
from .capability_manifests import capability_report, provider_manifest
from .certification_matrix import summarize_live_matrix
from .harness_contracts import harness_report_from_project


class ProjectDetector:
    def detect(self, root: str | Path) -> dict[str, Any]:
        root = Path(root).expanduser().resolve()
        exists = root.exists()
        provider_scaffolding = {
            ".DS_Store", ".git", ".dynosai", ".cursor", ".codex", ".agents",
            ".gitignore", ".mcp.json", "AGENTS.md", "CLAUDE.md",
        }
        files = [] if not exists else list(root.iterdir())
        # Provider/DynosAI bootstrap metadata must not turn an otherwise empty
        # greenfield directory into a brownfield project. Cursor acceptance, for
        # example, needs a project-local .cursor/mcp.json before the agent starts.
        has_code = any(p.name not in provider_scaffolding for p in files)
        has_git = (root / ".git").exists()
        db_path = root / ".dynosai" / "knowledge.db"
        has_dynosai = False
        if db_path.exists():
            try:
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
                    has_dynosai = bool(row)
                finally:
                    con.close()
            except sqlite3.Error:
                has_dynosai = False
        dirty = []
        if has_git:
            try:
                import subprocess
                r = subprocess.run(["git", "status", "--porcelain"], cwd=root, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=8)
                dirty = [x for x in r.stdout.splitlines() if x.strip()] if r.returncode == 0 else []
            except Exception:
                dirty = []
        probable_monorepo = False
        if has_code:
            markers = 0
            for child in files:
                if child.is_dir() and any((child / marker).exists() for marker in ("pyproject.toml", "package.json", "Cargo.toml", "pom.xml", ".git")):
                    markers += 1
            probable_monorepo = markers >= 2
        if has_dynosai: classification = "EXISTING_DYNOSAI_PROJECT"
        elif has_git and dirty: classification = "DIRTY_GIT_REPO"
        elif has_git and has_code: classification = "MONOREPO" if probable_monorepo else "EXISTING_GIT_REPO"
        elif has_code: classification = "NEW_CODE_NO_GIT"
        else: classification = "EMPTY_DIRECTORY"
        stacks: list[str] = []
        if exists:
            if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists(): stacks.append("python")
            if (root / "package.json").exists():
                stacks.append("typescript" if (root / "tsconfig.json").exists() else "javascript")
            if (root / "Cargo.toml").exists(): stacks.append("rust")
            if list(root.glob("*.sln")) or list(root.glob("*.csproj")): stacks.append("dotnet")
            if (root / "pom.xml").exists() or (root / "gradlew").exists(): stacks.append("java")
        primary_language = stacks[0] if stacks else "python"
        candidates = ValidationDiscovery(root).discover() if exists else []
        unit = next((item for item in candidates if item.get("name") == "unit"), None)
        suggested_test_command = " ".join(unit.get("command") or []) if unit else "pytest"
        return {
            "root": str(root), "exists": exists, "classification": classification,
            "has_code": has_code, "has_git": has_git, "has_dynosai": has_dynosai,
            "dirty": dirty, "probable_monorepo": probable_monorepo,
            "stacks": stacks, "primary_language": primary_language,
            "suggested_test_command": suggested_test_command,
            "validation_candidates": candidates,
        }


class ProviderSessionService:
    def __init__(self, engine: DynosAI):
        self.engine = engine

    def create_or_resume(self, provider: str, work_id: str, workspace: Path, strategy: str) -> dict[str, Any]:
        existing = self.engine.db.one(
            "SELECT * FROM provider_sessions WHERE provider=? AND work_id=? AND state='active' ORDER BY created_at DESC LIMIT 1",
            (provider, work_id),
        )
        if existing:
            self.engine.db.execute("UPDATE provider_sessions SET last_seen=? WHERE id=?", (utc_now(), existing["id"]))
            return {**existing, "resumed": True}
        sid = self.engine.db.next_id("PSES", "provider_sessions")
        now = utc_now()
        permissions = {"phase": self.engine.get_work(work_id)["state"], "write": False, "scope": []}
        self.engine.db.execute(
            "INSERT INTO provider_sessions(id,provider,work_id,workspace,strategy,state,permissions_json,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, provider, work_id, str(workspace), strategy, "active", json_dumps(permissions), now, now),
        )
        self.engine.db.execute("UPDATE work_items SET provider_session_id=?,workspace_strategy=?,updated_at=? WHERE id=?", (sid, strategy, now, work_id))
        self.engine.db.audit("ProviderSessionCreated", sid, {"provider": provider, "work_id": work_id, "workspace": str(workspace), "strategy": strategy})
        return {"id": sid, "provider": provider, "work_id": work_id, "workspace": str(workspace), "strategy": strategy, "state": "active", "permissions_json": json_dumps(permissions), "resumed": False}

    def active(self, provider: str, work_id: str | None = None) -> dict[str, Any] | None:
        if work_id:
            return self.engine.db.one("SELECT * FROM provider_sessions WHERE provider=? AND work_id=? AND state='active' ORDER BY created_at DESC LIMIT 1", (provider, work_id))
        return self.engine.db.one("SELECT * FROM provider_sessions WHERE provider=? AND state='active' ORDER BY last_seen DESC LIMIT 1", (provider,))

    def close_for_work(self, work_id: str, state: str = "completed") -> None:
        self.engine.db.execute("UPDATE provider_sessions SET state=?,last_seen=? WHERE work_id=? AND state='active'",(state,utc_now(),work_id))
        self.engine.db.audit("ProviderSessionClosed",work_id,{"state":state})

    def update_permissions(self, work_id: str) -> dict[str, Any] | None:
        work = self.engine.get_work(work_id)
        sid = work.get("provider_session_id")
        if not sid: return None
        write = work["state"] == "implementing"
        scope=[]
        if write:
            plan=self.engine.artifacts.artifact(work_id,"plan") or {}
            for item in (plan.get("metadata") or {}).get("files",[]):
                if str(item.get("action","")).lower() in {"modify","create","delete","test"} and item.get("path"):
                    scope.append(str(item["path"]).replace("\\","/"))
        permissions={"phase":work["state"],"write":write,"scope":sorted(set(scope))}
        self.engine.db.execute("UPDATE provider_sessions SET permissions_json=?,last_seen=? WHERE id=?",(json_dumps(permissions),utc_now(),sid))
        self.engine.db.audit("ProviderSessionPermissionsChanged",sid,permissions)
        return permissions


class DynosAIApplication:
    """Headless application API shared by MCP, CLI and future graphical clients."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.engine = DynosAI(self.root)
        self.detector = ProjectDetector()
        self.sessions = ProviderSessionService(self.engine)
        self.interactions = HumanInteractionService(self.engine.db)
        self.events = ApplicationEventBus(self.engine.db)

    def detect_project(self) -> dict[str, Any]:
        info = self.detector.detect(self.root)
        if info.get("has_dynosai"):
            try: self.engine.db.initialize(); self.events.emit("ProjectDetected", payload=info)
            except Exception: pass
        return info

    def initialize_project(
        self,
        *,
        name: str | None = None,
        agent: str | None = None,
        allow_git_init: bool = False,
        language: str | None = None,
        test_command: str | None = None,
    ) -> dict[str, Any]:
        info = self.detector.detect(self.root)
        cls = info["classification"]
        if cls == "EXISTING_DYNOSAI_PROJECT":
            self.engine.db.initialize(); self.engine.runtime.register_project(self.root, self.engine.db.get_meta("project_name", self.root.name))
            return {"action": "attach", "project": self.engine.db.get_meta("project_name", self.root.name), "detection": info, "status": self.status()}
        if cls == "DIRTY_GIT_REPO":
            raise GitError("Repository contains uncommitted changes. Commit/stash them or use analysis-only mode before adoption.")
        if cls in {"EXISTING_GIT_REPO", "MONOREPO"}:
            result = self.engine.adopt(
                name=name,
                agent=agent,
                language=language or info.get("primary_language") or "python",
                test_command=test_command or info.get("suggested_test_command") or "pytest",
            )
            return {"action": "adopt", "detection": info, **result}
        if cls == "NEW_CODE_NO_GIT" and not allow_git_init:
            return {"action": "analysis_only", "detection": info, "message": "Git is required before governed implementation."}
        result = self.engine.initialize(
            name=name,
            language=language or info.get("primary_language") or "python",
            test_command=test_command or info.get("suggested_test_command") or "pytest",
            agent=agent,
            mode="brownfield" if cls == "NEW_CODE_NO_GIT" else "greenfield",
        )
        if cls == "NEW_CODE_NO_GIT":
            result["bootstrap"] = self.engine.sync(bootstrap=True)
            result["baseline_specs"] = self.engine.bootstrap_brownfield_specs()
            self.engine.git.commit_main_changes("docs: add DynosAI brownfield baseline")
        return {"action": "initialize", "detection": info, **result}

    def start_feature(self, description: str, *, provider: str, workspace_strategy: str = "interactive_branch", at_provider_boundary: bool = True) -> dict[str, Any]:
        self.engine._ensure_ready()
        if workspace_strategy not in {"interactive_branch", "isolated_worktree"}:
            raise ValueError("workspace_strategy must be interactive_branch or isolated_worktree")
        if workspace_strategy == "interactive_branch":
            if self.engine.git.status(self.root):
                raise GitError("Interactive feature start requires a clean workspace")
            main = self.engine.db.get_meta("main_branch", self.engine.git.current_branch()) or "main"
            current = self.engine.git.current_branch(self.root)
            if current != main:
                raise GitError(f"Interactive feature start requires the baseline branch {main}; current branch is {current}")
        work = self.engine.start(description)
        if workspace_strategy == "interactive_branch":
            branch = self.engine.git.create_interactive_branch(work["id"], work["title"], require_clean=False)
            self.engine.db.execute("UPDATE work_items SET branch=?,worktree=?,workspace_strategy=?,updated_at=? WHERE id=?", (branch, str(self.root), workspace_strategy, utc_now(), work["id"]))
        else:
            self.engine.db.execute("UPDATE work_items SET workspace_strategy=?,updated_at=? WHERE id=?", (workspace_strategy, utc_now(), work["id"]))
        session = self.sessions.create_or_resume(provider, work["id"], self.root, workspace_strategy)
        self.engine.db.set_meta("default_agent", provider)
        model_control=None
        route=None
        if str(provider).lower() in {"cursor","codex","openai"}:
            normalized="codex" if str(provider).lower()=="openai" else str(provider).lower()
            model_control=ModelControlPlane(self.root).for_work(normalized,work,at_provider_boundary=at_provider_boundary).to_dict()
            route=model_control["route"]
        agent_config=(AgentConfiguration(self.root).resolve(provider=("codex" if str(provider).lower()=="openai" else str(provider).lower()), state=work.get("state")) if str(provider).lower() in {"cursor","codex","openai"} else None)
        checkpoint=ContextCheckpointStore(self.root).capture(self.engine,work["id"],label="work-start",reason="work_start")
        self.events.emit("WorkStarted", work_id=work["id"], session_id=session.get("id"), payload={"provider": provider, "workspace_strategy": workspace_strategy, "model_route": route, "model_control":model_control, "agent_config": agent_config,"context_checkpoint":checkpoint})
        return {**work, "provider_session": session, "workspace_strategy": workspace_strategy, "model_route": route, "model_control":model_control, "agent_config": agent_config,"context_checkpoint":checkpoint}

    def status(self, work_id: str | None = None) -> dict[str, Any]:
        try: work = self.engine.get_work(work_id)
        except Exception: work = None
        return {
            "project": self.engine.db.get_meta("project_name", self.root.name),
            "work": work,
            "pending_interactions": [x.to_dict() for x in self.interactions.pending_for_work(work["id"])] if work else [],
        }

    def list_work(self, *, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent governed work for graphical/headless clients."""
        self.engine.db.initialize()
        if state:
            rows = self.engine.db.query("SELECT * FROM work_items WHERE state=? ORDER BY updated_at DESC LIMIT ?", (state, limit))
        else:
            rows = self.engine.db.query("SELECT * FROM work_items ORDER BY updated_at DESC LIMIT ?", (limit,))
        return [self._decorate_work(dict(row)) for row in rows]

    def _decorate_work(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("id") == "PROJECT" or item.get("state") not in {"ready", "implementing"}:
            return item
        try:
            item["team"] = self.engine.schedule(item["id"])
        except Exception:
            pass
        return item

    def validation_discovery(self) -> dict[str, Any]:
        """Detect repository validation commands without implicitly approving them."""
        candidates = ValidationDiscovery(self.root).discover()
        approved: dict[str, dict[str, Any]] = {}
        info = self.detector.detect(self.root)
        if info.get("has_dynosai"):
            self.engine.db.initialize()
            for row in self.engine.db.query("SELECT name,command_json,source,approved FROM validation_profiles ORDER BY name"):
                try:
                    command = json_loads(row.get("command_json"), [])
                except Exception:
                    command = []
                approved[row["name"]] = {"command": command, "source": row.get("source"), "approved": bool(row.get("approved"))}
        return {"candidates": candidates, "approved": approved}

    def approve_discovered_validations(self, names: list[str] | None = None, *, overwrite: bool = False) -> dict[str, Any]:
        """Persist selected discovered validations after explicit user approval."""
        self.engine._ensure_ready()
        candidates = ValidationDiscovery(self.root).discover()
        result = ValidationDiscovery.apply(self.engine.db, candidates, names, overwrite=overwrite)
        self.events.emit("ValidationProfilesApproved", payload=result)
        return {**result, "profiles": self.validation_discovery()}

    def model_routing(self, provider: str | None = None) -> dict[str, Any]:
        """Expose effective provider/model routes for project-facing clients."""
        return ProviderModelRouting(self.root).show(provider)

    def set_project_model_route(
        self,
        provider: str,
        model: str,
        *,
        effort: str | None = None,
        activity: str | None = None,
    ) -> dict[str, Any]:
        """Persist a project-scoped model route without changing workflow authority."""
        result = ProviderModelRouting(self.root).set(
            provider, model, effort=effort, activity=activity, scope="project"
        )
        if self.detector.detect(self.root).get("has_dynosai"):
            self.events.emit(
                "ProjectModelRouteUpdated",
                payload={"provider": provider, "activity": activity, "model": model, "effort": effort},
            )
        return {**result, "effective": self.model_routing(provider)}

    def reset_project_model_route(self, provider: str, *, activity: str | None = None) -> dict[str, Any]:
        """Remove a project-scoped override and reveal the inherited route again."""
        result = ProviderModelRouting(self.root).reset(provider, activity=activity, scope="project")
        if self.detector.detect(self.root).get("has_dynosai"):
            self.events.emit(
                "ProjectModelRouteReset",
                payload={"provider": provider, "activity": activity},
            )
        return {**result, "effective": self.model_routing(provider)}

    def risk_assessment(self, work_id: str | None = None) -> dict[str, Any]:
        """Return a deterministic advisory risk score for the project/current work."""
        info = self.detector.detect(self.root)
        if info.get("has_dynosai"):
            self.engine.db.initialize()
            return RiskAssessment(self.root).assess(self.engine, work_id)
        return RiskAssessment(self.root).assess()

    def explain_blockers(self, work_id: str | None = None) -> dict[str, Any]:
        """Explain why the project/work cannot advance in user-facing terms."""
        info = self.detector.detect(self.root)
        items: list[dict[str, Any]] = []
        if not info.get("exists"):
            items.append({"code": "PROJECT.MISSING", "message": "The selected project directory does not exist.", "action": "Choose an existing directory."})
            return {"blocked": True, "items": items, "recommended_action": items[0]["action"]}
        if not info.get("has_dynosai"):
            items.append({"code": "PROJECT.NOT_INITIALIZED", "message": "DynosAI has not been initialized for this project.", "action": "Initialize or adopt the project."})
            if info.get("classification") == "DIRTY_GIT_REPO":
                items.append({"code": "GIT.DIRTY", "message": "The Git repository contains uncommitted changes.", "action": "Commit or stash the changes before adoption."})
            return {"blocked": True, "items": items, "recommended_action": items[0]["action"]}
        self.engine.db.initialize()
        try:
            work = self.engine.get_work(work_id)
        except Exception:
            return {"blocked": False, "items": [], "recommended_action": "Start a governed work item."}
        for interaction in self.interactions.pending_for_work(work["id"]):
            items.append({"code": f"GATE.{str(interaction.gate or interaction.kind).upper()}", "message": interaction.message, "action": "Review and resolve the pending decision.", "interaction_id": interaction.id})
        try:
            score, findings = self.engine.quality.health_score(work["id"])
        except Exception:
            score, findings = 100, []
        for finding in findings:
            if finding.severity == "blocking":
                items.append({"code": finding.code, "message": finding.message, "action": "Resolve the quality finding before the next governed gate.", "entity_id": finding.entity_id})
        try:
            schedule = self.engine.schedule(work["id"])
            if schedule.get("blocked"):
                items.append({"code": "TASK.DEPENDENCY", "message": f"{len(schedule['blocked'])} task(s) are waiting on dependencies.", "action": "Complete the ready task wave first."})
        except Exception:
            pass
        contract = self.engine.action_contract(work["id"]) or {}
        if not items and contract.get("human_gate"):
            items.append({"code": "GATE.REQUIRED", "message": str(contract.get("message") or "A human decision is required."), "action": "Review the current gate."})
        recommendation = items[0]["action"] if items else str(contract.get("next_action") or contract.get("message") or "Continue the governed workflow.")
        return {"blocked": bool(items), "items": items, "recommended_action": recommendation, "work_id": work["id"], "state": work.get("state"), "quality": score}

    def setup_status(self) -> dict[str, Any]:
        """Return a user-facing project setup model for Local Studio and other clients."""
        info = self.detector.detect(self.root)
        validations = self.validation_discovery()
        candidates = validations.get("candidates") or []
        approved = validations.get("approved") or {}
        approved_count = sum(1 for item in approved.values() if item.get("approved"))
        pending_checks = [item for item in candidates if not (approved.get(str(item.get("name") or "")) or {}).get("approved")]
        dirty_count = len(info.get("dirty") or [])
        initialized = bool(info.get("has_dynosai"))

        steps: list[dict[str, str]] = []
        if info.get("exists"):
            stacks = ", ".join(info.get("stacks") or []) or "project files"
            steps.append({"id": "project", "label": "Project detected", "state": "complete", "detail": f"Found {stacks}."})
        else:
            steps.append({"id": "project", "label": "Project detected", "state": "blocked", "detail": "The selected directory does not exist."})

        if info.get("has_git"):
            if dirty_count and not initialized:
                steps.append({"id": "git", "label": "Git is ready", "state": "blocked", "detail": f"{dirty_count} uncommitted change(s) must be committed or stashed before adoption."})
            elif dirty_count:
                steps.append({"id": "git", "label": "Git is ready", "state": "complete", "detail": f"Repository detected; {dirty_count} working-tree change(s) are currently present."})
            else:
                steps.append({"id": "git", "label": "Git is ready", "state": "complete", "detail": "Repository detected and the working tree is clean."})
        elif info.get("has_code"):
            steps.append({"id": "git", "label": "Git is ready", "state": "ready", "detail": "DynosAI can initialize Git before governed work starts."})
        else:
            steps.append({"id": "git", "label": "Git is ready", "state": "ready", "detail": "Git will be initialized with the new DynosAI project."})

        if initialized:
            steps.append({"id": "dynosai", "label": "DynosAI initialized", "state": "complete", "detail": "Durable governed project state is available."})
        elif dirty_count and info.get("has_git"):
            steps.append({"id": "dynosai", "label": "DynosAI initialized", "state": "blocked", "detail": "Initialization is blocked until the Git working tree is clean."})
        else:
            steps.append({"id": "dynosai", "label": "DynosAI initialized", "state": "ready", "detail": "Ready to create or adopt governed project state."})

        if candidates:
            if pending_checks:
                steps.append({"id": "checks", "label": "Project checks reviewed", "state": "ready", "detail": f"{len(pending_checks)} detected validation check(s) still need review."})
            else:
                steps.append({"id": "checks", "label": "Project checks reviewed", "state": "complete", "detail": f"{approved_count} governed validation check(s) approved."})
        else:
            steps.append({"id": "checks", "label": "Project checks reviewed", "state": "complete", "detail": "No common validation commands require approval."})

        if not info.get("exists"):
            primary = {"code": "inspect", "label": "Choose a valid project", "title": "Project folder not found", "description": "Launch Studio again with an existing project directory.", "short_status": "Project missing"}
        elif not initialized and dirty_count and info.get("has_git"):
            primary = {"code": "clean_git", "label": "Review Git changes", "title": "Clean the Git working tree first", "description": "DynosAI will not adopt a repository while it contains uncommitted changes. Commit or stash them, then refresh Studio.", "short_status": "Git needs attention"}
        elif not initialized:
            primary = {"code": "initialize", "label": "Initialize project", "title": "Initialize DynosAI for this project", "description": "Create durable local workflow state and connect the repository to DynosAI governance.", "short_status": "Needs initialization"}
        elif pending_checks:
            primary = {"code": "review_checks", "label": "Review project checks", "title": "Review the detected project checks", "description": "Choose which detected test, lint, type-check and build commands DynosAI may use as governed evidence.", "short_status": "Review checks"}
        else:
            primary = {"code": "new_task", "label": "Create a new task", "title": "Ready for governed work", "description": "Describe the outcome you want and DynosAI will guide the specification, plan, implementation, validation and review flow.", "short_status": "Ready"}

        ready = initialized and not pending_checks
        return {
            "ready": ready,
            "initialized": initialized,
            "dirty_count": dirty_count,
            "candidate_checks": len(candidates),
            "approved_checks": approved_count,
            "pending_checks": len(pending_checks),
            "steps": steps,
            "primary_action": primary,
        }

    def _completion_review_detail(self, work_id: str, summary: dict[str, Any]) -> dict[str, Any]:
        """Bounded Requirement → Evidence → Diff → Validation view for code/merge gates."""
        extra: dict[str, Any] = {}
        try:
            risk = self.risk_assessment(work_id)
            extra["integrity"] = evaluate_integrity(self.engine, work_id, risk_level=str((risk or {}).get("level") or ""))
            extra["cost"] = governed_change_cost(self.engine.db, work_id)
            extra["cost"]["requirements_satisfied"] = extra["integrity"].get("requirements_satisfied")
        except Exception:
            extra.setdefault("integrity", {})
        validations = self.engine.db.query(
            "SELECT command,status,exit_code,created_at FROM validations WHERE work_id=? ORDER BY id DESC LIMIT 8",
            (work_id,),
        )
        extra["validations"] = [
            {"command": row.get("command"), "status": row.get("status"), "exit_code": row.get("exit_code"), "created_at": row.get("created_at")}
            for row in validations
        ]
        evidence = self.engine.db.query(
            "SELECT id,task_id,kind,source,status,created_at FROM evidence WHERE work_id=? ORDER BY created_at DESC LIMIT 8",
            (work_id,),
        )
        extra["evidence"] = [
            {"id": row.get("id"), "task_id": row.get("task_id"), "kind": row.get("kind"), "status": row.get("status")}
            for row in evidence
        ]
        try:
            run = self.engine.db.one("SELECT worktree,base_commit FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,))
            cwd = Path(run["worktree"]) if run and run.get("worktree") else self.engine.root
            base = run.get("base_commit") if run else None
            diff = self.engine.git.policy_filtered_diff(cwd, base, max_chars=8000)
            extra["diff"] = {
                "files": diff.get("files") or [],
                "denied": diff.get("denied") or [],
                "truncated": bool(diff.get("truncated")),
                "text": redact_text(str(diff.get("diff") or ""))[:8000],
            }
        except Exception:
            extra["diff"] = {"files": [], "denied": [], "truncated": False, "text": ""}
        extra["unresolved_risks"] = [
            item.get("message") or item.get("code") for item in ((summary.get("findings") or [])[:8])
        ]
        extra["final_state"] = (summary.get("work") or {}).get("state")
        try:
            extra["execution_policy"] = self.engine.execution_policy()
        except Exception:
            extra["execution_policy"] = {}
        return extra

    def list_reviews(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return pending human decisions with bounded, user-facing review context."""
        info = self.detector.detect(self.root)
        if not info.get("has_dynosai"):
            return []
        self.engine.db.initialize()
        rows = self.engine.db.query(
            "SELECT h.*, w.title AS work_title, w.state AS work_state "
            "FROM human_interactions h LEFT JOIN work_items w ON w.id=h.work_id "
            "WHERE h.status='pending' ORDER BY h.created_at LIMIT ?",
            (limit,),
        )
        reviews: list[dict[str, Any]] = []
        for row in rows:
            item = {
                "id": row["id"],
                "work_id": row.get("work_id"),
                "work_title": row.get("work_title"),
                "work_state": row.get("work_state"),
                "session_id": row.get("session_id"),
                "kind": row.get("kind"),
                "gate": row.get("gate"),
                "message": row.get("message"),
                "requested_schema": json_loads(row.get("schema_json"), {}),
                "provider": row.get("provider"),
                "created_at": row.get("created_at"),
            }
            work_id = row.get("work_id")
            if work_id and row.get("kind") == "gate_approval":
                try:
                    summary = self.engine.review_summary(work_id)
                    spec = ((summary.get("spec") or {}).get("metadata") or {})
                    plan = ((summary.get("plan") or {}).get("metadata") or {})
                    findings = summary.get("findings") or []
                    item["detail"] = {
                        "spec": {
                            "objective": spec.get("objective"),
                            "requirements": (spec.get("requirements") or [])[:12],
                            "acceptance_criteria": (spec.get("acceptance_criteria") or [])[:18],
                        } if spec else None,
                        "plan": {
                            "approach": plan.get("approach"),
                            "tasks": (plan.get("tasks") or [])[:20],
                            "files": (plan.get("files") or [])[:30],
                            "risks": (plan.get("risks") or [])[:8],
                            "architecture_delta": (plan.get("architecture_delta") or [])[:12],
                            "task_count": len(plan.get("tasks") or []),
                            "file_count": len(plan.get("files") or []),
                        } if plan else None,
                        "tasks": (summary.get("tasks") or [])[:20],
                        "quality": {
                            "score": summary.get("quality_score", 100),
                            "findings": findings[:20],
                            "finding_count": len(findings),
                            "blocking": sum(1 for finding in findings if str(finding.get("severity") or "") == "blocking"),
                        },
                    }
                    if row.get("gate") in {"code", "merge"}:
                        item["detail"].update(self._completion_review_detail(work_id, summary))
                except Exception:
                    item["detail"] = {}
            reviews.append(item)
        return reviews

    def auto_approve_enabled(self) -> bool:
        info = self.detector.detect(self.root)
        if not info.get("has_dynosai"):
            return False
        try:
            self.engine.db.initialize()
            return self.engine.db.get_meta("studio_auto_approve", "0").strip().lower() in {"1", "true", "yes", "on"}
        except Exception:
            return False

    def set_auto_approve(self, enabled: bool) -> dict[str, Any]:
        """Persist Studio auto-approval and apply it to pending workflow gates."""
        self.engine._ensure_ready()
        self.engine.db.set_meta("studio_auto_approve", "1" if enabled else "0")
        applied: list[dict[str, Any]] = []
        if enabled:
            discovery = self.validation_discovery()
            pending_checks = [
                str(item.get("name") or "")
                for item in (discovery.get("candidates") or [])
                if str(item.get("name") or "") and not (discovery.get("approved") or {}).get(item.get("name") or {}, {}).get("approved")
            ]
            if pending_checks:
                self.approve_discovered_validations(pending_checks)
            for work in self.list_work(limit=100):
                work_id = str(work.get("id") or "")
                if not work_id or work_id == "PROJECT" or str(work.get("state") or "") == "done":
                    continue
                applied.extend(self.apply_auto_approvals(work_id))
        return {"auto_approve": enabled, "applied": applied}

    def execution_policy(self) -> dict[str, Any]:
        """Host-visible execution profile evidence. The model cannot change this."""
        try:
            return self.engine.execution_policy()
        except Exception:
            return {"profile": "balanced", "os_network_enforcement": False, "human_gates": "required", "enforcement": "decision_only"}

    def set_execution_profile(self, name: str) -> dict[str, Any]:
        """Persist Strict/Balanced/Autonomous. Does not skip human gates or spawn providers."""
        self.engine._ensure_ready()
        evidence = self.engine.set_execution_profile(name)
        self.events.emit("ExecutionProfileSet", payload={"profile": evidence.get("profile"), "os_network_enforcement": False})
        return evidence

    def harness_report(self) -> dict[str, Any]:
        """Host-visible optional harness settings. Agents cannot change these through MCP."""
        info = self.detector.detect(self.root)
        if not info.get("has_dynosai"):
            return harness_report_from_project()
        try:
            return self.engine.harness_report()
        except Exception:
            return harness_report_from_project()

    def set_harness_features(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Persist optional harness settings. This is a host/Studio action, not an MCP tool."""
        self.engine._ensure_ready()
        report = self.engine.set_harness_features(updates)
        self.events.emit("HarnessFeaturesSet", payload={"features": {item["name"]: item["enabled"] for item in report.get("features") or []}})
        return report

    def provider_capabilities(self, name: str | None = None) -> dict[str, Any]:
        """Host-owned adapter inventory. Additional clients are not assumed certified."""
        if name:
            return provider_manifest(name)
        return capability_report()

    def apply_auto_approvals(self, work_id: str) -> list[dict[str, Any]]:
        """Approve pending specification/plan/code/merge gates and ordinary scope requests when auto-mode is on."""
        if not self.auto_approve_enabled():
            return []
        applied: list[dict[str, Any]] = []
        for pending in list(self.interactions.pending_for_work(work_id)):
            if pending.kind not in {"gate_approval", "scope_extension"}:
                continue
            try:
                self.resolve_interaction(pending.id, "accept", {"decision": "approve", "source": "studio_auto"})
            except GitError:
                break
            applied.append({"interaction_id": pending.id, "gate": pending.gate, "kind": pending.kind, "source": "studio_auto"})
        return applied

    def list_review_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return resolved human decisions, including automatic Studio approvals."""
        info = self.detector.detect(self.root)
        if not info.get("has_dynosai"):
            return []
        self.engine.db.initialize()
        rows = self.engine.db.query(
            "SELECT h.*, w.title AS work_title, w.state AS work_state "
            "FROM human_interactions h LEFT JOIN work_items w ON w.id=h.work_id "
            "WHERE h.status!='pending' ORDER BY COALESCE(h.resolved_at, h.created_at) DESC LIMIT ?",
            (limit,),
        )
        history: list[dict[str, Any]] = []
        for row in rows:
            response = json_loads(row.get("response_json"), {}) or {}
            source = "studio_auto" if str(response.get("source") or "") == "studio_auto" else "human"
            history.append({
                "id": row["id"],
                "work_id": row.get("work_id"),
                "work_title": row.get("work_title"),
                "work_state": row.get("work_state"),
                "kind": row.get("kind"),
                "gate": row.get("gate"),
                "message": row.get("message"),
                "status": row.get("status"),
                "source": source,
                "decision": response.get("decision"),
                "created_at": row.get("created_at"),
                "resolved_at": row.get("resolved_at"),
            })
        return history

    def project_overview(self, work_id: str | None = None) -> dict[str, Any]:
        """Aggregate the bounded read model used by Local Studio."""
        detection = self.detector.detect(self.root)
        overview: dict[str, Any] = {
            "detection": detection,
            "project": self.root.name,
            "initialized": bool(detection.get("has_dynosai")),
            "validations": self.validation_discovery(),
            "risk": self.risk_assessment(work_id),
            "setup": self.setup_status(),
        }
        if not detection.get("has_dynosai"):
            overview["work"] = []
            overview["status"] = None
            overview["blockers"] = self.explain_blockers(work_id)
            overview["auto_approve"] = False
            overview["execution_policy"] = {"profile": "balanced", "os_network_enforcement": False, "human_gates": "required", "enforcement": "decision_only"}
            overview["provider_capabilities"] = capability_report()
            overview["harness"] = harness_report_from_project()
            overview["live_matrix"] = summarize_live_matrix()
            overview["governed_change"] = empty_governed_change_aggregate()
            return overview
        self.engine.db.initialize()
        overview["project"] = self.engine.db.get_meta("project_name", self.root.name)
        overview["work"] = self.list_work(limit=12)
        overview["status"] = self.status(work_id)
        overview["blockers"] = self.explain_blockers(work_id)
        overview["review_count"] = len(self.list_reviews(limit=100))
        overview["auto_approve"] = self.auto_approve_enabled()
        try:
            overview["execution_policy"] = self.engine.execution_policy()
        except Exception:
            overview["execution_policy"] = {"profile": "balanced", "os_network_enforcement": False, "human_gates": "required", "enforcement": "decision_only"}
        try:
            overview["eval_intelligence"] = self.engine.eval_intelligence()
        except Exception:
            overview["eval_intelligence"] = {"cases": [], "predictive_routing": "shadow", "live_provider_evals": False}
        overview["provider_capabilities"] = capability_report()
        overview["harness"] = self.harness_report()
        overview["live_matrix"] = summarize_live_matrix()
        try:
            overview["governed_change"] = aggregate_governed_change_cost(self.engine.db)
        except Exception:
            overview["governed_change"] = empty_governed_change_aggregate()
        return overview

    def resume(self, provider: str, work_id: str | None = None, *, at_provider_boundary: bool = True) -> dict[str, Any]:
        work = self.engine.get_work(work_id)
        strategy = work.get("workspace_strategy") or "interactive_branch"
        workspace = Path(work.get("worktree") or self.root)
        session = self.sessions.create_or_resume(provider, work["id"], workspace, strategy)
        permissions = self.sessions.update_permissions(work["id"])
        model_control=None
        route=None
        if str(provider).lower() in {"cursor","codex","openai"}:
            normalized="codex" if str(provider).lower()=="openai" else str(provider).lower()
            model_control=ModelControlPlane(self.root).for_work(normalized,work,at_provider_boundary=at_provider_boundary).to_dict()
            route=model_control["route"]
        agent_config=(AgentConfiguration(self.root).resolve(provider=("codex" if str(provider).lower()=="openai" else str(provider).lower()), state=work.get("state")) if str(provider).lower() in {"cursor","codex","openai"} else None)
        checkpoint=ContextCheckpointStore(self.root).reference(work["id"])
        self.events.emit("WorkResumed", work_id=work["id"], session_id=session.get("id"), payload={"provider": provider, "state": work.get("state"), "model_route": route, "model_control":model_control, "agent_config": agent_config,"context_checkpoint":checkpoint})
        return {"work": work, "provider_session": session, "permissions": permissions, "next": self.engine.action_contract(work["id"]), "model_route": route, "model_control":model_control, "agent_config": agent_config,"context_checkpoint":checkpoint}

    def next_action(self, provider: str, work_id: str | None = None, execute: bool = False) -> dict[str, Any]:
        work = self.engine.get_work(work_id)
        transition = None
        checkpoint_store=ContextCheckpointStore(self.root)
        checkpoint=None
        if execute:
            before = work["state"]
            raw = self.engine.continue_work(work["id"])
            work = self.engine.get_work(work["id"])
            transition = {"from_state": before, "to_state": work["state"], "message": raw.get("message"), "run_id": raw.get("run_id")}
            if before != work["state"]:
                checkpoint=checkpoint_store.capture(self.engine,work["id"],label=f"{activity_for_state(before)}-to-{activity_for_state(work['state'])}",reason="phase_boundary")
            self.events.emit("StateChanged", work_id=work["id"], session_id=work.get("provider_session_id"), payload={**transition,"context_checkpoint":checkpoint})
        self.sessions.update_permissions(work["id"])
        session = self.sessions.active(provider, work["id"])
        interaction = self.interactions.gate_request(work, session_id=(session or {}).get("id"), provider=provider)
        if interaction and interaction.kind == "gate_approval" and self.auto_approve_enabled():
            self.resolve_interaction(interaction.id, "accept", {"decision": "approve", "source": "studio_auto"})
            work = self.engine.get_work(work["id"])
            interaction = None
        elif interaction:
            # Dedupe in HumanInteractionService means repeated polling does not create
            # a second logical request; event consumers can safely correlate by id.
            previous = self.engine.db.one("SELECT COUNT(*) AS n FROM application_events WHERE event_type='HumanInteractionRequested' AND payload LIKE ?", (f'%{interaction.id}%',))
            if not previous or int(previous.get("n") or 0) == 0:
                self.events.emit("HumanInteractionRequested", work_id=work["id"], session_id=(session or {}).get("id"), payload={"interaction_id": interaction.id, "kind": interaction.kind, "gate": interaction.gate})
        model_control=None
        route=None
        if str(provider).lower() in {"cursor","codex","openai"}:
            normalized="codex" if str(provider).lower()=="openai" else str(provider).lower()
            usage=None
            usage_path=os.environ.get("DYNOSAI_TOKEN_USAGE_FILE")
            if usage_path:
                try:
                    import json as _json
                    usage=_json.loads(Path(usage_path).read_text(encoding="utf-8"))
                except Exception: usage=None
            # The workflow transition is authoritative for phase identity. The
            # token recorder may be owned by the acceptance/provider harness in a
            # different process and can observe this boundary slightly later.
            # Align the read-only usage view *before* model-control evaluation so
            # a known new phase starts at phase_live=0 instead of process_fallback.
            usage=align_usage_to_activity(usage, activity_for_state(work.get("state")))
            model_control=ModelControlPlane(self.root).for_work(normalized,work,usage=usage,at_provider_boundary=False).to_dict()
            route=model_control["route"]
        agent_config=(AgentConfiguration(self.root).resolve(provider=("codex" if str(provider).lower()=="openai" else str(provider).lower()), state=work.get("state")) if str(provider).lower() in {"cursor","codex","openai"} else None)
        latest_checkpoint=checkpoint_store.reference(work["id"])
        return {
            "work": {k: work.get(k) for k in ("id","title","work_type","state","quality_score","workspace_strategy","provider_session_id")},
            "model_route": route,
            "model_control": model_control,
            "agent_config": agent_config,
            "context_checkpoint": latest_checkpoint,
            "next": self.engine.next_action(work["id"]),
            "contract": self.engine.action_contract(work["id"]),
            "transition": transition,
            "human_interaction": interaction.to_dict() if interaction else None,
        }

    def resolve_interaction(self, interaction_id: str, action: str, content: dict[str, Any] | None = None) -> dict[str, Any]:
        interaction = self.interactions.resolve(interaction_id, action, content)
        if not interaction.work_id:
            return {"interaction": interaction.to_dict()}
        payload = content or {}
        # Scope elicitations are terminal for the linked SCOPE row even when the
        # provider answers the form with MCP action=accept and decision=request_changes.
        # They must not cancel the whole work item and must never remain pending.
        if interaction.kind == "scope_extension":
            scope_id = None
            if interaction.dedupe_key and str(interaction.dedupe_key).startswith("scope:"):
                scope_id = str(interaction.dedupe_key).split(":", 1)[1]
            decision = str(payload.get("decision") or ("cancel" if action == "cancel" else "request_changes" if action == "decline" else "approve")).lower()
            if decision == "approve" and action == "accept":
                result = self.engine.approve_scope_request(scope_id) if scope_id else {"approved": False, "message": "Scope interaction is not linked to a request"}
            else:
                terminal = "cancel" if action == "cancel" or decision == "cancel" else "request_changes"
                result = self.engine.resolve_scope_request(scope_id, terminal, str(payload.get("comments") or "")) if scope_id else {"approved": False, "message": "Scope interaction is not linked to a request"}
            work = self.engine.get_work(interaction.work_id)
            self.sessions.update_permissions(work["id"])
            self.events.emit("HumanInteractionResolved", work_id=work["id"], session_id=interaction.session_id, payload={"interaction_id": interaction.id, "action": action, "status": interaction.status, "scope_status": result.get("status"), "source": payload.get("source")})
            return {"interaction": interaction.to_dict(), "result": result, "work": work}
        if action == "cancel":
            self.engine.db.execute("UPDATE work_items SET active=0,updated_at=? WHERE id=?", (utc_now(), interaction.work_id))
            self.sessions.close_for_work(interaction.work_id,"cancelled")
            return {"interaction": interaction.to_dict(), "cancelled": True}
        if action == "decline":
            return {"interaction": interaction.to_dict(), "declined": True}
        if interaction.kind == "gate_approval":
            decision = str(payload.get("decision") or "approve")
            comments = str(payload.get("comments") or "").strip()
            if decision == "request_changes":
                if not comments: raise ValueError("comments are required when requesting changes")
                result = self.engine.review(interaction.work_id, changes=comments)
            elif decision == "cancel":
                self.engine.db.execute("UPDATE work_items SET active=0,updated_at=? WHERE id=?", (utc_now(), interaction.work_id)); result={"cancelled":True}
            else:
                result = self.engine.review(interaction.work_id, approve=interaction.gate)
        elif interaction.kind == "clarification":
            answer = str(payload.get("answer") or "").strip()
            if not answer: raise ValueError("answer is required")
            result = self.engine.register_decision(interaction.work_id, interaction.message, answer)
        else:
            result = {"accepted": True}
        work = self.engine.get_work(interaction.work_id)
        if work.get("state")=="done": self.sessions.close_for_work(work["id"],"completed")
        else: self.sessions.update_permissions(work["id"])
        self.events.emit("HumanInteractionResolved", work_id=work["id"], session_id=interaction.session_id, payload={"interaction_id": interaction.id, "action": action, "status": interaction.status, "source": (content or {}).get("source")})
        return {"interaction": interaction.to_dict(), "result": result, "work": work}

    def list_events(self, *, after_id: int = 0, work_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.events.list(after_id=after_id, work_id=work_id, limit=limit)

    def propose_eval_improvement(self, case_id: str) -> dict[str, Any]:
        """Host-owned eval improvement: inbox work only, no provider spawn."""
        result = self.engine.propose_eval_improvement(case_id)
        self.events.emit(
            "EvalImprovementProposed",
            work_id=(result.get("work") or {}).get("id"),
            payload={"case_id": case_id, "spawn_provider": False, "auto_start": False},
        )
        return result

    def import_acceptance_bundle(self, path: str) -> dict[str, Any]:
        """Host-owned acceptance ZIP import: bounded eval cases, inbox-only, no provider spawn."""
        result = self.engine.import_acceptance_bundle(path)
        self.events.emit(
            "AcceptanceBundleImported",
            payload={"imported": result.get("imported"), "spawn_provider": False, "auto_start": False},
        )
        return result
