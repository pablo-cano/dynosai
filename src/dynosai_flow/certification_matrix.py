# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Versioned MATRIX_1.0 live-certification evidence.

Four provider-aware cells (Codex/Cursor × greenfield/brownfield) stay
``not_run`` until a real provider run writes evidence. Historical 0.13
Quality 100 results are not 1.0 proof and must not be copied forward.
Schema stays v6: this file is documentation/runtime evidence, not a new
authority table.

Retries are explicit trials. A later attempt must not erase a prior failure
or convert it into PASS.
"""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .mcp_protocol import CURRENT_PROTOCOL, SUPPORTED_PROTOCOLS
from .version import DISPLAY_VERSION, __version__

MATRIX_SCHEMA = "MATRIX_1.0"
CELL_STATUSES = ("not_run", "run", "pass", "fail")
CELL_KEYS = (
    ("codex", "greenfield"),
    ("codex", "brownfield"),
    ("cursor", "greenfield"),
    ("cursor", "brownfield"),
)
SCENARIO_FOR_MODE = {
    "greenfield": "fibonacci",
    "brownfield": "orderflow-contract-discounts",
}
HISTORICAL_MATRIX_PATH = "docs/validation/final-matrix-0.13.0.json"
LIVE_MATRIX_PATH = "docs/validation/matrix-1.0.json"
TRIAL_FIELD_NAMES = (
    "provider",
    "provider_client_version",
    "model",
    "dynosai_version",
    "dynosai_git_commit",
    "os",
    "python_version",
    "scenario",
    "mode",
    "started_at",
    "finished_at",
    "mcp_protocol_version",
    "acp_app_server_capabilities",
    "execution_profile",
    "harness_feature_state",
    "attempt",
    "token_usage",
    "estimated_cost",
    "validation_commands",
    "validation_results",
    "oracle_result",
    "scope_result",
    "git_evidence",
    "human_gate_evidence",
    "final_status",
    "failure_attribution",
    "artifact_paths",
    "artifact_hashes",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_commit(root: Path | None = None) -> str | None:
    target = root or _repo_root()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=target,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    commit = (result.stdout or "").strip()
    return commit or None


def _cli_version(command: str | None) -> str | None:
    if not command:
        return None
    try:
        result = subprocess.run(
            [command, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    return text.splitlines()[0].strip() if text else None


def probe_environment(root: Path | None = None) -> dict[str, Any]:
    """Record host facts. Missing values stay null/unknown; nothing is invented."""
    target = root or _repo_root()
    detected = {
        "codex": shutil.which("codex"),
        "cursor-agent": shutil.which("cursor-agent"),
        "cursor": shutil.which("cursor"),
    }
    return {
        "dynosai_version": __version__,
        "dynosai_display_version": DISPLAY_VERSION,
        "dynosai_git_commit": _git_commit(target),
        "os": platform.platform(),
        "python_version": sys.version.split()[0],
        "mcp_protocols_supported": list(SUPPORTED_PROTOCOLS),
        "mcp_protocol_current": CURRENT_PROTOCOL,
        "providers_detected": detected,
        "provider_client_versions": {
            "codex": _cli_version(detected.get("codex")),
            "cursor-agent": _cli_version(detected.get("cursor-agent")),
            "cursor": _cli_version(detected.get("cursor")),
        },
    }


def empty_trial(
    *,
    provider: str,
    mode: str,
    attempt: int = 1,
    environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = environment or probe_environment()
    trial = {name: None for name in TRIAL_FIELD_NAMES}
    trial.update({
        "provider": provider,
        "mode": mode,
        "scenario": SCENARIO_FOR_MODE.get(mode),
        "attempt": int(attempt),
        "dynosai_version": env.get("dynosai_version"),
        "dynosai_git_commit": env.get("dynosai_git_commit"),
        "os": env.get("os"),
        "python_version": env.get("python_version"),
        "mcp_protocol_version": env.get("mcp_protocol_current"),
        "provider_client_version": (env.get("provider_client_versions") or {}).get(
            "cursor-agent" if provider == "cursor" else provider
        ) or "unknown",
        "model": "unknown",
        "estimated_cost": None,
        "final_status": "not_run",
        "failure_attribution": None,
        "artifact_paths": [],
        "artifact_hashes": {},
    })
    return trial


def hash_file(path: str | Path) -> str | None:
    target = Path(path)
    if not target.is_file():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_cell(provider: str, mode: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "mode": mode,
        "status": "not_run",
        "quality_score": None,
        "oracle_passed": None,
        "evidence": None,
        "trials": [],
    }


def default_live_matrix() -> dict[str, Any]:
    """Honest 1.0 placeholder. Every cell is not_run; no copied scores."""
    return {
        "schema": MATRIX_SCHEMA,
        "schema_version": 1,
        "release_line": "1.0",
        "copied_from_historical": False,
        "all_passed": False,
        "historical_baseline": {
            "release": "0.13.0",
            "path": HISTORICAL_MATRIX_PATH,
            "note": "Historical core evidence. Not 1.0 live certification.",
        },
        "environment": probe_environment(),
        "cells": [default_cell(provider, mode) for provider, mode in CELL_KEYS],
    }


def cell_status_map(matrix: dict[str, Any]) -> dict[str, str]:
    return {f"{cell['provider']}.{cell['mode']}": str(cell["status"]) for cell in matrix.get("cells") or []}


def summarize_live_matrix(matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = matrix or load_live_matrix()
    return {
        "schema": payload["schema"],
        "schema_version": payload["schema_version"],
        "copied_from_historical": bool(payload.get("copied_from_historical")),
        "all_passed": bool(payload.get("all_passed")),
        "cells": cell_status_map(payload),
        "historical_baseline": (payload.get("historical_baseline") or {}).get("path") or HISTORICAL_MATRIX_PATH,
        "mcp_protocol_current": (payload.get("environment") or {}).get("mcp_protocol_current") or CURRENT_PROTOCOL,
        "trial_counts": {
            f"{cell['provider']}.{cell['mode']}": len(cell.get("trials") or [])
            for cell in payload.get("cells") or []
        },
    }


def _cell_has_real_pass_evidence(cell: dict[str, Any]) -> bool:
    if cell.get("evidence") == HISTORICAL_MATRIX_PATH:
        return False
    if cell.get("evidence"):
        return True
    for trial in cell.get("trials") or []:
        if str((trial or {}).get("final_status") or "") == "pass" and (trial.get("artifact_paths") or trial.get("artifact_hashes")):
            return True
    return False


def validate_live_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    """Reject copied 0.13 scores and inconsistent global pass flags."""
    if matrix.get("schema") != MATRIX_SCHEMA:
        raise ValueError("live matrix schema must be MATRIX_1.0")
    if int(matrix.get("schema_version") or 0) != 1:
        raise ValueError("MATRIX_1.0 schema_version must be 1")
    if matrix.get("copied_from_historical"):
        raise ValueError("1.0 live matrix must not be copied from historical 0.13 evidence")
    cells = list(matrix.get("cells") or [])
    keys = {(str(cell.get("provider")), str(cell.get("mode"))) for cell in cells}
    if keys != set(CELL_KEYS):
        raise ValueError("MATRIX_1.0 requires Codex/Cursor × greenfield/brownfield cells")
    for cell in cells:
        status = str(cell.get("status") or "")
        if status not in CELL_STATUSES:
            raise ValueError(f"invalid MATRIX_1.0 cell status: {status}")
        if cell.get("evidence") == HISTORICAL_MATRIX_PATH:
            raise ValueError("cannot attach 0.13 historical evidence as a 1.0 live cell")
        if status == "pass" and not _cell_has_real_pass_evidence(cell):
            raise ValueError("a MATRIX_1.0 pass requires real provider evidence")
        if status != "pass" and cell.get("quality_score") == 100:
            raise ValueError("Quality 100 cannot appear on a 1.0 cell that has not passed")
        trials = list(cell.get("trials") or [])
        for trial in trials:
            if not isinstance(trial, dict):
                raise ValueError("MATRIX_1.0 trials must be objects")
            if int(trial.get("attempt") or 0) < 1:
                raise ValueError("MATRIX_1.0 trial attempt must be >= 1")
            if str(trial.get("final_status") or "") == "pass" and status == "fail":
                # A later fail is allowed to keep history, but a silent rewrite of
                # a failed attempt into pass is rejected by append-only recording.
                continue
        attempts = [int(trial.get("attempt") or 0) for trial in trials]
        if attempts != sorted(attempts):
            raise ValueError("MATRIX_1.0 trial attempts must be recorded in order")
    passed = all(str(cell.get("status")) == "pass" for cell in cells)
    if bool(matrix.get("all_passed")) != passed:
        raise ValueError("all_passed must match the four provider-aware cells")
    return matrix


def load_live_matrix(path: str | Path | None = None) -> dict[str, Any]:
    """Load checked-in MATRIX_1.0 evidence, or the in-code not_run placeholder."""
    target = Path(path) if path else _repo_root() / LIVE_MATRIX_PATH
    if target.is_file():
        payload = json.loads(target.read_text(encoding="utf-8"))
        return validate_live_matrix(payload)
    return validate_live_matrix(default_live_matrix())


def save_live_matrix(matrix: dict[str, Any], path: str | Path | None = None) -> Path:
    payload = validate_live_matrix(matrix)
    target = Path(path) if path else _repo_root() / LIVE_MATRIX_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def append_trial(matrix: dict[str, Any], provider: str, mode: str, trial: dict[str, Any]) -> dict[str, Any]:
    """Append an explicit attempt. Never rewrite prior trials. Never auto-pass."""
    if int(trial.get("attempt") or 0) < 1:
        raise ValueError("trial attempt must be >= 1")
    found = None
    for cell in matrix.get("cells") or []:
        if cell.get("provider") == provider and cell.get("mode") == mode:
            found = cell
            break
    if found is None:
        raise ValueError(f"unknown MATRIX_1.0 cell: {provider}.{mode}")
    trials = list(found.get("trials") or [])
    next_attempt = (max((int(item.get("attempt") or 0) for item in trials), default=0) + 1)
    recorded = dict(trial)
    if trials and int(recorded.get("attempt") or 0) != next_attempt:
        recorded["attempt"] = next_attempt
    if not trials:
        recorded["attempt"] = int(recorded.get("attempt") or 1)
    status = str(recorded.get("final_status") or "run")
    if status not in CELL_STATUSES:
        raise ValueError(f"invalid trial final_status: {status}")
    trials.append(recorded)
    found["trials"] = trials
    found["status"] = status
    if status == "pass":
        found["oracle_passed"] = recorded.get("oracle_result")
        found["evidence"] = {
            "attempt": recorded.get("attempt"),
            "artifact_paths": recorded.get("artifact_paths"),
            "artifact_hashes": recorded.get("artifact_hashes"),
        }
    else:
        found["oracle_passed"] = recorded.get("oracle_result") if status != "not_run" else None
        if status == "fail":
            found["evidence"] = {
                "attempt": recorded.get("attempt"),
                "failure_attribution": recorded.get("failure_attribution"),
                "artifact_paths": recorded.get("artifact_paths"),
                "artifact_hashes": recorded.get("artifact_hashes"),
            }
    matrix["all_passed"] = all(str(cell.get("status")) == "pass" for cell in matrix.get("cells") or [])
    return validate_live_matrix(matrix)
