# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Eval intelligence: attribute failures, mine traces, propose governed improvements.

This is not live-provider scoring and it does not enable predictive routing.
Mined cases become bounded eval regressions stored under `.dynosai/runtime/`.
Improvement work stays in inbox until a human starts it. Schema stays v6.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .model_control import NON_MODEL_FAILURES, PREDICTIVE_LEARNING_EXCLUDED_FAILURES
from .util import json_loads, utc_now

LAYERS = ("model", "harness", "provider", "project", "infrastructure", "governance")
MAX_CASES = 20
CASES_FILE = Path(".dynosai") / "runtime" / "eval-cases.json"
POLICY = (
    "Failures are attributed before they become eval cases. "
    "Predictive routing stays in shadow mode. "
    "Improvement work is created in inbox only; DynosAI does not auto-start a provider."
)

KIND_LAYER = {
    "validation_precondition": "harness",
    "orchestrator_failure": "harness",
    "telemetry_failure": "harness",
    "context_not_compacted": "harness",
    "requirement_mismatch": "harness",
    "integrity_missed_gap": "harness",
    "validation_environment": "infrastructure",
    "provider": "provider",
    "human_gate": "governance",
    "scope": "governance",
    "validation": "project",
    "syntax": "project",
    "test": "project",
    "spec": "project",
    "multi_agent_overlap": "harness",
}


def attribute_failure(kind: str | None, *, source: str = "") -> dict[str, Any]:
    """Map a failure kind onto a layer. Conservative: unknown is not model-learning."""
    raw = str(kind or "unknown").strip() or "unknown"
    layer = KIND_LAYER.get(raw)
    if layer is None:
        text = f"{raw} {source}".lower()
        if any(token in text for token in ("scope", "gate", "human")):
            layer = "governance"
        elif "provider" in text:
            layer = "provider"
        elif any(token in text for token in ("env", "timeout", "network", "infrastructure")):
            layer = "infrastructure"
        elif any(token in text for token in ("validat", "test", "syntax", "spec")):
            layer = "project"
        elif any(token in text for token in ("harness", "orchestrat", "integrity", "context")):
            layer = "harness"
        else:
            layer = "harness" if raw in NON_MODEL_FAILURES else "model"
    excluded = raw in PREDICTIVE_LEARNING_EXCLUDED_FAILURES or layer != "model"
    return {
        "kind": raw,
        "layer": layer,
        "eligible_for_predictive_learning": (not excluded) and layer == "model",
        "predictive_routing": "shadow",
        "source": source or None,
    }


def _fingerprint(*parts: Any) -> str:
    blob = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _case_id(fingerprint: str) -> str:
    return f"EVAL-{fingerprint[:10].upper()}"


def _cases_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / CASES_FILE


def load_cases(root: str | Path) -> list[dict[str, Any]]:
    path = _cases_path(root)
    if not path.exists():
        return []
    payload = json_loads(path.read_text(encoding="utf-8"), [])
    return [dict(item) for item in payload if isinstance(item, dict)]


def save_cases(root: str | Path, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = _cases_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    bounded = cases[:MAX_CASES]
    path.write_text(json.dumps(bounded, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return bounded


def _make_case(
    *,
    kind: str,
    source: str,
    summary: str,
    scenario: str,
    work_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attribution = attribute_failure(kind, source=source)
    fingerprint = _fingerprint(attribution["layer"], kind, source, summary, work_id)
    return {
        "case_id": _case_id(fingerprint),
        "fingerprint": fingerprint,
        "status": "open",
        "scenario": scenario,
        "summary": summary,
        "work_id": work_id,
        "improvement_work_id": None,
        "attribution": attribution,
        "detail": detail or {},
        "updated_at": utc_now(),
    }


def _merge_cases(existing: list[dict[str, Any]], mined: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous = {str(item.get("fingerprint")): item for item in existing}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for case in mined:
        fingerprint = str(case.get("fingerprint") or "")
        prev = previous.get(fingerprint)
        if prev:
            case["case_id"] = prev.get("case_id") or case["case_id"]
            case["status"] = prev.get("status") or case["status"]
            case["improvement_work_id"] = prev.get("improvement_work_id")
            case["regression"] = prev.get("regression")
        merged.append(case)
        seen.add(fingerprint)
    for prev in existing:
        fingerprint = str(prev.get("fingerprint") or "")
        if fingerprint in seen:
            continue
        if str(prev.get("status") or "") in {"proposed", "regressed"}:
            merged.append(prev)
    return merged[:MAX_CASES]


def mine_traces(db: Any, root: str | Path) -> list[dict[str, Any]]:
    """Build bounded regression cases from local validations, audit, evals and model-control traces."""
    mined: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(case: dict[str, Any]) -> None:
        fingerprint = str(case.get("fingerprint") or "")
        if not fingerprint or fingerprint in seen:
            return
        seen.add(fingerprint)
        mined.append(case)

    for row in db.query("SELECT work_id,command,status,exit_code,output,created_at FROM validations ORDER BY id DESC LIMIT 40"):
        status = str(row.get("status") or "").lower()
        if status in {"passed", "ok", "success", "fixture"}:
            continue
        output = str(row.get("output") or "")[:240]
        kind = "validation_environment" if "environment" in output.lower() else "validation"
        add(_make_case(
            kind=kind,
            source="validation",
            summary=f"Validation {status or 'failed'}: {row.get('command')}",
            scenario="validation_failure",
            work_id=row.get("work_id"),
            detail={"command": row.get("command"), "exit_code": row.get("exit_code"), "status": status},
        ))

    for row in db.query("SELECT event_type,entity_id,payload,created_at FROM audit WHERE event_type IN ('MCPToolFailed','ValidationFailed') ORDER BY id DESC LIMIT 40"):
        payload = json_loads(row.get("payload"), {})
        kind = str(payload.get("failure_kind") or payload.get("kind") or "orchestrator_failure")
        add(_make_case(
            kind=kind,
            source="audit",
            summary=f"{row.get('event_type')}: {row.get('entity_id')}",
            scenario="mined_regression",
            work_id=payload.get("work_id"),
            detail={"event_type": row.get("event_type"), "entity_id": row.get("entity_id")},
        ))

    eval_dir = Path(root).expanduser().resolve() / ".dynosai" / "runtime" / "eval-results"
    if eval_dir.is_dir():
        for path in sorted(eval_dir.glob("*.json"), reverse=True)[:20]:
            record = json_loads(path.read_text(encoding="utf-8"), {})
            if record.get("success") and not record.get("failure_category"):
                continue
            add(_make_case(
                kind=str(record.get("failure_category") or "unknown"),
                source="eval_record",
                summary=f"Eval {record.get('scenario')} failed",
                scenario=str(record.get("scenario") or "mined_regression"),
                detail={"path": str(path), "scenario": record.get("scenario")},
            ))

    events_path = Path(root).expanduser().resolve() / ".dynosai" / "runtime" / "model-control.jsonl"
    if events_path.exists():
        lines = events_path.read_text(encoding="utf-8").splitlines()[-40:]
        for line in reversed(lines):
            event = json_loads(line, {})
            if event.get("event") != "model_outcome" or event.get("success"):
                continue
            add(_make_case(
                kind=str(event.get("failure_kind") or "unknown"),
                source="model_control",
                summary=f"Model outcome failed during {event.get('activity') or 'unknown'}",
                scenario="mined_regression",
                work_id=event.get("work_id"),
                detail={"activity": event.get("activity"), "provider": event.get("provider")},
            ))

    return mined[:MAX_CASES]


def build_report(db: Any, root: str | Path) -> dict[str, Any]:
    """Refresh mined cases and return the operator-facing eval-intelligence report."""
    merged = _merge_cases(load_cases(root), mine_traces(db, root))
    saved = save_cases(root, merged)
    layers = {layer: 0 for layer in LAYERS}
    for case in saved:
        layer = str((case.get("attribution") or {}).get("layer") or "harness")
        layers[layer] = layers.get(layer, 0) + 1
    return {
        "policy": POLICY,
        "predictive_routing": "shadow",
        "live_provider_evals": False,
        "auto_start_provider": False,
        "cases": saved,
        "open_cases": [case["case_id"] for case in saved if case.get("status") == "open"],
        "proposed_cases": [case["case_id"] for case in saved if case.get("status") == "proposed"],
        "regressed_cases": [case["case_id"] for case in saved if case.get("status") == "regressed"],
        "by_layer": layers,
        "schema": "runtime-files",
    }


def case_by_id(root: str | Path, case_id: str) -> dict[str, Any] | None:
    for case in load_cases(root):
        if case.get("case_id") == case_id:
            return case
    return None


def mark_proposed(root: str | Path, case_id: str, work_id: str) -> dict[str, Any]:
    cases = load_cases(root)
    updated = None
    for case in cases:
        if case.get("case_id") == case_id:
            case["status"] = "proposed"
            case["improvement_work_id"] = work_id
            case["updated_at"] = utc_now()
            updated = case
            break
    if updated is None:
        raise ValueError(f"unknown eval case: {case_id}")
    save_cases(root, cases)
    return updated


def mark_regressed(root: str | Path, case_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
    cases = load_cases(root)
    updated = None
    for case in cases:
        if case.get("case_id") == case_id:
            case["status"] = "regressed"
            case["regression"] = evidence
            case["updated_at"] = utc_now()
            updated = case
            break
    if updated is None:
        raise ValueError(f"unknown eval case: {case_id}")
    save_cases(root, cases)
    return updated


def improvement_description(case: dict[str, Any]) -> str:
    attribution = case.get("attribution") or {}
    return (
        f"Eval improvement for {case.get('case_id')}: {case.get('summary')}. "
        f"Layer={attribution.get('layer')} kind={attribution.get('kind')}. "
        "Keep the existing governed workflow. Do not expand scope beyond this regression."
    )
