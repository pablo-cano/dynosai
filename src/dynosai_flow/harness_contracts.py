# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Typed harness contracts shared by DynosAI Core, MCP and evals.

These schemas are validated outside the model. They describe governed
execution, not provider-specific wire formats.
"""

from __future__ import annotations

import os
from typing import Any, Literal, TypedDict

ARTIFACT_TYPES = (
    "tool_output",
    "search",
    "log",
    "diff",
    "evidence",
    "test_report",
    "file",
    "plan",
    "validation_report",
    "checkpoint",
)

HARNESS_COMPONENTS = (
    "context_handles",
    "skills",
    "retrieval",
    "compression",
    "replanning",
    "reviewer",
    "planner",
)

WORK_TERMINAL_STATES = frozenset({"done"})
WORK_RESUMABLE_STATES = frozenset({
    "inbox", "discovery", "spec_review", "plan_review", "ready",
    "implementing", "code_review", "validating", "ready_to_merge",
})

CompletionStatus = Literal["incomplete", "verified", "failed", "blocked", "done"]
RecoveryStatus = Literal[
    "running",
    "stalled",
    "context_exhausted",
    "provider_interrupted",
    "retry_budget_exhausted",
    "terminal",
]


class ContextHandleMeta(TypedDict, total=False):
    id: str
    artifact_type: str
    origin: str
    work_id: str | None
    task_id: str | None
    session_id: str | None
    size: int
    checksum: str
    preview: str
    created_at: str
    updated_at: str
    retrieval: str


class ProviderRequest(TypedDict, total=False):
    work_id: str
    activity: str
    provider: str
    prompt_ref: str | None
    handle_ids: list[str]


class ProviderResult(TypedDict, total=False):
    provider: str
    status: str
    exit_code: int | None
    summary: str
    handle_ids: list[str]
    error: str | None


class ExecutionState(TypedDict, total=False):
    work_id: str
    state: str
    activity: str
    run_id: str | None
    can_resume: bool
    terminal: bool


class RecoveryState(TypedDict, total=False):
    status: RecoveryStatus
    reason: str
    can_resume: bool
    terminal: bool
    requires_human: bool
    requires_replan: bool
    retry_count: int
    retry_budget: int


def harness_enabled(component: str, default: bool = True) -> bool:
    """Return whether a removable harness component is enabled.

    Components are hypotheses: they must be independently switchable so evals
    can compare WITH versus WITHOUT. Unknown names default to ``default``.
    """
    key = f"DYNOSAI_HARNESS_{str(component or '').strip().upper()}"
    raw = os.environ.get(key)
    if raw is None and component == "context_handles":
        raw = os.environ.get("DYNOSAI_CONTEXT_HANDLES")
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def classify_execution_state(
    *,
    work_state: str | None,
    stalled: bool = False,
    context_exhausted: bool = False,
    provider_interrupted: bool = False,
    retry_count: int = 0,
    retry_budget: int = 3,
    material_scope_change: bool = False,
    cancelled: bool = False,
) -> dict[str, Any]:
    """Classify long-running execution without treating chat as project state.

    Context exhaustion and provider restart must remain resumable: workflow
    truth lives in knowledge.db. Material scope changes never auto-replan.
    """
    state = str(work_state or "")
    retry_count = max(0, int(retry_count or 0))
    retry_budget = max(0, int(retry_budget or 0))
    if cancelled or state == "cancelled":
        return {
            "status": "terminal",
            "reason": "work_cancelled",
            "can_resume": False,
            "terminal": True,
            "requires_human": False,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    if state in WORK_TERMINAL_STATES:
        return {
            "status": "terminal",
            "reason": "work_completed",
            "can_resume": False,
            "terminal": True,
            "requires_human": False,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    if material_scope_change:
        return {
            "status": "stalled",
            "reason": "material_scope_change_requires_governance_gate",
            "can_resume": True,
            "terminal": False,
            "requires_human": True,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    if retry_budget and retry_count >= retry_budget:
        return {
            "status": "retry_budget_exhausted",
            "reason": "retry_budget_exhausted",
            "can_resume": False,
            "terminal": True,
            "requires_human": True,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    if provider_interrupted:
        return {
            "status": "provider_interrupted",
            "reason": "provider_process_interrupted_workflow_state_preserved",
            "can_resume": True,
            "terminal": False,
            "requires_human": False,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    if context_exhausted:
        return {
            "status": "context_exhausted",
            "reason": "reconstruct_from_checkpoint_and_handles",
            "can_resume": True,
            "terminal": False,
            "requires_human": False,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    if stalled:
        return {
            "status": "stalled",
            "reason": "stagnation_detected",
            "can_resume": True,
            "terminal": False,
            "requires_human": True,
            "requires_replan": False,
            "retry_count": retry_count,
            "retry_budget": retry_budget,
        }
    return {
        "status": "running",
        "reason": "active",
        "can_resume": state in WORK_RESUMABLE_STATES,
        "terminal": False,
        "requires_human": False,
        "requires_replan": False,
        "retry_count": retry_count,
        "retry_budget": retry_budget,
    }


def validate_artifact_type(value: str) -> str:
    if value not in ARTIFACT_TYPES:
        raise ValueError(f"unknown artifact type: {value}")
    return value
