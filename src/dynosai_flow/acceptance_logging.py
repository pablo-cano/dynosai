# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Acceptance trace normalization, process evidence capture, and portable log aggregation."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import utc_now
from .token_usage import latest_usage, aggregate_usage, runtime_metrics


def _safe_slug(value: str) -> str:
    out = []
    for ch in str(value):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out).strip("-") or "run"


def acceptance_log_home() -> Path:
    override = os.environ.get("DYNOSAI_ACCEPTANCE_LOG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".dynosai" / "logs" / "acceptance").resolve()


def new_run_id() -> str:
    stamp = utc_now().replace(":", "").replace("+00:00", "Z").replace("-", "")
    return f"ACCEPTANCE-{stamp}-{os.getpid()}"


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


@dataclass(slots=True)
class AcceptancePaths:
    root: Path

    @property
    def text(self) -> Path:
        return self.root / "acceptance.log"

    @property
    def events(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def state(self) -> Path:
        return self.root / "state.json"

    @property
    def metadata(self) -> Path:
        return self.root / "run.json"


class AcceptanceLogger:
    """Durable human + structured progress log for real-provider acceptance.

    Files are append/write-through on every event so a user can inspect or archive
    them while a provider is still running, after Ctrl+C, or after a hard timeout.
    Logging is diagnostic only and must never alter acceptance semantics.
    """

    def __init__(self, root: str | Path, *, run_id: str | None = None, console: bool = False, scope: str = "suite"):
        self.paths = AcceptancePaths(Path(root).expanduser().resolve())
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or self.paths.root.name
        self.console = bool(console)
        self.scope = scope
        self.started_monotonic = time.monotonic()
        self._last: dict[str, Any] = {}

    def initialize(self, **metadata: Any) -> None:
        data = {
            "run_id": self.run_id,
            "scope": self.scope,
            "created_at": utc_now(),
            "pid": os.getpid(),
            **metadata,
        }
        _atomic_write_json(self.paths.metadata, data)
        self.emit("log_initialized", phase="startup", message="persistent acceptance logging initialized", **metadata)

    def emit(
        self,
        event: str,
        *,
        phase: str | None = None,
        message: str | None = None,
        provider: str | None = None,
        scenario: str | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        now = utc_now()
        elapsed = round(time.monotonic() - self.started_monotonic, 3)
        item: dict[str, Any] = {
            "at": now,
            "elapsed_seconds": elapsed,
            "run_id": self.run_id,
            "scope": self.scope,
            "event": event,
        }
        if phase is not None:
            item["phase"] = phase
        if provider is not None:
            item["provider"] = provider
        if scenario is not None:
            item["scenario"] = scenario
        if message is not None:
            item["message"] = message
        if data:
            item["data"] = data

        _append_line(self.paths.events, json.dumps(item, ensure_ascii=False, default=str))
        prefix = f"[{now}]"
        target = "/".join(x for x in (provider, scenario) if x)
        label = f" {target}" if target else ""
        phase_text = f" [{phase}]" if phase else ""
        msg = message or event
        human = f"{prefix}{label}{phase_text} {msg}"
        if data:
            short = {k: v for k, v in data.items() if k in {"pid", "exit_code", "duration_seconds", "tool", "model", "status", "remaining_seconds", "stream_lines", "method", "path", "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens", "usage_quality"}}
            if short:
                human += " " + json.dumps(short, ensure_ascii=False, default=str)
        _append_line(self.paths.text, human)

        state = {
            "run_id": self.run_id,
            "scope": self.scope,
            "pid": os.getpid(),
            "updated_at": now,
            "elapsed_seconds": elapsed,
            "event": event,
            "phase": phase,
            "provider": provider,
            "scenario": scenario,
            "message": msg,
            "data": data,
            "log_file": str(self.paths.text),
            "events_file": str(self.paths.events),
        }
        self._last = state
        _atomic_write_json(self.paths.state, state)
        if self.console:
            print(human, flush=True)
        return item

    def heartbeat(self, *, provider: str | None = None, scenario: str | None = None, phase: str = "running", **data: Any) -> None:
        self.emit("heartbeat", phase=phase, provider=provider, scenario=scenario, message="still running", **data)


def update_latest_pointer(run_dir: Path) -> None:
    home = acceptance_log_home()
    home.mkdir(parents=True, exist_ok=True)
    latest = home / "latest"
    try:
        if latest.is_symlink() or latest.exists():
            if latest.is_dir() and not latest.is_symlink():
                shutil.rmtree(latest)
            else:
                latest.unlink()
        latest.symlink_to(run_dir.resolve(), target_is_directory=True)
    except Exception:
        # Symlinks can be restricted on some mounted filesystems. latest.txt is the
        # portable fallback and is always written.
        pass
    (home / "latest.txt").write_text(str(run_dir.resolve()) + "\n", encoding="utf-8")


def resolve_log_dir(value: str | Path | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    home = acceptance_log_home()
    latest = home / "latest"
    if latest.exists():
        try:
            return latest.resolve()
        except Exception:
            pass
    txt = home / "latest.txt"
    if txt.exists():
        raw = txt.read_text(encoding="utf-8").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    raise FileNotFoundError("No acceptance log run found. Run `dynosai debug acceptance` first or pass --log-dir.")


def _last_jsonl(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        last = None
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if not last:
            return None
        try:
            return json.loads(last)
        except Exception:
            return {"raw": last.rstrip("\n")[-2000:]}
    except Exception as exc:
        return {"error": str(exc)}


def _file_probe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        st = path.stat()
        return {"exists": True, "path": str(path), "size": st.st_size, "mtime": st.st_mtime}
    except Exception as exc:
        return {"exists": True, "path": str(path), "error": str(exc)}


def acceptance_status(log_dir: str | Path | None = None) -> dict[str, Any]:
    root = resolve_log_dir(log_dir)
    state = {}
    if (root / "state.json").exists():
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    cases = []
    cases_root = root / "cases"
    if cases_root.exists():
        for path in sorted(cases_root.glob("*/*/state.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                item["state_file"] = str(path)
                run_meta_path=path.parent/"run.json"
                meta=json.loads(run_meta_path.read_text(encoding="utf-8")) if run_meta_path.exists() else {}
                raw_logs=Path(meta.get("raw_logs") or "") if meta.get("raw_logs") else None
                if raw_logs:
                    item["raw_logs"] = str(raw_logs)
                    def persisted_or_raw(name: str) -> Path:
                        persisted=path.parent/name
                        return persisted if persisted.exists() else raw_logs/name
                    item["last_mcp_activity"] = _last_jsonl(persisted_or_raw("mcp-activity.jsonl"))
                    item["last_elicitation"] = _last_jsonl(persisted_or_raw("elicitation-trace.jsonl"))
                    item["provider_stream"] = _file_probe(persisted_or_raw("provider-stream.jsonl"))
                    item["provider_stderr"] = _file_probe(persisted_or_raw("provider-stderr.log"))
                    item["codex_trace"] = _file_probe(persisted_or_raw("codex-app-server.jsonl"))
                    item["token_usage"] = latest_usage(path.parent) or latest_usage(raw_logs)
                    live_usage=item.get("token_usage") or {}
                    mcp_path=persisted_or_raw("mcp-activity.jsonl")
                    provider_stream=persisted_or_raw("provider-stream.jsonl") if str(item.get("provider") or "").lower()=="cursor" else persisted_or_raw("codex-app-server.jsonl")
                    item["runtime_metrics"] = runtime_metrics(item.get("provider"),live_usage.get("elapsed_seconds"),mcp_path=mcp_path,provider_stream_path=provider_stream)
                else:
                    item["token_usage"] = latest_usage(path.parent)
                    live_usage=item.get("token_usage") or {}
                    item["runtime_metrics"] = runtime_metrics(item.get("provider"),live_usage.get("elapsed_seconds"),mcp_path=path.parent/"mcp-activity.jsonl",provider_stream_path=path.parent/("provider-stream.jsonl" if str(item.get("provider") or "").lower()=="cursor" else "codex-app-server.jsonl"))
                cases.append(item)
            except Exception as exc:
                cases.append({"state_file": str(path), "error": str(exc)})
    active_case=None
    valid=[c for c in cases if c.get("updated_at")]
    if valid:
        active_case=max(valid,key=lambda x:str(x.get("updated_at")))
    usage_items=[c.get("token_usage") for c in cases if isinstance(c.get("token_usage"),dict)]
    return {
        "log_dir": str(root),
        "state": state,
        "active_case": active_case,
        "cases": cases,
        "token_usage": aggregate_usage(usage_items),
        "log_file": str(root / "acceptance.log"),
        "events_file": str(root / "events.jsonl"),
        "tail_command": f"tail -f {root / 'acceptance.log'}",
        "bundle_command": f"dynosai debug acceptance-logs --log-dir {root}",
    }


def acceptance_usage(log_dir: str | Path | None = None) -> dict[str, Any]:
    status=acceptance_status(log_dir)
    processes=[]
    for case in status.get("cases") or []:
        usage=case.get("token_usage")
        if not isinstance(usage,dict):
            continue
        processes.append({
            "provider":case.get("provider") or usage.get("provider"),
            "scenario":case.get("scenario") or usage.get("scenario"),
            **usage,
        })
    return {
        "log_dir":status.get("log_dir"),
        "active_case":status.get("active_case") and {
            "provider":status["active_case"].get("provider"),
            "scenario":status["active_case"].get("scenario"),
            "phase":status["active_case"].get("phase"),
            "message":status["active_case"].get("message"),
            "token_usage":status["active_case"].get("token_usage"),
            "runtime_metrics":status["active_case"].get("runtime_metrics"),
        },
        "totals":aggregate_usage(processes),
        "runtime_totals":{
            "workflow_elapsed_seconds":round(sum(float((c.get("runtime_metrics") or {}).get("workflow_elapsed_seconds") or 0) for c in status.get("cases") or []),3),
            "dynosai_mcp_seconds":round(sum(float((c.get("runtime_metrics") or {}).get("dynosai_mcp_seconds") or 0) for c in status.get("cases") or []),3),
            "mcp_calls":sum(int((c.get("runtime_metrics") or {}).get("mcp_calls") or 0) for c in status.get("cases") or []),
            "reconnect_count":sum(int((c.get("runtime_metrics") or {}).get("reconnect_count") or 0) for c in status.get("cases") or []),
        },
        "processes":processes,
    }


def bundle_acceptance_logs(log_dir: str | Path | None = None, output: str | Path | None = None) -> dict[str, Any]:
    root = resolve_log_dir(log_dir)
    if output:
        target = Path(output).expanduser().resolve()
    else:
        target = root.parent / f"{_safe_slug(root.name)}-logs.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    added=set()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                try:
                    arc=Path("telemetry")/path.relative_to(root)
                    zf.write(path,arc); added.add(str(arc))
                except Exception:
                    pass
        # If the acceptance workspace still exists, include only diagnostic evidence
        # (not the whole generated source project) so the ZIP remains small and safe
        # to share while a provider is still running.
        meta_path=root/"run.json"
        workspace=None
        if meta_path.exists():
            try:
                meta=json.loads(meta_path.read_text(encoding="utf-8")); workspace=Path(meta.get("workspace") or "") if meta.get("workspace") else None
            except Exception:
                workspace=None
        if workspace and workspace.exists():
            evidence=[]
            for name in ("setup.json","provider-probes.json","summary-final.json"):
                p=workspace/name
                if p.exists(): evidence.append(p)
            evidence.extend(p for p in workspace.rglob("*") if p.is_file() and "logs" in p.parts)
            for path in sorted(set(evidence)):
                try:
                    arc=Path("workspace-evidence")/path.relative_to(workspace)
                    if str(arc) not in added:
                        zf.write(path,arc); added.add(str(arc))
                except Exception:
                    pass
    return {
        "log_dir": str(root),
        "bundle": str(target),
        "files": len(added),
        "share_this_file": str(target),
    }
