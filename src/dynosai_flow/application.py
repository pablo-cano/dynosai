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
                r = subprocess.run(["git", "status", "--porcelain"], cwd=root, text=True, capture_output=True, timeout=8)
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
        agent_config=(AgentConfiguration(self.root).resolve(provider=("codex" if str(provider).lower()=="openai" else str(provider).lower()), state=work.get("state")) if str(provider).lower() in {"cursor","codex","openai","claude"} else None)
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
        return [dict(row) for row in rows]

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

    def project_overview(self, work_id: str | None = None) -> dict[str, Any]:
        """Aggregate the bounded read model used by Local Studio."""
        detection = self.detector.detect(self.root)
        overview: dict[str, Any] = {
            "detection": detection,
            "project": self.root.name,
            "initialized": bool(detection.get("has_dynosai")),
            "validations": self.validation_discovery(),
            "risk": self.risk_assessment(work_id),
        }
        if not detection.get("has_dynosai"):
            overview["work"] = []
            overview["status"] = None
            overview["blockers"] = self.explain_blockers(work_id)
            return overview
        self.engine.db.initialize()
        overview["project"] = self.engine.db.get_meta("project_name", self.root.name)
        overview["work"] = self.list_work(limit=12)
        overview["status"] = self.status(work_id)
        overview["blockers"] = self.explain_blockers(work_id)
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
        agent_config=(AgentConfiguration(self.root).resolve(provider=("codex" if str(provider).lower()=="openai" else str(provider).lower()), state=work.get("state")) if str(provider).lower() in {"cursor","codex","openai","claude"} else None)
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
        if interaction:
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
        agent_config=(AgentConfiguration(self.root).resolve(provider=("codex" if str(provider).lower()=="openai" else str(provider).lower()), state=work.get("state")) if str(provider).lower() in {"cursor","codex","openai","claude"} else None)
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
            self.events.emit("HumanInteractionResolved", work_id=work["id"], session_id=interaction.session_id, payload={"interaction_id": interaction.id, "action": action, "status": interaction.status, "scope_status": result.get("status")})
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
        self.events.emit("HumanInteractionResolved", work_id=work["id"], session_id=interaction.session_id, payload={"interaction_id": interaction.id, "action": action, "status": interaction.status})
        return {"interaction": interaction.to_dict(), "result": result, "work": work}

    def list_events(self, *, after_id: int = 0, work_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.events.list(after_id=after_id, work_id=work_id, limit=limit)
