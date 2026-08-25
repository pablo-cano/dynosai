# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Adaptive model-control decisions, phase budgets, routing evidence, and safe transition policy."""

from __future__ import annotations

import hashlib
import json
import os
import statistics
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .model_routing import ModelRoute, ProviderModelRouting, activity_for_state, normalize_activity, normalize_provider
from .predictive_routing import PredictiveRoutingMemory
from .util import utc_now

# The control plane is intentionally small and auditable. It chooses among three
# model tiers and never hides an explicit project/machine provider-model pin.
MODEL_TIERS: dict[str, dict[str, dict[str, str]]] = {
    "cursor": {
        "economy": {"model": "gpt-5.6-luna", "effort": "medium"},
        "standard": {"model": "gpt-5.6-terra", "effort": "medium"},
        "strong": {"model": "gpt-5.6-sol", "effort": "medium"},
    },
    "codex": {
        "economy": {"model": "gpt-5.6-luna", "effort": "medium"},
        "standard": {"model": "gpt-5.6-terra", "effort": "medium"},
        "strong": {"model": "gpt-5.6-sol", "effort": "medium"},
    },
}

TIER_ORDER = ("economy", "standard", "strong")

# Budgets are PHASE budgets. Process totals are useful diagnostics, but they are
# never comparable with these numbers and therefore can never justify escalation.
PHASE_TOKEN_BUDGETS: dict[str, dict[str, int]] = {
    "discovery": {"context_soft": 40_000, "context_hard": 70_000, "generated_soft": 4_000, "generated_hard": 8_000},
    "specification": {"context_soft": 45_000, "context_hard": 80_000, "generated_soft": 5_000, "generated_hard": 10_000},
    "planning": {"context_soft": 55_000, "context_hard": 95_000, "generated_soft": 6_000, "generated_hard": 12_000},
    "implementation": {"context_soft": 160_000, "context_hard": 260_000, "generated_soft": 20_000, "generated_hard": 35_000},
    "code_review": {"context_soft": 80_000, "context_hard": 140_000, "generated_soft": 8_000, "generated_hard": 15_000},
    "validation": {"context_soft": 45_000, "context_hard": 80_000, "generated_soft": 4_000, "generated_hard": 8_000},
    "merge": {"context_soft": 25_000, "context_hard": 45_000, "generated_soft": 2_000, "generated_hard": 4_000},
}

PHASE_BASE_TIER = {activity: "economy" for activity in PHASE_TOKEN_BUDGETS}

PHASE_EFFORT = {
    "discovery": {"economy": "low", "standard": "medium", "strong": "medium"},
    "specification": {"economy": "medium", "standard": "medium", "strong": "medium"},
    "planning": {"economy": "medium", "standard": "medium", "strong": "medium"},
    "implementation": {"economy": "medium", "standard": "medium", "strong": "medium"},
    "code_review": {"economy": "medium", "standard": "medium", "strong": "high"},
    "validation": {"economy": "low", "standard": "medium", "strong": "medium"},
    "merge": {"economy": "low", "standard": "medium", "strong": "medium"},
}

NON_MODEL_FAILURES = {"validation_precondition", "validation_environment", "scope", "provider", "human_gate", "orchestrator_failure", "telemetry_failure"}
PREDICTIVE_LEARNING_EXCLUDED_FAILURES = set(NON_MODEL_FAILURES)


@dataclass(slots=True)
class ComplexityAssessment:
    score: int
    level: str
    signals: dict[str, Any]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BudgetAssessment:
    activity: str
    budget: dict[str, int]
    context_tokens: int | None
    generated_tokens: int | None
    context_ratio: float | None
    generated_ratio: float | None
    measurement_scope: str
    status: str
    actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ModelControlDecision:
    provider: str
    activity: str
    route: ModelRoute
    tier: str
    policy: str
    complexity: ComplexityAssessment
    budget: BudgetAssessment
    escalation: dict[str, Any]
    retry_policy: dict[str, Any]
    safe_boundary: bool
    apply_now: bool
    reasons: list[str]
    route_requested: dict[str, Any] | None = None
    route_applied: dict[str, Any] | None = None
    route_verified: bool | None = None
    pending_transition: dict[str, Any] | None = None
    route_recommended: dict[str, Any] | None = None
    prediction: dict[str, Any] | None = None
    context_strategy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        route = self.route.to_dict()
        value["route"] = route  # backwards-compatible selected candidate alias
        value["candidate_route"] = route
        # ``route_recommended`` is reserved for an actual transition proposal.
        # Keep the always-present candidate separate so aggregate metrics cannot
        # confuse route evaluation with a real recommendation event.
        value["route_recommended"] = self.route_recommended
        return value


def _atomic_json(path: Path, value: Any) -> None:
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
        except OSError:
            pass


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default


def _tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return 0


def _max_tier(a: str, b: str) -> str:
    return TIER_ORDER[max(_tier_index(a), _tier_index(b))]


def _route_tier(provider: str, route: dict[str, Any] | ModelRoute | None) -> str | None:
    if route is None:
        return None
    data = route.to_dict() if isinstance(route, ModelRoute) else route
    model = str((data or {}).get("model") or "")
    for tier, cfg in MODEL_TIERS.get(provider, {}).items():
        if str(cfg.get("model")) == model:
            return tier
    return None


def _validation_fingerprint(row: dict[str, Any]) -> str:
    output = " ".join(str(row.get("output") or "").lower().split())[-4000:]
    raw = f"{row.get('command') or ''}|{row.get('exit_code')}|{output}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def classify_validation_failure(
    message: str | None,
    *,
    exit_code: int | None = None,
    pending_planned_files: list[str] | tuple[str, ...] | set[str] | None = None,
) -> str:
    text = str(message or "").lower()
    pending = {str(x).replace("\\", "/").lstrip("./") for x in (pending_planned_files or []) if str(x).strip()}
    # A plan-stage validation can legitimately have no tests yet. This is a
    # Validation preconditions are orchestration signals, not evidence that the current model failed.
    # also recognizes unittest's misleading "start directory is not importable"
    # when that directory belongs to a still-pending planned test task.
    if (
        "no tests ran" in text
        or "ran 0 tests" in text
        or "collected 0 items" in text
        or "no tests collected" in text
        or "no test files" in text
        or "tests not created" in text
        or "tests do not exist" in text
        or "validation profile not approved" in text
    ):
        return "validation_precondition"
    import re as _re
    start_dir = None
    match = _re.search(r"start directory is not importable:\s*[\"']([^\"']+)[\"']", text)
    if match:
        start_dir = match.group(1).replace("\\", "/").strip("./")
    if start_dir and any(path == start_dir or path.startswith(start_dir + "/") for path in pending):
        return "validation_precondition"
    if pending and any(path.startswith("tests/") for path in pending):
        if ("no such file or directory" in text and "tests" in text) or ("cannot find" in text and "tests" in text):
            return "validation_precondition"
    if any(x in text for x in ("command not found", "module not found", "modulenotfounderror", "validation_environment_missing")) or exit_code == 127:
        return "validation_environment"
    if any(x in text for x in ("syntaxerror", "parse error", "compile error", "indentationerror")):
        return "syntax"
    if any(x in text for x in ("scope", "out of scope", "not approved")):
        return "scope"
    if any(x in text for x in ("spec", "requirement", "acceptance criteria")):
        return "spec"
    if any(x in text for x in ("mcp", "tool", "schema", "invalid argument")):
        return "tool"
    if any(x in text for x in ("timeout", "rate limit", "connection", "provider", "server error")):
        return "provider"
    if any(x in text for x in ("assert", "test failed", "failures", "pytest", "unittest")) or (exit_code not in (None, 0)):
        return "test"
    return "unknown"


def retry_policy_for(failure_kind: str | None, consecutive_failures: int) -> dict[str, Any]:
    kind = str(failure_kind or "none")
    retryable = kind not in {"scope", "provider", "none", "validation_precondition", "validation_environment"}
    actions = {
        "syntax": ["read_failing_region", "apply_minimal_fix", "rerun_targeted_validation"],
        "test": ["read_failure_and_related_code", "apply_targeted_fix", "rerun_failed_validation"],
        "spec": ["reload_accepted_spec_checkpoint", "repair_to_requirement", "rerun_relevant_validation"],
        "tool": ["repair_tool_arguments_from_contract", "retry_once"],
        "scope": ["request_scope_extension_or_stop"],
        "provider": ["retry_provider_at_safe_boundary"],
        "validation_precondition": ["continue_plan_until_validation_preconditions_exist"],
        "validation_environment": ["report_validation_environment_missing", "do_not_escalate_model"],
        "unknown": ["collect_discriminating_evidence", "retry_once"],
        "none": [],
    }.get(kind, ["collect_discriminating_evidence", "retry_once"])
    return {
        "failure_kind": kind,
        "consecutive_failures": int(consecutive_failures),
        "retryable": retryable,
        "counts_as_model_failure": kind not in NON_MODEL_FAILURES and kind != "none",
        "max_same_tier_retries": 1,
        "same_tier_retry_allowed": bool(retryable and consecutive_failures <= 1),
        "escalate_after_retry": bool(retryable and consecutive_failures >= 2),
        "actions": actions,
    }


class ModelControlPlane:
    """Adaptive, evidence-driven model control for managed provider sessions.

    0.12.1 keeps the verified 0.11.x safety invariants and adds a transparent
    predictive layer learned from real model outcomes. Historical evidence may
    adjust a rules-based tier only with sufficient samples; context pressure is
    explicitly excluded from capability prediction and remains a compaction /
    retrieval problem.
    """

    STATE_SCHEMA_VERSION = 3

    def __init__(self, project_root: str | Path, *, policy: str | None = None):
        self.root = Path(project_root).expanduser().resolve()
        self.runtime = self.root / ".dynosai" / "runtime"
        self.state_path = self.runtime / "model-control.json"
        self.events_path = self.runtime / "model-control.jsonl"
        self.policy = str(policy or os.environ.get("DYNOSAI_MODEL_CONTROL_POLICY") or "adaptive").lower()
        if self.policy not in {"adaptive", "pinned", "economy", "standard", "strong"}:
            raise ValueError(f"Unsupported model control policy: {self.policy}")

    def _db(self):
        try:
            from .db import Database
            db = Database(self.root)
            if db.path.exists():
                db.initialize()
                return db
        except Exception:
            pass
        return None

    def _work_signals(self, work_id: str | None) -> dict[str, Any]:
        db = self._db()
        if not db or not work_id:
            return {}
        work = db.one("SELECT * FROM work_items WHERE id=?", (work_id,)) or {}
        tasks = db.query("SELECT id,state,files,requirements,acceptance FROM tasks WHERE work_id=?", (work_id,))
        decisions = db.query("SELECT id FROM decisions WHERE work_id=?", (work_id,))
        scope = db.query("SELECT id,status FROM scope_requests WHERE work_id=?", (work_id,))
        validations = db.query("SELECT command,status,exit_code,output,created_at FROM validations WHERE work_id=? ORDER BY id DESC LIMIT 12", (work_id,))
        code_files = int((db.one("SELECT COUNT(*) AS n FROM code_files") or {"n": 0})["n"])

        pending_planned_files: set[str] = set()
        for task in tasks:
            if str(task.get("state") or "") != "completed":
                try:
                    pending_planned_files.update(str(x).replace("\\", "/") for x in json.loads(task.get("files") or "[]"))
                except Exception:
                    pass

        consecutive = 0
        failure_kind = None
        seen: set[str] = set()
        preconditions = 0
        duplicate_observations = 0
        for row in validations:
            if str(row.get("status") or "").lower() in {"passed", "success", "ok"} or int(row.get("exit_code") or 0) == 0:
                break
            kind = classify_validation_failure(row.get("output"), exit_code=row.get("exit_code"), pending_planned_files=pending_planned_files)
            if kind in NON_MODEL_FAILURES:
                preconditions += 1
                continue
            fingerprint = _validation_fingerprint(row)
            if fingerprint in seen:
                duplicate_observations += 1
                continue
            seen.add(fingerprint)
            consecutive += 1
            if failure_kind is None:
                failure_kind = kind

        planned_files: set[str] = set()
        req_count = 0
        for task in tasks:
            try:
                planned_files.update(json.loads(task.get("files") or "[]"))
            except Exception:
                pass
            try:
                req_count += len(json.loads(task.get("requirements") or "[]"))
            except Exception:
                pass

        recent_tools = []
        try:
            rows = db.query("SELECT entity_id,payload FROM audit WHERE event_type='MCPToolCalled' ORDER BY id DESC LIMIT 20")
            for row in rows:
                payload = row.get("payload")
                try:
                    payload = json.loads(payload or "{}") if isinstance(payload, str) else (payload or {})
                except Exception:
                    payload = {}
                if payload.get("work_id") in (None, work_id):
                    tool = str(row.get("entity_id") or "")
                    if tool and tool != "dynosai_get_next_action":
                        recent_tools.append(tool)
            recent_tools = recent_tools[:8]
        except Exception:
            recent_tools = []
        repeated = 0
        if recent_tools:
            head = recent_tools[0]
            for tool in recent_tools:
                if tool == head:
                    repeated += 1
                else:
                    break
        tool_loop_stagnation = max(0, repeated - 2)
        return {
            "work_state": work.get("state"),
            "work_type": work.get("work_type"),
            "repository_files": code_files,
            "task_count": len(tasks),
            "planned_file_count": len(planned_files),
            "requirement_count": req_count,
            "decision_count": len(decisions),
            "pending_scope_requests": sum(1 for x in scope if str(x.get("status")) == "pending"),
            "scope_request_count": len(scope),
            "consecutive_validation_failures": consecutive,
            "failure_kind": failure_kind,
            "validation_precondition_observations": preconditions,
            "deduplicated_validation_observations": duplicate_observations,
            "recent_non_orchestrator_tools": recent_tools,
            "tool_loop_stagnation": tool_loop_stagnation,
        }

    @staticmethod
    def assess_complexity(signals: dict[str, Any] | None = None) -> ComplexityAssessment:
        s = dict(signals or {})
        score = 10
        reasons: list[str] = []
        repo = int(s.get("repository_files") or 0)
        files = int(s.get("planned_file_count") or 0)
        tasks = int(s.get("task_count") or 0)
        reqs = int(s.get("requirement_count") or 0)
        pending_scope = int(s.get("pending_scope_requests") or 0)
        failures = int(s.get("consecutive_validation_failures") or 0)
        ambiguity = int(s.get("unresolved_ambiguities") or 0)
        if repo >= 40:
            score += 15; reasons.append("brownfield_repository")
        if repo >= 200:
            score += 10; reasons.append("large_repository")
        if files >= 5:
            score += 10; reasons.append("multi_file_change")
        if files >= 12:
            score += 10; reasons.append("wide_change_surface")
        if tasks >= 4:
            score += 8; reasons.append("multi_task_plan")
        if reqs >= 8:
            score += 8; reasons.append("many_requirements")
        if pending_scope:
            # Governance/scope state is not evidence that a stronger model would
            # perform better. Preserve the signal for policy/UI, but never let it
            # raise complexity or model tier.
            reasons.append("scope_governance_pending_no_capability_signal")
        if ambiguity:
            score += min(20, ambiguity * 8); reasons.append("unresolved_ambiguity")
        if failures:
            score += min(25, failures * 12); reasons.append("validation_failures")
        score = max(0, min(100, score))
        level = "simple" if score < 35 else "normal" if score < 70 else "complex"
        return ComplexityAssessment(score, level, s, reasons)

    @staticmethod
    def assess_budget(activity: str, usage: dict[str, Any] | None = None) -> BudgetAssessment:
        activity = normalize_activity(activity)
        budget = dict(PHASE_TOKEN_BUDGETS[activity])
        usage = usage or {}
        context: int | None = None
        generated: int | None = None
        measurement_scope = "unmeasured"

        rows = [x for x in (usage.get("phase_usage") or []) if isinstance(x, dict) and normalize_activity(str(x.get("phase") or activity)) == activity]
        live_alloc = [x for x in rows if x.get("allocation_state") == "live" and (x.get("delta_context_read_tokens") is not None or x.get("delta_generated_tokens") is not None)]
        alloc = live_alloc[-1:] or [x for x in rows if x.get("delta_context_read_tokens") is not None or x.get("delta_generated_tokens") is not None]
        if alloc:
            context = sum(int(x.get("delta_context_read_tokens") or 0) for x in alloc)
            generated = sum(int(x.get("delta_generated_tokens") or 0) for x in alloc)
            measurement_scope = "phase_live" if live_alloc else "phase_allocated"
        else:
            raw_context = usage.get("context_read_tokens")
            raw_generated = usage.get("generated_tokens", usage.get("output_tokens"))
            if raw_context is not None or raw_generated is not None:
                measurement_scope = "process_fallback"
                try:
                    context = int(raw_context) if raw_context is not None else None
                except Exception:
                    context = None
                try:
                    generated = int(raw_generated) if raw_generated is not None else None
                except Exception:
                    generated = None

        if measurement_scope not in {"phase_allocated", "phase_live"}:
            # Diagnostic process totals are intentionally preserved, but ratios are
            # withheld because comparing them to a phase budget is dimensionally wrong.
            actions = ["compact_context_checkpoint", "narrow_tool_surface", "repair_phase_telemetry"]
            status = "unallocatable" if measurement_scope == "process_fallback" else "unmeasured"
            return BudgetAssessment(activity, budget, context, generated, None, None, measurement_scope, status, actions)

        cr = round((context or 0) / budget["context_soft"], 3) if budget["context_soft"] else None
        gr = round((generated or 0) / budget["generated_soft"], 3) if budget["generated_soft"] else None
        hard = bool((context is not None and context > budget["context_hard"]) or (generated is not None and generated > budget["generated_hard"]))
        soft = bool((context is not None and context > budget["context_soft"]) or (generated is not None and generated > budget["generated_soft"]))
        if hard:
            status = "hard_exceeded"
            actions = ["compact_context_checkpoint", "narrow_tool_surface", "targeted_retrieval", "drop_stale_context", "deduplicate_evidence", "reevaluate_after_compaction"]
        elif soft:
            status = "soft_exceeded"
            actions = ["compact_context_checkpoint", "narrow_tool_surface", "deduplicate_evidence"]
        else:
            status = "within_budget"
            actions = []
        return BudgetAssessment(activity, budget, context, generated, cr, gr, measurement_scope, status, actions)

    @staticmethod
    def context_strategy_for(activity: str, budget: BudgetAssessment) -> dict[str, Any]:
        activity = normalize_activity(activity)
        if budget.status == "hard_exceeded":
            mode, max_batch, max_lines = "critical", 2, 120
        elif budget.status == "soft_exceeded":
            mode, max_batch, max_lines = "compact", 4, 220
        elif budget.status in {"unallocatable", "unmeasured"}:
            mode, max_batch, max_lines = "telemetry_safe", 4, 220
        elif activity in {"implementation", "code_review", "validation", "merge"}:
            mode, max_batch, max_lines = "lean", 4, 260
        else:
            mode, max_batch, max_lines = "normal", 8, 400
        sections = {
            "planning": ["spec", "tasks"],
            "implementation": ["tasks", "validations"],
            "code_review": ["tasks", "recent_runs", "validations"],
            "validation": ["validations", "recent_runs"],
            "merge": ["validations", "recent_runs", "scope_requests"],
        }.get(activity, ["work", "tasks"])
        return {
            "mode": mode,
            "checkpoint_first": mode != "normal",
            "checkpoint_sections": sections,
            "reuse_evidence_refs": True,
            "avoid_repeat_reads": True,
            "max_read_batch": max_batch,
            "max_file_slice_lines": max_lines,
            "drop_stale_context": mode in {"compact", "critical"},
            "actions": list(budget.actions),
            "principle": "reuse_authoritative_checkpoint_before_reloading_context",
        }

    @staticmethod
    def _complexity_tier(activity: str, complexity: ComplexityAssessment) -> str:
        if activity in {"discovery", "specification", "planning", "merge"}:
            return "standard" if complexity.level == "complex" else "economy"
        if activity == "implementation":
            return "economy" if complexity.level == "simple" else "standard"
        if activity == "code_review":
            return "economy" if complexity.level == "simple" else ("standard" if complexity.level == "normal" else "strong")
        if activity == "validation":
            return "standard" if int(complexity.signals.get("consecutive_validation_failures") or 0) else "economy"
        return PHASE_BASE_TIER.get(activity, "economy")

    def _state(self) -> dict[str, Any]:
        state = _load_json(self.state_path, {"schema_version": self.STATE_SCHEMA_VERSION, "works": {}, "updated_at": None})
        if not isinstance(state, dict):
            state = {"schema_version": self.STATE_SCHEMA_VERSION, "works": {}, "updated_at": None}
        state.setdefault("works", {})
        state["schema_version"] = self.STATE_SCHEMA_VERSION
        return state

    def _save_state(self, value: dict[str, Any]) -> None:
        value["schema_version"] = self.STATE_SCHEMA_VERSION
        value["updated_at"] = utc_now()
        _atomic_json(self.state_path, value)

    def _append_event(self, value: dict[str, Any]) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": utc_now(), **value}, ensure_ascii=False, default=str) + "\n")

    def _work_row(self, state: dict[str, Any], provider: str, work_id: str) -> dict[str, Any]:
        return state.setdefault("works", {}).setdefault(
            f"{provider}:{work_id}",
            {"tier": "economy", "same_tier_retries": 0, "history": [], "pending_transition": None, "requested_route": None, "applied_route": None, "verified_route": None, "last_route_verified": None},
        )

    def record_outcome(
        self,
        provider: str,
        work_id: str,
        *,
        success: bool,
        failure_kind: str | None = None,
        activity: str | None = None,
        failure_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        provider = normalize_provider(provider)
        state = self._state()
        row = self._work_row(state, provider, work_id)
        kind = str(failure_kind or "unknown") if not success else None
        outcome_tier = (
            _route_tier(provider, row.get("applied_route"))
            or str(row.get("applied_tier") or row.get("tier") or "economy")
        )
        if outcome_tier not in TIER_ORDER:
            outcome_tier = "economy"
        # Success is a hard invariant: it can never be persisted as a model
        # failure, even if a caller accidentally supplied stale failure metadata.
        counted = False if success else True
        deduplicated = False
        if success:
            failure_fingerprint = None
            row["same_tier_retries"] = 0
            row["last_failure_kind"] = None
            row["last_failure_fingerprint"] = None
            row["tier"] = "economy"
            row["pending_transition"] = None
        elif kind in NON_MODEL_FAILURES:
            counted = False
        elif failure_fingerprint and failure_fingerprint == row.get("last_failure_fingerprint"):
            counted = False
            deduplicated = True
        else:
            row["same_tier_retries"] = int(row.get("same_tier_retries") or 0) + 1
            row["last_failure_kind"] = kind
            row["last_failure_fingerprint"] = failure_fingerprint
            if row["same_tier_retries"] >= 2:
                row["tier"] = TIER_ORDER[min(len(TIER_ORDER) - 1, _tier_index(str(row.get("tier") or "economy")) + 1)]
                row["same_tier_retries"] = 0
        eligible_for_learning = bool(
            not deduplicated
            and (success or (counted and kind not in PREDICTIVE_LEARNING_EXCLUDED_FAILURES))
        )
        event = {
            "event": "model_outcome",
            "provider": provider,
            "work_id": work_id,
            "activity": activity,
            "success": success,
            "failure_kind": kind,
            "failure_fingerprint": failure_fingerprint,
            "counts_as_model_failure": counted,
            "eligible_for_learning": eligible_for_learning,
            "deduplicated": deduplicated,
            "outcome_tier": outcome_tier,
            "tier": row.get("tier"),
        }
        row.setdefault("history", []).append(event)
        row["history"] = row["history"][-30:]
        self._save_state(state)
        self._append_event(event)
        try:
            predictive = PredictiveRoutingMemory(self.root).record(
                provider, activity, outcome_tier, success=success,
                counts_as_model_failure=counted, failure_kind=kind,
                eligible_for_learning=eligible_for_learning,
                work_id=work_id, failure_fingerprint=failure_fingerprint,
            )
            if predictive is not None:
                row["predictive_outcome"] = predictive
        except Exception:
            pass
        return dict(row)

    def observe_provider_route(
        self,
        provider: str,
        work_id: str,
        route: dict[str, Any] | ModelRoute,
        *,
        verified: bool | None,
        observed_model: str | None = None,
        observed_effort: str | None = None,
        activity: str | None = None,
        source: str = "provider",
        initial_route: bool = False,
    ) -> dict[str, Any]:
        """Record provider truth after a safe boundary.

        `verified=True` means the provider accepted the requested route without a
        reroute. We only then count it as applied and verified. If a provider
        reports a different observed model, that observation is stored but is not
        credited as the requested transition.
        """
        provider = normalize_provider(provider)
        state = self._state()
        row = self._work_row(state, provider, work_id)
        requested = route.to_dict() if isinstance(route, ModelRoute) else dict(route)
        requested_tier = _route_tier(provider, requested)
        previous_tier = _route_tier(provider, row.get("applied_route")) or "economy"
        row["requested_route"] = requested
        event_prefix = "initial_route" if initial_route else "route"
        request_event = {"event": f"{event_prefix}_requested", "provider": provider, "work_id": work_id, "activity": activity, "route": requested, "tier": requested_tier, "source": source, "transition_kind": "initial" if initial_route else "model_control"}
        self._append_event(request_event)

        applied = None
        if verified is True:
            applied = dict(requested)
            row["applied_route"] = applied
            row["verified_route"] = applied
            row["last_route_verified"] = True
            if requested_tier:
                row["applied_tier"] = requested_tier
            pending = row.get("pending_transition") or {}
            if isinstance(pending, dict) and ((pending.get("route") or {}).get("model") == applied.get("model")):
                row["pending_transition"] = None
            escalation_applied = bool(requested_tier and _tier_index(requested_tier) > _tier_index(previous_tier))
            self._append_event({"event": f"{event_prefix}_applied", "provider": provider, "work_id": work_id, "activity": activity, "route": applied, "tier": requested_tier, "escalation": escalation_applied if not initial_route else False, "source": source, "transition_kind": "initial" if initial_route else "model_control"})
            self._append_event({"event": f"{event_prefix}_verified", "provider": provider, "work_id": work_id, "activity": activity, "route": applied, "tier": requested_tier, "verified": True, "escalation": escalation_applied if not initial_route else False, "source": source, "transition_kind": "initial" if initial_route else "model_control"})
        elif verified is False:
            row["last_route_verified"] = False
            observed = None
            if observed_model:
                observed = {**requested, "model": observed_model}
                if observed_effort is not None:
                    observed["effort"] = observed_effort
                row["applied_route"] = observed
                row["applied_tier"] = _route_tier(provider, observed)
            self._append_event({"event": f"{event_prefix}_verification_failed", "provider": provider, "work_id": work_id, "activity": activity, "requested_route": requested, "observed_route": observed, "verified": False, "source": source, "transition_kind": "initial" if initial_route else "model_control"})
        else:
            row["last_route_verified"] = None
            self._append_event({"event": f"{event_prefix}_verification_unavailable", "provider": provider, "work_id": work_id, "activity": activity, "requested_route": requested, "source": source, "transition_kind": "initial" if initial_route else "model_control"})
        self._save_state(state)
        return dict(row)

    def decide(
        self,
        provider: str,
        activity: str,
        *,
        work_id: str | None = None,
        usage: dict[str, Any] | None = None,
        extra_signals: dict[str, Any] | None = None,
        at_provider_boundary: bool = False,
    ) -> ModelControlDecision:
        provider = normalize_provider(provider)
        activity = normalize_activity(activity)
        explicit = ProviderModelRouting(self.root).resolve(provider, activity)
        signals = self._work_signals(work_id)
        signals.update(extra_signals or {})
        budget = self.assess_budget(activity, usage)
        context_strategy = self.context_strategy_for(activity, budget)
        # Context pressure is an efficiency signal, not a model-capability
        # signal. A larger model does not repair an oversized prompt and must
        # never be selected merely because a phase exceeded its token budget.
        complexity = self.assess_complexity(signals)
        failure_kind = signals.get("failure_kind")
        failures = int(signals.get("consecutive_validation_failures") or 0)
        retry = retry_policy_for(failure_kind, failures)
        reasons: list[str] = []
        prediction: dict[str, Any] = {
            "schema_version": 1,
            "prediction_used": False,
            "action": "not_evaluated",
            "reason": "explicit_or_unavailable",
        }

        state = self._state()
        row = self._work_row(state, provider, work_id) if work_id else {}
        pending = row.get("pending_transition") if isinstance(row, dict) else None

        if explicit.source not in {"builtin_default"} or self.policy == "pinned":
            tier = "pinned"
            route = explicit
            reasons.append("explicit_provider_model_route")
            escalation_reasons: list[str] = []
            escalation = {"requested": False, "recommended": False, "request_sent": False, "reason": None, "from_tier": None, "to_tier": None, "applied": False, "verified": False}
        else:
            tier = self.policy if self.policy in TIER_ORDER else self._complexity_tier(activity, complexity)
            base_tier = tier
            remembered = str(row.get("tier") or "economy") if row else "economy"
            tier = _max_tier(tier, remembered)
            predictive_transition_reasons: list[str] = []
            if self.policy == "adaptive":
                try:
                    prediction = PredictiveRoutingMemory(self.root).assess(provider, activity, tier, complexity.level)
                    predicted_tier = str(prediction.get("recommended_tier") or tier)
                    if predicted_tier in TIER_ORDER and predicted_tier != tier:
                        if _tier_index(predicted_tier) > _tier_index(tier):
                            predictive_transition_reasons.append("predictive_capability_history")
                            reasons.append("predictive_capability_history")
                            tier = predicted_tier
                        elif remembered == "economy":
                            predictive_transition_reasons.append("predictive_cost_downshift")
                            reasons.append("predictive_cost_downshift")
                            tier = predicted_tier
                        else:
                            prediction = {**prediction, "prediction_used": False, "vetoed": True, "veto_reason": "current_work_failure_memory"}
                except Exception:
                    prediction = {"schema_version": 1, "prediction_used": False, "action": "unavailable", "reason": "predictive_memory_unavailable"}
            escalation_reasons = []
            if retry.get("escalate_after_retry"):
                escalation_reasons.append("repeated_validation_failure")
            if int(signals.get("pending_scope_requests") or 0):
                reasons.append("scope_governance_pending_no_capability_escalation")
            if int(signals.get("tool_loop_stagnation") or 0) >= 2:
                escalation_reasons.append("tool_loop_stagnation")
            if budget.measurement_scope in {"process_fallback", "unmeasured"}:
                reasons.append("telemetry_not_comparable_no_escalation")
            elif budget.status in {"soft_exceeded", "hard_exceeded"}:
                reasons.append("phase_token_pressure_compaction_first")
            if escalation_reasons:
                tier = TIER_ORDER[min(len(TIER_ORDER) - 1, _tier_index(tier) + 1)]

            # A pending recommendation from a suspended turn becomes eligible at
            # the next safe provider boundary. Never silently downgrade it before
            # the boundary unless a successful quality gate cleared it.
            if at_provider_boundary and isinstance(pending, dict):
                pending_tier = str(pending.get("tier") or "")
                if pending_tier in TIER_ORDER:
                    tier = _max_tier(tier, pending_tier)
                    reasons.append("pending_transition_consumed_at_safe_boundary")

            model = MODEL_TIERS[provider][tier]
            effort = PHASE_EFFORT.get(activity, {}).get(tier) or model.get("effort")
            route = ModelRoute(provider, activity, model["model"], effort, "adaptive_model_control", "managed", "turn_override" if provider == "codex" else "session_or_resume_boundary")
            capability_escalation_reasons = list(escalation_reasons)
            if "predictive_capability_history" in predictive_transition_reasons:
                capability_escalation_reasons.append("predictive_capability_history")
            escalation = {
                # `requested` remains a backwards-compatible alias for the old
                # recommendation flag. New telemetry uses recommended/request_sent.
                "requested": bool(capability_escalation_reasons),
                "recommended": bool(capability_escalation_reasons),
                "request_sent": False,
                "reason": capability_escalation_reasons,
                "from_tier": base_tier,
                "to_tier": tier,
                "applied": False,
                "verified": False,
            }
            reasons.extend(complexity.reasons)
            reasons.extend(escalation_reasons)
            if not reasons:
                reasons.append("cheapest_tier_expected_to_clear_next_gate")

        safe_boundary = bool(at_provider_boundary)
        apply_now = bool(at_provider_boundary)
        route_requested = None
        route_recommended = None
        route_applied = dict(row.get("applied_route")) if isinstance(row.get("applied_route"), dict) else None
        route_verified = row.get("last_route_verified") if row else None

        if work_id and explicit.source in {"builtin_default"} and self.policy != "pinned":
            recommended = route.to_dict()
            applied_route = row.get("applied_route") if isinstance(row, dict) else None
            route_differs_from_applied = bool(
                isinstance(applied_route, dict)
                and (
                    str(applied_route.get("model") or "") != str(recommended.get("model") or "")
                    or str(applied_route.get("effort") or "") != str(recommended.get("effort") or "")
                )
            )
            transition_reasons = list(escalation_reasons)
            for item in predictive_transition_reasons:
                if item not in transition_reasons:
                    transition_reasons.append(item)
            if route_differs_from_applied and not transition_reasons:
                transition_reasons = ["same_tier_effort_optimization"]

            if transition_reasons and not at_provider_boundary:
                pending_transition = {
                    "route": recommended,
                    "tier": tier,
                    "activity": activity,
                    "reason": list(transition_reasons),
                    "recommended_at": utc_now(),
                }
                row["pending_transition"] = pending_transition
                route_recommended = recommended
                if "predictive_cost_downshift" in transition_reasons:
                    transition_kind = "predictive_cost_downshift"
                elif "predictive_capability_history" in transition_reasons:
                    transition_kind = "predictive_capability"
                else:
                    transition_kind = "escalation" if escalation_reasons else "effort_optimization"
                self._append_event({"event": "route_recommended", "provider": provider, "work_id": work_id, "activity": activity, "route": recommended, "tier": tier, "reason": transition_reasons, "safe_boundary": False, "transition_kind": transition_kind})
                self._save_state(state)
                pending = pending_transition
            elif at_provider_boundary:
                route_requested = recommended
                row["requested_route"] = recommended
                escalation["request_sent"] = bool(capability_escalation_reasons or pending)
                self._append_event({"event": "route_requested", "provider": provider, "work_id": work_id, "activity": activity, "route": recommended, "tier": tier, "reason": escalation_reasons or ((pending or {}).get("reason") if isinstance(pending, dict) else []), "safe_boundary": True})
                self._save_state(state)

        pending_transition = dict(row.get("pending_transition")) if isinstance(row.get("pending_transition"), dict) else None
        decision = ModelControlDecision(
            provider, activity, route, tier, self.policy, complexity, budget, escalation, retry,
            safe_boundary, apply_now, reasons, route_requested, route_applied, route_verified, pending_transition, route_recommended, prediction, context_strategy,
        )
        self._append_event({"event": "model_route_decision", "work_id": work_id, **decision.to_dict()})
        return decision

    def for_work(self, provider: str, work: dict[str, Any], *, usage: dict[str, Any] | None = None, at_provider_boundary: bool = False) -> ModelControlDecision:
        return self.decide(provider, activity_for_state(work.get("state")), work_id=work.get("id"), usage=usage, at_provider_boundary=at_provider_boundary)

    def show(self, provider: str | None = None, work_id: str | None = None) -> dict[str, Any]:
        providers = [normalize_provider(provider)] if provider else ["cursor", "codex"]
        state = self._state()
        return {
            "schema_version": self.STATE_SCHEMA_VERSION,
            "policy": self.policy,
            "providers": {p: {"tiers": MODEL_TIERS[p], "activities": {a: self.decide(p, a, work_id=work_id).to_dict() for a in PHASE_TOKEN_BUDGETS}} for p in providers},
            "token_budgets": PHASE_TOKEN_BUDGETS,
            "state_path": str(self.state_path),
            "state": state,
            "economic_safety_principle": "unmeasured_or_process_fallback_telemetry_never_escalates_model",
            "predictive_routing": {
                "enabled": self.policy == "adaptive",
                "memory_path": str(PredictiveRoutingMemory(self.root).path),
                "principle": "historical_capability_outcomes_only_context_pressure_excluded",
            },
        }
