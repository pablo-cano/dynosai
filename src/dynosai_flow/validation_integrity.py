# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Validation Integrity: requirement → acceptance → evidence → validation → result.

Passing tests is not sufficient proof that the requested change was implemented.
The implementation agent does not define the completion standard.
"""

from __future__ import annotations

from typing import Any

from .db import Database
from .util import json_loads


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return json_loads(value, []) if value not in (None, "") else []


def _ids(items: list[Any], *, key: str = "id") -> list[str]:
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            ident = str(item.get(key) or item.get("requirement") or "").strip()
            if ident:
                out.append(ident)
    return out


def evaluate_integrity(engine: Any, work_id: str, *, risk_level: str | None = None) -> dict[str, Any]:
    """Build the governed evidence chain and flag weak sole-proof tests.

    ``engine`` is the DynosAI facade. Integrity is advisory in 0.15: it does not
    silently change register_result, but code/merge review must expose it.
    """
    work = engine.get_work(work_id)
    spec = engine.artifacts.artifact(work_id, "spec") or {}
    spec_meta = spec.get("metadata") or {}
    if isinstance(spec_meta, str):
        spec_meta = json_loads(spec_meta, {})
    requirements = _as_list(spec_meta.get("requirements"))
    acceptance = _as_list(spec_meta.get("acceptance_criteria") or spec_meta.get("acceptance"))
    tasks = [
        Database.decode(row, "requirements", "acceptance", "files", "validation_commands", "evidence_required")
        for row in engine.db.query("SELECT * FROM tasks WHERE work_id=? ORDER BY id", (work_id,))
    ]
    evidence_rows = engine.db.query(
        "SELECT * FROM evidence WHERE work_id=? ORDER BY created_at", (work_id,)
    )
    validations = engine.db.query(
        "SELECT id,command,status,exit_code FROM validations WHERE work_id=? ORDER BY id", (work_id,)
    )
    links = engine.db.query(
        "SELECT * FROM task_evidence_links WHERE task_id IN (SELECT id FROM tasks WHERE work_id=?)",
        (work_id,),
    )
    req_ids = _ids(requirements)
    ac_by_req: dict[str, list[str]] = {rid: [] for rid in req_ids}
    for item in acceptance:
        if isinstance(item, dict):
            req = str(item.get("requirement") or "")
            ac_id = str(item.get("id") or "")
            if req in ac_by_req and ac_id:
                ac_by_req[req].append(ac_id)
        elif isinstance(item, str):
            continue
    tasks_by_req: dict[str, list[str]] = {rid: [] for rid in req_ids}
    for task in tasks:
        for rid in _as_list(task.get("requirements")):
            rid = str(rid)
            tasks_by_req.setdefault(rid, []).append(str(task.get("id")))
    evidence_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in evidence_rows:
        evidence_by_task.setdefault(str(row.get("task_id") or ""), []).append(row)

    passed_validations = [row for row in validations if str(row.get("status") or "") in {"passed", "ok", "success"} or int(row.get("exit_code") or 1) == 0]
    failed_validations = [row for row in validations if row not in passed_validations]
    channels: list[str] = []
    for row in validations:
        command = " ".join(_as_list(row.get("command")) if isinstance(_as_list(row.get("command")), list) else [str(row.get("command") or "")])
        lowered = command.lower()
        if any(token in lowered for token in ("pytest", "unittest", "npm test", "cargo test", "go test")):
            channels.append("tests")
        elif any(token in lowered for token in ("ruff", "eslint", "lint")):
            channels.append("lint")
        elif any(token in lowered for token in ("mypy", "tsc", "typecheck")):
            channels.append("typecheck")
        elif any(token in lowered for token in ("build", "compile")):
            channels.append("build")
        else:
            channels.append("other")
    unique_channels = sorted(set(channels))

    chains = []
    weak_reasons: list[str] = []
    high_risk = str(risk_level or "").lower() in {"high", "critical"}
    for rid in req_ids:
        ac_ids = ac_by_req.get(rid) or []
        task_ids = tasks_by_req.get(rid) or []
        evid_ids: list[str] = []
        self_test_only = False
        for tid in task_ids:
            for ev in evidence_by_task.get(tid, []):
                evid_ids.append(str(ev.get("id")))
                payload = json_loads(ev.get("payload"), {}) if not isinstance(ev.get("payload"), dict) else ev.get("payload")
                actual = set(str(x) for x in (payload or {}).get("actual_files") or [])
                tests = set(str(x) for x in (payload or {}).get("test_files") or [])
                if tests and tests <= actual and not (unique_channels - {"tests", "other"}):
                    self_test_only = True
        missing_ac = not ac_ids
        missing_task = not task_ids
        missing_evidence = not evid_ids
        weak = missing_ac or missing_task or missing_evidence or (self_test_only and (high_risk or True))
        reasons = []
        if missing_ac:
            reasons.append("requirement_missing_acceptance")
        if missing_task:
            reasons.append("requirement_missing_task")
        if missing_evidence:
            reasons.append("requirement_missing_evidence")
        if self_test_only:
            reasons.append("self_authored_tests_are_sole_proof")
        if reasons:
            weak_reasons.extend(f"{rid}:{item}" for item in reasons)
        chains.append({
            "requirement_id": rid,
            "acceptance_ids": ac_ids,
            "task_ids": task_ids,
            "evidence_ids": evid_ids,
            "weak": bool(reasons),
            "reasons": reasons,
        })

    linked_pairs = len(links)
    requirements_satisfied = bool(req_ids) and all(
        chain["acceptance_ids"] and chain["task_ids"] and chain["evidence_ids"] for chain in chains
    )
    blocking_gaps = [chain for chain in chains if any(r in chain["reasons"] for r in ("requirement_missing_acceptance", "requirement_missing_task", "requirement_missing_evidence"))]
    high_risk_weak_tests = [chain for chain in chains if "self_authored_tests_are_sole_proof" in chain["reasons"] and high_risk]
    return {
        "work_id": work_id,
        "state": work.get("state"),
        "risk_level": risk_level,
        "requirements": req_ids,
        "chains": chains,
        "channels": unique_channels,
        "validation_passed": bool(passed_validations) and not failed_validations,
        "validation_runs": len(validations),
        "evidence_links": linked_pairs,
        "requirements_satisfied": requirements_satisfied,
        "weak_evidence": [item for item in chains if item["weak"]],
        "blocking_gaps": blocking_gaps,
        "high_risk_self_tests": high_risk_weak_tests,
        "completion_standard": "dynosai_core",
        "notes": [
            "Passing tests do not by themselves prove the original requirement.",
            "Self-authored tests created in the same implementation must not be the only proof for high-risk requirements.",
            "The implementation agent does not define completion.",
        ],
        "weak_reasons": weak_reasons,
    }
