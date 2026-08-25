# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Calibration of token and context estimators from completed provider telemetry."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import tempfile
from pathlib import Path
from typing import Any

from .util import utc_now


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
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 2, "series": {}}
        if not isinstance(value, dict):
            return {"schema_version": 2, "series": {}}
        value.setdefault("series", {})
        value["schema_version"] = 2
        return value
    except Exception:
        return {"schema_version": 2, "series": {}}


def _ratio(exact: int | None, estimated: int | None) -> float | None:
    """Return an inspectable exact/estimate ratio without the old 50x ceiling.

    Real provider traces can differ by hundreds of times from visible-stream token
    estimates (especially Cursor/Codex reasoning and cached context). 0.11.x
    clipped these ratios at 50, making convergence impossible. We still bound a
    single pathological sample, but at a deliberately wide 2048x range.
    """
    try:
        e = int(exact or 0)
        q = int(estimated or 0)
    except Exception:
        return None
    if e <= 0 or q <= 0:
        return None
    return max(0.05, min(2048.0, e / q))


def _median_abs_deviation(values: list[float]) -> float:
    if not values:
        return 0.0
    med = statistics.median(values)
    return float(statistics.median([abs(x - med) for x in values]))


def _factor_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"factor": 1.0, "mad": 0.0, "relative_mad": 0.0}
    factor = float(statistics.median(values))
    mad = _median_abs_deviation(values)
    return {
        "factor": round(factor, 6),
        "mad": round(mad, 6),
        "relative_mad": round(mad / max(0.000001, abs(factor)), 6),
    }


def _sample_signature(provider: str, model: str | None, scenario: str | None, estimated: dict[str, Any], exact: dict[str, Any]) -> str:
    payload = {
        "provider": provider,
        "model": model,
        "scenario": scenario,
        "estimated_context": int(estimated.get("context_read_tokens") or estimated.get("observed_context_tokens_estimate") or 0),
        "estimated_generated": int(estimated.get("generated_tokens") or estimated.get("output_tokens") or 0),
        "exact_context": int(exact.get("context_read_tokens") or 0),
        "exact_generated": int(exact.get("generated_tokens") or exact.get("output_tokens") or 0),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


class UsageEstimatorCalibration:
    """Robust calibration from final provider truth vs visible-stream estimates.

    0.12.1 deliberately keeps this transparent rather than introducing an opaque
    model. Each completed provider process contributes at most one sample. The
    rolling median is resilient to outliers, the ratio range can now represent
    real cached-context gaps, and confidence/dispersion are exposed so consumers
    can decide whether a prediction is trustworthy.
    """

    HISTORY_LIMIT = 50

    def __init__(self, path: str | Path | None = None):
        raw = path or os.environ.get("DYNOSAI_TOKEN_CALIBRATION_PATH")
        self.path = Path(raw).expanduser().resolve() if raw else Path.home() / ".dynosai" / "token-estimator-calibration.json"

    @staticmethod
    def _key(provider: str, model: str | None, scenario: str | None = None) -> str:
        return "|".join([str(provider or "unknown").lower(), str(model or "unknown").lower(), str(scenario or "*").lower()])

    @staticmethod
    def _confidence(samples: int, relative_mad: float) -> float:
        # Five clean completed runs are enough for high confidence; dispersion
        # reduces confidence instead of silently pretending the series is stable.
        sample_conf = min(1.0, max(0.0, samples / 5.0))
        dispersion_penalty = 1.0 / (1.0 + max(0.0, relative_mad))
        return round(sample_conf * dispersion_penalty, 4)

    def factors(self, provider: str, model: str | None, scenario: str | None = None) -> dict[str, Any]:
        data = _load(self.path)
        keys = [self._key(provider, model, scenario), self._key(provider, model, None)]
        row = None
        used = None
        for key in keys:
            candidate = (data.get("series") or {}).get(key)
            if candidate and candidate.get("samples"):
                row = candidate
                used = key
                break
        if not row:
            return {
                "available": False,
                "samples": 0,
                "context_factor": 1.0,
                "generated_factor": 1.0,
                "confidence": 0.0,
                "reliability": "untrained",
                "context_relative_mad": 0.0,
                "generated_relative_mad": 0.0,
                "key": None,
            }
        samples = int(row.get("samples") or 0)
        context_relative_mad = float(row.get("context_relative_mad") or 0.0)
        generated_relative_mad = float(row.get("generated_relative_mad") or 0.0)
        confidence = self._confidence(samples, max(context_relative_mad, generated_relative_mad))
        reliability = "high" if confidence >= 0.75 else "medium" if confidence >= 0.4 else "low"
        return {
            "available": True,
            "samples": samples,
            "context_factor": float(row.get("context_factor") or 1.0),
            "generated_factor": float(row.get("generated_factor") or 1.0),
            "confidence": confidence,
            "reliability": reliability,
            "context_relative_mad": context_relative_mad,
            "generated_relative_mad": generated_relative_mad,
            "key": used,
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def apply_factors(estimate: dict[str, Any], factors: dict[str, Any]) -> dict[str, Any]:
        """Apply a caller-supplied calibration snapshot.

        TokenUsageRecorder freezes this snapshot at process start so the run is
        evaluated only with information available *before* that run. Newly learned
        final factors are reserved for the next process, eliminating post-hoc
        calibration leakage.
        """
        context = int(estimate.get("context_read_tokens") or estimate.get("observed_context_tokens_estimate") or 0)
        generated = int(estimate.get("generated_tokens") or estimate.get("output_tokens") or 0)
        if not factors["available"]:
            return {
                "quality": "raw_estimate",
                "context_read_tokens": context,
                "generated_tokens": generated,
                "processed_tokens": context + generated,
                "calibration": factors,
            }
        c = max(0, int(round(context * factors["context_factor"])))
        g = max(0, int(round(generated * factors["generated_factor"])))
        return {
            "quality": "calibrated_estimate",
            "context_read_tokens": c,
            "generated_tokens": g,
            "processed_tokens": c + g,
            "calibration": factors,
            "prediction_confidence": factors["confidence"],
        }

    def apply(self, provider: str, model: str | None, estimate: dict[str, Any], scenario: str | None = None) -> dict[str, Any]:
        return self.apply_factors(estimate, self.factors(provider, model, scenario))

    def update(self, provider: str, model: str | None, estimated: dict[str, Any], exact: dict[str, Any], scenario: str | None = None) -> dict[str, Any]:
        est_context = int(estimated.get("context_read_tokens") or estimated.get("observed_context_tokens_estimate") or 0)
        est_generated = int(estimated.get("generated_tokens") or estimated.get("output_tokens") or 0)
        exact_context = int(exact.get("context_read_tokens") or 0)
        exact_generated = int(exact.get("generated_tokens") or exact.get("output_tokens") or 0)
        cr = _ratio(exact_context, est_context)
        gr = _ratio(exact_generated, est_generated)
        signature = _sample_signature(provider, model, scenario, estimated, exact)
        data = _load(self.path)
        series = data.setdefault("series", {})
        keys = [self._key(provider, model, scenario), self._key(provider, model, None)]
        updated_any = False
        for key in keys:
            row = series.setdefault(key, {"context_ratios": [], "generated_ratios": [], "sample_signatures": [], "samples": 0})
            # Migrate legacy series whose exact/estimate ratios were clipped at
            # 50x. Those samples cannot be statistically repaired after the fact
            # and would pin the rolling median to the obsolete ceiling, so the
            # first compatible final sample starts a clean v2 series.
            if int(row.get("calibration_version") or 1) < 2:
                legacy_context = [float(x) for x in (row.get("context_ratios") or [])]
                legacy_generated = [float(x) for x in (row.get("generated_ratios") or [])]
                legacy_capped = bool(
                    (legacy_context and max(legacy_context) <= 50.000001 and float(row.get("context_factor") or 1.0) >= 49.9)
                    or (legacy_generated and max(legacy_generated) <= 50.000001 and float(row.get("generated_factor") or 1.0) >= 49.9)
                )
                if legacy_capped:
                    row["context_ratios"] = []
                    row["generated_ratios"] = []
                    row["sample_signatures"] = []
                    row["samples"] = 0
                row["calibration_version"] = 2
            signatures = list(row.get("sample_signatures") or [])
            if signature in signatures:
                continue
            if cr is not None:
                row.setdefault("context_ratios", []).append(round(cr, 6))
                row["context_ratios"] = row["context_ratios"][-self.HISTORY_LIMIT :]
            if gr is not None:
                row.setdefault("generated_ratios", []).append(round(gr, 6))
                row["generated_ratios"] = row["generated_ratios"][-self.HISTORY_LIMIT :]
            signatures.append(signature)
            row["sample_signatures"] = signatures[-self.HISTORY_LIMIT :]
            row["samples"] = len(row["sample_signatures"])
            cstats = _factor_stats([float(x) for x in (row.get("context_ratios") or [])])
            gstats = _factor_stats([float(x) for x in (row.get("generated_ratios") or [])])
            row["calibration_version"] = 2
            row["context_factor"] = cstats["factor"]
            row["generated_factor"] = gstats["factor"]
            row["context_mad"] = cstats["mad"]
            row["generated_mad"] = gstats["mad"]
            row["context_relative_mad"] = cstats["relative_mad"]
            row["generated_relative_mad"] = gstats["relative_mad"]
            row["updated_at"] = utc_now()
            updated_any = True
        if updated_any:
            data["updated_at"] = utc_now()
            _atomic(self.path, data)
        return self.factors(provider, model, scenario)
