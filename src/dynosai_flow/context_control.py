# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Context-pressure assessment and deterministic context strategy selection."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .model_routing import activity_for_state
from .util import utc_now


def _atomic(path: Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n"
    fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",dir=str(path.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as fh:
            fh.write(payload);fh.flush();os.fsync(fh.fileno())
        os.replace(tmp,path)
    finally:
        try:
            if os.path.exists(tmp):os.unlink(tmp)
        except OSError:pass


def _decode(value: Any, default: Any) -> Any:
    if isinstance(value,(list,dict)): return value
    try:return json.loads(value) if value not in (None,"") else default
    except Exception:return default


class EvidenceCache:
    """Provider-session read de-duplication with content-derived invalidation.

    First observation returns full evidence plus a stable reference. Asking for
    the same authoritative read again returns only the reference if the result is
    byte-for-byte unchanged. Any changed file/symbol payload naturally produces a
    new digest and therefore a full response.
    """
    def __init__(self):
        self._seen: dict[str,dict[str,str]]={}

    @staticmethod
    def _digest(value:Any)->tuple[str,str]:
        raw=json.dumps(value,ensure_ascii=False,sort_keys=True,default=str,separators=(",",":"))
        digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return digest,raw

    def compact(self,scope:str,key:str,value:Any)->tuple[Any,dict[str,Any]]:
        digest,raw=self._digest(value); cache=self._seen.setdefault(str(scope),{})
        ref=f"EVID-{digest}"
        previous=cache.get(key)
        if previous==digest:
            compact={"ref":ref,"unchanged":True,"cache_key":key}
            return compact,{"mode":"evidence_ref","ref":ref,"unchanged":True,"estimated_bytes_omitted":max(0,len(raw)-len(json.dumps(compact)))}
        cache[key]=digest
        if isinstance(value,dict):
            full=dict(value);full["_dynosai_evidence_ref"]=ref
        else:
            full={"value":value,"_dynosai_evidence_ref":ref}
        return full,{"mode":"evidence_full","ref":ref,"unchanged":False,"estimated_bytes_omitted":0}


class ContextCheckpointStore:
    """Compact structured workflow checkpoints at phase boundaries.

    A checkpoint is deliberately not a conversational summary. It contains only
    accepted/authoritative state needed by later phases: decisions, approved
    artifacts, task status, validations, touched files and unresolved governance
    signals. Providers that can start a fresh turn/session can use it as the
    primary resume context; long-running turns can still use it as a compact
    authoritative anchor.
    """
    def __init__(self,root:str|Path):
        self.root=Path(root).expanduser().resolve()
        self.base=self.root/".dynosai"/"runtime"/"context-checkpoints"

    def _engine_data(self,engine:Any,work_id:str)->dict[str,Any]:
        work=engine.get_work(work_id)
        decisions=[{"question":x.get("question"),"answer":x.get("answer")} for x in engine.db.query("SELECT question,answer FROM decisions WHERE work_id=? ORDER BY created_at",(work_id,))]
        spec=engine.artifacts.artifact(work_id,"spec")
        plan=engine.artifacts.artifact(work_id,"plan")
        tasks=[]
        for row in engine.db.query("SELECT id,title,state,requirements,acceptance,files,depends_on FROM tasks WHERE work_id=? ORDER BY id",(work_id,)):
            tasks.append({"id":row.get("id"),"title":row.get("title"),"state":row.get("state"),"requirements":_decode(row.get("requirements"),[]),"acceptance":_decode(row.get("acceptance"),[]),"files":_decode(row.get("files"),[]),"depends_on":_decode(row.get("depends_on"),[])})
        validations=[{"command":x.get("command"),"status":x.get("status"),"exit_code":x.get("exit_code")} for x in engine.db.query("SELECT command,status,exit_code FROM validations WHERE work_id=? ORDER BY id DESC LIMIT 8",(work_id,))]
        runs=[]
        for row in engine.db.query("SELECT id,status,summary,files_changed,verified_files,verification_status FROM runs WHERE work_id=? ORDER BY started_at DESC LIMIT 3",(work_id,)):
            runs.append({"id":row.get("id"),"status":row.get("status"),"summary":row.get("summary"),"files_changed":_decode(row.get("files_changed"),[]),"verified_files":_decode(row.get("verified_files"),[]),"verification_status":row.get("verification_status")})
        scope=[{"path":x.get("path"),"action":x.get("action"),"status":x.get("status")} for x in engine.db.query("SELECT path,action,status FROM scope_requests WHERE work_id=? ORDER BY created_at",(work_id,))]
        def compact_artifact(artifact:dict[str,Any]|None)->dict[str,Any]|None:
            if not artifact:return None
            meta=artifact.get("metadata") or {}
            if isinstance(meta,str):meta=_decode(meta,{})
            keys=("objective","scope","out_of_scope","requirements","acceptance_criteria","business_rules","unknowns","approach","architecture_delta","files","risks","validation_profiles","tasks")
            return {k:meta.get(k) for k in keys if meta.get(k) not in (None,[],{},"")}
        return {
            "work":{"id":work.get("id"),"title":work.get("title"),"description":work.get("description"),"state":work.get("state"),"quality_score":work.get("quality_score"),"workspace_strategy":work.get("workspace_strategy")},
            "activity":activity_for_state(work.get("state")),
            "decisions":decisions,
            "spec":compact_artifact(spec),
            "plan":compact_artifact(plan),
            "tasks":tasks,
            "validations":validations,
            "recent_runs":runs,
            "scope_requests":scope,
        }

    def capture(self,engine:Any,work_id:str,*,label:str|None=None,reason:str="phase_boundary")->dict[str,Any]:
        data=self._engine_data(engine,work_id); activity=data.get("activity") or "unknown"
        raw=json.dumps(data,ensure_ascii=False,sort_keys=True,default=str,separators=(",",":"))
        digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        existing=self.latest(work_id)
        if existing and existing.get("digest")==digest:
            ref=self.reference(work_id) or {}
            ref["reused"]=True
            ref["reason"]=reason
            return ref
        path=self.base/work_id/f"{label or activity}.json"
        value={
            "schema_version":2,"work_id":work_id,"activity":activity,"reason":reason,"created_at":utc_now(),
            "digest":digest,"estimated_tokens":max(1,len(raw)//4),
            "sections_available":["work","decisions","spec","plan","tasks","validations","recent_runs","scope_requests"],
            "data":data,
        }
        _atomic(path,value)
        latest=self.base/work_id/"latest.json";_atomic(latest,{**value,"path":str(path)})
        return self.reference(work_id) or {"ref":f"CTX-{digest}","activity":activity,"path":str(path),"estimated_tokens":value["estimated_tokens"],"created_at":value["created_at"],"reason":reason}

    def latest(self,work_id:str)->dict[str,Any]|None:
        path=self.base/work_id/"latest.json"
        try:
            value=json.loads(path.read_text(encoding="utf-8"));
            return value
        except Exception:return None

    def reference(self,work_id:str)->dict[str,Any]|None:
        """Return a small stable reference instead of replaying full checkpoint data."""
        latest=self.latest(work_id)
        if not latest:return None
        data=latest.get("data") or {}
        active_tasks=[]; files=[]
        for item in data.get("tasks") or []:
            state=str(item.get("state") or "")
            if state not in {"done","completed","verified"}:
                active_tasks.append({k:item.get(k) for k in ("id","title","state","files","depends_on")})
                files.extend(str(x) for x in (item.get("files") or []) if x)
        for run in data.get("recent_runs") or []:
            files.extend(str(x) for x in (run.get("verified_files") or run.get("files_changed") or []) if x)
        unique_files=[]
        for value in files:
            if value not in unique_files:unique_files.append(value)
        compact={
            "ref":f"CTX-{latest.get('digest')}",
            "work_id":work_id,
            "activity":latest.get("activity"),
            "reason":latest.get("reason"),
            "created_at":latest.get("created_at"),
            "path":latest.get("path"),
            "estimated_tokens":int(latest.get("estimated_tokens") or 0),
            "estimated_tokens_full":int(latest.get("estimated_tokens") or 0),
            "sections_available":latest.get("sections_available") or ["work","decisions","spec","plan","tasks","validations","recent_runs","scope_requests"],
            "active_tasks":active_tasks[:8],
            "relevant_files":unique_files[:20],
            "validation_status":[{k:x.get(k) for k in ("command","status","exit_code")} for x in (data.get("validations") or [])[:4]],
            "reuse_policy":"use_reference_first_load_only_required_sections",
        }
        compact_raw=json.dumps(compact,ensure_ascii=False,separators=(",",":"),default=str)
        full_raw=json.dumps(latest,ensure_ascii=False,separators=(",",":"),default=str)
        compact["estimated_bytes_full"]=len(full_raw)
        compact["estimated_bytes_reference"]=len(compact_raw)
        compact["estimated_bytes_avoided"]=max(0,len(full_raw)-len(compact_raw))
        compact["estimated_tokens_reference"]=max(1,len(compact_raw)//4)
        full=max(1,int(latest.get("estimated_tokens") or 1))
        compact["estimated_tokens_avoided"]=max(0,full-compact["estimated_tokens_reference"])
        return compact

    def load_sections(self,work_id:str,sections:list[str]|None=None)->dict[str,Any]:
        latest=self.latest(work_id)
        if not latest:raise ValueError(f"No context checkpoint exists for {work_id}")
        available=latest.get("sections_available") or ["work","decisions","spec","plan","tasks","validations","recent_runs","scope_requests"]
        requested=list(sections or ["work","tasks","validations"])
        invalid=[x for x in requested if x not in available]
        if invalid:raise ValueError("Unknown checkpoint sections: "+", ".join(invalid))
        data=latest.get("data") or {}
        selected={key:data.get(key) for key in requested}
        return {
            "ref":f"CTX-{latest.get('digest')}",
            "work_id":work_id,
            "activity":latest.get("activity"),
            "sections":requested,
            "data":selected,
            "retrieval_policy":"targeted_checkpoint_section_load",
        }

    def prompt_capsule(self,work_id:str,max_chars:int|None=None,activity:str|None=None)->str|None:
        """Return a phase-aware bounded authoritative resume capsule.

        0.12.x prefers the checkpoint reference and active execution state. Full
        accepted spec/plan data is not replayed into Implementation/Review/
        Validation/Merge unless explicitly loaded through DynosAI MCP.
        """
        latest=self.latest(work_id)
        if not latest:return None
        data=latest.get("data") or {}
        activity=str(activity or latest.get("activity") or data.get("activity") or "discovery")
        budgets={
            "discovery":9000,"specification":9000,"planning":7000,
            "implementation":5500,"code_review":4000,"validation":3500,"merge":2500,
        }
        if max_chars is None:max_chars=budgets.get(activity,5500)
        ref=self.reference(work_id) or {"ref":f"CTX-{latest.get('digest')}"}
        tasks=data.get("tasks") or []
        active=[x for x in tasks if str(x.get("state") or "") not in {"done","completed","verified"}]
        compact={
            "ref":ref.get("ref"),
            "activity":activity,
            "work":data.get("work"),
            "decisions":data.get("decisions") or [],
            "active_tasks":[{k:x.get(k) for k in ("id","title","state","files","depends_on","acceptance")} for x in active[:8]],
            "validations":data.get("validations") or [],
            "scope_requests":data.get("scope_requests") or [],
            "relevant_files":ref.get("relevant_files") or [],
            "checkpoint_sections":ref.get("sections_available") or [],
            "reuse_policy":"do_not_reload_accepted_context_unless_needed",
        }
        if activity in {"discovery","specification","planning"}:
            compact["spec"]=data.get("spec")
            compact["plan"]=data.get("plan") if activity=="planning" else None
        else:
            compact["accepted_artifacts"]={
                "spec":{"checkpoint_ref":compact["ref"],"load_section":"spec","omitted":"reuse_checkpoint"},
                "plan":{"checkpoint_ref":compact["ref"],"load_section":"plan","omitted":"reuse_checkpoint"},
            }
        raw=json.dumps(compact,ensure_ascii=False,separators=(",",":"),default=str)
        if len(raw)>max_chars:
            compact["decisions"]=(compact.get("decisions") or [])[-6:]
            compact["validations"]=(compact.get("validations") or [])[:3]
            raw=json.dumps(compact,ensure_ascii=False,separators=(",",":"),default=str)
        if len(raw)>max_chars:
            compact["active_tasks"]=[{k:x.get(k) for k in ("id","title","state","files")} for x in (compact.get("active_tasks") or [])]
            raw=json.dumps(compact,ensure_ascii=False,separators=(",",":"),default=str)
        if len(raw)>max_chars:
            raw=raw[:max_chars]+"…"
        return "DynosAI authoritative context checkpoint (reuse before reread):\n"+raw
