# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider usage and estimated list-price telemetry aggregation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

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
