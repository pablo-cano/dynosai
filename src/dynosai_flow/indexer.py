# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Repository code indexing for files, symbols, tests, and structural relationships."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .db import Database
from .util import iter_source_files, sha256_file, utc_now, json_dumps
from .policy import PathPolicyEngine

LANGUAGE_BY_SUFFIX = {
    ".py": "python", ".rs": "rust", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".java": "java", ".cs": "csharp", ".go": "go",
    ".sql": "sql",
}


class CodeIndexer:
    """Incremental code intelligence index with stable symbol identities.

    Python uses AST for symbols, nesting, calls, imports, routes and test links.
    Other languages use tree-sitter-language-pack when the optional ``codegraph``
    extra is installed, with a deterministic regex fallback so brownfield adoption
    never blocks.
    """

    def __init__(self, root: str | Path, db: Database):
        self.root = Path(root).resolve(); self.db = db

    def index(self, commit: str) -> dict[str, int]:
        files_seen: set[str] = set(); changed = symbols_total = calls_total = routes_total = 0
        changed_paths: list[str] = []
        tracked = self._source_inventory()
        for path in tracked:
            rel = path.relative_to(self.root).as_posix(); files_seen.add(rel)
            content_hash = sha256_file(path)
            existing = self.db.one("SELECT content_hash FROM code_files WHERE path=?", (rel,))
            if existing and existing["content_hash"] == content_hash:
                continue
            changed += 1; changed_paths.append(rel)
            text = path.read_text(encoding="utf-8", errors="replace")
            language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower(), "unknown")
            self.db.execute(
                "INSERT INTO code_files(path,language,content_hash,size,indexed_commit,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET language=excluded.language,content_hash=excluded.content_hash,size=excluded.size,indexed_commit=excluded.indexed_commit,updated_at=excluded.updated_at",
                (rel, language, content_hash, path.stat().st_size, commit, utc_now()),
            )
            old_ids = [r["id"] for r in self.db.query("SELECT id FROM symbols WHERE path=?", (rel,))]
            for sid in old_ids:
                self.db.execute("DELETE FROM call_edges WHERE source_symbol=? OR target_symbol=?", (sid, sid))
                self.db.execute("DELETE FROM test_links WHERE test_symbol=? OR target_symbol=?", (sid, sid))
            self.db.execute("DELETE FROM routes WHERE file_path=?", (rel,))
            self.db.execute("DELETE FROM symbols WHERE path=?", (rel,)); self.db.execute("DELETE FROM imports WHERE path=?", (rel,))
            if language == "python":
                extracted = self._python(rel, text)
                parser_backend = "python-ast"
            elif language == "sql":
                # SQL migrations are first-class retrieval context, but
                # they do not need a full call/symbol graph. Keep indexing cheap
                # and deterministic while still placing the complete migration
                # text in FTS/semantic file context.
                extracted = self._generic(rel, text, language)
                parser_backend = "sql-text"
            else:
                extracted = self._tree_sitter(rel, text, language)
                if extracted is not None:
                    parser_backend = "tree-sitter"
                else:
                    extracted = self._generic(rel, text, language)
                    parser_backend = "regex-fallback"
            self.db.set_meta(f"parser_backend:{language}", parser_backend)
            for item in extracted["symbols"]:
                self.db.execute(
                    "INSERT OR REPLACE INTO symbols(id,path,name,kind,start_line,end_line,signature,docstring,body,qualified_name,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (item["id"], rel, item["name"], item["kind"], item["start_line"], item["end_line"], item["signature"], item["docstring"], item["body"], item["qualified_name"], item.get("parent_id")),
                )
                self.db.index_search("symbol", item["id"], item["qualified_name"], f"{item['signature']}\n{item['docstring']}\n{item['body'][:2500]}")
                symbols_total += 1
            for module in extracted["imports"]:
                if module: self.db.execute("INSERT OR IGNORE INTO imports(path,module) VALUES(?,?)", (rel, module))
            for edge in extracted["calls"]:
                self.db.execute("INSERT OR IGNORE INTO call_edges(source_symbol,target_name,target_symbol,path,line) VALUES(?,?,?,?,?)", (edge["source_symbol"], edge["target_name"], None, rel, edge["line"]))
                calls_total += 1
            for route in extracted["routes"]:
                self.db.execute("INSERT OR REPLACE INTO routes(id,path,method,symbol_id,file_path,line) VALUES(?,?,?,?,?,?)", (route["id"], route["path"], route["method"], route["symbol_id"], rel, route["line"]))
                routes_total += 1
            self.db.index_search("file", rel, rel, text[:12000])

        for row in self.db.query("SELECT path FROM code_files"):
            if row["path"] not in files_seen:
                self.db.execute("DELETE FROM code_files WHERE path=?", (row["path"],))
        self._resolve_calls_and_tests()
        self.db.set_meta("indexed_commit", commit)
        self.db.audit("CodeIndexed", None, {"commit": commit, "files_changed": changed, "symbols": symbols_total, "calls": calls_total, "routes": routes_total})
        return {"files_changed": changed, "symbols": symbols_total, "calls": calls_total, "routes": routes_total, "files_total": len(files_seen), "changed_paths": changed_paths}

    def _source_inventory(self) -> list[Path]:
        """Prefer Git tracked/untracked source files over filesystem-wide walks."""
        import subprocess
        try:
            proc=subprocess.run(["git","ls-files","--cached","--others","--exclude-standard"],cwd=self.root,text=True,capture_output=True,timeout=20)
            if proc.returncode==0:
                paths=[]
                policy=PathPolicyEngine(self.root)
                for rel in proc.stdout.splitlines():
                    path=self.root/rel
                    if path.is_file() and path.suffix.lower() in LANGUAGE_BY_SUFFIX and policy.decision(rel,"read",agent=True).allowed:
                        paths.append(path)
                return sorted(set(paths))
        except Exception:
            pass
        return list(iter_source_files(self.root))

    def extract_file(self, root: Path, rel: str) -> dict[str, list[Any]]:
        path=(root/rel).resolve()
        text=path.read_text(encoding="utf-8",errors="replace")
        language=LANGUAGE_BY_SUFFIX.get(path.suffix.lower(),"unknown")
        if language=="python": return self._python(rel,text)
        if language=="sql": return self._generic(rel,text,language)
        extracted=self._tree_sitter(rel,text,language)
        return extracted if extracted is not None else self._generic(rel,text,language)

    def refresh_overlay(self, run_id: str, worktree: Path, paths: list[str]) -> dict[str, Any]:
        from .util import sha256_file
        indexed=0
        for rel in paths:
            path=worktree/rel
            if not path.exists() or path.suffix.lower() not in LANGUAGE_BY_SUFFIX: continue
            extracted=self.extract_file(worktree,rel)
            self.db.execute("INSERT INTO run_overlays(run_id,path,content_hash,symbols,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(run_id,path) DO UPDATE SET content_hash=excluded.content_hash,symbols=excluded.symbols,updated_at=excluded.updated_at",(run_id,rel,sha256_file(path),json_dumps(extracted.get("symbols",[])),utc_now()))
            indexed+=1
        return {"run_id":run_id,"files":indexed}

    def _python(self, rel: str, text: str) -> dict[str, list[Any]]:
        lines = text.splitlines(); symbols: list[dict[str, Any]]=[]; imports: list[str]=[]; calls: list[dict[str, Any]]=[]; routes: list[dict[str, Any]]=[]
        try: tree = ast.parse(text)
        except SyntaxError: return {"symbols": symbols, "imports": imports, "calls": calls, "routes": routes}
        module = rel[:-3].replace("/", ".") if rel.endswith(".py") else rel.replace("/", ".")

        class Visitor(ast.NodeVisitor):
            def __init__(self, outer: CodeIndexer): self.stack: list[tuple[str,str,str]]=[]; self.outer=outer
            def visit_Import(self, node: ast.Import): imports.extend(a.name for a in node.names)
            def visit_ImportFrom(self, node: ast.ImportFrom): imports.append(node.module or "")
            def _symbol(self, node: ast.AST, name: str, kind: str, args: list[str]|None=None):
                qual = ".".join([q for q,_,_ in self.stack] + [name]); stable=f"python://{module}/{qual}"
                end=getattr(node,"end_lineno",getattr(node,"lineno",1)); start=getattr(node,"lineno",1)
                body="\n".join(lines[start-1:end]); parent=self.stack[-1][1] if self.stack else None
                sig=f"class {name}" if kind=="class" else f"{name}({', '.join(args or [])})"
                item={"id":stable,"qualified_name":qual,"name":name,"kind":kind,"start_line":start,"end_line":end,"signature":sig,"docstring":ast.get_docstring(node) or "","body":body,"parent_id":parent}
                symbols.append(item)
                # Common Python web decorators: @app.get('/x'), @router.post('/x'), @route('/x', methods=['GET'])
                for dec in getattr(node,"decorator_list",[]):
                    if not isinstance(dec, ast.Call): continue
                    fn=self.outer._call_name(dec.func); method=""
                    low=fn.lower()
                    for m in ("get","post","put","patch","delete","options","head"):
                        if low.endswith("."+m) or low==m: method=m.upper(); break
                    if not method and low.endswith("route"): method="ANY"
                    if method and dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value,str):
                        route_path=dec.args[0].value; rid=f"route://{method}/{route_path}::{stable}"
                        routes.append({"id":rid,"path":route_path,"method":method,"symbol_id":stable,"line":start})
                self.stack.append((name,stable,kind)); self.generic_visit(node); self.stack.pop()
            def visit_ClassDef(self,node:ast.ClassDef): self._symbol(node,node.name,"class")
            def visit_FunctionDef(self,node:ast.FunctionDef): self._symbol(node,node.name,"method" if self.stack and self.stack[-1][2]=="class" else "function",[a.arg for a in node.args.args])
            def visit_AsyncFunctionDef(self,node:ast.AsyncFunctionDef): self.visit_FunctionDef(node)  # type: ignore[arg-type]
            def visit_Call(self,node:ast.Call):
                if self.stack:
                    calls.append({"source_symbol":self.stack[-1][1],"target_name":self.outer._call_name(node.func),"line":getattr(node,"lineno",0)})
                self.generic_visit(node)
        Visitor(self).visit(tree)
        return {"symbols":symbols,"imports":sorted(set(imports)),"calls":calls,"routes":routes}

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute):
            base=CodeIndexer._call_name(node.value)
            return f"{base}.{node.attr}" if base else node.attr
        return ""

    def _tree_sitter(self, rel: str, text: str, language: str) -> dict[str, list[Any]] | None:
        """Best-effort structural extraction via the optional tree-sitter pack.

        The method intentionally degrades to ``None`` on an unavailable grammar or
        parser API mismatch; brownfield adoption must remain usable without the
        optional dependency.
        """
        try:
            from tree_sitter_language_pack import get_parser  # type: ignore
        except Exception:
            return None
        language_names = {
            "typescript": ["tsx" if rel.endswith(".tsx") else "typescript", "typescript"],
            "javascript": ["javascript"], "rust": ["rust"], "java": ["java"],
            "csharp": ["c_sharp", "csharp"], "go": ["go"],
        }
        parser = None
        for name in language_names.get(language, [language]):
            try:
                parser = get_parser(name)
                if parser is not None:
                    break
            except Exception:
                continue
        if parser is None:
            return None
        source = text.encode("utf-8")
        try:
            tree = parser.parse(source); root = tree.root_node
        except Exception:
            return None
        symbols: list[dict[str, Any]]=[]; imports: list[str]=[]; calls: list[dict[str, Any]]=[]; routes: list[dict[str, Any]]=[]
        module=rel.replace("/", ".")
        symbol_types={"class_declaration":"class","class_definition":"class","interface_declaration":"interface","struct_item":"struct","struct_declaration":"struct","function_declaration":"function","function_definition":"function","function_item":"function","method_definition":"method","method_declaration":"method","method_definition_signature":"method"}
        call_types={"call_expression","call","invocation_expression"}; import_types={"import_statement","import_declaration","use_declaration","use_item"}
        def node_text(node: Any)->str:
            try: return source[node.start_byte:node.end_byte].decode("utf-8",errors="replace")
            except Exception: return ""
        def name_for(node: Any)->str:
            try:
                child=node.child_by_field_name("name")
                if child is not None: return node_text(child).strip()
            except Exception: pass
            try:
                for child in node.named_children:
                    if child.type in {"identifier","type_identifier","property_identifier"}: return node_text(child).strip()
            except Exception: pass
            return ""
        stack: list[tuple[str,str,str]]=[]
        def visit(node: Any)->None:
            ntype=getattr(node,"type",""); pushed=False
            if ntype in symbol_types:
                name=name_for(node)
                if name:
                    qual=".".join([x[0] for x in stack]+[name]); sid=f"{language}://{module}/{qual}"
                    start=int(getattr(node,"start_point",(0,0))[0])+1; end=int(getattr(node,"end_point",(start-1,0))[0])+1
                    body=node_text(node); signature=body.splitlines()[0].strip()[:500] if body else name
                    symbols.append({"id":sid,"qualified_name":qual,"name":name,"kind":symbol_types[ntype],"start_line":start,"end_line":end,"signature":signature,"docstring":"","body":body[:20000],"parent_id":stack[-1][1] if stack else None})
                    stack.append((name,sid,symbol_types[ntype])); pushed=True
            elif ntype in import_types:
                raw=node_text(node); m=re.search(r"(?:from\s+|import\s+|require\s*\(|use\s+)(?:['\"])?([\w./:@-]+)",raw)
                if m: imports.append(m.group(1))
            elif ntype in call_types and stack:
                target=""
                try:
                    fn=node.child_by_field_name("function") or node.child_by_field_name("name")
                    if fn is not None: target=node_text(fn).strip()
                except Exception: pass
                if target: calls.append({"source_symbol":stack[-1][1],"target_name":target,"line":int(getattr(node,"start_point",(0,0))[0])+1})
            try: children=node.named_children
            except Exception: children=[]
            for child in children: visit(child)
            if pushed: stack.pop()
        visit(root)
        return {"symbols":symbols,"imports":sorted(set(imports)),"calls":calls,"routes":routes}

    def _generic(self, rel: str, text: str, language: str) -> dict[str, list[Any]]:
        symbols=[]; imports=[]
        patterns = [
            ("class", re.compile(r"^\s*(?:export\s+)?(?:public\s+)?(?:class|interface|struct)\s+([A-Za-z_$][\w$]*)")),
            ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+)?([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:\{|=>)")),
        ]
        lines=text.splitlines()
        for lineno,line in enumerate(lines,1):
            for kind,pattern in patterns:
                m=pattern.search(line)
                if m:
                    name=m.group(1); module=rel.replace("/", "."); sid=f"{language}://{module}/{name}"
                    symbols.append({"id":sid,"qualified_name":name,"name":name,"kind":kind,"start_line":lineno,"end_line":lineno,"signature":line.strip()[:400],"docstring":"","body":line.strip(),"parent_id":None}); break
            imp=re.search(r"(?:from|import|require\()\s*['\"]?([\w./@-]+)",line)
            if imp: imports.append(imp.group(1))
        return {"symbols":symbols,"imports":sorted(set(imports)),"calls":[],"routes":[]}

    def _resolve_calls_and_tests(self) -> None:
        """Resolve call/test relations without O(tests × symbols) scans.

        Resolution is deliberately conservative: ambiguous call targets remain
        unresolved rather than inventing graph edges. Test links primarily reuse
        resolved call edges and only fall back to identifier references in each
        test body.
        """
        symbols=self.db.query("SELECT id,name,qualified_name,path,parent_id,body FROM symbols")
        by_name: dict[str,list[dict[str,Any]]]={}
        by_id={s["id"]:s for s in symbols}
        for sym in symbols: by_name.setdefault(sym["name"],[]).append(sym)

        edges=self.db.query("SELECT c.id,c.source_symbol,c.target_name,c.path,s.parent_id source_parent,s.qualified_name source_qualified FROM call_edges c LEFT JOIN symbols s ON s.id=c.source_symbol WHERE c.target_symbol IS NULL OR c.target_symbol='' ")
        for edge in edges:
            target=str(edge.get("target_name") or ""); short=target.split(".")[-1]
            candidates=by_name.get(short,[])
            if not candidates: continue
            ranked=[]
            for candidate in candidates:
                score=0
                qual=str(candidate.get("qualified_name") or "")
                # Explicit receiver/class names are much stronger than basename matches.
                if "." in target and (qual.endswith(target) or qual.endswith(target.replace("self.",""))): score+=6
                if candidate["path"]==edge["path"]: score+=4
                elif Path(candidate["path"]).parent==Path(edge["path"]).parent: score+=2
                if edge.get("source_parent") and candidate.get("parent_id")==edge.get("source_parent"): score+=5
                if target.startswith("self.") and candidate.get("parent_id")==edge.get("source_parent"): score+=5
                ranked.append((score,candidate))
            ranked.sort(key=lambda x:(-x[0],x[1]["id"]))
            if not ranked: continue
            # Refuse ambiguous guesses. A zero-score single candidate is acceptable;
            # ties at the best score remain unresolved.
            top_score=ranked[0][0]
            if len(ranked)>1 and ranked[1][0]==top_score: continue
            self.db.execute("UPDATE call_edges SET target_symbol=? WHERE id=?",(ranked[0][1]["id"],edge["id"]))

        self.db.execute("DELETE FROM test_links")
        tests=[s for s in symbols if "test" in s["path"].lower() or s["name"].lower().startswith("test")]
        production_ids={s["id"] for s in symbols if s not in tests}
        for test in tests:
            linked:set[str]=set()
            # Strongest signal: a resolved call made by the test symbol.
            for edge in self.db.query("SELECT target_symbol FROM call_edges WHERE source_symbol=? AND target_symbol IS NOT NULL AND target_symbol!=''",(test["id"],)):
                target=edge["target_symbol"]
                if target in production_ids:
                    self.db.execute("INSERT OR IGNORE INTO test_links(test_symbol,target_symbol,confidence,reason) VALUES(?,?,?,?)",(test["id"],target,1.0,"El test llama directamente al símbolo")); linked.add(target)
            # Fallback: tokenize this test only and consult the name index. No full
            # production-symbol loop is performed.
            words=set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b",str(test.get("body") or "")))
            for word in words:
                for prod in by_name.get(word,[]):
                    if prod["id"] in production_ids and prod["id"] not in linked:
                        self.db.execute("INSERT OR IGNORE INTO test_links(test_symbol,target_symbol,confidence,reason) VALUES(?,?,?,?)",(test["id"],prod["id"],0.88,"El cuerpo del test referencia el símbolo")); linked.add(prod["id"])
