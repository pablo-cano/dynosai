# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""HTTP application server backing DynosAI Studio."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .application import DynosAIApplication
from .git_manager import GitError
from .studio_projects import StudioProjectRegistry, browse_directory, create_directory
from .version import __version__
from .capability_manifests import SHIPPED_PROVIDERS, capability_report, provider_manifest


SUPPORTED_STUDIO_PROVIDERS = set(SHIPPED_PROVIDERS)


class StudioExecutionManager:
    """Launch and observe coding-agent work without blocking the Studio HTTP UI.

    Studio owns the host-side launch. The coding agent still talks to DynosAI
    through the existing MCP/governance contract, so browser code never gains
    shell or database authority.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._runs: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _provider_command(provider: str) -> str | None:
        provider = str(provider).lower()
        override = os.environ.get("DYNOSAI_CURSOR_BIN" if provider == "cursor" else "DYNOSAI_CODEX_BIN")
        if override:
            return override
        if provider == "cursor":
            # ``agent`` is Cursor's current primary CLI entrypoint;
            # ``cursor-agent`` remains a backwards-compatible alias.
            return shutil.which("cursor-agent") or shutil.which("agent")
        return shutil.which("codex")

    def available(self, provider: str) -> bool:
        return bool(self._provider_command(provider))

    @staticmethod
    def _key(root: str | Path, work_id: str) -> tuple[str, str]:
        return (str(Path(root).expanduser().resolve()), str(work_id))

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in record.items() if k not in {"process", "stdout_handle", "stderr_handle"}}

    @staticmethod
    def _tail(path: str | Path | None, *, max_chars: int = 5000) -> str:
        if not path:
            return ""
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        # Provider logs may contain terminal colour/control sequences even when
        # launched without a console. Studio diagnostics should be readable text.
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        return text[-max_chars:].strip()

    @staticmethod
    def _progress_entries(*texts: str, limit: int = 40) -> list[dict[str, Any]]:
        """Extract bounded user-facing provider progress from Studio logs."""
        out: list[dict[str, Any]] = []
        for text in texts:
            for raw in str(text or "").splitlines():
                marker = "DYNOSAI_PROGRESS|"
                if marker not in raw:
                    continue
                value = raw.split(marker, 1)[1].strip()
                at = None
                message = value
                if "|" in value:
                    maybe_at, rest = value.split("|", 1)
                    if "T" in maybe_at and len(maybe_at) >= 19 and maybe_at[:4].isdigit():
                        at = maybe_at
                        message = rest.strip()
                if message and (not out or out[-1]["text"] != message or out[-1].get("at") != at):
                    out.append({"text": message[:500], "at": at})
        return out[-limit:]

    @classmethod
    def _progress_lines(cls, *texts: str, limit: int = 40) -> list[str]:
        return [item["text"] for item in cls._progress_entries(*texts, limit=limit)]

    @staticmethod
    def _file_activity(path: str | Path | None) -> float:
        if not path:
            return 0.0
        try:
            return Path(path).stat().st_mtime
        except OSError:
            return 0.0

    def status(self, root: str | Path, work_id: str | None = None) -> dict[str, Any]:
        root_key = str(Path(root).expanduser().resolve())
        with self._lock:
            rows = [self._public(row.copy()) for (project, wid), row in self._runs.items() if project == root_key and (work_id is None or wid == str(work_id))]
        for row in rows:
            stderr_tail = self._tail(row.get("stderr"), max_chars=12000)
            stdout_tail = self._tail(row.get("stdout"), max_chars=8000)
            row["activity_log"] = self._progress_entries(stderr_tail, stdout_tail)
            row["activity"] = [item["text"] for item in row["activity_log"]]
            row["stderr_tail"] = stderr_tail[-5000:]
            row["stdout_tail"] = stdout_tail[-3000:]
            latest = max(self._file_activity(row.get("stderr")), self._file_activity(row.get("stdout")))
            if latest:
                from datetime import datetime, timezone
                row["last_activity_at"] = datetime.fromtimestamp(latest, timezone.utc).isoformat()
        rows.sort(key=lambda row: str(row.get("started_at") or ""), reverse=True)
        return {"items": rows}

    def start(self, root: str | Path, provider: str, work_id: str, *, on_exit=None) -> dict[str, Any]:
        root = Path(root).expanduser().resolve()
        provider = str(provider).lower()
        key = self._key(root, work_id)
        with self._lock:
            previous = self._runs.get(key)
            if previous and previous.get("status") == "running":
                proc = previous.get("process")
                if proc is not None and proc.poll() is None:
                    return self._public(previous.copy())

        executable = self._provider_command(provider)
        if not executable:
            record = {
                "work_id": str(work_id), "provider": provider, "status": "unavailable",
                "message": f"{provider} is not installed or is not available to DynosAI Studio.",
            }
            with self._lock:
                self._runs[key] = record
            return record.copy()

        log_dir = root / ".dynosai" / "runtime" / "studio"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / f"{work_id}-{provider}.stdout.log"
        stderr_path = log_dir / f"{work_id}-{provider}.stderr.log"
        stdout_handle = stdout_path.open("a", encoding="utf-8")
        stderr_handle = stderr_path.open("a", encoding="utf-8")
        command = [sys.executable, "-m", "dynosai_flow.cli", "--project", str(root), "open", "--agent", provider, "--headless"]
        child_env = os.environ.copy()
        child_env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1"})
        kwargs: dict[str, Any] = {
            "cwd": root, "stdin": subprocess.DEVNULL, "stdout": stdout_handle, "stderr": stderr_handle,
            "env": child_env,
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(command, **kwargs)
        except Exception as exc:
            stdout_handle.close(); stderr_handle.close()
            record = {
                "work_id": str(work_id), "provider": provider, "status": "failed",
                "message": str(exc), "stdout": str(stdout_path), "stderr": str(stderr_path),
            }
            with self._lock:
                self._runs[key] = record
            return record.copy()

        from .util import utc_now
        launch_state = ""
        try:
            launch_state = str(DynosAIApplication(root).engine.get_work(work_id).get("state") or "")
        except Exception:
            pass
        record = {
            "work_id": str(work_id), "provider": provider, "status": "running", "pid": process.pid,
            "started_at": utc_now(), "stdout": str(stdout_path), "stderr": str(stderr_path),
            "message": "Coding agent is working.", "process": process,
            "stdout_handle": stdout_handle, "stderr_handle": stderr_handle,
            "launch_state": launch_state,
        }
        with self._lock:
            self._runs[key] = record

        def watch() -> None:
            return_code = process.wait()
            stdout_handle.close(); stderr_handle.close()
            outcome: dict[str, Any] = {}
            if on_exit is not None:
                try:
                    outcome = on_exit(root, provider, str(work_id), return_code) or {}
                except Exception as exc:  # keep Studio observable even if reconciliation fails
                    outcome = {"status": "failed", "message": f"Workflow reconciliation failed: {exc}"}
            with self._lock:
                current = self._runs.get(key, record)
                stderr_tail=self._tail(current.get("stderr"))
                stdout_tail=self._tail(current.get("stdout"))
                current.update({
                    "exit_code": return_code,
                    "finished_at": utc_now(),
                    "status": outcome.get("status") or ("failed" if return_code else "stopped"),
                    "message": outcome.get("message") or ("Coding agent exited with an error." if return_code else "Coding agent stopped before the workflow reached a review or completion."),
                    "work_state": outcome.get("work_state"),
                    "stderr_tail": stderr_tail,
                    "stdout_tail": stdout_tail,
                })
                current.pop("process", None); current.pop("stdout_handle", None); current.pop("stderr_handle", None)

        threading.Thread(target=watch, name=f"dynosai-studio-{work_id}", daemon=True).start()
        return self._public(record.copy())


class StudioAPI:
    """Provider-neutral JSON API for Studio.

    A Studio process can start without selecting a repository. Project-scoped
    application state is constructed only after the user explicitly opens or
    creates a project, or when ``--project`` is supplied at launch time.
    """

    def __init__(self, root: str | Path | None = None, *, registry_path: str | Path | None = None, execution_manager: StudioExecutionManager | None = None):
        self._lock = threading.RLock()
        self.registry = StudioProjectRegistry(registry_path)
        self.execution = execution_manager or StudioExecutionManager()
        self.root: Path | None = None
        self.app: DynosAIApplication | None = None
        if root is not None:
            self.switch_project(root)

    @property
    def has_project(self) -> bool:
        return self.root is not None and self.app is not None

    def _require_app(self) -> DynosAIApplication:
        if self.app is None:
            raise RuntimeError("no project is selected")
        return self.app

    def switch_project(self, path: str | Path) -> dict[str, Any]:
        project = self.registry.resolve_project(path)
        with self._lock:
            self.root = project
            self.app = DynosAIApplication(project)
            entry = self.registry.touch(project)
        return {"opened": True, "project": entry, "root": str(project), "overview": self.app.project_overview()}

    def close_project(self) -> dict[str, Any]:
        with self._lock:
            previous = str(self.root) if self.root else None
            self.root = None
            self.app = None
        return {"closed": True, "previous": previous, "current": None}

    def _provider_for_work(self, app: DynosAIApplication, work_id: str) -> str:
        app.engine.db.initialize()
        provider = str(app.engine.db.get_meta("default_agent", "codex") or "codex").lower()
        return provider if provider in SUPPORTED_STUDIO_PROVIDERS else "codex"

    @staticmethod
    def _settle_work(root: Path, provider: str, work_id: str) -> dict[str, Any]:
        """Finish host-owned deterministic transitions after an agent returns."""
        app = DynosAIApplication(root)
        app.engine._ensure_ready()
        for _ in range(8):
            work = app.engine.get_work(work_id)
            state = str(work.get("state") or "")
            before = state
            if state == "inbox":
                app.next_action(provider, work_id, execute=True)
            elif state == "discovery":
                if not app.engine.artifacts.artifact(work_id, "spec"):
                    break
                app.next_action(provider, work_id, execute=True)
            elif state == "implementing":
                run = app.engine.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,))
                remaining = int((app.engine.db.one("SELECT COUNT(*) n FROM tasks WHERE work_id=? AND state!='completed'", (work_id,)) or {"n": 0})["n"] or 0)
                if not run or run.get("status") != "verified" or remaining:
                    break
                app.next_action(provider, work_id, execute=True)
            elif state == "validating":
                try:
                    app.next_action(provider, work_id, execute=True)
                except GitError:
                    break
            elif state == "ready_to_merge":
                try:
                    app.next_action(provider, work_id, execute=False)
                except GitError:
                    break
            else:
                break
            after = str(app.engine.get_work(work_id).get("state") or "")
            if after == before:
                break
        latest = app.engine.get_work(work_id)
        try:
            app.next_action(provider, work_id, execute=False)
        except GitError as exc:
            return {"status": "stopped", "message": str(exc), "work_state": str(latest.get("state") or "")}
        pending = app.interactions.pending_for_work(work_id)
        latest = app.engine.get_work(work_id)
        state = str(latest.get("state") or "")
        if pending:
            return {"status": "waiting_review", "message": "Waiting for your approval.", "work_state": state}
        if state == "done":
            return {"status": "done", "message": "Work completed.", "work_state": state}
        if state == "implementing":
            run = app.engine.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1", (work_id,))
            remaining = int((app.engine.db.one("SELECT COUNT(*) n FROM tasks WHERE work_id=? AND state!='completed'", (work_id,)) or {"n": 0})["n"] or 0)
            verification = str((run or {}).get("verification_status") or (run or {}).get("status") or "")
            if verification == "failed":
                return {"status": "stopped", "message": "The coding agent stopped before a verified implementation result. The last registration was outside the approved plan or failed checks. Retry the current step.", "work_state": state}
            if not run or str(run.get("status") or "") != "verified" or remaining:
                return {"status": "stopped", "message": "The coding agent stopped before registering a verified implementation result. Retry the current step.", "work_state": state}
        if state in {"discovery", "plan_review", "ready", "implementing"}:
            return {"status": "stopped", "message": "The coding agent stopped before completing the current step. You can continue it from Work.", "work_state": state}
        if state in {"code_review", "validating", "ready_to_merge"}:
            return {"status": "stopped", "message": "The current governed step did not finish. Continue it from Work.", "work_state": state}
        return {"status": "idle", "message": "Workflow is ready for the next step.", "work_state": state}

    def _launch_state(self, root: Path, work_id: str) -> str:
        try:
            for item in self.execution.status(root, work_id).get("items") or []:
                if str(item.get("work_id") or "") == str(work_id) and item.get("launch_state"):
                    return str(item.get("launch_state") or "")
        except Exception:
            pass
        return ""

    def _on_execution_exit(self, root: Path, provider: str, work_id: str, return_code: int) -> dict[str, Any]:
        app = DynosAIApplication(root)
        before = self._launch_state(root, work_id) or str(app.engine.get_work(work_id).get("state") or "")
        applied: list[dict[str, Any]] = []
        if app.auto_approve_enabled():
            applied = app.apply_auto_approvals(work_id)
        outcome = self._settle_work(root, provider, work_id)
        outcome = self._maybe_auto_continue(
            root, provider, work_id, return_code, outcome, before_state=before, applied=applied,
        )
        if return_code and outcome.get("status") not in {"waiting_review", "done", "running"}:
            outcome["status"] = "failed"
            outcome["message"] = f"{provider} exited with code {return_code}. Review the work status or retry the agent."
        return outcome

    def _maybe_auto_continue(
        self,
        root: Path,
        provider: str,
        work_id: str,
        return_code: int,
        outcome: dict[str, Any],
        *,
        before_state: str | None = None,
        applied: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        app = DynosAIApplication(root)
        if not app.auto_approve_enabled() or return_code:
            return outcome
        extra = app.apply_auto_approvals(work_id)
        combined = list(applied or []) + extra
        outcome = self._settle_work(root, provider, work_id)
        after = str(app.engine.get_work(work_id).get("state") or "")
        if outcome.get("status") not in {"stopped", "idle"}:
            return outcome
        if after not in {"discovery", "plan_review", "ready", "implementing", "validating"}:
            return outcome
        state_changed = before_state is not None and after != before_state
        if not state_changed and not combined:
            return outcome
        launched = self.execution.start(root, provider, work_id, on_exit=self._on_execution_exit)
        if launched.get("status") == "running":
            return {"status": "running", "message": "Auto-approved; coding agent continues.", "work_state": after}
        return outcome

    def _launch_work(self, app: DynosAIApplication, work_id: str, provider: str | None = None) -> dict[str, Any]:
        provider = provider or self._provider_for_work(app, work_id)
        return self.execution.start(self.root, provider, work_id, on_exit=self._on_execution_exit)

    def _no_project(self) -> tuple[int, dict[str, Any]]:
        return 409, {"error": "no_project", "message": "select or create a project first"}

    def get(self, path: str, query: dict[str, list[str]] | None = None) -> tuple[int, dict[str, Any]]:
        query = query or {}
        work_id = (query.get("work_id") or [None])[0]
        with self._lock:
            if path == "/api/health":
                return 200, {"ok": True, "version": __version__, "root": str(self.root) if self.root else None, "project_selected": self.has_project}
            if path == "/api/projects":
                return 200, {"current": str(self.root) if self.root else None, "items": self.registry.list(current=self.root)}
            if path == "/api/provider-capabilities":
                name = (query.get("provider") or [None])[0]
                try:
                    return 200, provider_manifest(name) if name else capability_report()
                except ValueError as exc:
                    return 400, {"error": "validation", "message": str(exc)}
            if not self.has_project:
                return self._no_project()
            app = self._require_app()
            if path == "/api/project/detect":
                return 200, app.detect_project()
            if path == "/api/setup":
                return 200, app.setup_status()
            if path == "/api/overview":
                return 200, app.project_overview(work_id)
            if path == "/api/reviews":
                return 200, {
                    "items": app.list_reviews(limit=100),
                    "history": app.list_review_history(limit=50),
                    "auto_approve": app.auto_approve_enabled(),
                }
            if path == "/api/project-settings":
                return 200, {"auto_approve": app.auto_approve_enabled(), "execution_policy": app.execution_policy(), "provider_capabilities": app.provider_capabilities()}
            if path == "/api/execution":
                return 200, self.execution.status(self.root, work_id)
            if path == "/api/work":
                detection = app.detector.detect(self.root)
                if not detection.get("has_dynosai"):
                    return 200, {"items": []}
                return 200, {"items": app.list_work(state=(query.get("state") or [None])[0])}
            if path == "/api/validations":
                return 200, app.validation_discovery()
            if path == "/api/model-routing":
                provider = (query.get("provider") or [None])[0]
                try:
                    return 200, app.model_routing(provider)
                except ValueError as exc:
                    return 400, {"error": "validation", "message": str(exc)}
            if path == "/api/risk":
                return 200, app.risk_assessment(work_id)
            if path == "/api/blockers":
                return 200, app.explain_blockers(work_id)
            if path == "/api/events":
                detection = app.detector.detect(self.root)
                if not detection.get("has_dynosai"):
                    return 200, {"items": []}
                app.engine.db.initialize()
                after = int((query.get("after_id") or ["0"])[0] or 0)
                return 200, {"items": app.list_events(after_id=after, work_id=work_id, limit=100)}
        return 404, {"error": "not_found", "message": f"Unknown API path: {path}"}

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/api/projects/open":
            raw = str(payload.get("path") or "").strip()
            if not raw:
                return 400, {"error": "validation", "message": "project path is required"}
            try:
                return 200, self.switch_project(raw)
            except ValueError as exc:
                return 400, {"error": "validation", "message": str(exc)}
        if path == "/api/projects/close":
            return 200, self.close_project()
        if path == "/api/projects/create":
            try:
                created = self.registry.create_project(
                    parent=str(payload.get("parent") or "").strip(),
                    name=str(payload.get("name") or "").strip(),
                    template=str(payload.get("template") or "empty").strip().lower(),
                )
                switched = self.switch_project(created["path"])
                return 200, {**created, **switched}
            except (ValueError, RuntimeError) as exc:
                return 400, {"error": "validation", "message": str(exc)}
        if path == "/api/projects/remove":
            raw = str(payload.get("path") or "").strip()
            if not raw:
                return 400, {"error": "validation", "message": "project path is required"}
            try:
                project = self.registry.resolve_project(raw) if Path(raw).expanduser().exists() else Path(raw).expanduser().resolve()
            except (OSError, ValueError):
                project = Path(raw).expanduser().resolve()
            if self.root is not None and project == self.root:
                return 409, {"error": "current_project", "message": "close the selected project before removing it from recent projects"}
            return 200, {"removed": self.registry.remove(project), "path": str(project)}
        if path == "/api/filesystem/list":
            try:
                return 200, browse_directory(payload.get("path"))
            except ValueError as exc:
                return 400, {"error": "validation", "message": str(exc)}
        if path == "/api/filesystem/mkdir":
            try:
                created = create_directory(payload.get("parent"), str(payload.get("name") or ""))
                return 200, {**created, "listing": browse_directory(payload.get("parent"))}
            except ValueError as exc:
                return 400, {"error": "validation", "message": str(exc)}

        if not self.has_project:
            return self._no_project()
        app = self._require_app()
        with self._lock:
            if path == "/api/project/initialize":
                agent = str(payload.get("agent") or "codex").lower()
                if agent not in SUPPORTED_STUDIO_PROVIDERS:
                    return 400, {"error": "validation", "message": "agent must be cursor or codex"}
                result = app.initialize_project(
                    name=payload.get("name"),
                    agent=agent,
                    allow_git_init=bool(payload.get("allow_git_init", False)),
                    language=payload.get("language"),
                    test_command=payload.get("test_command"),
                )
                self.registry.touch(self.root, display_name=str(result.get("project") or self.root.name))
                return 200, result
            if path == "/api/project-settings":
                if "auto_approve" not in payload:
                    return 400, {"error": "validation", "message": "auto_approve is required"}
                if not app.detector.detect(self.root).get("has_dynosai"):
                    return 409, {"error": "not_initialized", "message": "initialize the project before changing auto-approve"}
                snapshots = [
                    (str(work.get("id") or ""), str(work.get("state") or ""))
                    for work in app.list_work(limit=50)
                ]
                result = app.set_auto_approve(bool(payload.get("auto_approve")))
                if result.get("auto_approve"):
                    continued = []
                    for work_id, before in snapshots:
                        if not work_id or work_id == "PROJECT" or before == "done":
                            continue
                        provider = self._provider_for_work(app, work_id)
                        outcome = self._settle_work(self.root, provider, work_id)
                        outcome = self._maybe_auto_continue(self.root, provider, work_id, 0, outcome, before_state=before)
                        continued.append({"work_id": work_id, "status": outcome.get("status"), "work_state": outcome.get("work_state")})
                    result["continued"] = continued
                return 200, result
            if path == "/api/work/start":
                description = str(payload.get("description") or "").strip()
                if not description:
                    return 400, {"error": "validation", "message": "description is required"}
                provider = str(payload.get("provider") or "codex").lower()
                if provider not in SUPPORTED_STUDIO_PROVIDERS:
                    return 400, {"error": "validation", "message": "provider must be cursor or codex"}
                if not self.execution.available(provider):
                    return 409, {"error": "provider_unavailable", "message": f"{provider} is not installed or is not available to DynosAI Studio"}
                result = app.start_feature(
                    description,
                    provider=provider,
                    workspace_strategy=str(payload.get("workspace_strategy") or "interactive_branch"),
                )
                # Host-owned first transition prevents a newly-created task from
                # sitting forever in Inbox while the user thinks Studio is frozen.
                workflow = app.next_action(provider, result["id"], execute=True)
                execution = self._launch_work(app, result["id"], provider)
                return 200, {**result, "workflow": workflow, "execution": execution}
            if path == "/api/execution/start":
                work_id = str(payload.get("work_id") or "").strip()
                if not work_id:
                    return 400, {"error": "validation", "message": "work_id is required"}
                try:
                    work = app.engine.get_work(work_id)
                except Exception:
                    return 404, {"error": "not_found", "message": "work item not found"}
                provider = self._provider_for_work(app, work_id)
                if not self.execution.available(provider):
                    return 409, {"error": "provider_unavailable", "message": f"{provider} is not installed or is not available to DynosAI Studio"}
                if app.auto_approve_enabled():
                    app.apply_auto_approvals(work_id)
                pending = app.interactions.pending_for_work(work_id)
                if pending:
                    return 409, {"error": "human_gate", "message": "this work item requires review instead of another agent run"}
                work = app.engine.get_work(work_id)
                state = str(work.get("state") or "")
                if state == "done":
                    return 200, {"status": "done", "message": "Work completed.", "work_state": state, "work_id": work_id}
                if state in {"spec_review", "code_review", "validating", "ready_to_merge"}:
                    outcome = self._settle_work(self.root, provider, work_id)
                    work = app.engine.get_work(work_id)
                    state = str(work.get("state") or "")
                    pending = app.interactions.pending_for_work(work_id)
                    if pending or outcome.get("status") == "waiting_review":
                        return 409, {"error": "human_gate", "message": "this work item requires review instead of another agent run"}
                    if state == "done" or outcome.get("status") == "done":
                        return 200, {**outcome, "work_id": work_id}
                    if state in {"spec_review", "code_review", "ready_to_merge"}:
                        return 409, {
                            "error": "blocked",
                            "message": outcome.get("message") or "this work item requires review instead of another agent run",
                        }
                    if state in {"discovery", "plan_review", "ready", "implementing", "validating"}:
                        return 200, self._launch_work(app, work_id, provider)
                    return 200, {**outcome, "work_id": work_id}
                return 200, self._launch_work(app, work_id, provider)
            if path == "/api/validations/approve":
                names = payload.get("names")
                if names is not None and (not isinstance(names, list) or not all(isinstance(x, str) for x in names)):
                    return 400, {"error": "validation", "message": "names must be an array of validation profile names"}
                return 200, app.approve_discovered_validations(names, overwrite=bool(payload.get("overwrite", False)))
            if path == "/api/model-routing/set":
                provider = str(payload.get("provider") or "").strip().lower()
                model = str(payload.get("model") or "").strip()
                activity = payload.get("activity")
                if provider not in SUPPORTED_STUDIO_PROVIDERS or not model:
                    return 400, {"error": "validation", "message": "provider and model are required"}
                try:
                    return 200, app.set_project_model_route(
                        provider, model, effort=payload.get("effort"), activity=activity
                    )
                except ValueError as exc:
                    return 400, {"error": "validation", "message": str(exc)}
            if path == "/api/model-routing/reset":
                provider = str(payload.get("provider") or "").strip().lower()
                if provider not in SUPPORTED_STUDIO_PROVIDERS:
                    return 400, {"error": "validation", "message": "provider must be cursor or codex"}
                try:
                    return 200, app.reset_project_model_route(provider, activity=payload.get("activity"))
                except ValueError as exc:
                    return 400, {"error": "validation", "message": str(exc)}
            if path == "/api/eval/propose":
                case_id = str(payload.get("case_id") or "").strip()
                if not case_id:
                    return 400, {"error": "validation", "message": "case_id is required"}
                try:
                    return 200, app.propose_eval_improvement(case_id)
                except ValueError as exc:
                    return 400, {"error": "validation", "message": str(exc)}
            if path == "/api/execution-profile":
                profile = str(payload.get("profile") or "").strip()
                if not profile:
                    return 400, {"error": "validation", "message": "profile is required"}
                if not app.detector.detect(self.root).get("has_dynosai"):
                    return 409, {"error": "not_initialized", "message": "initialize the project before changing the execution profile"}
                try:
                    return 200, {"execution_policy": app.set_execution_profile(profile)}
                except ValueError as exc:
                    return 400, {"error": "validation", "message": str(exc)}
            if path == "/api/interaction/resolve":
                interaction_id = str(payload.get("interaction_id") or "").strip()
                action = str(payload.get("action") or "").strip()
                if not interaction_id or not action:
                    return 400, {"error": "validation", "message": "interaction_id and action are required"}
                content = payload.get("content") if isinstance(payload.get("content"), dict) else None
                result = app.resolve_interaction(interaction_id, action, content)
                work = result.get("work") if isinstance(result, dict) else None
                if work and not result.get("cancelled") and str(work.get("state") or "") != "done":
                    provider = self._provider_for_work(app, str(work["id"]))
                    state = str(work.get("state") or "")
                    if state == "validating":
                        result["workflow"] = self._settle_work(self.root, provider, str(work["id"]))
                    elif state in {"discovery", "plan_review", "ready", "implementing"}:
                        result["execution"] = self._launch_work(app, str(work["id"]), provider)
                return 200, result
        return 404, {"error": "not_found", "message": f"Unknown API path: {path}"}


class DynosAIStudioServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the optional selected project API."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], root: str | Path | None = None, *, registry_path: str | Path | None = None):
        host = address[0]
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("DynosAI Studio only binds to a loopback address")
        self.api = StudioAPI(root, registry_path=registry_path)
        super().__init__(address, DynosAIStudioHandler)


class DynosAIStudioHandler(BaseHTTPRequestHandler):
    server: DynosAIStudioServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003 - stdlib override
        return

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: Exception) -> None:
        self._json(500, {"error": exc.__class__.__name__, "message": str(exc)})

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol
        if not self._host_allowed():
            self._json(403, {"error": "forbidden_host"})
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                status, payload = self.server.api.get(parsed.path, parse_qs(parsed.query))
                self._json(status, payload)
            except Exception as exc:
                self._error(exc)
            return
        self._static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib protocol
        if not self._host_allowed():
            self._json(403, {"error": "forbidden_host"})
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._json(404, {"error": "not_found"})
            return
        if (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"error": "content_type", "message": "application/json is required"})
            return
        try:
            length = min(int(self.headers.get("Content-Length") or "0"), 1_000_000)
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                self._json(400, {"error": "validation", "message": "JSON object required"})
                return
            status, result = self.server.api.post(parsed.path, payload)
            self._json(status, result)
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid_json"})
        except Exception as exc:
            self._error(exc)

    def _static(self, path: str) -> None:
        requested = "index.html" if path in {"", "/"} else path.lstrip("/")
        if requested not in {"index.html", "app.js", "i18n.js", "styles.css", "theme-init.js", "icon.svg"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        asset = resources.files("dynosai_flow.studio_assets").joinpath(requested)
        data = asset.read_bytes()
        content_type = mimetypes.guess_type(requested)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith(("text/", "application/javascript")) else content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)


def create_server(
    root: str | Path | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    registry_path: str | Path | None = None,
) -> DynosAIStudioServer:
    """Create, but do not start, a Studio server."""
    return DynosAIStudioServer((host, int(port)), root, registry_path=registry_path)


def serve_studio(root: str | Path | None = None, *, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Serve Studio until interrupted."""
    server = create_server(root, host=host, port=port)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"DynosAI Studio {__version__} -> {url}")
    if root is not None:
        print(f"Project -> {Path(root).expanduser().resolve()}")
    else:
        print("Project hub -> no project selected")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
