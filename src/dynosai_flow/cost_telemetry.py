# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider usage and estimated list-price telemetry aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import json

# Versioned public list-price metadata. These values are observability inputs,
# not billing authority. They must never affect governance unless a user enables
# a cost policy explicitly. Prices are USD per 1M tokens.
PRICE_CATALOG_VERSION = "2026-08-24"
PRICE_CATALOG: dict[str, dict[str, dict[str, float | None]]] = {
    "codex": {
        "gpt-5.6-luna": {"input": 0.20, "cache_write": 0.25, "cache_read": 0.02, "output": 1.20},
        "gpt-5.6-terra": {"input": 2.00, "cache_write": 2.50, "cache_read": 0.20, "output": 12.00},
        "gpt-5.6-sol": {"input": 4.00, "cache_write": 5.00, "cache_read": 0.40, "output": 20.00},
    },
    "cursor": {
        "gpt-5.6-luna": {"input": 0.20, "cache_write": 0.25, "cache_read": 0.02, "output": 1.20},
        "gpt-5.6-terra": {"input": 2.00, "cache_write": 2.50, "cache_read": 0.20, "output": 12.00},
        "gpt-5.6-sol": {"input": 4.00, "cache_write": 5.00, "cache_read": 0.40, "output": 20.00},
        "grok-4.6": {"input": 2.00, "cache_write": None, "cache_read": 0.50, "output": 6.00},
        "cursor-grok-4.6": {"input": 2.00, "cache_write": None, "cache_read": 0.50, "output": 6.00},
        "composer-2.5": {"input": 0.50, "cache_write": None, "cache_read": 0.20, "output": 2.50},
    },
}
PRICE_SOURCES = {
    "codex": "OpenAI public API list pricing",
    "cursor": "Cursor public model pricing",
}


def _norm_model(model: str | None) -> str:
    value = str(model or "").strip().lower().replace("_", "-")
    for suffix in ("-medium", "-low", "-high", "-xhigh", "-max", "-none"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    if value.startswith("cursor-gpt-"):
        value = value[len("cursor-"):]
    return value


def _n(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class CostEstimate:
    provider: str
    model: str
    catalog_version: str
    pricing_source: str
    quality: str
    fresh_input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    input_usd: float
    cache_read_usd: float
    cache_write_usd: float | None
    output_usd: float
    estimated_list_price_usd: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimate_list_price(provider: str, model: str | None, usage: dict[str, Any] | None) -> dict[str, Any] | None:
    provider = str(provider or "").lower()
    model_key = _norm_model(model)
    prices = (PRICE_CATALOG.get(provider) or {}).get(model_key)
    if not prices:
        return None
    usage = usage or {}
    inp = _n(usage.get("input_tokens"))
    cached = _n(usage.get("cached_input_tokens"))
    cache_write = _n(usage.get("cache_write_input_tokens"))
    output = _n(usage.get("output_tokens"))

    # Cursor reports fresh input and cache reads separately. Codex cumulative
    # input includes cached input, so billing fresh input is the remainder.
    fresh_input = inp if provider == "cursor" else max(0, inp - cached)
    input_usd = fresh_input / 1_000_000 * float(prices["input"] or 0)
    cache_read_usd = cached / 1_000_000 * float(prices["cache_read"] or 0)
    cache_write_rate = prices.get("cache_write")
    cache_write_usd = None if cache_write_rate is None else cache_write / 1_000_000 * float(cache_write_rate)
    output_usd = output / 1_000_000 * float(prices["output"] or 0)
    total = input_usd + cache_read_usd + (cache_write_usd or 0.0) + output_usd
    quality = "exact_usage_list_price" if str(usage.get("quality") or "") == "exact" else "estimated_usage_list_price"
    return CostEstimate(
        provider=provider,
        model=model_key,
        catalog_version=PRICE_CATALOG_VERSION,
        pricing_source=PRICE_SOURCES.get(provider, "public list pricing"),
        quality=quality,
        fresh_input_tokens=fresh_input,
        cached_input_tokens=cached,
        cache_write_input_tokens=cache_write,
        output_tokens=output,
        input_usd=round(input_usd, 8),
        cache_read_usd=round(cache_read_usd, 8),
        cache_write_usd=None if cache_write_usd is None else round(cache_write_usd, 8),
        output_usd=round(output_usd, 8),
        estimated_list_price_usd=round(total, 8),
        note="Estimated public list-price equivalent, not an invoice. Subscription pools, discounts, regional uplifts and provider-specific billing can differ.",
    ).to_dict()


def _payload_dict(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {}
    return payload if isinstance(payload, dict) else {}


def _audit_rows_for_work(db: Any, work_id: str, event_type: str) -> list[dict[str, Any]]:
    rows = db.query("SELECT entity_id,payload FROM audit WHERE event_type=?", (event_type,))
    matched: list[dict[str, Any]] = []
    for row in rows:
        payload = _payload_dict(row)
        if str(row.get("entity_id") or "") == work_id or str(payload.get("work_id") or "") == work_id:
            matched.append(payload)
    return matched


def _audit_for_work(db: Any, work_id: str, event_type: str) -> int:
    return len(_audit_rows_for_work(db, work_id, event_type))


def _measured_sum(rows: list[dict[str, Any]], key: str) -> int | None:
    values = []
    for row in rows:
        if key not in row or row.get(key) is None:
            continue
        values.append(_n(row.get(key)))
    if not values:
        return None
    return sum(values)


def _mcp_surface_size() -> int:
    from .mcp import LEGACY_TOOLS, TOOLS
    return len({str(item.get("name") or "") for item in [*TOOLS, *LEGACY_TOOLS] if item.get("name")})


def governed_change_cost(db: Any, work_id: str, *, usage: dict[str, Any] | None = None, provider: str | None = None, model: str | None = None, elapsed_seconds: float | None = None) -> dict[str, Any]:
    """Raw observability for COST PER SUCCESSFUL GOVERNED CHANGE.

    Monetary figures are derived only when the public catalog matches. Tokens,
    tools, retries and human gates are always stored as raw counts.
    """
    work = db.one("SELECT * FROM work_items WHERE id=?", (work_id,))
    if not work:
        raise ValueError(f"unknown work: {work_id}")
    usage = usage or {}
    validations = db.query("SELECT status,exit_code FROM validations WHERE work_id=?", (work_id,))
    def _row_passed(row: dict[str, Any]) -> bool:
        exit_code = row.get("exit_code")
        if exit_code is not None and str(exit_code) != "":
            try:
                return int(exit_code) == 0
            except (TypeError, ValueError):
                return False
        return str(row.get("status") or "").lower() in {"passed", "ok", "success"}
    validation_status = "passed" if validations and all(_row_passed(row) for row in validations) else ("failed" if validations else "not_run")
    human = db.query("SELECT kind,status,gate,response_json FROM human_interactions WHERE work_id=?", (work_id,))
    gates = [row for row in human if str(row.get("kind")) == "gate_approval"]
    corrections = [row for row in human if str(row.get("status")) in {"declined", "cancelled"}]
    metrics = db.query(
        "SELECT m.context_tokens,m.retries,m.test_passed FROM runs r LEFT JOIN run_metrics m ON m.run_id=r.id WHERE r.work_id=?",
        (work_id,),
    )
    input_tokens = _n(usage.get("input_tokens"))
    output_tokens = _n(usage.get("output_tokens"))
    cached = _n(usage.get("cached_input_tokens"))
    reasoning = _n(usage.get("reasoning_output_tokens"))
    provider_key = str(provider or "").lower()
    fresh_input = input_tokens if provider_key == "cursor" else max(0, input_tokens - cached)
    context_tokens = sum(_n(row.get("context_tokens")) for row in metrics) or _n(usage.get("context_tokens"))
    estimate = estimate_list_price(provider or "", model, usage) if provider and model and usage else None
    success = str(work.get("state") or "") == "done"
    called = _audit_rows_for_work(db, work_id, "MCPToolCalled")
    mcp_calls = len(called)
    return {
        "metric": "cost_per_successful_governed_change",
        "work_id": work_id,
        "success": success,
        "state": work.get("state"),
        "requirements_satisfied": None,
        "validation_status": validation_status,
        "input_tokens": input_tokens,
        "fresh_input_tokens": fresh_input,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning,
        "context_tokens": context_tokens,
        "cached_input_tokens": cached,
        "model_calls": _n(usage.get("model_calls")),
        "tool_calls": mcp_calls,
        "mcp_calls": mcp_calls,
        "mcp_failures": _audit_for_work(db, work_id, "MCPToolFailed"),
        "mcp_rejections": _audit_for_work(db, work_id, "MCPToolRejected"),
        "mcp_normalizations": _audit_for_work(db, work_id, "MCPToolNormalized"),
        "mcp_duration_ms": _measured_sum(called, "duration_ms"),
        "mcp_result_bytes": _measured_sum(called, "result_bytes"),
        "retries": sum(_n(row.get("retries")) for row in metrics),
        "provider_failures": _audit_for_work(db, work_id, "MCPToolFailed"),
        "elapsed_seconds": elapsed_seconds,
        "human_gates": len(gates),
        "human_corrections": len(corrections),
        "replans": _audit_for_work(db, work_id, "ReplanRequested") + _audit_for_work(db, work_id, "BoundedReplan"),
        "estimated_list_price_usd": None if not estimate else estimate.get("estimated_list_price_usd"),
        "cost_estimate": estimate,
        "note": "Raw usage. Monetary cost is catalog-derived observability, not an invoice. Unused advertised MCP tools are availability, not waste.",
    }


def empty_governed_change_aggregate() -> dict[str, Any]:
    return {
        "metric": "aggregate_governed_change_cost",
        "completed_work": 0,
        "successful": 0,
        "mcp_tool_surface_size": _mcp_surface_size(),
        "mcp_tool_surface_note": "experimental_context_overhead",
        "note": "Unused advertised MCP tools are availability, not waste.",
    }


def aggregate_governed_change_cost(db: Any) -> dict[str, Any]:
    """Completed-work rollup of governed_change_cost(). Does not invent waste metrics."""
    rows = db.query("SELECT id,state FROM work_items WHERE id!='PROJECT'")
    completed = [row for row in rows if str(row.get("state") or "") == "done"]
    costs = [governed_change_cost(db, str(row["id"])) for row in completed]
    prices = [float(item.get("estimated_list_price_usd")) for item in costs if item.get("estimated_list_price_usd") is not None]
    return {
        "metric": "aggregate_governed_change_cost",
        "completed_work": len(completed),
        "successful": sum(1 for item in costs if item.get("success")),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in costs),
        "fresh_input_tokens": sum(int(item.get("fresh_input_tokens") or 0) for item in costs),
        "cached_input_tokens": sum(int(item.get("cached_input_tokens") or 0) for item in costs),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in costs),
        "reasoning_output_tokens": sum(int(item.get("reasoning_output_tokens") or 0) for item in costs),
        "retries": sum(int(item.get("retries") or 0) for item in costs),
        "human_gates": sum(int(item.get("human_gates") or 0) for item in costs),
        "human_corrections": sum(int(item.get("human_corrections") or 0) for item in costs),
        "elapsed_seconds": sum(float(item.get("elapsed_seconds") or 0) for item in costs),
        "mcp_calls": sum(int(item.get("mcp_calls") or 0) for item in costs),
        "mcp_failures": sum(int(item.get("mcp_failures") or 0) for item in costs),
        "mcp_rejections": sum(int(item.get("mcp_rejections") or 0) for item in costs),
        "mcp_normalizations": sum(int(item.get("mcp_normalizations") or 0) for item in costs),
        "estimated_list_price_usd": None if not prices else round(sum(prices), 8),
        "items": costs,
        "mcp_tool_surface_size": _mcp_surface_size(),
        "mcp_tool_surface_note": "experimental_context_overhead",
        "note": "Unused advertised MCP tools are availability, not waste.",
    }


def aggregate_cost(estimates: list[dict[str, Any] | None]) -> dict[str, Any]:
    rows = [x for x in estimates if isinstance(x, dict)]
    total = round(sum(float(x.get("estimated_list_price_usd") or 0) for x in rows), 8)
    return {
        "catalog_version": PRICE_CATALOG_VERSION,
        "processes": len(rows),
        "estimated_list_price_usd": total,
        "by_provider": {
            provider: round(sum(float(x.get("estimated_list_price_usd") or 0) for x in rows if x.get("provider") == provider), 8)
            for provider in sorted({str(x.get("provider")) for x in rows})
        },
        "note": "Estimated public list-price equivalent only; not actual billing.",
    }
