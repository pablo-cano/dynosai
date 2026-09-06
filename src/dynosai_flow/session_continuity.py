# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Bounded same-session Codex continuation for certification trials.

A trial is one provider session. Continuation turns are not MATRIX retries
and must not start a second App Server, thread, or governed DYN work item.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .util import utc_now


MAX_PROVIDER_TURNS = 3
DONE_STATES = {"done"}
TERMINAL_FAILURE_STATES = {"failed", "cancelled", "canceled"}
EXTRA_DYN_WORK = "extra_dyn_work"
NEW_PROVIDER_SESSION = "new_provider_session"
PROVIDER_NO_PROGRESS = "provider_no_progress"
CONTINUATION_BUDGET_EXHAUSTED = "continuation_budget_exhausted"
TERMINAL_FAILURE = "terminal_failure"
USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "observed_token_total",
)


class ContinuationError(RuntimeError):
    """Same-session continuation ended the trial with a certification failure."""


def empty_progress_snapshot() -> dict[str, Any]:
    return {
        "work_id": None,
        "work_state": None,
        "phase": None,
        "current_action": None,
        "completed_task_count": 0,
        "validation_state": None,
        "observed_gates": (),
        "git_revision": None,
        "governed_work_items": 0,
        "provider_session_id": None,
    }


def progress_fingerprint(snapshot: dict[str, Any] | None) -> tuple[Any, ...]:
    payload = snapshot or empty_progress_snapshot()
    gates = payload.get("observed_gates") or ()
    if isinstance(gates, list):
        gates = tuple(gates)
    return (
        payload.get("work_id"),
        payload.get("work_state"),
        payload.get("phase"),
        payload.get("current_action"),
        int(payload.get("completed_task_count") or 0),
        payload.get("validation_state"),
        gates,
        payload.get("git_revision"),
        int(payload.get("governed_work_items") or 0),
    )


def usage_snapshot(payload: dict[str, Any] | None) -> dict[str, int]:
    src = payload or {}
    return {key: int(src.get(key) or 0) for key in USAGE_KEYS}


def usage_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, int]:
    start = usage_snapshot(before)
    end = usage_snapshot(after)
    return {key: max(0, end[key] - start[key]) for key in USAGE_KEYS}


def continuation_prompt(*, work_id: str | None = None, state: str | None = None) -> str:
    lines = [
        "Continue the existing DynosAI governed work from its current authoritative state.",
        "Do not restart or create another work item.",
        "Call dynosai_get_next_action with execute=true and continue the approved workflow.",
        "Use the existing DYN work item and current task queue.",
        "Do not stop until DynosAI reports done or a genuine terminal blocker is reached.",
    ]
    if work_id:
        lines.append(f"Existing work_id: {work_id}.")
    if state:
        lines.append(f"Current DynosAI state: {state}.")
    return "\n".join(lines)


def inspect_authoritative_state(project: Path) -> dict[str, Any]:
    """Read fixture DynosAI authority. Never create a project just to inspect."""
    snapshot = empty_progress_snapshot()
    db_path = Path(project) / ".dynosai" / "knowledge.db"
    if not db_path.is_file():
        return snapshot
    from .application import DynosAIApplication
    from .model_routing import activity_for_state

    app = DynosAIApplication(Path(project))
    app.engine.db.initialize()
    all_rows = app.engine.db.query(
        "SELECT id,work_type,state,quality_score,provider_session_id FROM work_items ORDER BY created_at"
    )
    governed = [row for row in all_rows if str(row.get("id") or "").startswith("DYN-")]
    snapshot["governed_work_items"] = len(governed)
    if not governed:
        return snapshot
    work = governed[0]
    wid = str(work.get("id") or "")
    state = str(work.get("state") or "")
    snapshot["work_id"] = wid
    snapshot["work_state"] = state
    snapshot["phase"] = activity_for_state(state)
    snapshot["current_action"] = state
    snapshot["provider_session_id"] = work.get("provider_session_id")
    try:
        completed = app.engine.db.query(
            "SELECT id FROM tasks WHERE work_id=? AND state IN ('done','verified','completed')",
            (wid,),
        )
        snapshot["completed_task_count"] = len(completed)
    except Exception:
        snapshot["completed_task_count"] = 0
    try:
        gates = app.engine.db.query(
            "SELECT gate,status FROM human_interactions WHERE work_id=? ORDER BY created_at",
            (wid,),
        )
        snapshot["observed_gates"] = tuple(f"{item.get('gate')}:{item.get('status')}" for item in gates)
    except Exception:
        snapshot["observed_gates"] = ()
    try:
        validations = app.engine.db.query(
            "SELECT status FROM validations WHERE work_id=? ORDER BY created_at DESC LIMIT 1",
            (wid,),
        )
        if validations:
            snapshot["validation_state"] = validations[0].get("status")
    except Exception:
        snapshot["validation_state"] = None
    git_dir = Path(project) / ".git"
    if git_dir.exists():
        import subprocess

        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            snapshot["git_revision"] = (proc.stdout or "").strip() or None
    return snapshot


@dataclass(slots=True)
class ContinuationDecision:
    action: str
    reason: str | None = None
    prompt: str | None = None


def decide_continuation(
    *,
    turn_index: int,
    max_turns: int = MAX_PROVIDER_TURNS,
    thread_id: str | None,
    completed_thread_id: str | None,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> ContinuationDecision:
    """Decide whether the same Codex thread may start another turn."""
    if thread_id and completed_thread_id and str(completed_thread_id) != str(thread_id):
        return ContinuationDecision("stop", NEW_PROVIDER_SESSION)
    after_snap = after or empty_progress_snapshot()
    governed = int(after_snap.get("governed_work_items") or 0)
    if governed > 1:
        return ContinuationDecision("stop", EXTRA_DYN_WORK)
    state = str(after_snap.get("work_state") or "")
    if state in DONE_STATES:
        return ContinuationDecision("stop", None)
    if state in TERMINAL_FAILURE_STATES:
        return ContinuationDecision("stop", TERMINAL_FAILURE)
    if governed == 0:
        return ContinuationDecision("stop", None)
    if progress_fingerprint(after_snap) == progress_fingerprint(before):
        return ContinuationDecision("stop", PROVIDER_NO_PROGRESS)
    if int(turn_index) >= int(max_turns):
        return ContinuationDecision("stop", CONTINUATION_BUDGET_EXHAUSTED)
    return ContinuationDecision(
        "continue",
        None,
        continuation_prompt(work_id=after_snap.get("work_id"), state=state),
    )


def record_turn(
    *,
    turn_index: int,
    turn_id: str | None,
    started_at: str | None,
    finished_at: str | None = None,
    status: str | None = None,
    token_usage: dict[str, Any] | None = None,
    dynosai_calls: int | None = None,
    work_state_before: str | None = None,
    work_state_after: str | None = None,
    progress_made: bool | None = None,
) -> dict[str, Any]:
    return {
        "turn_index": int(turn_index),
        "turn_id": turn_id,
        "started_at": started_at,
        "finished_at": finished_at or utc_now(),
        "status": status,
        "token_usage": token_usage,
        "dynosai_calls": dynosai_calls,
        "work_state_before": work_state_before,
        "work_state_after": work_state_after,
        "progress_made": progress_made,
    }


Inspector = Callable[[Path], dict[str, Any]]
