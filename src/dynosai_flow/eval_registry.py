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
import sys
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
CATALOG_VERSION = 2
TIER_A = "A"
TIER_B = "B"


def _case(
    case_id: str,
    *,
    family: str,
    summary: str,
    tier: str,
    grader: str,
    provenance: str,
    created_from_real_failure: bool,
    expected_invariants: tuple[str, ...],
    broken: bool = False,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "version": "1",
        "family": family,
        "summary": summary,
        "tier": tier,
        "grader": grader,
        "grader_version": "1",
        "provenance": provenance,
        "created_from_real_failure": created_from_real_failure,
        "expected_invariants": list(expected_invariants),
        "broken": broken,
        "human_review_state": "open" if broken else "fixture",
    }


SCENARIOS: tuple[dict[str, Any], ...] = (
    _case("greenfield_implementation", family="greenfield", summary="New feature from an approved spec/plan.", tier=TIER_A, grader="structural", provenance="canonical MATRIX_1.0 greenfield cell", created_from_real_failure=False, expected_invariants=("spec_plan_required", "human_gates")),
    _case("brownfield_feature", family="brownfield", summary="Add a feature to an existing repository.", tier=TIER_A, grader="structural", provenance="canonical MATRIX_1.0 brownfield cell", created_from_real_failure=False, expected_invariants=("existing_code_preserved", "human_gates")),
    _case("brownfield_migration", family="brownfield", summary="Migrate existing behavior without inventing a greenfield rewrite.", tier=TIER_B, grader="structural", provenance="brownfield adoption failures in 0.14-0.16", created_from_real_failure=True, expected_invariants=("schema_v6", "no_silent_rewrite")),
    _case("bug_fix", family="bugfix", summary="Fix a failing behavior without expanding scope.", tier=TIER_B, grader="structural", provenance="scope-creep bugfix reviews", created_from_real_failure=True, expected_invariants=("scope_unchanged_except_fix",)),
    _case("refactor", family="refactor", summary="Restructure code while preserving behavior.", tier=TIER_B, grader="structural", provenance="refactor-as-feature attempts", created_from_real_failure=False, expected_invariants=("behavior_preserved",)),
    _case("partial_dirty_repo", family="brownfield", summary="Dirty or partial checkout must remain visible; work cannot pretend the tree is clean.", tier=TIER_B, grader="dirty_repo", provenance="dirty-repo bootstrap refusals", created_from_real_failure=True, expected_invariants=("git_status_visible", "no_hidden_clean")),
    _case("scope_violation", family="governance", summary="Writes outside the approved plan are denied.", tier=TIER_B, grader="scope_violation", provenance="PathPolicyEngine / Git diff vs plan mismatches", created_from_real_failure=True, expected_invariants=("unplanned_path_denied",)),
    _case("requested_scope_extension", family="governance", summary="Scope growth requires an explicit human-gated request.", tier=TIER_B, grader="scope_extension", provenance="dynosai_request_scope_extension contract", created_from_real_failure=True, expected_invariants=("no_silent_scope_growth",)),
    _case("validation_failure", family="validation", summary="Configured checks fail; completion must not be claimed.", tier=TIER_B, grader="validation_failure", provenance="false completion claims after red tests", created_from_real_failure=True, expected_invariants=("completion_not_claimed",)),
    _case("validation_dependency_missing", family="validation", summary="Missing planned test artifacts are a validation_precondition, not a model failure.", tier=TIER_B, grader="validation_precondition", provenance="validation_precondition MCP contract", created_from_real_failure=True, expected_invariants=("not_model_failure", "scope_extension_not_required")),
    _case("incorrect_completion_claim", family="integrity", summary="An agent saying done is not verification.", tier=TIER_B, grader="completion_claim", provenance="independent result verification", created_from_real_failure=True, expected_invariants=("core_verifies_git_and_evidence",)),
    _case("requirement_mismatch", family="integrity", summary="Implementation evidence does not cover the requirement.", tier=TIER_B, grader="requirement_mismatch", provenance="Validation Integrity gaps", created_from_real_failure=True, expected_invariants=("blocking_gaps_detected",)),
    _case("provider_interruption", family="recovery", summary="Provider process dies mid-turn; workflow state must survive.", tier=TIER_B, grader="provider_interruption", provenance="provider restart during implementation", created_from_real_failure=True, expected_invariants=("can_resume", "not_terminal")),
    _case("resume_recovery", family="recovery", summary="Resume from durable DynosAI state after restart.", tier=TIER_B, grader="resume_recovery", provenance="session recovery after provider restart", created_from_real_failure=True, expected_invariants=("can_resume",)),
    _case("timeout", family="recovery", summary="A timed-out runtime is reported as timeout, not success.", tier=TIER_B, grader="timeout", provenance="LocalExecutionRuntime timeout evidence", created_from_real_failure=True, expected_invariants=("timeout_is_not_success",)),
    _case("git_divergence", family="git", summary="Git remains source authority when workflow state and the worktree diverge.", tier=TIER_B, grader="git_divergence", provenance="Git vs knowledge.db authority split", created_from_real_failure=True, expected_invariants=("git_is_source_authority",)),
    _case("lease_overlap", family="multi_agent", summary="Overlapping leases on the same files cannot run in one wave.", tier=TIER_B, grader="lease_overlap", provenance="0.16 lease claim denials", created_from_real_failure=True, expected_invariants=("overlap_serialized",)),
    _case("fan_in_conflict", family="multi_agent", summary="Fan-in reports overlapping files as a conflict.", tier=TIER_B, grader="team_overlap", provenance="wave-scoped fan-in", created_from_real_failure=True, expected_invariants=("conflict_detected",)),
    _case("disjoint_parallel_work", family="multi_agent", summary="File-disjoint leases may be claimed by two host sessions.", tier=TIER_B, grader="disjoint_parallel", provenance="RC2 two-session lease proof", created_from_real_failure=False, expected_invariants=("disjoint_claimable",)),
    _case("team_overlap", family="multi_agent", summary="Overlapping worker scopes must fail fan-in without live providers.", tier=TIER_B, grader="team_overlap", provenance="offline team_overlap fixture", created_from_real_failure=True, expected_invariants=("conflict_detected",)),
    _case("rejected_human_gate", family="gates", summary="A rejected spec/plan/code/merge gate does not auto-advance.", tier=TIER_B, grader="rejected_gate", provenance="human gate refusals", created_from_real_failure=True, expected_invariants=("gate_required", "no_self_approve")),
    _case("requested_changes", family="gates", summary="Requested changes keep the work in review rather than completing it.", tier=TIER_B, grader="requested_changes", provenance="code review requested_changes decisions", created_from_real_failure=True, expected_invariants=("not_complete",)),
    _case("secret_path_attempt", family="security", summary="Sensitive paths such as .env are denied to agents.", tier=TIER_B, grader="secret_path", provenance="threat-model path/secret enforcement", created_from_real_failure=True, expected_invariants=("sensitive_denied",)),
    _case("symlink_path_escape", family="security", summary="Symlinks that resolve outside the project root are denied.", tier=TIER_B, grader="symlink_escape", provenance="PathPolicyEngine symlink escape", created_from_real_failure=True, expected_invariants=("escape_denied",)),
    _case("malformed_provider_response", family="provider", summary="Malformed JSON-RPC does not become a successful tool result.", tier=TIER_B, grader="malformed_provider", provenance="provider transport parse failures", created_from_real_failure=True, expected_invariants=("parse_failure_is_error",)),
    _case("cost_attribution", family="cost", summary="Governed-change cost stays raw usage; success is not inferred from spend.", tier=TIER_B, grader="cost_attribution", provenance="governed_change_cost aggregates", created_from_real_failure=False, expected_invariants=("raw_usage", "not_a_quality_score")),
    _case("context_pressure", family="context", summary="Large artifacts should be referenced, not replayed.", tier=TIER_B, grader="context_pressure", provenance="context handles 0.15", created_from_real_failure=True, expected_invariants=("bytes_omitted_when_enabled",)),
    _case("context_handle_recovery", family="context", summary="Persisted handles remain readable after a restart.", tier=TIER_B, grader="handle_recovery", provenance="dynosai_retrieve_handle after compacting", created_from_real_failure=True, expected_invariants=("handle_readable",)),
    _case("mined_regression", family="intelligence", summary="Placeholder target for a mined local-trace regression case.", tier=TIER_B, grader="structural", provenance="eval intelligence mining", created_from_real_failure=True, expected_invariants=("inbox_only",)),
)


def scenario(scenario_id: str) -> dict[str, Any]:
    for item in SCENARIOS:
        if item["id"] == scenario_id:
            return dict(item)
    raise ValueError(f"unknown eval scenario: {scenario_id}")


def catalog_summary() -> dict[str, Any]:
    items = [dict(item) for item in SCENARIOS]
    return {
        "catalog_version": CATALOG_VERSION,
        "registry_version": REGISTRY_VERSION,
        "count": len(items),
        "tier_a": [item["id"] for item in items if item.get("tier") == TIER_A],
        "tier_b": [item["id"] for item in items if item.get("tier") == TIER_B],
        "broken": [item["id"] for item in items if item.get("broken")],
        "created_from_real_failure": [item["id"] for item in items if item.get("created_from_real_failure")],
    }


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
        extra = 1
        while path.exists():
            path = self.base / f"{row['scenario']}-{stamp}-{extra}.json"
            extra += 1
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return {**row, "path": str(path)}

    def run_offline(self, scenario_id: str, *, harness: dict[str, Any] | None = None, engine: Any | None = None) -> dict[str, Any]:
        """Deterministic fixture run used as the 0.15 measurement baseline."""
        spec = scenario(scenario_id)
        harness = dict(harness or {})
        started = time.perf_counter()
        grader = str(spec.get("grader") or "")
        if grader == "context_pressure":
            result = self._eval_context_pressure(harness)
        elif grader == "requirement_mismatch":
            result = self._eval_requirement_mismatch(engine)
        elif grader == "provider_interruption":
            result = {"success": True, "recovery": classify_execution_state(work_state="implementing", provider_interrupted=True), "failure_category": None}
            result["success"] = bool(result["recovery"]["can_resume"] and not result["recovery"]["terminal"])
        elif grader == "resume_recovery":
            result = {"success": True, "recovery": classify_execution_state(work_state="code_review", context_exhausted=True), "failure_category": None}
            result["success"] = bool(result["recovery"]["can_resume"])
        elif grader == "validation_failure":
            result = {"success": True, "validation_result": "failed", "completion_claimed": False, "failure_category": "validation"}
        elif grader in {"team_overlap", "fan_in_conflict", "lease_overlap"}:
            from .team_scheduler import detect_path_conflicts
            conflicts = detect_path_conflicts(["src/a.py", "src/shared.py"], ["src/b.py", "src/shared.py"])
            result = {
                "success": bool(conflicts),
                "conflicts": conflicts,
                "failure_category": None if conflicts else "multi_agent_overlap_missed",
                "note": "offline fixture; no live providers",
            }
        elif grader == "disjoint_parallel":
            from .team_scheduler import detect_path_conflicts
            conflicts = detect_path_conflicts(["src/alpha.py"], ["src/beta.py"])
            result = {"success": not bool(conflicts), "conflicts": conflicts, "failure_category": None if not conflicts else "false_overlap"}
        elif grader == "scope_violation":
            result = self._eval_scope_violation()
        elif grader == "secret_path":
            result = self._eval_secret_path()
        elif grader == "symlink_escape":
            result = self._eval_symlink_escape()
        elif grader == "timeout":
            result = self._eval_timeout()
        elif grader == "handle_recovery":
            result = self._eval_handle_recovery()
        elif grader == "malformed_provider":
            result = self._eval_malformed_provider()
        elif grader == "cost_attribution":
            result = self._eval_cost_attribution()
        elif grader in {"structural", "dirty_repo", "scope_extension", "validation_precondition", "completion_claim", "git_divergence", "rejected_gate", "requested_changes"}:
            result = {
                "success": True,
                "validation_result": "fixture",
                "failure_category": None,
                "note": "structural fixture only; no live provider",
                "invariants": spec.get("expected_invariants"),
            }
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
            "tier": spec.get("tier"),
            "grader": spec.get("grader"),
            "grader_version": spec.get("grader_version"),
            "provenance": spec.get("provenance"),
            "created_from_real_failure": spec.get("created_from_real_failure"),
            "expected_invariants": spec.get("expected_invariants"),
            "broken": spec.get("broken"),
            "human_review_state": spec.get("human_review_state"),
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

    def _eval_scope_violation(self) -> dict[str, Any]:
        from .policy import PathPolicyEngine
        root = self.root / "eval-scope"
        root.mkdir(parents=True, exist_ok=True)
        (root / "src").mkdir(exist_ok=True)
        denied = PathPolicyEngine(root).decision(root.parent / "outside.py", "write")
        return {"success": (not denied.allowed) and "escapes" in str(denied.reason), "reason": denied.reason}

    def _eval_secret_path(self) -> dict[str, Any]:
        from .policy import PathPolicyEngine
        root = self.root / "eval-secret"
        root.mkdir(parents=True, exist_ok=True)
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        denied = PathPolicyEngine(root).decision(".env", "read")
        return {"success": (not denied.allowed) and "sensitive" in str(denied.reason), "reason": denied.reason}

    def _eval_symlink_escape(self) -> dict[str, Any]:
        from .policy import PathPolicyEngine
        root = self.root / "eval-symlink"
        root.mkdir(parents=True, exist_ok=True)
        outside = self.root / "eval-symlink-secret.txt"
        outside.write_text("token\n", encoding="utf-8")
        link = root / "inside.txt"
        try:
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(outside)
        except OSError as exc:
            return {"success": True, "skipped": True, "note": f"symlinks unavailable: {exc}"}
        denied = PathPolicyEngine(root).decision("inside.txt", "read")
        return {"success": not denied.allowed, "reason": denied.reason}

    def _eval_timeout(self) -> dict[str, Any]:
        from .execution_runtime import LocalExecutionRuntime
        runtime = LocalExecutionRuntime(self.root)
        result = runtime.run([sys.executable, "-c", "import time; time.sleep(5)"], cwd=self.root, timeout=1)
        return {"success": bool(result.timed_out) and int(result.returncode) != 0, "timed_out": result.timed_out, "returncode": result.returncode}

    def _eval_handle_recovery(self) -> dict[str, Any]:
        store = ContextHandleStore(self.root)
        meta = store.put("log", "eval-recovery", "abcdefghijklmnopqrstuvwxyz")
        restored = ContextHandleStore(self.root)
        payload = restored.retrieve(meta["id"], offset=0, limit=8)
        return {"success": payload.get("content") == "abcdefgh" and bool(payload.get("truncated")), "handle_id": meta.get("id")}

    def _eval_malformed_provider(self) -> dict[str, Any]:
        try:
            json.loads("{not json")
            parsed = True
        except json.JSONDecodeError:
            parsed = False
        return {"success": parsed is False, "parse_failure_is_error": parsed is False}

    def _eval_cost_attribution(self) -> dict[str, Any]:
        from .cost_telemetry import empty_governed_change_aggregate
        aggregate = empty_governed_change_aggregate()
        note = str(aggregate.get("note") or "")
        success = aggregate.get("metric") == "aggregate_governed_change_cost" and "not waste" in note.lower()
        return {"success": success, "metric": aggregate.get("metric"), "note": note}

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
