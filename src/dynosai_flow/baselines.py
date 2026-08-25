# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Reference efficiency baselines used by diagnostics and regression reporting."""

from __future__ import annotations

from typing import Any

# Reference values are derived from accepted real-provider evidence and are used only for regression reporting.
# Baselines are intentionally descriptive, not release gates: model changes make
# token/time comparisons useful for context but not apples-to-apples efficiency
# claims. The comparison therefore exposes `comparable_model` explicitly.
REAL_ACCEPTANCE_BASELINES: dict[str, dict[str, dict[str, dict[str, Any]]]] = {
    "0.9.5": {
        "cursor": {
            "fibonacci": {
                "model": "grok-4.6",
                "effort": "medium",
                "input_tokens": 179451,
                "cached_input_tokens": 1008512,
                "output_tokens": 8018,
                "context_read_tokens": 1187963,
                "processed_tokens": 1195981,
                "mcp_calls": 31,
            },
            "orderflow-contract-discounts": {
                "model": "grok-4.6",
                "effort": "medium",
                "input_tokens": 364470,
                "cached_input_tokens": 2520576,
                "output_tokens": 20388,
                "context_read_tokens": 2885046,
                "processed_tokens": 2905434,
                "mcp_calls": 52,
            },
        },
        "codex": {
            "fibonacci": {
                "model": "gpt-5.6-sol",
                "effort": "medium",
                "input_tokens": 986585,
                "cached_input_tokens": 905984,
                "output_tokens": 7028,
                "reasoning_output_tokens": 1003,
                "provider_total_tokens": 993613,
                "context_read_tokens": 986585,
                "processed_tokens": 993613,
            },
            "orderflow-contract-discounts": {
                "model": "gpt-5.6-sol",
                "effort": "medium",
                "input_tokens": 2328195,
                "cached_input_tokens": 2215680,
                "output_tokens": 12951,
                "reasoning_output_tokens": 1615,
                "provider_total_tokens": 2341146,
                "context_read_tokens": 2328195,
                "processed_tokens": 2341146,
            },
        },
    },
    "0.10.0": {
        "cursor": {
            "fibonacci": {
                "model": "grok-4.6",
                "effort": "medium",
                "input_tokens": 172537,
                "cached_input_tokens": 539520,
                "output_tokens": 3720,
                "context_read_tokens": 712057,
                "processed_tokens": 715777,
                "mcp_calls": 19,
                "duration_seconds": 966.133,
                "reconnect_count": 3,
            }
        }
    },
}


def _pct_change(current: Any, previous: Any) -> float | None:
    try:
        current = float(current)
        previous = float(previous)
    except (TypeError, ValueError):
        return None
    if previous == 0:
        return 0.0 if current == 0 else None
    return round(((current - previous) / previous) * 100.0, 2)


def compare_to_baseline(
    *,
    provider: str,
    scenario: str,
    model: str | None,
    effort: str | None,
    usage: dict[str, Any] | None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    provider = str(provider or "").lower()
    scenario = str(scenario or "")
    usage = usage or {}
    runtime = runtime or {}

    # Prefer newest baseline for this provider/scenario. A model mismatch is
    # surfaced rather than hidden so users do not mistake a cheaper-model test
    # for a pure orchestration efficiency comparison.
    selected_version = None
    baseline = None
    for version in reversed(tuple(REAL_ACCEPTANCE_BASELINES)):
        candidate = (((REAL_ACCEPTANCE_BASELINES.get(version) or {}).get(provider) or {}).get(scenario))
        if candidate:
            selected_version = version
            baseline = candidate
            break
    if not baseline or not selected_version:
        return None

    requested_model = str(model or "").lower()
    baseline_model = str(baseline.get("model") or "").lower()
    comparable_model = bool(requested_model and baseline_model and requested_model == baseline_model and str(effort or "").lower() == str(baseline.get("effort") or "").lower())
    current_values = {
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "context_read_tokens": usage.get("context_read_tokens"),
        "processed_tokens": usage.get("processed_tokens"),
        "mcp_calls": runtime.get("mcp_calls"),
        "duration_seconds": runtime.get("workflow_elapsed_seconds"),
        "reconnect_count": runtime.get("reconnect_count"),
    }
    changes = {
        key: _pct_change(value, baseline.get(key))
        for key, value in current_values.items()
        if value is not None and baseline.get(key) is not None
    }
    return {
        "baseline_version": selected_version,
        "baseline_model": baseline.get("model"),
        "baseline_effort": baseline.get("effort"),
        "current_model": model,
        "current_effort": effort,
        "comparable_model": comparable_model,
        "comparison_kind": "same_model" if comparable_model else "cross_model_context_only",
        "baseline": dict(baseline),
        "current": current_values,
        "change_pct": changes,
        "warning": None if comparable_model else "Model/effort differs from baseline; token/time deltas are contextual and must not be attributed solely to DynosAI orchestration changes.",
    }
