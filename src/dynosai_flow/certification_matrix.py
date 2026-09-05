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
from .release_manifest import (
    _posix,
    certification_subject_identity,
    source_tree_identity,
)
from .runtime_paths import default_matrix_workspace
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
CERTIFICATION_ALLOWED_DIRTY_PATHS = frozenset({LIVE_MATRIX_PATH})
TRIAL_FIELD_NAMES = (
    "provider",
    "provider_client_version",
    "model",
    "dynosai_version",
    "candidate_version",
    "dynosai_git_commit",
    "git_dirty",
    "source_tree_sha256",
    "source_file_count",
    "certification_subject_sha256",
    "certification_subject_file_count",
    "certification_dirty_paths",
    "unexpected_dirty_paths",
    "os",
    "python_version",
    "scenario",
    "mode",
    "started_at",
    "finished_at",
    "mcp_protocol_version",
    "mcp_protocols_observed",
    "mcp_protocol_note",
    "acp_app_server_capabilities",
    "execution_profile",
    "harness_feature_state",
    "retry_history",
    "attempt",
    "token_usage",
    "estimated_cost",
    "validation_commands",
    "validation_results",
    "oracle_result",
    "scope_result",
    "git_evidence",
    "human_gate_evidence",
    "codex_runtime_preflight",
    "final_status",
    "failure_attribution",
    "artifact_paths",
    "artifact_hashes",
    "artifact_refs",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


class CertificationAborted(RuntimeError):
    """Live MATRIX aborted before any provider process started."""

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


def _git_dirty_paths(root: Path) -> list[str]:
    """Repo-relative dirty paths from `git status --porcelain -z`."""
    if not (root / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    paths: list[str] = []
    chunks = result.stdout.split(b"\0")
    index = 0
    while index < len(chunks):
        raw = chunks[index]
        index += 1
        if not raw:
            continue
        text = raw.decode("utf-8", errors="surrogateescape")
        if len(text) < 4:
            continue
        status = text[:2]
        path = text[3:]
        if "R" in status or "C" in status:
            if index < len(chunks) and chunks[index]:
                path = chunks[index].decode("utf-8", errors="surrogateescape")
                index += 1
        posix = _posix(path)
        if posix:
            paths.append(posix)
    return paths


def classify_certification_dirty(root: Path | None = None) -> dict[str, list[str]]:
    """Split dirty paths into allowed MATRIX evidence vs unexpected product changes."""
    target = (root or _repo_root()).resolve()
    allowed: list[str] = []
    unexpected: list[str] = []
    for path in _git_dirty_paths(target):
        if path in CERTIFICATION_ALLOWED_DIRTY_PATHS:
            allowed.append(path)
        else:
            unexpected.append(path)
    return {
        "certification_dirty_paths": allowed,
        "unexpected_dirty_paths": unexpected,
    }


def certification_subject_mismatch_message(expected: str, current: str) -> str:
    return (
        "Certification subject changed.\n"
        f"Expected: {expected}\n"
        f"Current: {current}\n"
        "Provider execution was not started."
    )


def unexpected_dirty_abort_message(paths: list[str]) -> str:
    listed = ", ".join(paths) if paths else "(none)"
    return (
        "ABORT CERTIFICATION\n"
        f"unexpected_dirty_paths: {listed}\n"
        "Provider execution was not started."
    )


def guard_live_certification(
    root: Path | None = None,
    *,
    expected_subject_sha256: str,
) -> dict[str, Any]:
    """Validate candidate identity and dirty policy before launching a provider."""
    target = (root or _repo_root()).resolve()
    env = probe_environment(target)
    dirty = classify_certification_dirty(target)
    current = str(env.get("certification_subject_sha256") or "")
    expected = str(expected_subject_sha256 or "").strip().lower()
    if current.lower() != expected:
        raise CertificationAborted(
            certification_subject_mismatch_message(expected_subject_sha256, current),
            reason="subject_mismatch",
        )
    if dirty["unexpected_dirty_paths"]:
        raise CertificationAborted(
            unexpected_dirty_abort_message(dirty["unexpected_dirty_paths"]),
            reason="unexpected_dirty",
        )
    return {**env, **dirty}


def trial_candidate_identity(trial: dict[str, Any] | None) -> tuple[str | None, str | None]:
    """Canonical candidate identity: git commit + certification subject, not the source ZIP hash."""
    payload = trial or {}
    commit = payload.get("dynosai_git_commit")
    subject = payload.get("certification_subject_sha256")
    return (
        str(commit) if commit else None,
        str(subject) if subject else None,
    )


def latest_trial_for_candidate(
    cell: dict[str, Any] | None,
    git_commit: str | None,
    certification_subject_sha256: str | None,
) -> dict[str, Any] | None:
    commit = str(git_commit or "").strip()
    subject = str(certification_subject_sha256 or "").strip()
    if not commit or not subject:
        return None
    matches: list[dict[str, Any]] = []
    for trial in (cell or {}).get("trials") or []:
        if not isinstance(trial, dict):
            continue
        trial_commit, trial_subject = trial_candidate_identity(trial)
        if trial_commit == commit and trial_subject == subject:
            matches.append(trial)
    return matches[-1] if matches else None


def candidate_certification_status(
    git_commit: str | None,
    certification_subject_sha256: str | None,
    matrix: dict[str, Any] | None = None,
    *,
    candidate_version: str | None = None,
) -> dict[str, Any]:
    """Latest matching trial per cell for one immutable candidate identity.

    Attempt numbers may differ across cells. Historical PASS from another
    commit or subject SHA does not certify this candidate.
    """
    payload = matrix or load_live_matrix()
    cells: dict[str, dict[str, Any]] = {}
    present = True
    passed = True
    for provider, mode in CELL_KEYS:
        key = f"{provider}.{mode}"
        cell = next(
            (item for item in (payload.get("cells") or []) if item.get("provider") == provider and item.get("mode") == mode),
            None,
        )
        trial = latest_trial_for_candidate(cell, git_commit, certification_subject_sha256)
        if trial is None:
            cells[key] = {"attempt": None, "status": None}
            present = False
            passed = False
            continue
        status = str(trial.get("final_status") or "")
        cells[key] = {"attempt": int(trial.get("attempt") or 0), "status": status}
        if status != "pass":
            passed = False
    return {
        "candidate_version": candidate_version or DISPLAY_VERSION,
        "dynosai_git_commit": git_commit,
        "certification_subject_sha256": certification_subject_sha256,
        "cells": cells,
        "all_cells_present": present,
        "all_passed": bool(present and passed),
    }


def matrix_all_passed_for_environment(matrix: dict[str, Any]) -> bool:
    env = matrix.get("environment") or {}
    return bool(
        candidate_certification_status(
            env.get("dynosai_git_commit"),
            env.get("certification_subject_sha256"),
            matrix,
            candidate_version=env.get("dynosai_display_version") or env.get("candidate_version"),
        )["all_passed"]
    )


def classify_matrix_failure(trial: dict[str, Any] | None) -> str | None:
    """Diagnostic class. Does not rewrite stored trials."""
    payload = trial or {}
    attribution = str(payload.get("failure_attribution") or "")
    note = str(payload.get("mcp_protocol_note") or "")
    observed = payload.get("mcp_protocols_observed") or []
    blob = json.dumps(payload, default=str)
    refusal = "Refusing to create helper binaries under temporary dir"
    if refusal in blob or refusal in attribution:
        return "provider_runtime_layout"
    if "governed DYN work item" in attribution and (not observed or "no MCP" in note.lower()):
        return "provider_runtime_layout"
    return None


def attempt_n_same_candidate(matrix: dict[str, Any], attempt: int = 3) -> dict[str, Any]:
    """Historical diagnostic. Release gates use candidate_certification_status instead."""
    identities: list[tuple[str | None, str | None]] = []
    missing: list[str] = []
    for provider, mode in CELL_KEYS:
        cell = next(
            (item for item in (matrix.get("cells") or []) if item.get("provider") == provider and item.get("mode") == mode),
            None,
        )
        trial = None
        for item in (cell or {}).get("trials") or []:
            if isinstance(item, dict) and int(item.get("attempt") or 0) == int(attempt):
                trial = item
                break
        key = f"{provider}.{mode}"
        if trial is None:
            missing.append(key)
            continue
        identities.append(trial_candidate_identity(trial))
    unique = {item for item in identities}
    all_same = (
        len(missing) == 0
        and len(identities) == len(CELL_KEYS)
        and len(unique) == 1
        and unique != {(None, None)}
        and all(commit and subject for commit, subject in unique)
    )
    result_key = f"all_attempt{attempt}_same_candidate"
    return {
        "attempt": int(attempt),
        "trial_count": len(identities),
        "missing_cells": missing,
        "identities": [{"dynosai_git_commit": commit, "certification_subject_sha256": subject} for commit, subject in identities],
        result_key: all_same,
        "all_attempt3_same_candidate": all_same if int(attempt) == 3 else None,
    }


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


def _git_dirty(root: Path | None = None) -> bool | None:
    target = root or _repo_root()
    if not (target / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return bool((result.stdout or "").strip())


def observed_mcp_protocols_from_payloads(payloads: list[Any]) -> list[str]:
    seen: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        proto = payload.get("mcp_protocol")
        if proto:
            text = str(proto)
            if text not in seen:
                seen.append(text)
    return seen


def collapse_observed_mcp_protocols(observed: list[str] | None) -> dict[str, Any]:
    values = [str(item) for item in (observed or []) if item]
    unique: list[str] = []
    for item in values:
        if item not in unique:
            unique.append(item)
    if len(unique) == 1:
        return {"mcp_protocol_version": unique[0], "mcp_protocols_observed": unique, "mcp_protocol_note": None}
    if not unique:
        return {
            "mcp_protocol_version": None,
            "mcp_protocols_observed": [],
            "mcp_protocol_note": "no MCP tool calls observed",
        }
    return {
        "mcp_protocol_version": None,
        "mcp_protocols_observed": unique,
        "mcp_protocol_note": "multiple MCP protocols observed; not collapsing to a single mcp_protocol_version",
    }


def _portable_artifact_name(path: str | Path) -> str:
    return Path(str(path).replace("\\", "/")).name


def _lookup_hash(hashes: dict[str, Any], path: str) -> str | None:
    if path in hashes:
        value = hashes.get(path)
        return str(value) if value else None
    name = _portable_artifact_name(path)
    if name in hashes:
        value = hashes.get(name)
        return str(value) if value else None
    for key, value in hashes.items():
        if _portable_artifact_name(key) == name:
            return str(value) if value else None
    return None


def publicize_artifact_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace machine-local paths with portable names + SHA256 refs."""
    out = dict(payload)
    paths = [str(item) for item in (out.get("artifact_paths") or [])]
    hashes = dict(out.get("artifact_hashes") or {}) if isinstance(out.get("artifact_hashes"), dict) else {}
    existing_refs = list(out.get("artifact_refs") or []) if isinstance(out.get("artifact_refs"), list) else []
    if not paths and not hashes and not existing_refs:
        return out
    names: list[str] = []
    portable_hashes: dict[str, Any] = {}
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths or list(hashes.keys()):
        name = _portable_artifact_name(path)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
        sha = _lookup_hash(hashes, path)
        portable_hashes[name] = sha
        refs.append({"name": name, "sha256": sha, "local_only": True})
    for ref in existing_refs:
        if not isinstance(ref, dict):
            continue
        name = str(ref.get("name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        sha = ref.get("sha256")
        portable_hashes[name] = sha
        refs.append({"name": name, "sha256": sha, "local_only": True})
    if names:
        out["artifact_paths"] = names
        out["artifact_hashes"] = portable_hashes
        out["artifact_refs"] = refs
    return out


def publicize_environment(environment: dict[str, Any] | None) -> dict[str, Any]:
    env = dict(environment or {})
    detected = env.get("providers_detected")
    if isinstance(detected, dict):
        env["providers_detected"] = {str(key): bool(value) for key, value in detected.items()}
    return env


def publicize_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    """Public MATRIX_1.0 JSON must not embed machine-local absolute paths."""
    payload = json.loads(json.dumps(matrix))
    payload["environment"] = publicize_environment(payload.get("environment") if isinstance(payload.get("environment"), dict) else {})
    cells = []
    for cell in payload.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        item = publicize_artifact_fields(cell)
        if isinstance(item.get("evidence"), dict):
            item["evidence"] = publicize_artifact_fields(item["evidence"])
        trials = []
        for trial in item.get("trials") or []:
            if isinstance(trial, dict):
                trials.append(publicize_artifact_fields(trial))
            else:
                trials.append(trial)
        item["trials"] = trials
        cells.append(item)
    payload["cells"] = cells
    return payload


def probe_environment(root: Path | None = None) -> dict[str, Any]:
    """Record host facts. Missing values stay null/unknown; nothing is invented."""
    target = root or _repo_root()
    identity = source_tree_identity(target)
    subject = certification_subject_identity(target)
    detected = {
        "codex": shutil.which("codex"),
        "cursor-agent": shutil.which("cursor-agent"),
        "cursor": shutil.which("cursor"),
    }
    return {
        "dynosai_version": __version__,
        "dynosai_display_version": DISPLAY_VERSION,
        "dynosai_git_commit": _git_commit(target),
        "git_dirty": _git_dirty(target),
        "source_tree_sha256": identity["source_tree_sha256"],
        "source_file_count": identity["source_file_count"],
        "certification_subject_sha256": subject["certification_subject_sha256"],
        "certification_subject_file_count": subject["certification_subject_file_count"],
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
        "candidate_version": env.get("dynosai_display_version") or env.get("candidate_version") or DISPLAY_VERSION,
        "dynosai_git_commit": env.get("dynosai_git_commit"),
        "git_dirty": env.get("git_dirty"),
        "source_tree_sha256": env.get("source_tree_sha256"),
        "source_file_count": env.get("source_file_count"),
        "certification_subject_sha256": env.get("certification_subject_sha256"),
        "certification_subject_file_count": env.get("certification_subject_file_count"),
        "certification_dirty_paths": env.get("certification_dirty_paths"),
        "unexpected_dirty_paths": env.get("unexpected_dirty_paths"),
        "os": env.get("os"),
        "python_version": env.get("python_version"),
        "mcp_protocol_version": None,
        "mcp_protocols_observed": [],
        "mcp_protocol_note": None,
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
        if str((trial or {}).get("final_status") or "") == "pass" and (
            trial.get("artifact_paths") or trial.get("artifact_hashes") or trial.get("artifact_refs")
        ):
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
    passed = matrix_all_passed_for_environment(matrix)
    if bool(matrix.get("all_passed")) != passed:
        raise ValueError("all_passed must match current-candidate PASS across the four provider-aware cells")
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
    posix = target.as_posix().replace("\\", "/")
    if posix.endswith("docs/validation/matrix-1.0.json"):
        payload = validate_live_matrix(publicize_matrix(payload))
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
    matrix["all_passed"] = matrix_all_passed_for_environment(matrix)
    return validate_live_matrix(matrix)
