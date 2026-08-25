# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Shadow predictive routing memory and conservative historical recommendations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .util import utc_now

TIER_ORDER = ("economy", "standard", "strong")


def _atomic(path: Path, value: Any) -> None:
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


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "series": {}}
        if not isinstance(value, dict):
            value = {"schema_version": 1, "series": {}}
    except Exception:
        value = {"schema_version": 1, "series": {}}
    value.setdefault("series", {})
    value["schema_version"] = 1
    return value


def _tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(str(tier))
    except ValueError:
        return 0


class PredictiveRoutingMemory:
    """Small transparent outcome memory for model-tier decisions.

    This is intentionally not an opaque ML model. It learns completed model
    outcomes by provider/activity/tier, applies a Beta prior to avoid overfitting
    tiny samples, and only changes the rules-based tier when historical evidence
    is sufficiently strong. Context pressure is not an input: 0.11.x established
    that oversized context must be fixed through compaction, not by buying a more
    expensive model.
    """

    MIN_SAMPLES = 4
    HISTORY_LIMIT = 100

    def __init__(self, project_root: str | Path):
        self.root = Path(project_root).expanduser().resolve()
        self.path = self.root / ".dynosai" / "runtime" / "predictive-routing.json"

    @staticmethod
    def _key(provider: str, activity: str, tier: str) -> str:
        return f"{provider.lower()}|{activity.lower()}|{tier.lower()}"

    def record(
        self,
        provider: str,
        activity: str | None,
        tier: str,
        *,
        success: bool,
        counts_as_model_failure: bool,
        failure_kind: str | None = None,
        eligible_for_learning: bool = True,
        work_id: str | None = None,
        failure_fingerprint: str | None = None,
    ) -> dict[str, Any] | None:
        if not activity or tier not in TIER_ORDER:
            return None
        data = _load(self.path)
        key = self._key(provider, activity, tier)
        row = data["series"].setdefault(
            key,
            {
                "provider": provider,
                "activity": activity,
                "tier": tier,
                "attempts": 0,
                "successes": 0,
                "model_failures": 0,
                "non_model_failures": 0,
                "excluded_observations": 0,
                "failure_kinds": {},
                "recent": [],
                "observation_keys": [],
            },
        )
        if work_id:
            suffix = "success" if success else (failure_fingerprint or str(failure_kind or "failure"))
            observation_key = f"{work_id}|{activity}|{tier}|{suffix}"
            keys = list(row.get("observation_keys") or [])
            if observation_key in keys:
                result = self.stats(provider, activity, tier)
                result["deduplicated"] = True
                result["eligible_for_learning"] = bool(eligible_for_learning)
                return result
            keys.append(observation_key)
            row["observation_keys"] = keys[-self.HISTORY_LIMIT :]
        if not eligible_for_learning:
            row["excluded_observations"] = int(row.get("excluded_observations") or 0) + 1
            row.setdefault("recent", []).append({"at": utc_now(), "success": bool(success), "model_failure": bool(counts_as_model_failure), "eligible_for_learning": False, "failure_kind": failure_kind})
            row["recent"] = row["recent"][-self.HISTORY_LIMIT :]
            row["updated_at"] = utc_now()
            data["updated_at"] = utc_now()
            _atomic(self.path, data)
            result = self.stats(provider, activity, tier)
            result["deduplicated"] = False
            result["eligible_for_learning"] = False
            return result
        if success:
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["successes"] = int(row.get("successes") or 0) + 1
        elif counts_as_model_failure:
            row["attempts"] = int(row.get("attempts") or 0) + 1
            row["model_failures"] = int(row.get("model_failures") or 0) + 1
            kinds = row.setdefault("failure_kinds", {})
            kind = str(failure_kind or "unknown")
            kinds[kind] = int(kinds.get(kind) or 0) + 1
        else:
            row["non_model_failures"] = int(row.get("non_model_failures") or 0) + 1
        row.setdefault("recent", []).append({"at": utc_now(), "success": bool(success), "model_failure": bool(counts_as_model_failure), "eligible_for_learning": True, "failure_kind": failure_kind})
        row["recent"] = row["recent"][-self.HISTORY_LIMIT :]
        row["updated_at"] = utc_now()
        data["updated_at"] = utc_now()
        _atomic(self.path, data)
        result = self.stats(provider, activity, tier)
        result["deduplicated"] = False
        return result

    def stats(self, provider: str, activity: str, tier: str) -> dict[str, Any]:
        data = _load(self.path)
        row = dict((data.get("series") or {}).get(self._key(provider, activity, tier)) or {})
        attempts = int(row.get("attempts") or 0)
        successes = int(row.get("successes") or 0)
        failures = int(row.get("model_failures") or 0)
        # Beta(2,1) prior: optimistic enough to keep economy as the default, but
        # repeated real failures quickly reduce predicted success.
        predicted_success = (successes + 2.0) / (attempts + 3.0)
        confidence = min(1.0, attempts / 8.0)
        return {
            "provider": provider,
            "activity": activity,
            "tier": tier,
            "samples": attempts,
            "successes": successes,
            "model_failures": failures,
            "non_model_failures": int(row.get("non_model_failures") or 0),
            "excluded_observations": int(row.get("excluded_observations") or 0),
            "observed_success_rate": round(successes / attempts, 4) if attempts else None,
            "predicted_success_probability": round(predicted_success, 4),
            "confidence": round(confidence, 4),
            "failure_kinds": dict(row.get("failure_kinds") or {}),
            "updated_at": row.get("updated_at"),
        }

    def assess(self, provider: str, activity: str, rules_tier: str, complexity_level: str) -> dict[str, Any]:
        current = rules_tier if rules_tier in TIER_ORDER else "economy"
        current_stats = self.stats(provider, activity, current)
        recommendation = current
        action = "keep_rules_tier"
        reason = "insufficient_predictive_evidence"

        # Proven cheap-tier history can override a complexity-only standard tier
        # even when the expensive tier itself has no samples yet. The caller may
        # still veto this when current-work failure memory requires a higher tier.
        economy = self.stats(provider, activity, "economy")
        if current != "economy" and complexity_level != "complex" and economy["samples"] >= 5 and economy["confidence"] >= 0.5 and float(economy["predicted_success_probability"]) >= 0.82:
            recommendation = "economy"
            action = "predictive_cost_downshift"
            reason = "economy_history_high_success"
        elif current_stats["samples"] >= self.MIN_SAMPLES and current_stats["confidence"] >= 0.5:
            p = float(current_stats["predicted_success_probability"])
            idx = _tier_index(current)
            # Escalate only on repeated capability evidence. A simple task gets
            # an even stricter threshold so a few noisy failures do not spend more.
            failure_threshold = 0.42 if complexity_level == "simple" else 0.55
            if p < failure_threshold and idx < len(TIER_ORDER) - 1:
                next_tier = TIER_ORDER[idx + 1]
                next_stats = self.stats(provider, activity, next_tier)
                # If the expensive tier has history, require it to look better;
                # otherwise allow a cautious one-step experiment after enough
                # failures at the current tier.
                if next_stats["samples"] == 0 or float(next_stats["predicted_success_probability"]) >= p + 0.08:
                    recommendation = next_tier
                    action = "predictive_capability_escalation"
                    reason = "historical_tier_success_below_threshold"
            else:
                action = "predictive_keep"
                reason = "historical_tier_success_acceptable"

        all_tiers = {tier: self.stats(provider, activity, tier) for tier in TIER_ORDER}
        return {
            "schema_version": 1,
            "rules_tier": current,
            "recommended_tier": recommendation,
            "action": action,
            "reason": reason,
            "evidence": current_stats,
            "tiers": all_tiers,
            "prediction_used": recommendation != current,
            "safety": "context_pressure_excluded_from_predictive_capability_routing",
        }

    def snapshot(self) -> dict[str, Any]:
        return _load(self.path)
