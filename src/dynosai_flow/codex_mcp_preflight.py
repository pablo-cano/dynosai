# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Deterministic Codex App Server MCP preflight with no model turn.

Shapes follow the current OpenAI Codex app-server protocol:

- ``initialize`` / ``initialized``
- ``config/read`` (``includeLayers``, optional ``cwd``)
- ``mcpServerStatus/list`` (``data[]``, optional ``threadId``, ``detail``)
- ``mcpServer/startupStatus/updated`` (and legacy ``mcpServer/status/updated``)
- ``mcpServer/tool/call`` (requires ``threadId``; no ``turn/start``)
- ``thread/start`` only to materialize MCP runtime; never ``turn/start``
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .managed_runtime import ManagedProviderRuntime, run_codex_version_preflight
from .mcp import READ_ONLY_TOOLS
from .provider_process import spawn_provider_process, stop_provider_process
from .runtime_paths import (
    UnsafeCodexRuntime,
    assert_codex_home_safe,
    helper_binary_refusal_detected,
    managed_codex_home,
    path_is_under_temp,
)
from .token_usage import iter_codex_rollout_samples, mcp_runtime_metrics
from .util import executable_command
from .version import __version__

DYNOSAI_MCP_SERVER = "dynosai"
PREFLIGHT_TOOL = "dynosai_get_next_action"
PREFLIGHT_TOOL_ARGUMENTS = {"execute": False}
STARTUP_READY = "ready"
STARTUP_FAILED = "failed"
STARTUP_STARTING = "starting"
STARTUP_CANCELLED = "cancelled"
STARTUP_TIMEOUT_SEC = 45
SECRET_KEY_RE = re.compile(
    r"(auth|token|api[_-]?key|secret|password|authorization|openai)",
    re.IGNORECASE,
)

CLASSIFICATION_HOME_MISMATCH = "codex_home_mismatch"
CLASSIFICATION_CONFIG_MISSING = "codex_effective_config_missing_dynosai_mcp"
CLASSIFICATION_CONFIG_SOURCE = "codex_config_source_mismatch"
CLASSIFICATION_REGISTRY_MISSING = "mcp_registry_missing_dynosai"
CLASSIFICATION_STARTUP_FAILED = "mcp_startup_failed"
CLASSIFICATION_STARTUP_TIMEOUT = "mcp_startup_timeout"
CLASSIFICATION_READY_WITHOUT_TOOLS = "mcp_ready_without_tools"
CLASSIFICATION_TOOL_CALL_FAILED = "mcp_direct_tool_call_failed"
CLASSIFICATION_ACTIVITY_MISSING = "mcp_activity_missing"
CLASSIFICATION_MODEL_TURN = "model_turn_started"
CLASSIFICATION_TOKENS = "model_tokens_consumed"
CLASSIFICATION_INITIALIZE = "app_server_initialize_failed"
CLASSIFICATION_VERSION = "codex_version_preflight_failed"
CLASSIFICATION_UNSAFE = "unsafe_codex_runtime"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def same_path(left: str | Path | None, right: str | Path | None) -> bool:
    if not left or not right:
        return False
    a = Path(str(left)).expanduser()
    b = Path(str(right)).expanduser()
    try:
        a = a.resolve()
        b = b.resolve()
    except OSError:
        pass
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def thread_start_params(cwd: str | Path, *, sandbox: str = "workspace-write") -> dict[str, Any]:
    """Live-compatible thread/start params that must not override MCP servers.

    Official ``thread/start`` may accept request-level config. DynosAI omits
    ``mcp_servers`` / ``mcpServers`` so the managed ``config.toml`` remains
    authoritative. No ``model`` and no ``turn/start``.
    """
    return {
        "cwd": str(Path(cwd)),
        "approvalPolicy": {
            "granular": {
                "sandbox_approval": False,
                "rules": False,
                "skill_approval": False,
                "request_permissions": False,
                "mcp_elicitations": True,
            }
        },
        "sandbox": sandbox,
    }


def params_override_mcp_servers(params: dict[str, Any] | None) -> bool:
    payload = _as_dict(params)
    return any(key in payload for key in ("mcp_servers", "mcpServers"))


def extract_initialize(result: dict[str, Any] | None) -> dict[str, Any]:
    payload = _as_dict(result)
    return {
        "userAgent": payload.get("userAgent") or payload.get("user_agent"),
        "codexHome": payload.get("codexHome") or payload.get("codex_home"),
        "platformFamily": payload.get("platformFamily") or payload.get("platform_family"),
        "platformOs": payload.get("platformOs") or payload.get("platform_os"),
    }


def extract_config_object(config_read: dict[str, Any] | None) -> dict[str, Any]:
    payload = _as_dict(config_read)
    nested = payload.get("config")
    return _as_dict(nested) if nested is not None else payload


def extract_mcp_servers(config_read: dict[str, Any] | None) -> dict[str, Any]:
    config = extract_config_object(config_read)
    servers = config.get("mcpServers")
    if servers is None:
        servers = config.get("mcp_servers")
    if isinstance(servers, dict):
        return dict(servers)
    mapped: dict[str, Any] = {}
    for item in _as_list(servers):
        row = _as_dict(item)
        name = str(row.get("name") or row.get("id") or "").strip()
        if name:
            mapped[name] = row
    return mapped


def extract_dynosai_mcp(config_read: dict[str, Any] | None) -> dict[str, Any] | None:
    servers = extract_mcp_servers(config_read)
    for key, value in servers.items():
        if str(key).strip().lower() == DYNOSAI_MCP_SERVER:
            row = _as_dict(value)
            row.setdefault("name", key)
            return row
    return None


def dynosai_transport_verified(server: dict[str, Any] | None, *, expected_python: str | Path | None = None) -> bool:
    if not server:
        return False
    enabled = server.get("enabled")
    if enabled is False or str(enabled).strip().lower() in {"false", "0", "no"}:
        return False
    transport = str(server.get("transport") or server.get("type") or "stdio").strip().lower()
    if transport not in {"", "stdio", "stdiotransport"}:
        return False
    args = [str(item) for item in _as_list(server.get("args"))]
    if args != ["-m", "dynosai_flow.mcp"]:
        return False
    command = str(server.get("command") or "")
    if expected_python and command and not same_path(command, expected_python):
        return False
    return bool(command)


def extract_user_config_file(config_read: dict[str, Any] | None) -> str | None:
    payload = _as_dict(config_read)
    for layer in _as_list(payload.get("layers")):
        row = _as_dict(layer)
        name = row.get("name")
        if isinstance(name, dict):
            if str(name.get("type") or "").lower() == "user" and name.get("file"):
                return str(name["file"])
        file_path = row.get("file") or row.get("filePath") or row.get("path")
        label = str(name or row.get("source") or "").lower()
        if file_path and "user" in label:
            return str(file_path)
    origins = payload.get("origins")
    if isinstance(origins, dict):
        for key, meta in origins.items():
            blob = json.dumps(meta, default=str)
            if "user" in str(key).lower() or '"type": "user"' in blob.lower() or '"type":"user"' in blob.lower():
                row = _as_dict(meta)
                nested = row.get("name") if isinstance(row.get("name"), dict) else row
                file_path = _as_dict(nested).get("file") or row.get("file")
                if file_path:
                    return str(file_path)
    return None


def extract_status_servers(status_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    payload = _as_dict(status_result)
    rows = payload.get("data")
    if rows is None:
        rows = payload.get("servers") or payload.get("mcpServers")
    servers: list[dict[str, Any]] = []
    for item in _as_list(rows):
        row = _as_dict(item)
        if row:
            servers.append(row)
    return servers


def find_dynosai_status(servers: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in servers:
        name = str(row.get("name") or row.get("server") or "").strip().lower()
        if name == DYNOSAI_MCP_SERVER:
            return row
    return None


def tool_names_from_server(server: dict[str, Any] | None) -> list[str]:
    if not server:
        return []
    tools = server.get("tools")
    names: list[str] = []
    if isinstance(tools, dict):
        names = [str(name) for name in tools.keys() if str(name).strip()]
    else:
        for item in _as_list(tools):
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            else:
                row = _as_dict(item)
                name = str(row.get("name") or row.get("tool") or "").strip()
                if name:
                    names.append(name)
    return sorted(set(names))


def capture_startup_notification(message: dict[str, Any], *, at: str | None = None) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    if method not in {"mcpServer/startupStatus/updated", "mcpServer/status/updated"}:
        return None
    params = _as_dict(message.get("params"))
    name = str(params.get("name") or params.get("server") or "").strip()
    status = str(params.get("status") or "").strip().lower()
    if not name:
        return None
    return {
        "server": name,
        "status": status,
        "error": params.get("error"),
        "failureReason": params.get("failureReason") or params.get("failure_reason"),
        "threadId": params.get("threadId") or params.get("thread_id"),
        "timestamp": at or utc_now(),
        "method": method,
    }


def dynosai_startup_sequence(notifications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in notifications if str(item.get("server") or "").lower() == DYNOSAI_MCP_SERVER]


def latest_dynosai_startup_status(notifications: list[dict[str, Any]]) -> str | None:
    sequence = dynosai_startup_sequence(notifications)
    if not sequence:
        return None
    status = str(sequence[-1].get("status") or "").strip().lower()
    return status or None


def runtime_status_from_server(server: dict[str, Any] | None) -> str | None:
    if not server:
        return None
    value = server.get("runtimeStatus") or server.get("runtime_status") or server.get("status")
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"connected", "ready"}:
        return STARTUP_READY
    if text in {"failed", "failure"}:
        return STARTUP_FAILED
    if text in {"starting", "notstarted", "not_started"}:
        return STARTUP_STARTING
    if text in {"cancelled", "canceled", "disabled"}:
        return STARTUP_CANCELLED
    return text or None


def choose_preflight_tool(tool_names: list[str]) -> str | None:
    available = [name for name in tool_names if str(name).startswith("dynosai_")]
    if PREFLIGHT_TOOL in available:
        return PREFLIGHT_TOOL
    for name in available:
        if name in READ_ONLY_TOOLS:
            return name
    return available[0] if available else None


def tool_call_succeeded(response: dict[str, Any] | None) -> bool:
    payload = _as_dict(response)
    if payload.get("error"):
        return False
    result = payload.get("result")
    body = _as_dict(result) if result is not None else payload
    if body.get("isError") is True or body.get("is_error") is True:
        return False
    return bool(body) or result is not None


def count_model_tokens(codex_home: str | Path) -> int:
    total = 0
    for sample in iter_codex_rollout_samples(codex_home):
        usage = sample.get("usage") or {}
        total += int(usage.get("input_tokens") or 0)
        total += int(usage.get("output_tokens") or 0)
        total += int(usage.get("cached_input_tokens") or 0)
        total += int(usage.get("reasoning_output_tokens") or 0)
    return total


def python_import_probe(executable: str | Path) -> dict[str, Any]:
    command = [str(executable), "-c", "import dynosai_flow; print(dynosai_flow.__file__)"]
    report = {
        "configured_python": str(executable),
        "python_exists": Path(str(executable)).exists() or shutil.which(str(executable)) is not None,
        "dynosai_importable": False,
        "dynosai_import_path": None,
        "error": None,
    }
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["error"] = str(exc)
        return report
    path = (completed.stdout or "").strip().splitlines()
    if completed.returncode == 0 and path:
        report["dynosai_importable"] = True
        report["dynosai_import_path"] = path[0]
    else:
        report["error"] = (completed.stderr or completed.stdout or "").strip()[:2000]
    return report


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)):
                redacted[key] = "[redacted]"
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str) and len(value) > 8 and SECRET_KEY_RE.search(value) and ("=" in value or value.startswith("sk-")):
        return "[redacted]"
    return value


def portable_codex_mcp_preflight(report: dict[str, Any] | None) -> dict[str, Any]:
    payload = _as_dict(report)
    tools = _as_dict(payload.get("tools"))
    names = tools.get("names") if isinstance(tools.get("names"), list) else []
    return {
        "status": payload.get("status"),
        "classification": payload.get("classification"),
        "provider": payload.get("provider") or "codex",
        "provider_version": payload.get("provider_version"),
        "model_turn_started": bool(payload.get("model_turn_started")),
        "codex_home_verified": bool(payload.get("codex_home_verified")),
        "config_read": {
            "status": (_as_dict(payload.get("config_read")).get("status")),
            "dynosai_server_present": bool(_as_dict(payload.get("config_read")).get("dynosai_server_present")),
            "transport_verified": bool(_as_dict(payload.get("config_read")).get("transport_verified")),
            "config_source_verified": bool(_as_dict(payload.get("config_read")).get("config_source_verified")),
        },
        "mcp_status": {
            "present": bool(_as_dict(payload.get("mcp_status")).get("present")),
            "status": _as_dict(payload.get("mcp_status")).get("status"),
            "error": _as_dict(payload.get("mcp_status")).get("error"),
            "failure_reason": _as_dict(payload.get("mcp_status")).get("failure_reason"),
        },
        "tools": {"count": int(tools.get("count") or 0), "names": list(names)[:31]},
        "direct_tool_call": {
            "status": _as_dict(payload.get("direct_tool_call")).get("status"),
            "tool": _as_dict(payload.get("direct_tool_call")).get("tool"),
        },
        "startup_statuses": [
            str(item.get("status") or "")
            for item in _as_list(payload.get("startup_notifications"))
            if isinstance(item, dict)
        ],
        "mcp_activity_present": bool(payload.get("mcp_activity_present")),
        "mcp_calls": int(payload.get("mcp_calls") or 0),
        "model_tokens": int(payload.get("model_tokens") or 0),
    }


def evaluate_codex_mcp_preflight(observed: dict[str, Any]) -> dict[str, Any]:
    """Classify a completed observation set. Pure; no process I/O."""
    expected_home = observed.get("expected_codex_home")
    observed_home = observed.get("observed_codex_home")
    home_verified = bool(observed.get("codex_home_verified"))
    if expected_home and observed_home:
        home_verified = same_path(expected_home, observed_home)
    config_read = _as_dict(observed.get("config_read"))
    mcp_status = _as_dict(observed.get("mcp_status"))
    tools = _as_dict(observed.get("tools"))
    direct = _as_dict(observed.get("direct_tool_call"))
    model_turn = bool(observed.get("model_turn_started"))
    tokens = int(observed.get("model_tokens") or 0)
    mcp_calls = int(observed.get("mcp_calls") or 0)
    activity = bool(observed.get("mcp_activity_present"))
    classification = observed.get("classification")
    status = "fail"

    if observed.get("unsafe"):
        classification = CLASSIFICATION_UNSAFE
    elif "version_status" in observed and observed.get("version_status") != "pass":
        classification = classification or CLASSIFICATION_VERSION
    elif "initialize_status" in observed and observed.get("initialize_status") != "pass":
        classification = classification or CLASSIFICATION_INITIALIZE
    elif expected_home and observed_home and not home_verified:
        classification = CLASSIFICATION_HOME_MISMATCH
    elif model_turn:
        classification = CLASSIFICATION_MODEL_TURN
    elif tokens:
        classification = CLASSIFICATION_TOKENS
    elif not config_read.get("dynosai_server_present"):
        classification = CLASSIFICATION_CONFIG_MISSING
    elif config_read.get("config_source_mismatch"):
        classification = CLASSIFICATION_CONFIG_SOURCE
    elif not mcp_status.get("present"):
        classification = CLASSIFICATION_REGISTRY_MISSING
    elif str(mcp_status.get("status") or "") == STARTUP_FAILED:
        classification = CLASSIFICATION_STARTUP_FAILED
    elif str(mcp_status.get("status") or "") in {STARTUP_STARTING, STARTUP_CANCELLED} or mcp_status.get("timeout"):
        classification = CLASSIFICATION_STARTUP_TIMEOUT
    elif str(mcp_status.get("status") or "") != STARTUP_READY:
        classification = classification or CLASSIFICATION_STARTUP_TIMEOUT
    elif int(tools.get("count") or 0) <= 0:
        classification = CLASSIFICATION_READY_WITHOUT_TOOLS
    elif direct.get("status") != "pass":
        classification = CLASSIFICATION_TOOL_CALL_FAILED
    elif not activity or mcp_calls < 1:
        classification = CLASSIFICATION_ACTIVITY_MISSING
    else:
        status = "pass"
        classification = None

    report = {
        "provider": "codex",
        "provider_version": observed.get("provider_version"),
        "model_turn_started": model_turn,
        "expected_codex_home": expected_home,
        "observed_codex_home": observed_home,
        "codex_home_verified": home_verified,
        "app_server_initialize": observed.get("app_server_initialize") or {},
        "config_read": {
            "status": "pass" if config_read.get("dynosai_server_present") else "fail",
            "dynosai_server_present": bool(config_read.get("dynosai_server_present")),
            "transport_verified": bool(config_read.get("transport_verified")),
            "config_source_verified": bool(config_read.get("config_source_verified")),
            "expected_config_file": config_read.get("expected_config_file"),
            "observed_config_file": config_read.get("observed_config_file"),
            "command": config_read.get("command"),
            "args": config_read.get("args"),
            "transport": config_read.get("transport") or "stdio",
        },
        "mcp_status": {
            "present": bool(mcp_status.get("present")),
            "status": mcp_status.get("status"),
            "error": mcp_status.get("error"),
            "failure_reason": mcp_status.get("failure_reason") or mcp_status.get("failureReason"),
        },
        "startup_notifications": list(observed.get("startup_notifications") or []),
        "tools": {
            "count": int(tools.get("count") or 0),
            "names": list(tools.get("names") or []),
        },
        "direct_tool_call": {
            "status": direct.get("status") or "fail",
            "tool": direct.get("tool"),
        },
        "mcp_activity_present": activity,
        "mcp_calls": mcp_calls,
        "model_tokens": tokens,
        "python_probe": observed.get("python_probe") or {},
        "classification": classification,
        "status": status,
        "codex_version_preflight": observed.get("codex_version_preflight") or {},
    }
    return report


class _AppServerClient:
    def __init__(self, proc: subprocess.Popen[str], trace: Path, stderr_path: Path):
        self.proc = proc
        self.trace = trace
        self.stderr_path = stderr_path
        self.lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 1
        self.startup_notifications: list[dict[str, Any]] = []
        self.model_turn_started = False
        self.token_usage_events: list[dict[str, Any]] = []
        threading.Thread(target=self._reader, name="dynosai-codex-mcp-preflight-reader", daemon=True).start()
        threading.Thread(target=self._stderr, name="dynosai-codex-mcp-preflight-stderr", daemon=True).start()

    def _reader(self) -> None:
        try:
            assert self.proc.stdout is not None
            for raw in self.proc.stdout:
                self.lines.put(raw)
        finally:
            self.lines.put(None)

    def _stderr(self) -> None:
        if self.proc.stderr is None:
            return
        with self.stderr_path.open("a", encoding="utf-8") as handle:
            for raw in self.proc.stderr:
                handle.write(raw)
                handle.flush()

    def _append(self, payload: dict[str, Any]) -> None:
        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def _id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def send(self, payload: dict[str, Any]) -> None:
        self._append({"direction": "client->codex", "payload": payload, "at": utc_now()})
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def _handle_server_request(self, message: dict[str, Any]) -> bool:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if request_id is None or not method:
            return False
        if method == "mcpServer/elicitation/request":
            params = _as_dict(message.get("params"))
            request = params.get("request") if isinstance(params.get("request"), dict) else params
            meta = _as_dict(params.get("_meta") or _as_dict(request).get("_meta"))
            kind = str(meta.get("codex_approval_kind") or "")
            if kind == "mcp_tool_call":
                self.send({"id": request_id, "result": {"action": "accept", "content": {}}})
                return True
            self.send({"id": request_id, "result": {"action": "cancel", "content": {}}})
            return True
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            self.send({"id": request_id, "result": {"decision": "acceptForSession"}})
            return True
        if method == "item/permissions/requestApproval":
            permissions = _as_dict(_as_dict(message.get("params")).get("permissions"))
            self.send({"id": request_id, "result": {"scope": "session", "permissions": permissions}})
            return True
        return False

    def _ingest(self, message: dict[str, Any]) -> None:
        method = str(message.get("method") or "")
        if method in {"turn/started", "turn/completed"} or method.startswith("turn/"):
            self.model_turn_started = True
        note = capture_startup_notification(message)
        if note:
            self.startup_notifications.append(note)
        if method == "thread/tokenUsage/updated":
            self.token_usage_events.append(_as_dict(message.get("params")))

    def request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
        request_id = self._id()
        self.send({"method": method, "id": request_id, "params": params or {}})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                raw = self.lines.get(timeout=min(1.0, remaining))
            except queue.Empty:
                if self.proc.poll() is not None:
                    break
                continue
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except Exception:
                self._append({"direction": "codex->client", "raw": raw, "at": utc_now()})
                continue
            self._append({"direction": "codex->client", "payload": message, "at": utc_now()})
            if not isinstance(message, dict):
                continue
            self._ingest(message)
            if self._handle_server_request(message):
                continue
            if message.get("id") == request_id:
                return message
        return {"id": request_id, "error": {"message": f"timeout waiting for {method}"}}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.send({"method": method, "params": params or {}})

    def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                raw = self.lines.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    return
                continue
            if raw is None:
                return
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if isinstance(message, dict):
                self._append({"direction": "codex->client", "payload": message, "at": utc_now()})
                self._ingest(message)
                self._handle_server_request(message)


def write_preflight_evidence(logs: Path, report: dict[str, Any]) -> Path:
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / "codex-mcp-preflight.json"
    portable = portable_codex_mcp_preflight(report)
    local = redact_secrets(report)
    path.write_text(json.dumps({"portable": portable, "local": local}, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def run_codex_mcp_preflight(
    project: str | Path,
    *,
    logs: str | Path | None = None,
    executable: str | None = None,
    timeout: int = 90,
) -> dict[str, Any]:
    """Run initialize + config/read + MCP status + direct tool call. No turn/start."""
    project_path = Path(project).expanduser()
    logs_path = Path(logs) if logs else project_path.parent / "codex-mcp-preflight-logs"
    logs_path.mkdir(parents=True, exist_ok=True)
    expected_home = managed_codex_home(project_path)
    observed: dict[str, Any] = {
        "expected_codex_home": str(expected_home),
        "model_turn_started": False,
        "model_tokens": 0,
        "startup_notifications": [],
        "config_read": {},
        "mcp_status": {},
        "tools": {"count": 0, "names": []},
        "direct_tool_call": {"status": "fail", "tool": PREFLIGHT_TOOL},
        "mcp_activity_present": False,
        "mcp_calls": 0,
    }
    try:
        assert_codex_home_safe(expected_home)
    except UnsafeCodexRuntime:
        observed["unsafe"] = True
        report = evaluate_codex_mcp_preflight(observed)
        write_preflight_evidence(logs_path, report)
        return report

    version = run_codex_version_preflight(project_path)
    observed["codex_version_preflight"] = version
    observed["provider_version"] = version.get("codex_version")
    observed["version_status"] = version.get("status")
    if version.get("status") != "pass" or version.get("helper_binary_refusal_detected"):
        report = evaluate_codex_mcp_preflight(observed)
        write_preflight_evidence(logs_path, report)
        return report

    mcp_activity = logs_path / "mcp-activity.jsonl"
    extra_env = {
        "DYNOSAI_ACCEPTANCE_PROCESS_TRACE": str(mcp_activity.resolve()),
        "DYNOSAI_TOKEN_USAGE_FILE": str((logs_path / "token-usage.json").resolve()),
    }
    project_path.mkdir(parents=True, exist_ok=True)
    managed = ManagedProviderRuntime(project_path).prepare(
        "codex", activity="discovery", tool_profile="acceptance", extra_env=extra_env
    )
    expected_home = Path(managed.env["CODEX_HOME"])
    expected_config = expected_home / "config.toml"
    observed["expected_codex_home"] = str(expected_home)
    expected_python = str(Path(sys.executable).absolute())
    observed["python_probe"] = python_import_probe(expected_python)

    binary = executable or shutil.which("codex")
    if not binary:
        observed["initialize_status"] = "fail"
        observed["classification"] = "missing_cli"
        report = evaluate_codex_mcp_preflight(observed)
        write_preflight_evidence(logs_path, report)
        return report

    env = os.environ.copy()
    env.update(managed.env)
    env["DYNOSAI_AGENT_PROVIDER"] = "codex"
    env["DYNOSAI_MANAGED_AGENT"] = "1"
    env["DYNOSAI_MCP_TOOL_PROFILE"] = "acceptance"
    env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE"] = str(mcp_activity.resolve())
    trace = logs_path / "codex-mcp-preflight-app-server.jsonl"
    stderr_path = logs_path / "codex-mcp-preflight-stderr.log"
    proc = spawn_provider_process(
        executable_command(binary, "app-server", "--listen", "stdio://"),
        cwd=project_path,
        env=env,
        text=True,
        encoding="utf-8",
        errors="strict",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    client = _AppServerClient(proc, trace, stderr_path)
    started = time.monotonic()
    try:
        init = client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "dynosai_codex_mcp_preflight",
                    "title": "DynosAI Codex MCP Preflight",
                    "version": __version__,
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                    "mcpServerOpenaiFormElicitation": True,
                },
            },
            timeout=30,
        )
        if init.get("error"):
            observed["initialize_status"] = "fail"
            observed["app_server_initialize"] = {"status": "fail", "error": init.get("error")}
            report = evaluate_codex_mcp_preflight(observed)
            write_preflight_evidence(logs_path, report)
            return report
        identity = extract_initialize(_as_dict(init.get("result")))
        observed["initialize_status"] = "pass"
        observed["observed_codex_home"] = identity.get("codexHome")
        observed["codex_home_verified"] = same_path(expected_home, identity.get("codexHome"))
        observed["app_server_initialize"] = {
            "status": "pass",
            "userAgent": identity.get("userAgent"),
            "codexHome": identity.get("codexHome"),
            "platformFamily": identity.get("platformFamily"),
            "platformOs": identity.get("platformOs"),
        }
        if not observed["codex_home_verified"]:
            report = evaluate_codex_mcp_preflight(observed)
            write_preflight_evidence(logs_path, report)
            return report
        client.notify("initialized", {})
        config_msg = client.request(
            "config/read",
            {"includeLayers": True, "cwd": str(project_path)},
            timeout=20,
        )
        config_result = _as_dict(config_msg.get("result"))
        dynosai = extract_dynosai_mcp(config_result)
        if dynosai and dynosai.get("command"):
            expected_python = str(dynosai.get("command"))
            observed["python_probe"] = python_import_probe(expected_python)
        observed_config_file = extract_user_config_file(config_result)
        source_verified = same_path(expected_config, observed_config_file) if observed_config_file else False
        source_mismatch = bool(observed_config_file) and not source_verified
        observed["config_read"] = {
            "dynosai_server_present": dynosai is not None,
            "transport_verified": dynosai_transport_verified(dynosai, expected_python=expected_python),
            "config_source_verified": source_verified,
            "config_source_mismatch": source_mismatch,
            "expected_config_file": str(expected_config),
            "observed_config_file": observed_config_file,
            "command": None if dynosai is None else dynosai.get("command"),
            "args": None if dynosai is None else dynosai.get("args"),
            "transport": None if dynosai is None else (dynosai.get("transport") or "stdio"),
        }
        if not dynosai or source_mismatch:
            report = evaluate_codex_mcp_preflight(observed)
            write_preflight_evidence(logs_path, report)
            return report

        global_status = client.request("mcpServerStatus/list", {"detail": "full"}, timeout=20)
        global_servers = extract_status_servers(_as_dict(global_status.get("result")))
        dynosai_global = find_dynosai_status(global_servers)
        if dynosai_global is None:
            observed["mcp_status"] = {"present": False, "status": None, "error": None, "failure_reason": None}
            report = evaluate_codex_mcp_preflight(observed)
            write_preflight_evidence(logs_path, report)
            return report

        start_params = thread_start_params(project_path)
        if params_override_mcp_servers(start_params):
            observed["classification"] = "mcp_request_override"
            report = evaluate_codex_mcp_preflight(observed)
            write_preflight_evidence(logs_path, report)
            return report
        thread_msg = client.request("thread/start", start_params, timeout=30)
        if thread_msg.get("error"):
            observed["mcp_status"] = {
                "present": True,
                "status": STARTUP_FAILED,
                "error": thread_msg.get("error"),
                "failure_reason": "thread_start_failed",
            }
            report = evaluate_codex_mcp_preflight(observed)
            write_preflight_evidence(logs_path, report)
            return report
        thread = _as_dict(_as_dict(thread_msg.get("result")).get("thread"))
        thread_id = thread.get("id")
        ready_deadline = time.monotonic() + STARTUP_TIMEOUT_SEC
        dynosai_row: dict[str, Any] | None = None
        startup_status = latest_dynosai_startup_status(client.startup_notifications)
        while time.monotonic() < ready_deadline:
            if client.model_turn_started:
                break
            listed = client.request(
                "mcpServerStatus/list",
                {"detail": "full", "threadId": thread_id} if thread_id else {"detail": "full"},
                timeout=15,
            )
            dynosai_row = find_dynosai_status(extract_status_servers(_as_dict(listed.get("result"))))
            startup_status = latest_dynosai_startup_status(client.startup_notifications) or runtime_status_from_server(dynosai_row)
            if startup_status in {STARTUP_READY, STARTUP_FAILED, STARTUP_CANCELLED}:
                break
            client.drain(0.4)
        tools = tool_names_from_server(dynosai_row)
        error = None if dynosai_row is None else (dynosai_row.get("toolsError") or dynosai_row.get("error"))
        failure_reason = None
        sequence = dynosai_startup_sequence(client.startup_notifications)
        if sequence:
            error = sequence[-1].get("error") or error
            failure_reason = sequence[-1].get("failureReason")
        timed_out = startup_status not in {STARTUP_READY, STARTUP_FAILED, STARTUP_CANCELLED}
        observed["startup_notifications"] = list(client.startup_notifications)
        observed["mcp_status"] = {
            "present": True,
            "status": STARTUP_STARTING if timed_out else startup_status,
            "error": error,
            "failure_reason": failure_reason,
            "timeout": timed_out,
        }
        if timed_out:
            observed["mcp_status"]["status"] = STARTUP_STARTING
            observed["python_probe"] = python_import_probe(expected_python)
        if startup_status == STARTUP_FAILED:
            observed["mcp_status"]["status"] = STARTUP_FAILED
            observed["python_probe"] = python_import_probe(expected_python)
        observed["tools"] = {"count": len(tools), "names": tools}
        if startup_status == STARTUP_READY and tools:
            tool = choose_preflight_tool(tools) or PREFLIGHT_TOOL
            arguments = dict(PREFLIGHT_TOOL_ARGUMENTS) if tool == PREFLIGHT_TOOL else {}
            call = client.request(
                "mcpServer/tool/call",
                {"threadId": thread_id, "server": DYNOSAI_MCP_SERVER, "tool": tool, "arguments": arguments},
                timeout=30,
            )
            ok = tool_call_succeeded(call) and not call.get("error")
            observed["direct_tool_call"] = {"status": "pass" if ok else "fail", "tool": tool, "error": call.get("error")}
            client.drain(0.5)
        observed["model_turn_started"] = client.model_turn_started
        observed["startup_notifications"] = list(client.startup_notifications)
    finally:
        stop_provider_process(proc)
        elapsed = time.monotonic() - started
        observed["duration_seconds"] = round(elapsed, 3)

    stderr_text = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    observed["helper_binary_refusal_detected"] = helper_binary_refusal_detected(stderr_text)
    metrics = mcp_runtime_metrics(mcp_activity)
    observed["mcp_activity_present"] = mcp_activity.exists() and mcp_activity.stat().st_size > 0
    observed["mcp_calls"] = int(metrics.get("mcp_calls") or 0)
    observed["model_tokens"] = count_model_tokens(expected_home)
    if path_is_under_temp(expected_home):
        observed["unsafe"] = True
    report = evaluate_codex_mcp_preflight(observed)
    write_preflight_evidence(logs_path, report)
    return report
