# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Hybrid textual, semantic, symbol, task, and evidence retrieval for bounded agent context."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .db import Database
from .semantic import SemanticIndex
from .policy import PathPolicyEngine
from .util import estimate_tokens, json_dumps, json_loads, overlap_score, tokens, utc_now, sha256_file


@dataclass(slots=True)
class ContextCandidate:
    entity_type: str; entity_id: str; title: str; body: str; reason: str; score: float; category: str
    def to_dict(self) -> dict[str, Any]: return asdict(self)

TYPE_WEIGHT={"rule":1.00,"feature":0.98,"requirement":0.97,"acceptance":0.97,"task":0.95,"symbol":0.94,"file":0.88,"decision":0.90,"spec":0.70,"plan":0.65,"baseline":0.25,"constitution":0.20}


class RetrievalEngine:
    """Top-K hybrid retrieval followed by graph expansion; never full-table scans per query."""
    def __init__(self, db: Database, semantic: SemanticIndex|None=None): self.db=db; self.semantic=semantic; self.policy=PathPolicyEngine(db.root)

    def search(self, query: str, limit: int=20, work_id: str|None=None) -> list[ContextCandidate]:
        candidates:dict[tuple[str,str],ContextCandidate]={}
        # Indexed lexical retrieval.
        for row in self.db.fts(query,limit=max(30,limit*4)):
            # FTS candidate generation uses OR semantics. Rerank by query coverage so a
            # project/package name present in every path cannot dominate a brownfield
            # request containing several more specific business/code terms.
            rank_quality=max(0.20,min(0.95,1/(1+abs(float(row.get("rank",1))))))
            coverage=overlap_score(query,f"{row['title']} {row['body'][:6000]}")
            raw=max(0.12,min(0.98,(0.28+0.70*coverage)*(0.86+0.14*rank_quality)))
            w=TYPE_WEIGHT.get(row["entity_type"],0.72)
            self._put(candidates,ContextCandidate(row["entity_type"],row["entity_id"],row["title"],row["body"],"Coincidencia textual FTS5 + cobertura de consulta",raw*w,"text"))
        # Indexed semantic retrieval.
        if self.semantic and self.semantic.ready():
            for hit in self.semantic.search(query,limit=max(30,limit*4)):
                w=TYPE_WEIGHT.get(hit.entity_type,0.72); score=(0.46+hit.score*0.52)*w
                self._put(candidates,ContextCandidate(hit.entity_type,hit.entity_id,hit.title,hit.body,f"Coincidencia semántica neuronal ({hit.model})",score,"semantic"))
        # Small exact/LIKE lookups are indexed by symbols.name/path and avoid SELECT *.
        needle=query.strip()
        if needle:
            q=f"%{needle[:120]}%"
            for row in self.db.query("SELECT id,path,name,qualified_name,signature,docstring,start_line,end_line FROM symbols WHERE name LIKE ? OR qualified_name LIKE ? ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END LIMIT ?",(q,q,needle,max(10,limit))):
                score=0.93 if row["name"].lower()==needle.lower() else 0.74
                self._put(candidates,ContextCandidate("symbol",row["id"],row.get("qualified_name") or row["name"],f"{row['path']}:{row['start_line']}-{row['end_line']}\n{row['signature']}\n{row['docstring']}","Coincidencia exacta de símbolo",score,"code"))
            for token in [x for x in needle.replace("\\","/").split() if len(x)>2][:3]:
                like=f"%{token}%"
                for row in self.db.query("SELECT path FROM code_files WHERE path LIKE ? LIMIT ?",(like,max(6,limit//2))):
                    self._put(candidates,ContextCandidate("file",row["path"],row["path"],"","Ruta indexada relacionada",0.58,"code"))
        # Prefer current/validated knowledge, demote broad governance docs and stale entities.
        result=sorted(candidates.values(),key=lambda x:x.score,reverse=True)[:limit]
        try:self.db.execute("INSERT INTO retrieval_events(query,work_id,returned_entities,returned_files,estimated_tokens,created_at) VALUES(?,?,?,?,?,?)",(query,work_id,json_dumps([[r.entity_type,r.entity_id] for r in result]),json_dumps([r.entity_id for r in result if r.entity_type=='file']),sum(estimate_tokens(r.body) for r in result),utc_now()))
        except Exception: pass
        return result

    def impact(self, query:str, limit:int=12, work_id:str|None=None)->dict[str,Any]:
        hits=self.search(query,limit=limit*3,work_id=work_id); files:dict[str,dict[str,Any]]={}; tests:dict[str,dict[str,Any]]={}; rules=[]; features=[]
        query_tokens=tokens(query); test_intent=bool(query_tokens & {"test","prueba","cobertura","cubr"})
        # A validated feature match is stronger evidence than a semantically similar
        # code symbol. Use its known implementation paths to condition graph expansion.
        feature_paths:set[str]=set(); matched_feature_ids:set[str]=set()
        def feature_ids_for_hit(hit:ContextCandidate)->list[str]:
            if hit.entity_type=="feature": return [hit.entity_id]
            if hit.entity_type=="rule" and hit.entity_id.startswith("FEATURE-"): return [hit.entity_id.split(":",1)[0]]
            work_id=None
            if hit.entity_type=="work": work_id=hit.entity_id
            elif hit.entity_type in {"spec","plan"}:
                row=self.db.one("SELECT work_id FROM artifacts WHERE id=?",(hit.entity_id,)); work_id=(row or {}).get("work_id")
            elif hit.entity_type=="task":
                row=self.db.one("SELECT work_id FROM tasks WHERE id=?",(hit.entity_id,)); work_id=(row or {}).get("work_id")
            elif hit.entity_type=="decision":
                row=self.db.one("SELECT work_id FROM decisions WHERE id=?",(hit.entity_id,)); work_id=(row or {}).get("work_id")
            if not work_id: return []
            return [row["id"] for row in self.db.query("SELECT id FROM features WHERE work_id=? AND status IN ('validated','current') ORDER BY updated_at DESC LIMIT 5",(work_id,))]
        feature_scores:dict[str,float]={}
        for hit in hits:
            for feature_id in feature_ids_for_hit(hit): feature_scores[feature_id]=max(feature_scores.get(feature_id,0.0),hit.score)
        for feature_id,feature_score in sorted(feature_scores.items(),key=lambda x:x[1],reverse=True):
            feature=self.db.one("SELECT * FROM features WHERE id=?",(feature_id,))
            if not feature: continue
            matched_feature_ids.add(feature_id)
            if not any(f["id"]==feature_id for f in features): features.append({"id":feature_id,"title":feature["title"],"score":round(feature_score,2),"status":feature["status"]})
            for r in self.db.query("SELECT rule FROM feature_rules WHERE feature_id=?",(feature_id,)):
                if r["rule"] not in rules: rules.append(r["rule"])
            for linked in self.db.query("SELECT * FROM feature_files WHERE feature_id=?",(feature_id,)):
                freshness=0.55 if linked["validity"]!="current" else 1.0; target=tests if linked["role"]=="test" or "test" in linked["path"].lower() else files
                clue=f"{linked['path']} {linked.get('symbol') or ''} {linked['reason']} {linked['role']}"
                lexical=overlap_score(query,clue)
                role_bonus=(0.07 if test_intent and target is tests else 0.05 if (not test_intent and target is files) else -0.03)
                confidence=min(1.0,max(0.0,float(linked["confidence"])*freshness*0.88 + lexical*0.18 + role_bonus))
                self._file(target,linked["path"],linked["role"],f"{linked['reason']} · evidencia validada + relevancia de consulta",confidence,linked.get("symbol"))
                if target is files:
                    feature_paths.add(linked["path"])
                    # Expand only structurally linked tests from the known feature file.
                    for sym in self.db.query("SELECT id FROM symbols WHERE path=? LIMIT 80",(linked["path"],)):
                        for tl in self.db.query("SELECT s.path,s.qualified_name,t.confidence FROM test_links t JOIN symbols s ON s.id=t.test_symbol WHERE t.target_symbol=? ORDER BY t.confidence DESC LIMIT 8",(sym["id"],)):
                            structural_test_conf=min(0.99 if test_intent else 0.70,float(tl.get("confidence") or 0.9))
                            self._file(tests,tl["path"],"test","Test enlazado a la implementación validada",structural_test_conf,tl.get("qualified_name"))
        for hit in hits:
            if hit.entity_type in {"feature","rule","work","spec","plan","task","decision"}:
                continue
            if hit.entity_type=="symbol":
                sym=self.db.one("SELECT * FROM symbols WHERE id=?",(hit.entity_id,))
                if sym:
                    # When a feature match exists, unrelated semantic symbols from the
                    # same broad domain are noise unless graph/feature evidence links them.
                    if feature_paths and hit.category=="semantic" and sym["path"] not in feature_paths:
                        continue
                    target=tests if "test" in sym["path"].lower() else files; direct_conf=hit.score if hit.category=="code" else hit.score*0.82
                    self._file(target,sym["path"],"review",hit.reason,direct_conf,sym.get("qualified_name") or sym["name"])
                    for tl in self.db.query("SELECT s.path,s.qualified_name FROM test_links t JOIN symbols s ON s.id=t.test_symbol WHERE t.target_symbol=? ORDER BY t.confidence DESC LIMIT 12",(sym["id"],)): self._file(tests,tl["path"],"test","Test enlazado al símbolo",0.97 if test_intent else 0.68,tl.get("qualified_name"))
                    for rel in self.db.query("SELECT s.path,s.qualified_name FROM call_edges c JOIN symbols s ON s.id=c.source_symbol WHERE c.target_symbol=? LIMIT 12",(sym["id"],)):
                        caller_target=tests if "test" in rel["path"].lower() else files
                        caller_role="test" if caller_target is tests else "review"
                        self._file(caller_target,rel["path"],caller_role,"Caller directo",0.70,rel.get("qualified_name"))
            elif hit.entity_type=="file":
                # Path hits remain useful, but are demoted when validated feature memory
                # already provides a narrower implementation footprint.
                conf=hit.score*(0.65 if feature_paths and hit.entity_id not in feature_paths else 1.0)
                self._file(tests if "test" in hit.entity_id.lower() else files,hit.entity_id,"review",hit.reason,conf,None)
        return {"query":query,"features":features[:5],"rules":rules[:10],"files":sorted(files.values(),key=lambda x:x["confidence"],reverse=True)[:limit],"tests":sorted(tests.values(),key=lambda x:x["confidence"],reverse=True)[:limit],"generated_at":utc_now()}

    def context_preview(self,work:dict[str,Any],max_tokens:int=3000,task_id:str|None=None,include_task_contract:bool=True)->dict[str,Any]:
        task=self.db.one("SELECT * FROM tasks WHERE id=? AND work_id=?",(task_id,work["id"])) if task_id else self._next_ready_task(work["id"])
        spec=self._decoded_artifact(work["id"],"spec"); plan=self._decoded_artifact(work["id"],"plan")
        # Implementation retrieval must follow the approved task/spec rather than the
        # original user sentence. Brownfield project/package names are often ubiquitous
        # and otherwise dominate FTS, causing irrelevant modules to displace the files
        # that actually implement the requirement.
        query_parts=[]
        if task:
            query_parts.extend([str(task.get("title") or ""),str(task.get("description") or "")])
            req_ids=set(json_loads(task.get("requirements"),[]))
            ac_ids=set(json_loads(task.get("acceptance"),[]))
        else:
            req_ids=set(); ac_ids=set()
        if spec:
            meta=spec["metadata"]
            query_parts.append(str(meta.get("objective") or ""))
            for req in meta.get("requirements",[]):
                if not req_ids or req.get("id") in req_ids: query_parts.append(str(req.get("text") or ""))
            for ac in meta.get("acceptance_criteria",[]):
                if not ac_ids or ac.get("id") in ac_ids: query_parts.append(str(ac.get("text") or ""))
            query_parts.extend(str(rule.get("text") or "") for rule in meta.get("business_rules",[])[:6])
        if plan: query_parts.append(str(plan["metadata"].get("approach") or ""))
        if not any(part.strip() for part in query_parts): query_parts=[str(work.get("title") or ""),str(work.get("description") or "")]
        query=" ".join(part.strip() for part in query_parts if part.strip())
        impact=self.impact(query,work_id=work["id"]); items=[]; used=0
        if task and include_task_contract:
            used=self._add(items,"task",task["id"],task["title"],task["description"],"Tarea actual",max_tokens,used)
            req_ids=set(json_loads(task.get("requirements"),[])); ac_ids=set(json_loads(task.get("acceptance"),[]))
            if spec:
                for req in spec["metadata"].get("requirements",[]):
                    if req["id"] in req_ids: used=self._add(items,"requirement",req["id"],req["id"],req["text"],"Requisito trazado",max_tokens,used)
                for ac in spec["metadata"].get("acceptance_criteria",[]):
                    if ac["id"] in ac_ids: used=self._add(items,"acceptance",ac["id"],ac["id"],ac["text"],"Criterio trazado",max_tokens,used)
                for rule in spec["metadata"].get("business_rules",[])[:6]: used=self._add(items,"rule",rule.get("id","RULE"),rule.get("id","Regla"),rule["text"],"Regla activa",max_tokens,used)
            if plan: used=self._add(items,"plan_delta",plan["id"],"Delta técnico",str(plan["metadata"].get("approach","")),"Enfoque aprobado",max_tokens,used)
            for path in json_loads(task.get("files"),[]): used=self._add(items,"file_hint",path,path,path,"Scope de tarea",max_tokens,used)
        for rule in impact["rules"][:4]: used=self._add(items,"existing_rule",rule[:50],"Regla existente",rule,"Memoria validada",max_tokens,used)
        for f in sorted([*impact["files"],*impact["tests"]],key=lambda x:x["confidence"],reverse=True)[:8]: used=self._add(items,"file_hint",f["path"],f["path"],f"{f['path']} — {f['reason']}",f["reason"],max_tokens,used)
        return {"work_id":work["id"],"task_id":task["id"] if task else None,"purpose":"implementation","context_mode":"full_task_context" if include_task_contract else "repository_delta_only","max_tokens":max_tokens,"estimated_tokens":used,"items":items,"excluded_summary":"Documentos completos, logs e historial no relacionado quedan fuera.","impact":impact}

    def find_symbol(self,query:str,limit:int=20,run_id:str|None=None)->list[dict[str,Any]]:
        q=f"%{query}%"; base=self.db.query("SELECT id,path,name,qualified_name,kind,start_line,end_line,signature,docstring FROM symbols WHERE name LIKE ? OR qualified_name LIKE ? OR signature LIKE ? ORDER BY CASE WHEN name=? THEN 0 ELSE 1 END,path,start_line LIMIT ?",(q,q,q,query,limit))
        if not run_id: return base
        needle=query.lower(); overlay=[]
        for row in self.db.query("SELECT path,symbols FROM run_overlays WHERE run_id=?",(run_id,)):
            for sym in json_loads(row.get("symbols"),[]):
                hay=" ".join(str(sym.get(k,"")) for k in ("name","qualified_name","signature")).lower()
                if needle in hay:
                    overlay.append({**sym,"path":row["path"],"overlay":True})
        merged={item["id"]:item for item in base}
        for item in overlay: merged[item["id"]]=item
        return sorted(merged.values(),key=lambda x:(0 if str(x.get("name",""))==query else 1,str(x.get("path","")),int(x.get("start_line",0))))[:limit]

    def get_symbol(self,symbol_id:str,include_body:bool=True,worktree:Path|None=None,run_id:str|None=None)->dict[str,Any]|None:
        row=self.db.one("SELECT * FROM symbols WHERE id=?",(symbol_id,))
        if row and worktree:
            overlay=self.db.one("SELECT symbols FROM run_overlays WHERE run_id=? AND path=?",(run_id,row["path"])) if run_id else None
            if overlay:
                for candidate in json_loads(overlay["symbols"],[]):
                    if candidate.get("id")==symbol_id: row={**row,**candidate,"overlay":True}; break
        if row and not include_body: row.pop("body",None)
        if row:
            row["callers"]=self.db.query("SELECT s.id,s.path,s.qualified_name FROM call_edges c JOIN symbols s ON s.id=c.source_symbol WHERE c.target_symbol=? LIMIT 30",(symbol_id,)); row["callees"]=self.db.query("SELECT c.target_name,c.target_symbol,s.path,s.qualified_name FROM call_edges c LEFT JOIN symbols s ON s.id=c.target_symbol WHERE c.source_symbol=? LIMIT 30",(symbol_id,)); row["tests"]=self.db.query("SELECT s.id,s.path,s.qualified_name,t.confidence,t.reason FROM test_links t JOIN symbols s ON s.id=t.test_symbol WHERE t.target_symbol=?",(symbol_id,))
        return row

    def file_slice(self,path:str,start_line:int,end_line:int,root:Path|None=None)->dict[str,Any]:
        base=(root or self.db.root).resolve(); policy=PathPolicyEngine(base); full=policy.require(path,"read",agent=True)
        # Planned create-scope files legitimately do not exist yet. Treat that as data, not
        # as an MCP failure, so agents can proceed without noisy exception/retry loops.
        if not full.exists():
            return {"path":path,"exists":False,"state":"not_created_yet","start_line":max(1,start_line),"end_line":0,"content":""}
        if not full.is_file():
            return {"path":path,"exists":True,"state":"not_a_file","start_line":0,"end_line":0,"content":""}
        lines=full.read_text(encoding="utf-8",errors="replace").splitlines(); start=max(1,start_line); end=min(len(lines),max(start,end_line)); return {"path":path,"exists":True,"state":"present","start_line":start,"end_line":end,"content":"\n".join(lines[start-1:end])}

    def _decoded_artifact(self,work_id:str,kind:str)->dict[str,Any]|None:
        row=self.db.one("SELECT * FROM artifacts WHERE work_id=? AND kind=? ORDER BY version DESC LIMIT 1",(work_id,kind));
        if row: row["metadata"]=json_loads(row.get("metadata"),{})
        return row
    def _next_ready_task(self,work_id:str)->dict[str,Any]|None:
        for t in self.db.query("SELECT * FROM tasks WHERE work_id=? AND state!='completed' ORDER BY id",(work_id,)):
            deps=json_loads(t.get("depends_on"),[])
            if all((self.db.one("SELECT state FROM tasks WHERE id=?",(d,)) or {}).get("state")=="completed" for d in deps): return t
        return None
    @staticmethod
    def _add(items:list[dict[str,Any]],typ:str,eid:str,title:str,content:str,reason:str,max_tokens:int,used:int)->int:
        cost=estimate_tokens(content)
        if used+cost<=max_tokens: items.append({"type":typ,"id":eid,"title":title,"content":content,"reason":reason,"tokens":cost}); return used+cost
        return used
    @staticmethod
    def _put(target:dict[tuple[str,str],ContextCandidate],item:ContextCandidate)->None:
        key=(item.entity_type,item.entity_id); current=target.get(key)
        if current is None or item.score>current.score: target[key]=item
    @staticmethod
    def _file(target:dict[str,dict[str,Any]],path:str,role:str,reason:str,confidence:float,symbol:str|None)->None:
        item={"path":path,"role":role,"reason":reason,"confidence":round(confidence,2),"symbol":symbol}; current=target.get(path)
        if current is None or confidence>current["confidence"]: target[path]=item
