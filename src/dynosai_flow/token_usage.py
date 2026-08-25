# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider token normalization, phase attribution, context accounting, and usage reporting."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .util import estimate_tokens, utc_now
from .cost_telemetry import estimate_list_price
from .estimator_calibration import UsageEstimatorCalibration
from .model_routing import ACTIVITIES, normalize_activity


TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
DERIVED_TOKEN_FIELDS = (
    "context_read_tokens",
    "generated_tokens",
    "processed_tokens",
)


def _atomic_write_json(path: Path, value: Any) -> None:
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


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass


def _to_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def normalize_usage(provider: str, raw: dict[str, Any] | None) -> dict[str, int | None]:
    """Normalize provider token counters while preserving unknown values as ``None``.

    0.10.1 deliberately distinguishes an unknown counter from a provider-reported
    zero. This is especially important for Cursor, which currently returns input,
    cache-read and output counters but no provider ``total_tokens`` and no separate
    reasoning-token counter in the headless result.
    """
    raw = raw or {}
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cacheReadTokens", "cache_read_tokens"),
        "cache_write_input_tokens": ("cache_write_input_tokens", "cacheWriteTokens", "cache_write_tokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    result: dict[str, int | None] = {}
    for canonical, keys in aliases.items():
        found = None
        for key in keys:
            if key in raw and raw.get(key) is not None:
                found = _to_nonnegative_int(raw.get(key))
                break
        result[canonical] = found
    return result


def provider_total_tokens(usage: dict[str, Any]) -> int | None:
    return _to_nonnegative_int(usage.get("total_tokens"))


def observed_token_total(usage: dict[str, Any]) -> int:
    """Legacy provider-independent fresh-input + output view.

    Kept for compatibility with 0.10.0 summaries. New consumers should prefer the
    unambiguous ``context_read_tokens``, ``generated_tokens`` and
    ``processed_tokens`` fields.
    """
    reported = provider_total_tokens(usage)
    if reported is not None:
        return reported
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


def context_read_tokens(provider: str, usage: dict[str, Any]) -> int:
    """How many input/context tokens the provider had to read for this process.

    Cursor reports cache reads alongside fresh input, so context read is
    ``input + cached_input``. Codex reports cached input as a subset of cumulative
    input, so adding it would double count and context read is simply ``input``.
    This is an operational metric, not a billing claim.
    """
    input_tokens = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_input_tokens") or 0)
    if str(provider or "").lower() == "cursor":
        return input_tokens + cached
    return input_tokens


def generated_tokens(usage: dict[str, Any]) -> int:
    return int(usage.get("output_tokens") or 0)


def processed_tokens(provider: str, usage: dict[str, Any]) -> int:
    return context_read_tokens(provider, usage) + generated_tokens(usage)


def _pct_error(estimated: int, exact: int | None) -> float | None:
    if exact is None:
        return None
    if exact == 0:
        return 0.0 if estimated == 0 else None
    return round(((estimated - exact) / exact) * 100.0, 2)


def _error_item(estimated: int, exact: int | None, *, comparable: bool = True, reason: str | None = None) -> dict[str, Any]:
    """Describe estimation error without manufacturing comparability.

    ``comparable=False`` is used when two counters have different semantics. This
    matters for Cursor: the live stream estimate approximates *observed context*,
    while provider ``input_tokens`` is fresh/non-cached input only.
    """
    return {
        "estimated": int(estimated),
        "exact": exact,
        "comparable": bool(comparable),
        "absolute_error": (int(estimated) - exact) if comparable and exact is not None else None,
        "error_pct": _pct_error(int(estimated), exact) if comparable else None,
        "reason": reason,
    }


def estimate_error(provider: str, estimated: dict[str, Any], exact: dict[str, Any]) -> dict[str, Any]:
    """Compare live estimates to semantically equivalent exact metrics.

    The 0.10.1 implementation compared the live Cursor context estimate to
    provider ``input_tokens``. That is invalid when nearly all input is served
    from cache because Cursor reports fresh input and cache-read tokens
    separately. 0.10.2 therefore evaluates the estimator against derived
    operational metrics:

    * live context estimate -> exact ``context_read_tokens``
    * live generated estimate -> exact ``generated_tokens``
    * live processed estimate -> exact ``processed_tokens``

    Raw fresh input is retained as a diagnostic entry but explicitly marked
    non-comparable for Cursor. Reasoning remains unscored when the provider does
    not expose an exact reasoning counter.
    """
    provider = str(provider or "").lower()
    est_context = context_read_tokens(provider, estimated)
    exact_context = context_read_tokens(provider, exact)
    est_generated = generated_tokens(estimated)
    exact_generated = generated_tokens(exact)
    est_processed = processed_tokens(provider, estimated)
    exact_processed = processed_tokens(provider, exact)

    exact_reasoning = _to_nonnegative_int(exact.get("reasoning_output_tokens"))
    result: dict[str, Any] = {
        "comparison_basis": "derived_operational_metrics",
        "context_read_tokens": _error_item(est_context, exact_context),
        "generated_tokens": _error_item(est_generated, exact_generated),
        "processed_tokens": _error_item(est_processed, exact_processed),
        "reasoning_output_tokens": _error_item(
            int(estimated.get("reasoning_output_tokens") or 0),
            exact_reasoning,
            comparable=exact_reasoning is not None,
            reason=None if exact_reasoning is not None else "provider_does_not_report_exact_reasoning_tokens",
        ),
    }

    fresh_exact = _to_nonnegative_int(exact.get("input_tokens"))
    fresh_est = int(estimated.get("input_tokens") or 0)
    if provider == "cursor":
        result["input_tokens"] = _error_item(
            fresh_est,
            fresh_exact,
            comparable=False,
            reason="live_input_estimate_represents_observed_context_not_provider_fresh_input",
        )
    else:
        # Codex cumulative input is also the operational context-read basis, so
        # this raw comparison remains meaningful when exact counters exist.
        result["input_tokens"] = _error_item(fresh_est, fresh_exact)
    result["output_tokens"] = _error_item(est_generated, _to_nonnegative_int(exact.get("output_tokens")))
    return result


@dataclass(slots=True)
class TokenUsageRecorder:
    """Write-through provider telemetry with exact and live-estimate channels.

    The live estimate is never promoted to provider truth. When exact counters
    arrive, the exact channel becomes authoritative while the estimate remains in
    the trace so its error can be measured and improved independently.
    """

    root: Path
    provider: str
    process_id: str
    model: str | None = None
    scenario: str | None = None
    mirror_roots: tuple[Path, ...] = ()
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _started_monotonic: float = field(default_factory=time.monotonic, init=False, repr=False)
    _exact: dict[str, int | None] = field(default_factory=lambda: {k: None for k in TOKEN_FIELDS}, init=False, repr=False)
    _estimated: dict[str, int] = field(default_factory=lambda: {k: 0 for k in TOKEN_FIELDS}, init=False, repr=False)
    _context_window: int | None = field(default=None, init=False, repr=False)
    _last_exact: dict[str, int | None] = field(default_factory=lambda: {k: None for k in TOKEN_FIELDS}, init=False, repr=False)
    _source: str = field(default="dynosai_estimate", init=False, repr=False)
    _quality: str = field(default="estimated", init=False, repr=False)
    _last_event: str = field(default="initialized", init=False, repr=False)
    _samples: int = field(default=0, init=False, repr=False)
    _session_id: str | None = field(default=None, init=False, repr=False)
    _phase: str | None = field(default=None, init=False, repr=False)
    _phase_points: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _calibration: UsageEstimatorCalibration = field(default_factory=UsageEstimatorCalibration, init=False, repr=False)
    _calibration_run_factors: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _calibration_update: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _prompt_estimate_tokens: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        self.provider = str(self.provider).lower()
        self.mirror_roots = tuple(Path(p).expanduser().resolve() for p in self.mirror_roots)
        # Freeze the calibration available before this provider process starts.
        # The final exact sample may train the *next* run but can never improve
        # the reported prediction of the run that produced it.
        try:
            self._calibration_run_factors = self._calibration.factors(self.provider, self.model, self.scenario)
        except Exception:
            self._calibration_run_factors = {"available": False, "samples": 0, "context_factor": 1.0, "generated_factor": 1.0, "confidence": 0.0, "reliability": "untrained"}
        for path in self._roots:
            path.mkdir(parents=True, exist_ok=True)
        self._persist("usage_initialized")

    @property
    def _roots(self) -> tuple[Path, ...]:
        values = [self.root]
        for path in self.mirror_roots:
            if path not in values:
                values.append(path)
        return tuple(values)

    @property
    def latest_path(self) -> Path:
        return self.root / "token-usage.json"

    @property
    def events_path(self) -> Path:
        return self.root / "token-usage.jsonl"

    def set_identity(self, *, session_id: str | None = None, phase: str | None = None, model: str | None = None) -> None:
        with self._lock:
            if session_id:
                self._session_id = str(session_id)
            if phase:
                new_phase = str(phase)
                if self._phase and new_phase != self._phase:
                    self._capture_phase_locked(self._phase, "phase_exit")
                self._phase = new_phase
            if model:
                self.model = str(model)
            self._persist_locked("identity_update")

    def seed_estimate(self, *, input_text: str = "", output_text: str = "", event: str = "estimate_seed") -> None:
        self.add_estimate(
            input_tokens=estimate_tokens(input_text) if input_text else 0,
            output_tokens=estimate_tokens(output_text) if output_text else 0,
            event=event,
        )

    def add_estimate(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_output_tokens: int = 0,
        cached_input_tokens: int = 0,
        event: str = "usage_estimate",
    ) -> dict[str, Any]:
        with self._lock:
            self._estimated["input_tokens"] += max(0, int(input_tokens or 0))
            self._estimated["output_tokens"] += max(0, int(output_tokens or 0))
            self._estimated["reasoning_output_tokens"] += max(0, int(reasoning_output_tokens or 0))
            self._estimated["cached_input_tokens"] += max(0, int(cached_input_tokens or 0))
            self._estimated["total_tokens"] = self._estimated["input_tokens"] + self._estimated["output_tokens"]
            if event in {"managed_prompt_estimate","estimate_seed"} and input_tokens:
                self._prompt_estimate_tokens += max(0,int(input_tokens or 0))
            return self._persist_locked(event)

    def observe_exact(
        self,
        raw_usage: dict[str, Any],
        *,
        source: str,
        context_window: int | None = None,
        last_usage: dict[str, Any] | None = None,
        event: str = "usage_exact",
        session_id: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_usage(self.provider, raw_usage)
        last_normalized = normalize_usage(self.provider, last_usage) if isinstance(last_usage, dict) else None
        with self._lock:
            for key in TOKEN_FIELDS:
                value = normalized.get(key)
                if value is None:
                    continue
                previous = self._exact.get(key)
                self._exact[key] = value if previous is None else max(int(previous), int(value))
            if last_normalized is not None:
                self._last_exact = dict(last_normalized)
            self._source = str(source)
            self._quality = "exact"
            if context_window:
                self._context_window = int(context_window)
            if session_id:
                self._session_id = str(session_id)
            if phase:
                new_phase = str(phase)
                if self._phase and new_phase != self._phase:
                    self._capture_phase_locked(self._phase, "phase_exit")
                self._phase = new_phase
            # Calibrate only once at process finalization and never apply the new factor to the same run. Exact token
            # streams can emit many cumulative samples; learning from every sample
            # overweighted a single run and made the historical factor unstable.
            # Keep a visible deferred marker for backwards-compatible telemetry.
            self._calibration_update = {
                "deferred_until_finalize": True,
                "reason": "final_exact_sample_only",
                "samples_not_committed": True,
            }
            return self._persist_locked(event)

    def current(self) -> dict[str, Any]:
        with self._lock:
            return self._summary_locked()

    def finalize(self, *, event: str = "usage_final") -> dict[str, Any]:
        with self._lock:
            if self._phase:
                self._capture_phase_locked(self._phase, "final")
            # One completed provider process contributes at most one calibration
            # observation. Provider truth remains untouched; only the independent
            # visible-stream estimator learns from the final exact counters.
            if self._quality == "exact":
                try:
                    exact_for_calibration = dict(self._exact)
                    exact_for_calibration["context_read_tokens"] = context_read_tokens(self.provider, self._exact)
                    exact_for_calibration["generated_tokens"] = generated_tokens(self._exact)
                    exact_for_calibration["processed_tokens"] = processed_tokens(self.provider, self._exact)
                    live_for_calibration = self._live_estimate_locked()
                    self._calibration_update = self._calibration.update(
                        self.provider, self.model, live_for_calibration, exact_for_calibration, self.scenario
                    )
                except Exception:
                    self._calibration_update = None
            return self._persist_locked(event, final=True)

    def _effective_locked(self) -> dict[str, Any]:
        if self._quality == "exact":
            return dict(self._exact)
        return dict(self._estimated)

    def _live_estimate_locked(self) -> dict[str, Any]:
        u = dict(self._estimated)
        observed_context = context_read_tokens(self.provider, u)
        return {
            "quality": "estimated",
            "source": "dynosai_stream_observation",
            # Compatibility fields. For Cursor, input_tokens here is an observed
            # context estimate, NOT an estimate of provider fresh/non-cached
            # input. Consumers should use context_read_tokens.
            "input_tokens": int(u.get("input_tokens") or 0),
            "input_semantics": "observed_context_estimate_not_provider_fresh_input" if self.provider == "cursor" else "provider_input_estimate",
            "cached_input_tokens": int(u.get("cached_input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "observed_context_tokens_estimate": observed_context,
            "observed_thinking_tokens_estimate": int(u.get("reasoning_output_tokens") or 0),
            "context_read_tokens": observed_context,
            "generated_tokens": generated_tokens(u),
            "processed_tokens": processed_tokens(self.provider, u),
        }

    def _provider_usage_locked(self) -> dict[str, Any]:
        if self._quality != "exact":
            return {k: None for k in TOKEN_FIELDS}
        return dict(self._exact)

    def _capture_phase_locked(self, phase: str, reason: str) -> None:
        effective = self._effective_locked()
        point = {
            "phase": phase,
            "reason": reason,
            "quality": self._quality,
            "source": self._source,
            "input_tokens": _to_nonnegative_int(effective.get("input_tokens")) or 0,
            "cached_input_tokens": _to_nonnegative_int(effective.get("cached_input_tokens")) or 0,
            "output_tokens": _to_nonnegative_int(effective.get("output_tokens")) or 0,
            "reasoning_output_tokens": _to_nonnegative_int(effective.get("reasoning_output_tokens")),
            "context_read_tokens": context_read_tokens(self.provider, effective),
            "generated_tokens": generated_tokens(effective),
            "processed_tokens": processed_tokens(self.provider, effective),
            "elapsed_seconds": round(max(0.0, time.monotonic() - self._started_monotonic), 3),
            "at": utc_now(),
        }
        if self._phase_points and self._phase_points[-1].get("phase") == phase and self._phase_points[-1].get("processed_tokens") == point["processed_tokens"] and self._phase_points[-1].get("quality") == point["quality"]:
            return
        self._phase_points.append(point)

    def _live_phase_point_locked(self) -> dict[str, Any] | None:
        """Return a comparable delta for the phase currently in progress.

        0.11.1 only materialised a phase delta when the phase exited. That made
        the final report correct but forced the live model-control plane to fall
        back to process-wide counters. 0.11.3 retains the same delta while the
        phase is active, using the most recent closed phase as the baseline.
        """
        if not self._phase:
            return None
        effective = self._effective_locked()
        current = {
            "phase": normalize_activity(self._phase),
            "reason": "phase_live",
            "quality": self._quality,
            "source": self._source,
            "input_tokens": _to_nonnegative_int(effective.get("input_tokens")) or 0,
            "cached_input_tokens": _to_nonnegative_int(effective.get("cached_input_tokens")) or 0,
            "output_tokens": _to_nonnegative_int(effective.get("output_tokens")) or 0,
            "reasoning_output_tokens": _to_nonnegative_int(effective.get("reasoning_output_tokens")),
            "context_read_tokens": context_read_tokens(self.provider, effective),
            "generated_tokens": generated_tokens(effective),
            "processed_tokens": processed_tokens(self.provider, effective),
            "elapsed_seconds": round(max(0.0, time.monotonic() - self._started_monotonic), 3),
            "at": utc_now(),
            "allocation_state": "live",
        }
        baseline = self._phase_points[-1] if self._phase_points else None
        if baseline is None:
            current["delta_context_read_tokens"] = current["context_read_tokens"]
            current["delta_generated_tokens"] = current["generated_tokens"]
            current["delta_processed_tokens"] = current["processed_tokens"]
            current["allocation_quality"] = current["quality"]
        elif baseline.get("quality") == current.get("quality"):
            current["delta_context_read_tokens"] = max(0, int(current["context_read_tokens"] or 0) - int(baseline.get("context_read_tokens") or 0))
            current["delta_generated_tokens"] = max(0, int(current["generated_tokens"] or 0) - int(baseline.get("generated_tokens") or 0))
            current["delta_processed_tokens"] = max(0, int(current["processed_tokens"] or 0) - int(baseline.get("processed_tokens") or 0))
            current["allocation_quality"] = current["quality"]
        else:
            current["delta_context_read_tokens"] = None
            current["delta_generated_tokens"] = None
            current["delta_processed_tokens"] = None
            current["allocation_quality"] = "mixed_unallocatable"
        return current

    @staticmethod
    def _synthetic_zero_phase(phase: str, baseline: dict[str, Any] | None) -> dict[str, Any]:
        baseline = baseline or {}
        return {
            "phase": phase,
            "reason": "synthetic_zero_duration_phase",
            "quality": baseline.get("quality") or "exact",
            "source": baseline.get("source") or "dynosai_phase_completion",
            "input_tokens": int(baseline.get("input_tokens") or 0),
            "cached_input_tokens": int(baseline.get("cached_input_tokens") or 0),
            "output_tokens": int(baseline.get("output_tokens") or 0),
            "reasoning_output_tokens": baseline.get("reasoning_output_tokens"),
            "context_read_tokens": int(baseline.get("context_read_tokens") or 0),
            "generated_tokens": int(baseline.get("generated_tokens") or 0),
            "processed_tokens": int(baseline.get("processed_tokens") or 0),
            "elapsed_seconds": float(baseline.get("elapsed_seconds") or 0.0),
            "at": baseline.get("at") or utc_now(),
            "delta_context_read_tokens": 0,
            "delta_generated_tokens": 0,
            "delta_processed_tokens": 0,
            "allocation_quality": baseline.get("allocation_quality") or baseline.get("quality") or "exact",
            "allocation_state": "synthetic_zero",
        }

    def _phase_summary_locked(self) -> list[dict[str, Any]]:
        raw_result: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for point in self._phase_points:
            row = dict(point)
            if previous is None:
                row["delta_context_read_tokens"] = point.get("context_read_tokens")
                row["delta_generated_tokens"] = point.get("generated_tokens")
                row["delta_processed_tokens"] = point.get("processed_tokens")
                row["allocation_quality"] = point.get("quality")
            elif previous.get("quality") == point.get("quality"):
                row["delta_context_read_tokens"] = max(0, int(point.get("context_read_tokens") or 0) - int(previous.get("context_read_tokens") or 0))
                row["delta_generated_tokens"] = max(0, int(point.get("generated_tokens") or 0) - int(previous.get("generated_tokens") or 0))
                row["delta_processed_tokens"] = max(0, int(point.get("processed_tokens") or 0) - int(previous.get("processed_tokens") or 0))
                row["allocation_quality"] = point.get("quality")
            else:
                # Cursor commonly switches from live estimates to one exact final
                # result. Do not allocate the resulting jump to the final phase.
                row["delta_context_read_tokens"] = None
                row["delta_generated_tokens"] = None
                row["delta_processed_tokens"] = None
                row["allocation_quality"] = "mixed_unallocatable"
            row["allocation_state"] = "closed"
            raw_result.append(row)
            previous = point

        live = self._live_phase_point_locked()
        # Avoid adding a zero-sized live duplicate after finalize captured the
        # same phase at the same cumulative token point.
        if live is not None:
            last = raw_result[-1] if raw_result else None
            duplicate = bool(
                last
                and last.get("phase") == live.get("phase")
                and last.get("processed_tokens") == live.get("processed_tokens")
                and last.get("quality") == live.get("quality")
            )
            if not duplicate:
                raw_result.append(live)

        # Preserve a complete canonical phase matrix. Gate-only phases may be
        # instantaneous and therefore consume exactly zero provider tokens; they
        # should still be visible instead of disappearing from telemetry.
        if not raw_result:
            return []
        by_phase: dict[str, dict[str, Any]] = {}
        for row in raw_result:
            try:
                key = normalize_activity(str(row.get("phase") or "discovery"))
            except Exception:
                continue
            by_phase[key] = row
        observed_indices = [ACTIVITIES.index(p) for p in by_phase if p in ACTIVITIES]
        if not observed_indices:
            return raw_result
        last_index = max(observed_indices)
        result: list[dict[str, Any]] = []
        baseline: dict[str, Any] | None = None
        for phase in ACTIVITIES[: last_index + 1]:
            row = by_phase.get(phase)
            if row is None:
                row = self._synthetic_zero_phase(phase, baseline)
            result.append(row)
            baseline = row
        return result

    def _observable_context_signals_locked(self) -> dict[str, Any]:
        """Visible, provider-independent signals for a structural context estimate.

        The old estimator was effectively ``visible characters × learned scalar``.
        For agentic runs cumulative provider input is driven by conversation turns:
        an early MCP response is re-read by many later model steps while a late one
        is read only once or twice. 0.12.2 therefore records actual MCP wire bytes
        and estimates this *context area* directly. It is intentionally advisory
        until enough real runs prove it more stable than scalar calibration.
        """
        trace=self.root/"mcp-activity.jsonl"
        response_bytes=[]
        calls=0
        if trace.exists():
            try:
                for raw in trace.read_text(encoding="utf-8",errors="replace").splitlines():
                    try:item=json.loads(raw)
                    except Exception:continue
                    if not isinstance(item,dict) or item.get("event")!="mcp_tool_end":continue
                    calls+=1
                    value=_to_nonnegative_int(item.get("response_bytes"))
                    if value is not None:response_bytes.append(value)
            except Exception:
                pass
        response_tokens=[max(1,(int(x)+3)//4) for x in response_bytes]
        prompt=max(0,int(self._prompt_estimate_tokens or 0))
        # Each provider step re-reads the initial prompt and all previous tool
        # responses. Weight early responses by the number of later MCP steps.
        area=prompt*max(1,calls+1)
        n=len(response_tokens)
        for i,tokens in enumerate(response_tokens):
            area += tokens*max(1,n-i)
        return {
            "quality":"observable_structural_estimate",
            "basis":"prompt_plus_mcp_context_area",
            "prompt_tokens_estimate":prompt,
            "mcp_calls_observed":calls,
            "mcp_response_bytes":sum(response_bytes),
            "mcp_response_tokens_estimate":sum(response_tokens),
            "context_read_tokens":int(area),
            "generated_tokens":int(self._estimated.get("output_tokens") or 0),
            "processed_tokens":int(area)+int(self._estimated.get("output_tokens") or 0),
            "routing_authoritative":False,
        }

    def _summary_locked(self) -> dict[str, Any]:
        usage = self._effective_locked()
        elapsed = max(0.001, time.monotonic() - self._started_monotonic)
        observed = observed_token_total(usage)
        last_has_values = any(v is not None for v in self._last_exact.values())
        last_observed = observed_token_total(self._last_exact) if last_has_values else 0
        context_pct = None
        if self._context_window and last_observed:
            context_pct = round((last_observed / max(1, self._context_window)) * 100.0, 2)
        exact_usage = self._provider_usage_locked()
        provider_total = provider_total_tokens(exact_usage) if self._quality == "exact" else None
        reason = _to_nonnegative_int(usage.get("reasoning_output_tokens"))
        if self._quality == "exact" and self._exact.get("reasoning_output_tokens") is None:
            reason = None
        live = self._live_estimate_locked()
        try:
            calibrated_live = self._calibration.apply_factors(live, self._calibration_run_factors)
        except Exception:
            calibrated_live = {"quality":"raw_estimate","context_read_tokens":live.get("context_read_tokens",0),"generated_tokens":live.get("generated_tokens",0),"processed_tokens":live.get("processed_tokens",0),"calibration":{"available":False}}
        structural_live=self._observable_context_signals_locked()
        result = {
            "schema_version": 4,
            "provider": self.provider,
            "process_id": self.process_id,
            "session_id": self._session_id,
            "scenario": self.scenario,
            "model": self.model,
            "phase": self._phase,
            "quality": self._quality,
            "source": self._source,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
            "cache_write_input_tokens": int(usage.get("cache_write_input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_output_tokens": reason,
            # Compatibility alias. It is null when the provider did not report a
            # total instead of pretending that unknown means zero.
            "total_tokens": provider_total,
            "provider_total_tokens": provider_total,
            "observed_token_total": observed,
            "context_read_tokens": context_read_tokens(self.provider, usage),
            "generated_tokens": generated_tokens(usage),
            "processed_tokens": processed_tokens(self.provider, usage),
            "cache_semantics": "parallel_cache_read" if self.provider == "cursor" else "cached_subset_of_input",
            "provider_usage": exact_usage,
            "live_estimate": live,
            "calibrated_live_estimate": calibrated_live,
            "structural_live_estimate": structural_live,
            "structural_estimate_error": (_error_item(int(structural_live.get("context_read_tokens") or 0),context_read_tokens(self.provider,self._exact)) if self._quality=="exact" else None),
            "calibration_before_run": dict(self._calibration_run_factors),
            "calibration_update": self._calibration_update,
            "estimate_error": estimate_error(self.provider, self._estimated, self._exact) if self._quality == "exact" else None,
            "phase_usage": self._phase_summary_locked(),
            "last_turn_usage": dict(self._last_exact) if last_has_values else None,
            "last_turn_observed_tokens": last_observed or None,
            "model_context_window": self._context_window,
            "last_turn_context_utilization_pct": context_pct,
            "tokens_per_minute": round(processed_tokens(self.provider, usage) * 60.0 / elapsed, 2) if processed_tokens(self.provider, usage) else 0.0,
            "input_tokens_per_minute": round(int(usage.get("input_tokens") or 0) * 60.0 / elapsed, 2) if usage.get("input_tokens") else 0.0,
            "output_tokens_per_minute": round(int(usage.get("output_tokens") or 0) * 60.0 / elapsed, 2) if usage.get("output_tokens") else 0.0,
            "elapsed_seconds": round(elapsed, 3),
            "samples": self._samples,
            "last_event": self._last_event,
            "updated_at": utc_now(),
        }
        result["cost_estimate"] = estimate_list_price(self.provider, self.model, result)
        return result

    def _persist(self, event: str, final: bool = False) -> dict[str, Any]:
        with self._lock:
            return self._persist_locked(event, final=final)

    def _persist_locked(self, event: str, final: bool = False) -> dict[str, Any]:
        self._samples += 1
        self._last_event = event
        summary = self._summary_locked()
        summary["final"] = bool(final)
        event_item = {"event": event, **summary}
        for root in self._roots:
            _append_jsonl(root / "token-usage.jsonl", event_item)
            _atomic_write_json(root / "token-usage.json", summary)
        return summary



def align_usage_to_activity(usage: dict[str, Any] | None, activity: str) -> dict[str, Any] | None:
    """Return a boundary-safe usage view aligned to ``activity``.

    The acceptance harness owns the live :class:`TokenUsageRecorder`, while the
    managed MCP server runs in a separate process and only sees its persisted
    ``token-usage.json`` snapshot. At a workflow transition the MCP server can
    therefore know the new phase a few milliseconds before the recorder watcher
    has observed the same transition.

    0.11.3 closes that ordering gap without rewriting provider truth. When the
    persisted snapshot still names the previous phase, this helper projects a
    zero-sized live row for the *known* new phase. Model control can then compare
    like-for-like phase telemetry immediately at the boundary. The next recorder
    sample replaces the projection with the real live delta.

    The input mapping is never mutated.
    """
    if not isinstance(usage, dict):
        return usage
    target = normalize_activity(activity)
    current_raw = usage.get("phase")
    try:
        current = normalize_activity(str(current_raw)) if current_raw else None
    except Exception:
        current = None

    rows = [dict(x) for x in (usage.get("phase_usage") or []) if isinstance(x, dict)]

    def row_phase(row: dict[str, Any]) -> str | None:
        try:
            return normalize_activity(str(row.get("phase") or target))
        except Exception:
            return None

    # If the recorder is already aligned and exposes a comparable live row, keep
    # provider telemetry untouched.
    if current == target:
        live = [x for x in rows if row_phase(x) == target and x.get("allocation_state") == "live"]
        if live:
            return dict(usage)

    out = dict(usage)
    out["phase"] = target
    context = _to_nonnegative_int(usage.get("context_read_tokens")) or 0
    generated = _to_nonnegative_int(usage.get("generated_tokens", usage.get("output_tokens"))) or 0
    processed = _to_nonnegative_int(usage.get("processed_tokens"))
    if processed is None:
        processed = context + generated
    projected = {
        "phase": target,
        "reason": "phase_boundary_identity_projection",
        "quality": usage.get("quality") or "estimated",
        "source": "dynosai_boundary_identity_sync",
        "input_tokens": _to_nonnegative_int(usage.get("input_tokens")) or 0,
        "cached_input_tokens": _to_nonnegative_int(usage.get("cached_input_tokens")) or 0,
        "output_tokens": _to_nonnegative_int(usage.get("output_tokens")) or 0,
        "reasoning_output_tokens": _to_nonnegative_int(usage.get("reasoning_output_tokens")),
        "context_read_tokens": context,
        "generated_tokens": generated,
        "processed_tokens": processed,
        "delta_context_read_tokens": 0,
        "delta_generated_tokens": 0,
        "delta_processed_tokens": 0,
        "allocation_quality": usage.get("quality") or "estimated",
        "allocation_state": "live",
        "identity_projection": True,
        "at": utc_now(),
    }
    # Replace only an older projected row for the same target. Closed/synthetic
    # history remains intact for the final phase matrix.
    rows = [x for x in rows if not (row_phase(x) == target and x.get("identity_projection") is True)]
    rows.append(projected)
    out["phase_usage"] = rows
    out["phase_identity_sync"] = {
        "from_phase": current,
        "to_phase": target,
        "projected_live_delta": 0,
        "source": "workflow_boundary",
    }
    return out


def cursor_usage_from_event(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("type") == "result" and isinstance(item.get("usage"), dict):
        return dict(item["usage"])
    return None


def cursor_estimated_output_text(item: dict[str, Any]) -> tuple[str, str]:
    """Return visible normal/thinking deltas for Cursor live estimates.

    Thinking text remains an *observation estimate* and is never mapped to an
    exact provider ``reasoning_output_tokens`` value.
    """
    if item.get("type") == "thinking" and item.get("subtype") == "delta":
        return "", str(item.get("text") or "")
    if item.get("type") == "assistant":
        message = item.get("message") or {}
        texts = []
        for content in message.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "text":
                texts.append(str(content.get("text") or ""))
        return "".join(texts), ""
    return "", ""


def codex_token_sample_from_rollout(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("type") != "event_msg":
        return None
    payload = item.get("payload") or {}
    if payload.get("type") != "token_count":
        return None
    info = payload.get("info") or {}
    total = info.get("total_token_usage")
    if not isinstance(total, dict):
        return None
    return {
        "usage": dict(total),
        "last_usage": dict(info.get("last_token_usage") or {}),
        "model_context_window": info.get("model_context_window"),
        "timestamp": item.get("timestamp"),
    }


def iter_codex_rollout_samples(home: str | Path) -> Iterable[dict[str, Any]]:
    home = Path(home)
    sessions = home / "sessions"
    if not sessions.exists():
        return []
    samples = []
    for path in sorted(sessions.rglob("rollout*.jsonl")):
        try:
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(raw)
                except Exception:
                    continue
                sample = codex_token_sample_from_rollout(item)
                if sample:
                    sample["path"] = str(path)
                    samples.append(sample)
        except OSError:
            continue
    return samples


def latest_usage(root: str | Path) -> dict[str, Any] | None:
    path = Path(root).expanduser().resolve() / "token-usage.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_iso(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def mcp_runtime_metrics(path: str | Path | None) -> dict[str, Any]:
    result = {"mcp_calls": 0, "mcp_seconds": 0.0}
    if not path:
        return result
    path = Path(path)
    if not path.exists():
        return result
    starts: dict[str, float] = {}
    completed = 0
    total = 0.0
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except Exception:
                continue
            request_id = str(item.get("request_id") or "")
            at = _parse_iso(item.get("at"))
            if item.get("event") == "mcp_tool_start" and request_id and at is not None:
                starts[request_id] = at
            elif item.get("event") == "mcp_tool_end" and request_id:
                completed += 1
                if at is not None and request_id in starts:
                    total += max(0.0, at - starts.pop(request_id))
    except OSError:
        return result
    return {"mcp_calls": completed, "mcp_seconds": round(total, 3)}


def provider_mcp_runtime_metrics(provider: str, path: str | Path | None) -> dict[str, Any]:
    """Recover DynosAI MCP call counts/durations from the provider protocol stream.

    This is a fallback only. The normalized mcp-activity trace remains the
    primary source because it is provider independent. A missing normalized
    trace must not turn real MCP traffic into zeros.
    """
    result = {"mcp_calls": 0, "mcp_seconds": 0.0, "source": "provider_stream"}
    if not path:
        return result
    path = Path(path)
    if not path.exists():
        return result
    provider = str(provider or "").lower()
    starts: dict[str, float] = {}
    completed = 0
    total = 0.0
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(raw)
            except Exception:
                continue
            at = _parse_iso(record.get("at"))
            if provider == "codex":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                method = str(payload.get("method") or "")
                params = payload.get("params")
                params = params if isinstance(params, dict) else {}
                item = params.get("item")
                if not isinstance(item, dict) or item.get("type") != "mcpToolCall":
                    continue
                if str(item.get("server") or "").lower() != "dynosai":
                    continue
                call_id = str(item.get("id") or item.get("callId") or item.get("call_id") or "")
                if method == "item/started":
                    if call_id and at is not None:
                        starts[call_id] = at
                elif method == "item/completed":
                    completed += 1
                    if call_id and at is not None and call_id in starts:
                        total += max(0.0, at - starts.pop(call_id))
            elif provider == "cursor":
                tool_call = record.get("tool_call")
                if not isinstance(tool_call, dict):
                    continue
                mcp = tool_call.get("mcpToolCall")
                if not isinstance(mcp, dict):
                    continue
                args = mcp.get("args")
                args = args if isinstance(args, dict) else {}
                server = args.get("serverIdentifier") or args.get("providerIdentifier") or mcp.get("server")
                if str(server or "").lower() != "dynosai":
                    continue
                call_id = str(mcp.get("id") or tool_call.get("id") or record.get("id") or "")
                subtype = str(record.get("subtype") or "").lower()
                status = str(mcp.get("status") or tool_call.get("status") or "").lower()
                is_start = subtype in {"started", "start"} or status in {"started", "running"}
                is_end = subtype in {"completed", "complete", "finished"} or status in {"completed", "success", "failed"}
                if is_start and call_id and at is not None:
                    starts[call_id] = at
                elif is_end:
                    completed += 1
                    if call_id and at is not None and call_id in starts:
                        total += max(0.0, at - starts.pop(call_id))
    except OSError:
        return result
    return {"mcp_calls": completed, "mcp_seconds": round(total, 3), "source": "provider_stream"}


def cursor_runtime_from_stream(path: str | Path | None) -> dict[str, Any]:
    result = {"reconnect_count": 0, "provider_api_seconds": None, "provider_reported_seconds": None}
    if not path:
        return result
    path = Path(path)
    if not path.exists():
        return result
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if item.get("type") == "connection" and item.get("subtype") == "reconnecting":
                result["reconnect_count"] += 1
            if item.get("type") == "result":
                api_ms = _to_nonnegative_int(item.get("duration_api_ms"))
                total_ms = _to_nonnegative_int(item.get("duration_ms"))
                if api_ms is not None:
                    result["provider_api_seconds"] = round(api_ms / 1000.0, 3)
                if total_ms is not None:
                    result["provider_reported_seconds"] = round(total_ms / 1000.0, 3)
    except OSError:
        return result
    return result


def runtime_metrics(provider: str, workflow_seconds: float | int | None, *, mcp_path: str | Path | None = None, provider_stream_path: str | Path | None = None) -> dict[str, Any]:
    workflow = max(0.0, float(workflow_seconds or 0.0))
    mcp = mcp_runtime_metrics(mcp_path)
    mcp_source = "normalized_trace" if int(mcp.get("mcp_calls") or 0) > 0 else "unavailable"
    if int(mcp.get("mcp_calls") or 0) == 0 and provider_stream_path:
        fallback = provider_mcp_runtime_metrics(provider, provider_stream_path)
        if int(fallback.get("mcp_calls") or 0) > 0:
            mcp = fallback
            mcp_source = "provider_stream_fallback"
    cursor = cursor_runtime_from_stream(provider_stream_path) if str(provider).lower() == "cursor" else {"reconnect_count": 0, "provider_api_seconds": None, "provider_reported_seconds": None}
    non_mcp = max(0.0, workflow - float(mcp.get("mcp_seconds") or 0.0))
    return {
        "workflow_elapsed_seconds": round(workflow, 3),
        "dynosai_mcp_seconds": float(mcp.get("mcp_seconds") or 0.0),
        "mcp_calls": int(mcp.get("mcp_calls") or 0),
        "mcp_metrics_source": mcp_source,
        "non_mcp_seconds": round(non_mcp, 3),
        "provider_api_seconds": cursor.get("provider_api_seconds"),
        "provider_reported_seconds": cursor.get("provider_reported_seconds"),
        "reconnect_count": int(cursor.get("reconnect_count") or 0),
        "mcp_share_pct": round((float(mcp.get("mcp_seconds") or 0.0) / workflow) * 100.0, 2) if workflow else 0.0,
    }


def aggregate_usage(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values = list(items)
    exact = sum(1 for item in values if item.get("quality") == "exact")
    estimated = len(values) - exact
    provider_totals = [item.get("provider_total_tokens") for item in values]
    provider_total_known = all(v is not None for v in provider_totals) if values else False
    return {
        "processes": len(values),
        "exact_processes": exact,
        "estimated_processes": estimated,
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in values),
        "cached_input_tokens": sum(int(item.get("cached_input_tokens") or 0) for item in values),
        "cache_write_input_tokens": sum(int(item.get("cache_write_input_tokens") or 0) for item in values),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in values),
        "reasoning_output_tokens": sum(int(item.get("reasoning_output_tokens") or 0) for item in values if item.get("reasoning_output_tokens") is not None) if any(item.get("reasoning_output_tokens") is not None for item in values) else None,
        "provider_total_tokens": sum(int(v or 0) for v in provider_totals) if provider_total_known else None,
        "total_tokens": sum(int(v or 0) for v in provider_totals) if provider_total_known else None,
        "observed_token_total": sum(int(item.get("observed_token_total") or 0) for item in values),
        "context_read_tokens": sum(int(item.get("context_read_tokens") or 0) for item in values),
        "generated_tokens": sum(int(item.get("generated_tokens") or 0) for item in values),
        "processed_tokens": sum(int(item.get("processed_tokens") or 0) for item in values),
        "estimated_list_price_usd": round(sum(float(((item.get("cost_estimate") or {}).get("estimated_list_price_usd")) or 0) for item in values), 8) if any(item.get("cost_estimate") for item in values) else None,
    }
