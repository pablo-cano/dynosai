# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Offline replay and authority-gate evaluation for predictive routing."""

from __future__ import annotations

import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .model_control import PREDICTIVE_LEARNING_EXCLUDED_FAILURES
from .predictive_routing import PredictiveRoutingMemory, TIER_ORDER


@dataclass(slots=True)
class HistoricalObservation:
    at: str | None
    source: str
    dynosai_version: str | None
    provider: str
    scenario: str
    activity: str
    rules_tier: str
    outcome_tier: str
    complexity_level: str
    success: bool
    counts_as_model_failure: bool
    failure_kind: str | None
    eligible_for_learning: bool
    eligibility_source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "source": self.source,
            "dynosai_version": self.dynosai_version,
            "provider": self.provider,
            "scenario": self.scenario,
            "activity": self.activity,
            "rules_tier": self.rules_tier,
            "outcome_tier": self.outcome_tier,
            "complexity_level": self.complexity_level,
            "success": self.success,
            "counts_as_model_failure": self.counts_as_model_failure,
            "failure_kind": self.failure_kind,
            "eligible_for_learning": self.eligible_for_learning,
            "eligibility_source": self.eligibility_source,
        }


def _summary(z: zipfile.ZipFile) -> dict[str, Any]:
    for name in ("suite/summary-final.json", "summary-final.json"):
        try:
            return json.loads(z.read(name).decode("utf-8"))
        except KeyError:
            continue
    return {}


def _scenario_from_path(name: str, fallback: str = "unknown") -> str:
    parts = Path(name).parts
    # <provider>/<scenario>/project/.dynosai/runtime/model-control.jsonl
    if len(parts) >= 2:
        return str(parts[1])
    return fallback


def _provider_from_path(name: str, fallback: str = "unknown") -> str:
    parts = Path(name).parts
    return str(parts[0]) if parts else fallback


def _eligibility(event: dict[str, Any]) -> tuple[bool, str, bool]:
    success = bool(event.get("success"))
    kind = None if success else str(event.get("failure_kind") or "unknown")
    # Success is a hard invariant in the controller. Legacy telemetry may
    # accidentally persisted success=true with counts_as_model_failure=true;
    # replay repairs that old telemetry rather than learning a false failure.
    counted = False if success else bool(event.get("counts_as_model_failure", True))
    explicit = event.get("eligible_for_learning")
    if isinstance(explicit, bool):
        eligible = bool(explicit and (success or (counted and kind not in PREDICTIVE_LEARNING_EXCLUDED_FAILURES)))
        return eligible, "explicit_current_policy", counted
    if success:
        return True, "legacy_success_repaired_by_success_invariant", False
    # Legacy failures that predate explicit learning eligibility are unsafe to
    # train on. Legacy validation preconditions can look like
    # a test/model failure. Keep them for audit, but exclude from learning.
    return False, "legacy_failure_without_explicit_learning_eligibility", counted


def observations_from_bundle(path: str | Path) -> list[HistoricalObservation]:
    p = Path(path).expanduser().resolve()
    observations: list[HistoricalObservation] = []
    with zipfile.ZipFile(p) as z:
        summary = _summary(z)
        version = summary.get("dynosai_version")
        fallback_scenario = str(summary.get("scenario") or "unknown")
        fallback_provider = str((summary.get("providers") or [summary.get("provider") or "unknown"])[0])
        names = [n for n in z.namelist() if n.endswith("project/.dynosai/runtime/model-control.jsonl")]
        for name in names:
            provider = _provider_from_path(name, fallback_provider)
            scenario = _scenario_from_path(name, fallback_scenario)
            latest_route: dict[tuple[str, str], dict[str, Any]] = {}
            for raw in z.read(name).decode("utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                if event.get("event") == "model_route_decision":
                    activity = str(event.get("activity") or "implementation")
                    latest_route[(provider, activity)] = event
                    continue
                if event.get("event") != "model_outcome":
                    continue
                activity = str(event.get("activity") or "implementation")
                route = latest_route.get((provider, activity), {})
                tier = str(event.get("outcome_tier") or event.get("tier") or route.get("tier") or "economy")
                if tier not in TIER_ORDER:
                    tier = "economy"
                rules_tier = str(((route.get("prediction") or {}).get("rules_tier")) or route.get("tier") or tier)
                if rules_tier not in TIER_ORDER:
                    rules_tier = tier
                complexity = str(((route.get("complexity") or {}).get("level")) or "simple")
                eligible, source, counted = _eligibility(event)
                success = bool(event.get("success"))
                kind = None if success else str(event.get("failure_kind") or "unknown")
                observations.append(HistoricalObservation(
                    at=event.get("at"), source=str(p), dynosai_version=str(version) if version else None,
                    provider=provider, scenario=scenario, activity=activity,
                    rules_tier=rules_tier, outcome_tier=tier, complexity_level=complexity,
                    success=success, counts_as_model_failure=counted, failure_kind=kind,
                    eligible_for_learning=eligible, eligibility_source=source,
                ))
    return observations


def _tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return 0


class PredictiveRouterValidator:
    """Chronological, offline validation of the current transparent router.

    The validator never launches a model and never mutates the user's project.
    It replays historical model outcomes in timestamp order, asks the current
    predictive policy what it *would* have recommended before each observation,
    then records the real outcome. Counterfactual tier outcomes are deliberately
    not invented: downshifts to an unobserved tier are reported as unknown.
    """

    AUTHORITY_MIN_SAMPLES = 20
    AUTHORITY_MIN_PROVIDERS = 2
    AUTHORITY_MIN_SCENARIOS = 2
    AUTHORITY_MIN_MODEL_FAILURES = 3
    MAX_FALSE_ESCALATION_RATE = 0.10

    @classmethod
    def validate(cls, paths: Iterable[str | Path]) -> dict[str, Any]:
        observations: list[HistoricalObservation] = []
        errors: list[dict[str, str]] = []
        for raw in paths:
            try:
                observations.extend(observations_from_bundle(raw))
            except Exception as exc:
                errors.append({"source": str(raw), "error": f"{type(exc).__name__}: {exc}"})
        observations.sort(key=lambda x: (str(x.at or ""), x.source, x.activity))

        replay_rows: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="dynosai-predictive-replay-") as tmp:
            memory = PredictiveRoutingMemory(tmp)
            false_escalations = 0
            true_escalation_signals = 0
            missed_escalations = 0
            escalation_recommendations = 0
            downshift_recommendations = 0
            unknown_downshifts = 0
            tier_steps_delta = 0
            eligible_count = 0
            model_failure_count = 0
            excluded_count = 0
            for obs in observations:
                assessment = memory.assess(obs.provider, obs.activity, obs.rules_tier, obs.complexity_level)
                recommended = str(assessment.get("recommended_tier") or obs.rules_tier)
                action = str(assessment.get("action") or "keep_rules_tier")
                used_prediction = bool(assessment.get("prediction_used"))
                if used_prediction:
                    tier_steps_delta += _tier_index(recommended) - _tier_index(obs.rules_tier)
                if action == "predictive_capability_escalation":
                    escalation_recommendations += 1
                    if obs.success:
                        false_escalations += 1
                    elif obs.counts_as_model_failure and obs.eligible_for_learning:
                        true_escalation_signals += 1
                if action == "predictive_cost_downshift":
                    downshift_recommendations += 1
                    if recommended != obs.outcome_tier:
                        unknown_downshifts += 1
                if obs.eligible_for_learning:
                    eligible_count += 1
                    if obs.counts_as_model_failure and not obs.success:
                        model_failure_count += 1
                        if action != "predictive_capability_escalation":
                            missed_escalations += 1
                else:
                    excluded_count += 1
                replay_rows.append({
                    **obs.to_dict(),
                    "recommendation_before_outcome": {
                        "recommended_tier": recommended,
                        "action": action,
                        "reason": assessment.get("reason"),
                        "prediction_used": used_prediction,
                        "evidence": assessment.get("evidence"),
                    },
                })
                memory.record(
                    obs.provider, obs.activity, obs.outcome_tier,
                    success=obs.success,
                    counts_as_model_failure=obs.counts_as_model_failure,
                    failure_kind=obs.failure_kind,
                    eligible_for_learning=obs.eligible_for_learning,
                    work_id=f"replay-{len(replay_rows)}",
                    failure_fingerprint=f"historical-{len(replay_rows)}" if not obs.success else None,
                )
            final_memory = memory.snapshot()

        providers = sorted({x.provider for x in observations if x.eligible_for_learning})
        scenarios = sorted({x.scenario for x in observations if x.eligible_for_learning})
        false_rate = (false_escalations / escalation_recommendations) if escalation_recommendations else 0.0
        learned_attempts = sum(int(row.get("attempts") or 0) for row in (final_memory.get("series") or {}).values())
        expected_learned_attempts = sum(1 for x in observations if x.eligible_for_learning and (x.success or x.counts_as_model_failure))
        gates = {
            "minimum_eligible_samples": eligible_count >= cls.AUTHORITY_MIN_SAMPLES,
            "provider_coverage": len(providers) >= cls.AUTHORITY_MIN_PROVIDERS,
            "scenario_coverage": len(scenarios) >= cls.AUTHORITY_MIN_SCENARIOS,
            "failure_evidence": model_failure_count >= cls.AUTHORITY_MIN_MODEL_FAILURES,
            "false_escalation_rate": false_rate <= cls.MAX_FALSE_ESCALATION_RATE,
            "unsafe_observations_learned": learned_attempts == expected_learned_attempts,
        }
        authority_ready = all(gates.values())
        return {
            "schema_version": 1,
            "mode": "offline_shadow_validation",
            "model_runs_executed": 0,
            "sources": [str(Path(x).expanduser().resolve()) for x in paths],
            "source_errors": errors,
            "observations": len(observations),
            "eligible_samples": eligible_count,
            "excluded_observations": excluded_count,
            "providers": providers,
            "scenarios": scenarios,
            "model_failures": model_failure_count,
            "metrics": {
                "escalation_recommendations": escalation_recommendations,
                "true_escalation_signals": true_escalation_signals,
                "false_escalations": false_escalations,
                "false_escalation_rate": round(false_rate, 4),
                "missed_escalations": missed_escalations,
                "downshift_recommendations": downshift_recommendations,
                "counterfactual_unknown_downshifts": unknown_downshifts,
                "tier_steps_delta": tier_steps_delta,
                "cost_simulation": "tier-direction-only; no invented cross-tier dollar prices or counterfactual outcomes",
            },
            "learning_audit": {"learned_attempts": learned_attempts, "expected_learned_attempts": expected_learned_attempts},
            "authority_gates": gates,
            "authority_ready": authority_ready,
            "recommended_runtime_mode": "adaptive" if authority_ready else "shadow",
            "decision": "authority_candidate" if authority_ready else "keep_predictive_router_in_shadow_mode",
            "replay": replay_rows,
            "final_memory": final_memory,
            "notes": [
                "Historical success=true always overrides stale counts_as_model_failure metadata.",
                "Legacy failures without explicit eligible_for_learning are audited but excluded from training.",
                "Counterfactual lower-tier outcomes are never fabricated.",
                "Context pressure and scope/governance are not predictive capability signals.",
            ],
        }
