# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Offline comparison of model/provider acceptance bundles."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .cost_telemetry import estimate_list_price


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _summary_from_zip(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as z:
        candidates=[n for n in z.namelist() if n.endswith("summary-final.json")]
        if not candidates:
            raise ValueError(f"No summary-final.json in {path}")
        preferred=next((x for x in candidates if x=="suite/summary-final.json"),candidates[0])
        return json.loads(z.read(preferred).decode("utf-8"))


def load_acceptance_summary(path: str|Path) -> dict[str,Any]:
    p=Path(path).expanduser().resolve()
    if p.suffix.lower()==".zip":return _summary_from_zip(p)
    return _load_json(p)


def process_rows(summary:dict[str,Any],source:str|None=None)->list[dict[str,Any]]:
    rows=[]
    for child in summary.get("children") or []:
        usage=child.get("token_usage") or {};runtime=child.get("runtime_metrics") or {};run=child.get("provider_run") or {};route=child.get("model_route") or run.get("model_route_requested") or {}
        cost=child.get("cost_estimate") or usage.get("cost_estimate") or {}
        if not cost:
            cost=estimate_list_price(str(child.get("provider") or ""), route.get("model") or usage.get("model"), usage) or {}
        oracle=child.get("oracle") or {};gates=child.get("gates") or {}
        rows.append({
            "source":source,
            "dynosai_version":summary.get("dynosai_version"),
            "provider":child.get("provider"),"scenario":child.get("scenario"),
            "model":route.get("model") or usage.get("model"),"effort":route.get("effort"),
            "passed":child.get("status")=="passed","oracle_passed":bool(oracle.get("passed")),"quality_score":int(child.get("quality_score") or 0),
            "strict_runtime":bool(gates.get("strict_managed_runtime_verified")),
            "elapsed_seconds":float(runtime.get("workflow_elapsed_seconds") or run.get("duration_seconds") or 0),
            "context_read_tokens":int(usage.get("context_read_tokens") or 0),"generated_tokens":int(usage.get("generated_tokens") or usage.get("output_tokens") or 0),"processed_tokens":int(usage.get("processed_tokens") or 0),
            "mcp_calls":int(runtime.get("mcp_calls") or 0),"reconnect_count":int(runtime.get("reconnect_count") or 0),
            "estimated_list_price_usd":cost.get("estimated_list_price_usd"),
        })
    # A child-only summary can also be ranked.
    if not rows and summary.get("provider"):
        usage=summary.get("token_usage") or {};runtime=summary.get("runtime_metrics") or {};route=summary.get("model_route") or {}
        rows.append({"source":source,"dynosai_version":summary.get("dynosai_version"),"provider":summary.get("provider"),"scenario":summary.get("scenario"),"model":route.get("model") or usage.get("model"),"effort":route.get("effort"),"passed":summary.get("status")=="passed","oracle_passed":bool((summary.get("oracle") or {}).get("passed")),"quality_score":int(summary.get("quality_score") or 0),"strict_runtime":bool((summary.get("gates") or {}).get("strict_managed_runtime_verified")),"elapsed_seconds":float(runtime.get("workflow_elapsed_seconds") or 0),"context_read_tokens":int(usage.get("context_read_tokens") or 0),"generated_tokens":int(usage.get("generated_tokens") or usage.get("output_tokens") or 0),"processed_tokens":int(usage.get("processed_tokens") or 0),"mcp_calls":int(runtime.get("mcp_calls") or 0),"reconnect_count":int(runtime.get("reconnect_count") or 0),"estimated_list_price_usd":((summary.get("cost_estimate") or usage.get("cost_estimate") or {}).get("estimated_list_price_usd"))})
    return rows


class ModelBenchmarkMatrix:
    """Rank existing real-provider runs without spending new model tokens."""
    @staticmethod
    def rank(paths:Iterable[str|Path])->dict[str,Any]:
        rows=[]
        for raw in paths:
            p=Path(raw).expanduser().resolve();rows.extend(process_rows(load_acceptance_summary(p),str(p)))
        if not rows:return {"rows":[],"ranked":[],"pareto":[]}
        successful=[r for r in rows if r["passed"] and r["oracle_passed"] and r["quality_score"]>=100 and r["strict_runtime"]]
        def metric(row,key,default=10**18):
            value=row.get(key)
            return default if value is None else float(value)
        ranked=sorted(successful,key=lambda r:(metric(r,"estimated_list_price_usd"),metric(r,"elapsed_seconds"),metric(r,"processed_tokens"),metric(r,"mcp_calls")))
        # Pareto: no other successful row is <= on cost/time/tokens with one strict improvement.
        pareto=[]
        for row in successful:
            dims=[metric(row,"estimated_list_price_usd"),metric(row,"elapsed_seconds"),metric(row,"processed_tokens")]
            dominated=False
            for other in successful:
                if other is row:continue
                od=[metric(other,"estimated_list_price_usd"),metric(other,"elapsed_seconds"),metric(other,"processed_tokens")]
                if all(a<=b for a,b in zip(od,dims)) and any(a<b for a,b in zip(od,dims)):
                    dominated=True;break
            if not dominated:pareto.append(row)
        return {"rows":rows,"successful_runs":len(successful),"ranked":ranked,"pareto":pareto,"ranking_policy":"quality/oracle/strict-runtime are hard gates; then minimize estimated list price, elapsed time, processed tokens and MCP calls","note":"Cross-model differences are descriptive. Use repeated representative runs before changing production routing."}
