# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Governed agent-team scheduling: DAG waves, leases and fan-in.

Parallelism is a scheduling problem. DynosAI does not spawn extra provider
processes. A host may start another governed session against an open lease.
Overlapping file sets never share a parallel wave. Schema stays v6: leases
are derived from tasks/runs (claimed_run), not a new authority table.
"""

from __future__ import annotations

from typing import Any, Iterable

from .util import json_loads

ROLES = ("implementer", "reviewer", "tester")
DEFAULT_TOKEN_BUDGET = 160_000
DEFAULT_TIME_BUDGET_SECONDS = 1_800
TEAM_POLICY = (
    "Tasks sharing planned files are never leased in parallel. "
    "DynosAI does not spawn extra provider processes; a second worker exists "
    "only if the host starts another governed session on an open lease. "
    "Reviewer work is the human code-review gate. Tester work is the "
    "governed validation contract on the same lease."
)


def _task_files(task: dict[str, Any]) -> list[str]:
    raw = task.get("files") or []
    if isinstance(raw, str):
        raw = json_loads(raw, [])
    files: list[str] = []
    for item in raw:
        path = str(item).strip() if not isinstance(item, dict) else str(item.get("path") or "").strip()
        if path and path not in files:
            files.append(path)
    return files


def _depends_on(task: dict[str, Any]) -> list[str]:
    raw = task.get("depends_on") or []
    if isinstance(raw, str):
        raw = json_loads(raw, [])
    return [str(item) for item in raw if str(item).strip()]


def _evidence(task: dict[str, Any]) -> list[str]:
    raw = task.get("evidence_required") or []
    if isinstance(raw, str):
        raw = json_loads(raw, [])
    return [str(item) for item in raw]


def _profiles(task: dict[str, Any]) -> list[str]:
    raw = task.get("validation_commands") or []
    if isinstance(raw, str):
        raw = json_loads(raw, [])
    return [str(item) for item in raw]


def follow_on_roles(task: dict[str, Any], *, risk_level: str = "") -> list[str]:
    """Roles that must complete after the implementer before fan-in.

    These are contracts, not extra spawned agents. Tester = validation on the
    lease. Reviewer = existing human code-review gate for high-risk work.
    """
    roles: list[str] = []
    files = _task_files(task)
    evidence = _evidence(task)
    if "test_result" in evidence or any("test" in path.lower() for path in files):
        roles.append("tester")
    if str(risk_level or "").lower() in {"high", "critical"}:
        roles.append("reviewer")
    return roles


def detect_path_conflicts(*file_sets: Iterable[str]) -> list[str]:
    """Return paths claimed by more than one set (fan-in conflicts)."""
    seen: dict[str, int] = {}
    for group in file_sets:
        for path in {str(item) for item in group if str(item).strip()}:
            seen[path] = seen.get(path, 0) + 1
    return sorted(path for path, count in seen.items() if count > 1)


def _lease_for(
    task: dict[str, Any],
    *,
    wave_id: str,
    risk_level: str,
    token_budget: int,
    time_budget_seconds: int,
) -> dict[str, Any]:
    files = _task_files(task)
    claimed = str(task.get("claimed_run") or "") or None
    state = str(task.get("state") or "")
    if state == "completed":
        status = "integrated"
    elif claimed:
        status = "claimed"
    else:
        status = "open"
    return {
        "lease_id": f"LEASE-{task['id']}",
        "wave_id": wave_id,
        "task_ids": [task["id"]],
        "role": "implementer",
        "follow_on_roles": follow_on_roles(task, risk_level=risk_level),
        "files": files,
        "scope_ceiling": files,
        "validation_profiles": _profiles(task),
        "evidence_required": _evidence(task),
        "token_budget": token_budget,
        "time_budget_seconds": time_budget_seconds,
        "status": status,
        "claimed_run": claimed,
        "spawn_provider": False,
    }


def build_team_plan(
    tasks: list[dict[str, Any]],
    *,
    risk_level: str = "",
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Turn an approved task DAG into serial/parallel waves with file-disjoint leases."""
    pending = {str(task["id"]): task for task in tasks if str(task.get("state") or "") != "completed"}
    completed = {str(task["id"]) for task in tasks if str(task.get("state") or "") == "completed"}
    waves: list[dict[str, Any]] = []
    parallel_batches: list[list[str]] = []
    wave_index = 0
    while pending:
        ready = [task for task in pending.values() if set(_depends_on(task)) <= completed]
        if not ready:
            break
        batch: list[dict[str, Any]] = []
        owned: set[str] = set()
        for task in ready:
            files = set(_task_files(task))
            if files & owned:
                continue
            batch.append(task)
            owned |= files
        if not batch:
            batch = [ready[0]]
        wave_index += 1
        wave_id = f"TEAM-WAVE-{wave_index}"
        kind = "parallel" if len(batch) > 1 else "serial"
        per_lease = max(8_000, int(token_budget / max(1, len(batch))))
        leases = [
            _lease_for(
                task,
                wave_id=wave_id,
                risk_level=risk_level,
                token_budget=per_lease,
                time_budget_seconds=time_budget_seconds,
            )
            for task in batch
        ]
        waves.append({
            "id": wave_id,
            "kind": kind,
            "leases": leases,
            "task_ids": [task["id"] for task in batch],
            "files": sorted(owned),
        })
        parallel_batches.append([task["id"] for task in batch])
        completed.update(task["id"] for task in batch)
        for task in batch:
            pending.pop(str(task["id"]), None)
    open_leases = [lease for wave in waves for lease in wave["leases"] if lease["status"] == "open"]
    claimed = [lease for wave in waves for lease in wave["leases"] if lease["status"] == "claimed"]
    return {
        "policy": TEAM_POLICY,
        "spawn_providers": False,
        "parallelism": "lease",
        "waves": waves,
        "parallel_batches": parallel_batches,
        "blocked": list(pending),
        "open_leases": [lease["lease_id"] for lease in open_leases],
        "claimed_leases": [lease["lease_id"] for lease in claimed],
        "current_wave": waves[0] if waves else None,
        "max_parallel": max((len(wave["leases"]) for wave in waves), default=0),
    }


def lease_for_task(plan: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for wave in plan.get("waves") or []:
        for lease in wave.get("leases") or []:
            if task_id in (lease.get("task_ids") or []):
                return lease
    return None


def claim_allowed(plan: dict[str, Any], task_id: str) -> dict[str, Any]:
    """Decide whether a session may claim a lease. Never authorizes extra spawns."""
    lease = lease_for_task(plan, task_id)
    if not lease:
        return {"allowed": False, "reason": f"task {task_id} is not in an open team wave", "spawn_provider": False}
    if lease["status"] == "integrated":
        return {"allowed": False, "reason": f"task {task_id} is already integrated", "spawn_provider": False}
    if lease["status"] == "claimed":
        return {"allowed": False, "reason": f"lease {lease['lease_id']} is already claimed", "spawn_provider": False, "lease": lease}
    current = plan.get("current_wave") or {}
    if lease.get("wave_id") != current.get("id"):
        return {"allowed": False, "reason": "only the current wave may be claimed", "spawn_provider": False, "lease": lease}
    for other in current.get("leases") or []:
        if other["lease_id"] == lease["lease_id"]:
            continue
        if other.get("status") == "claimed" and set(other.get("files") or []) & set(lease.get("files") or []):
            return {
                "allowed": False,
                "reason": "overlapping scope with a claimed sibling lease",
                "spawn_provider": False,
                "conflicts": detect_path_conflicts(other.get("files") or [], lease.get("files") or []),
                "lease": lease,
            }
    return {"allowed": True, "reason": "lease is open in the current wave", "spawn_provider": False, "lease": lease}


def fan_in_conflicts(leases: list[dict[str, Any]]) -> dict[str, Any]:
    """Conflict-aware fan-in: overlapping verified scopes cannot merge silently."""
    completed = [lease for lease in leases if lease.get("status") in {"integrated", "verified"}]
    groups = [lease.get("files") or [] for lease in completed]
    conflicts = detect_path_conflicts(*groups) if len(groups) > 1 else []
    return {
        "ready": [lease["lease_id"] for lease in completed if not conflicts],
        "conflicts": conflicts,
        "blocked": bool(conflicts),
        "policy": "Fan-in requires disjoint verified scopes or an explicit human merge resolution.",
    }
