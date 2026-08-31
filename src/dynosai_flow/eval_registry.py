# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Eval Registry v0: small, versioned, comparable harness measurements.

This is not hundreds of synthetic tests. It records a representative offline
baseline so later harness changes can be compared. Live provider quality is
out of scope until a recorded run exists. 0.17 adds offline multi-agent and
mined-regression fixtures; they still do not call providers.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from .agent_config import AgentConfiguration
from .context_handles import ContextHandleStore
from .harness_contracts import classify_execution_state, harness_enabled
from .util import utc_now
from .validation_integrity import evaluate_integrity

REGISTRY_VERSION = 1

SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "greenfield_implementation", "version": "1", "family": "greenfield", "summary": "New feature from an approved spec/plan."},
    {"id": "brownfield_feature", "version": "1", "family": "brownfield", "summary": "Add a feature to an existing repository."},
    {"id": "bug_fix", "version": "1", "family": "bugfix", "summary": "Fix a failing behavior without expanding scope."},
    {"id": "refactor", "version": "1", "family": "refactor", "summary": "Restructure code while preserving behavior."},
    {"id": "provider_interruption", "version": "1", "family": "recovery", "summary": "Provider process dies mid-turn; workflow state must survive."},
    {"id": "resume_recovery", "version": "1", "family": "recovery", "summary": "Resume from durable DynosAI state after restart."},
    {"id": "validation_failure", "version": "1", "family": "validation", "summary": "Configured checks fail; completion must not be claimed."},
    {"id": "requirement_mismatch", "version": "1", "family": "integrity", "summary": "Implementation evidence does not cover the requirement."},
    {"id": "context_pressure", "version": "1", "family": "context", "summary": "Large artifacts should be referenced, not replayed."},
    {"id": "team_overlap", "version": "1", "family": "multi_agent", "summary": "Overlapping worker scopes must fail fan-in without live providers."},
    {"id": "mined_regression", "version": "1", "family": "intelligence", "summary": "Placeholder target for a mined local-trace regression case."},
)


def scenario(scenario_id: str) -> dict[str, Any]:
    for item in SCENARIOS:
        if item["id"] == scenario_id:
            return dict(item)
    raise ValueError(f"unknown eval scenario: {scenario_id}")


def compare_records(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "success", "requirements_satisfied", "elapsed_ms", "tool_calls", "retries",
        "human_interventions", "input_tokens", "output_tokens", "context_bytes",
        "context_bytes_omitted",
    )
    delta = {}
    for key in keys:
        a, b = left.get(key), right.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            delta[key] = b - a
        elif a != b:
            delta[key] = {"from": a, "to": b}
    return {
        "left": {"scenario": left.get("scenario"), "harness": left.get("harness")},
        "right": {"scenario": right.get("scenario"), "harness": right.get("harness")},
        "delta": delta,
        "improved": bool(
            (right.get("success") and not left.get("success"))
            or (int(right.get("context_bytes_omitted") or 0) > int(left.get("context_bytes_omitted") or 0) and right.get("success") == left.get("success"))
        ),
    }


class EvalRegistry:
    """File-backed eval records. Not workflow authority; measurement only."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.base = self.root / ".dynosai" / "runtime" / "eval-results"

    def list_scenarios(self) -> list[dict[str, Any]]:
        return [dict(item) for item in SCENARIOS]

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("scenario", "scenario_version", "harness", "success")
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError("eval record missing: " + ", ".join(missing))
        scenario(str(payload["scenario"]))
        row = {
            "registry_version": REGISTRY_VERSION,
            "recorded_at": utc_now(),
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "failure_category": payload.get("failure_category"),
            "validation_result": payload.get("validation_result"),
            "requirements_satisfied": payload.get("requirements_satisfied"),
            "token_usage": payload.get("token_usage") or {},
            "context_usage": payload.get("context_usage") or {},
            "elapsed_ms": int(payload.get("elapsed_ms") or 0),
            "tool_calls": int(payload.get("tool_calls") or 0),
            "retries": int(payload.get("retries") or 0),
            "human_interventions": int(payload.get("human_interventions") or 0),
            **payload,
        }
        self.base.mkdir(parents=True, exist_ok=True)
        stamp = str(row["recorded_at"]).replace(":", "").replace("-", "")
        path = self.base / f"{row['scenario']}-{stamp}.json"
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return {**row, "path": str(path)}

    def run_offline(self, scenario_id: str, *, harness: dict[str, Any] | None = None, engine: Any | None = None) -> dict[str, Any]:
        """Deterministic fixture run used as the 0.15 measurement baseline."""
        spec = scenario(scenario_id)
        harness = dict(harness or {})
        started = time.perf_counter()
        if scenario_id == "context_pressure":
            result = self._eval_context_pressure(harness)
        elif scenario_id == "requirement_mismatch":
            result = self._eval_requirement_mismatch(engine)
        elif scenario_id == "provider_interruption":
            result = {"success": True, "recovery": classify_execution_state(work_state="implementing", provider_interrupted=True), "failure_category": None}
            result["success"] = bool(result["recovery"]["can_resume"] and not result["recovery"]["terminal"])
        elif scenario_id == "resume_recovery":
            result = {"success": True, "recovery": classify_execution_state(work_state="code_review", context_exhausted=True), "failure_category": None}
            result["success"] = bool(result["recovery"]["can_resume"])
        elif scenario_id == "validation_failure":
            result = {"success": True, "validation_result": "failed", "completion_claimed": False, "failure_category": "validation"}
        elif scenario_id == "team_overlap":
            from .team_scheduler import detect_path_conflicts
            conflicts = detect_path_conflicts(["src/a.py", "src/shared.py"], ["src/b.py", "src/shared.py"])
            result = {
                "success": bool(conflicts),
                "conflicts": conflicts,
                "failure_category": None if conflicts else "multi_agent_overlap_missed",
                "note": "offline fixture; no live providers",
            }
        elif scenario_id in {"greenfield_implementation", "brownfield_feature", "bug_fix", "refactor", "mined_regression"}:
            result = {"success": True, "validation_result": "fixture", "failure_category": None, "note": "structural fixture only; no live provider"}
        else:
            result = {"success": False, "failure_category": "unknown_scenario"}
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        record = {
            "scenario": spec["id"],
            "scenario_version": spec["version"],
            "provider": harness.get("provider") or "offline",
            "model": harness.get("model"),
            "harness": harness,
            "success": bool(result.get("success")),
            "requirements_satisfied": result.get("requirements_satisfied"),
            "validation_result": result.get("validation_result"),
            "elapsed_ms": elapsed_ms,
            "tool_calls": int(result.get("tool_calls") or 0),
            "retries": 0,
            "human_interventions": 0,
            "failure_category": result.get("failure_category"),
            "metrics": result,
            "input_tokens": int((result.get("token_usage") or {}).get("input_tokens") or 0),
            "output_tokens": int((result.get("token_usage") or {}).get("output_tokens") or 0),
            "context_bytes": int(result.get("context_bytes") or 0),
            "context_bytes_omitted": int(result.get("context_bytes_omitted") or 0),
        }
        return self.record(record)

    def _eval_context_pressure(self, harness: dict[str, Any]) -> dict[str, Any]:
        blob = ("diff --git a/large.py b/large.py\n" + ("+print('x')\n" * 4000))
        handles_on = bool(harness.get("context_handles", harness_enabled("context_handles", True)))
        store = ContextHandleStore(self.root)
        payload = store.wrap_tool_result(
            "dynosai_git_diff",
            {"base_commit": "HEAD", "files": ["large.py"], "denied": [], "diff": blob, "truncated": False},
            inline_limit=4096,
            enabled=handles_on,
        )
        metrics = payload.get("context_handle_metrics") or {}
        omitted = int(metrics.get("bytes_omitted") or 0)
        inline = payload.get("diff") == blob
        success = (not handles_on and inline) or (handles_on and omitted > 0 and payload.get("diff") != blob)
        return {
            "success": success,
            "context_bytes": int(metrics.get("bytes_full") or len(blob)),
            "context_bytes_omitted": omitted if handles_on else 0,
            "handle_id": metrics.get("handle_id"),
            "failure_category": None if success else "context_not_compacted",
        }

    def _eval_requirement_mismatch(self, engine: Any) -> dict[str, Any]:
        if engine is None:
            return {"success": True, "requirements_satisfied": False, "failure_category": "requirement_mismatch", "note": "no engine; structural miss expected"}
        work = engine.db.one("SELECT id FROM work_items WHERE id!='PROJECT' ORDER BY created_at DESC LIMIT 1")
        if not work:
            return {"success": True, "requirements_satisfied": False, "failure_category": "requirement_mismatch"}
        report = evaluate_integrity(engine, work["id"])
        detected = (not report.get("requirements_satisfied")) or bool(report.get("blocking_gaps"))
        return {
            "success": detected,
            "requirements_satisfied": report.get("requirements_satisfied"),
            "integrity": report,
            "failure_category": None if detected else "integrity_missed_gap",
        }

    def skill_eval(self, *, provider: str = "codex", activity: str = "implementation") -> dict[str, Any]:
        """Compare WITH skill versus WITHOUT skill on prompt/context size only.

        Quality/success against live providers is not claimed from this fixture.
        """
        with tempfile.TemporaryDirectory(prefix="dynosai-skill-eval-") as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("demo\n", encoding="utf-8")
            without = AgentConfiguration(root)
            without.init(mode="minimal", force=True)
            off = without.resolve(provider=provider, activity=activity)
            with_skills = AgentConfiguration(root)
            with_skills.init(mode="recommended", force=True)
            on = with_skills.resolve(provider=provider, activity=activity)
        off_tokens = int((off.get("context_plan") or {}).get("always_loaded_estimated_tokens") or 0)
        on_meta = int((on.get("context_plan") or {}).get("always_loaded_estimated_tokens") or 0)
        on_body = int((on.get("context_plan") or {}).get("on_demand_skill_body_estimated_tokens") or 0)
        comparison = compare_records(
            {
                "scenario": "skill_toggle",
                "harness": {"skills": False},
                "success": True,
                "context_bytes": off_tokens,
                "context_bytes_omitted": 0,
            },
            {
                "scenario": "skill_toggle",
                "harness": {"skills": True},
                "success": True,
                "context_bytes": on_meta + on_body,
                "context_bytes_omitted": on_body,
            },
        )
        return {
            "scenario": "skill_toggle",
            "scenario_version": "1",
            "provider": provider,
            "activity": activity,
            "without_skill": {"mode": off.get("mode"), "skills": [s.get("id") for s in off.get("skills") or []], "context_plan": off.get("context_plan")},
            "with_skill": {"mode": on.get("mode"), "skills": [s.get("id") for s in on.get("skills") or []], "context_plan": on.get("context_plan")},
            "comparison": comparison,
            "claim": "Skill bodies stay on-demand. This fixture measures context size, not live success.",
        }
