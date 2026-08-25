# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Offline benchmark helpers for comparing completed acceptance evidence."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from .retrieval import RetrievalEngine


class RetrievalBenchmark:
    def __init__(self, retrieval: RetrievalEngine): self.retrieval=retrieval

    def run(self, cases: list[dict[str, Any]], k: int = 5) -> dict[str, Any]:
        rows=[]; precision=recall=mrr=0.0; latencies=[]; candidate_counts=[]
        for case in cases:
            query=case["query"]; expected=set(case.get("expected_files",[]))
            started=time.perf_counter(); impact=self.retrieval.impact(query,limit=max(k,10)); elapsed_ms=(time.perf_counter()-started)*1000.0
            latencies.append(elapsed_ms)
            ranked=sorted([*impact.get("files",[]),*impact.get("tests",[])],key=lambda x:float(x.get("confidence",0)),reverse=True)
            candidate_counts.append(len(ranked))
            predicted=[]
            for item in ranked:
                if item["path"] not in predicted: predicted.append(item["path"])
                if len(predicted)>=k: break
            predicted_set=set(predicted)
            hits=len(expected & predicted_set); p=hits/max(1,len(predicted)); r=hits/max(1,len(expected)); rank=0
            for i,path in enumerate(predicted,1):
                if path in expected: rank=i; break
            rr=1.0/rank if rank else 0.0; precision+=p; recall+=r; mrr+=rr
            rows.append({"query":query,"expected_files":sorted(expected),"predicted_files":predicted,"precision_at_k":round(p,3),"recall_at_k":round(r,3),"reciprocal_rank":round(rr,3),"latency_ms":round(elapsed_ms,3),"candidates":len(ranked)})
        n=max(1,len(cases)); ordered=sorted(latencies)
        def pct(values:list[float], percentile:float)->float:
            if not values:return 0.0
            index=max(0,min(len(values)-1,math.ceil(percentile*len(values))-1)); return values[index]
        return {"cases":len(cases),"k":k,"precision_at_k":round(precision/n,3),"recall_at_k":round(recall/n,3),"mrr":round(mrr/n,3),"latency_ms":{"p50":round(pct(ordered,0.50),3),"p95":round(pct(ordered,0.95),3),"max":round(max(ordered) if ordered else 0.0,3)},"candidate_count":{"avg":round(sum(candidate_counts)/max(1,len(candidate_counts)),2),"max":max(candidate_counts) if candidate_counts else 0},"results":rows}

    @staticmethod
    def load(path: str | Path) -> list[dict[str, Any]]:
        data=json.loads(Path(path).read_text(encoding="utf-8")); return data["cases"] if isinstance(data,dict) else data
