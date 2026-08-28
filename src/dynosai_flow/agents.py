# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Managed-agent instructions, skills, rules, and provider integration helpers."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .db import Database
from .util import json_dumps, utc_now
from .runtime import RuntimeBroker, CURSOR_CONFIG_VERSION, user_home, runtime_dir
from .agent_config import AgentConfiguration


START = "<!-- DYNOSAI:START -->"
END = "<!-- DYNOSAI:END -->"
PROTOCOL = """# DynosAI Provider-Native Protocol

Este proyecto está gobernado por DynosAI. El agente escribe código; DynosAI Core gobierna workflow, memoria, scope, Git, validación y evidencia.

1. Para una necesidad funcional usa el MCP de DynosAI; no ejecutes `dynosai open` ni cambies de aplicación.
2. Una feature usa una única sesión persistente del proveedor desde Discovery hasta Done.
3. Consulta `dynosai_get_next_action`; su `contract` es autoritativo.
4. Las decisiones humanas (clarificaciones, Spec Review, Plan Review, scope, Code Review y Merge) se resuelven mediante MCP Elicitation. Nunca autoapruebes un gate.
5. No escribas código antes de que el estado sea `implementing`. Durante implementación respeta estrictamente el scope devuelto por DynosAI.
6. Usa `dynosai_submit_spec`, `dynosai_submit_plan` y `dynosai_register_result`; DynosAI valida cada contrato y evidencia.
7. Para contexto: reutiliza primero `context_checkpoint` y el `task_queue` actual; carga sólo secciones faltantes con `dynosai_checkpoint`. Usa `dynosai_ask` para memoria/SQL/texto, `dynosai_find_symbol` para identificadores y `dynosai_read` sólo para contenido concreto que aún no esté consolidado.
8. Git de escritura pertenece al controlador DynosAI; el agente no hace branch/commit/merge por su cuenta.
9. Las validaciones se ejecutan mediante `dynosai_run_validation` y perfiles aprobados.
10. Si la sesión del proveedor se reinicia, usa `dynosai_resume`; la fuente de verdad es `.dynosai/knowledge.db`, no el historial del chat.
11. Los Markdown son vistas regenerables.
"""

COMMANDS = {
    "dynosai": "Comprueba el estado DynosAI del proyecto mediante MCP. Si el proyecto no está inicializado, detecta el directorio y usa dynosai_project. Si hay una feature activa, usa dynosai_resume.",
    "dynosai-feature": "Inicia una feature dentro de esta misma sesión usando dynosai_work action=start. Sigue dynosai_get_next_action y usa MCP Elicitation para todas las decisiones humanas; no cierres ni relances el proveedor.",
    "dynosai-status": "Consulta el estado autoritativo con dynosai_work action=status y resume brevemente fase, gate pendiente, tarea actual y evidencias.",
    "dynosai-resume": "Recupera la feature activa con dynosai_resume y continúa desde el contrato autoritativo sin reconstruir el estado a partir del chat.",
}

CODEX_SKILL = """---
name: dynosai
description: Execute product-development work through the DynosAI governed SDD workflow. Use for initializing/adopting a project, starting or resuming a feature, reviewing status, or continuing a DynosAI work item.
---

# DynosAI

Use the DynosAI MCP server as the only workflow authority.

- Initialize/adopt/attach with `dynosai_project`.
- Start features with `dynosai_work` action `start`.
- Resume with `dynosai_resume`.
- Follow `dynosai_get_next_action` until a human interaction is required. If it returns an unchanged action/contract ref, reuse the prior authoritative action instead of polling again until repository/task state changes.
- Human decisions are MCP Elicitation requests. Never self-approve.
- Do not write code before implementation authorization.
- Reuse `task_queue` and checkpoint references before reading approved spec/plan again. If task_queue.execution_wave contains dependent tasks, implement the full wave and register it atomically in execution_wave.registration_order; do not stop after the first task.
- Never write Git directly.
- Keep the same Codex thread/session for the whole feature whenever possible.
- If the Codex thread is restarted, recover from DynosAI rather than chat history.

Always respect the repository AGENTS.md.
"""


def _managed_markdown(path: Path, content: str) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    block = f"{START}\n{content.rstrip()}\n{END}"
    if START in current and END in current:
        before, rest = current.split(START, 1)
        _, after = rest.split(END, 1)
        merged = before.rstrip() + ("\n\n" if before.strip() else "") + block + ("\n\n" + after.lstrip() if after.strip() else "\n")
    else:
        merged = current.rstrip() + ("\n\n" if current.strip() else "") + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(merged, encoding="utf-8")


def _merge_json(path: Path, updater) -> None:
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict): data = loaded
        except Exception as exc:
            raise ValueError(f"Refusing to overwrite invalid JSON config {path}: {exc}") from exc
    updater(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _merge_toml_dynosai(path: Path) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = current.splitlines(); out: list[str] = []; skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[mcp_servers.dynosai") and stripped.endswith("]"):
            skipping = True
            continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping = False
        if not skipping:
            out.append(line)
    while out and not out[-1].strip(): out.pop()
    if out: out.append("")
    out.extend(["# DYNOSAI managed MCP server", "[mcp_servers.dynosai]", f"command = {json.dumps(str(Path(sys.executable).absolute()))}", 'args = ["-m", "dynosai_flow.mcp"]', 'env = { DYNOSAI_AGENT_PROVIDER = "codex", DYNOSAI_RUNTIME_BROKER = "1" }', "enabled = true", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")


def _remove_managed_markdown(path: Path) -> None:
    if not path.exists(): return
    current=path.read_text(encoding="utf-8")
    if START not in current or END not in current: return
    before,rest=current.split(START,1); _,after=rest.split(END,1)
    merged=(before.rstrip()+("\n\n" if before.strip() and after.strip() else "")+after.lstrip()).rstrip()+"\n"
    if merged.strip(): path.write_text(merged,encoding="utf-8")
    else: path.unlink()

def _remove_json_server(path: Path) -> None:
    if not path.exists(): return
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except Exception: return
    servers=data.get("mcpServers")
    if isinstance(servers,dict): servers.pop("dynosai",None)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def _cursor_permission_manifest_path() -> Path:
    return runtime_dir() / "cursor-managed-permissions.json"


def _load_cursor_permission_manifest() -> dict[str, Any]:
    path=_cursor_permission_manifest_path()
    if not path.exists(): return {"configVersion":CURSOR_CONFIG_VERSION,"files":{}}
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data,dict): raise ValueError("manifest must be object")
    except Exception:
        data={"configVersion":CURSOR_CONFIG_VERSION,"files":{}}
    data["configVersion"]=CURSOR_CONFIG_VERSION
    if not isinstance(data.get("files"),dict): data["files"]={}
    return data


def _save_cursor_permission_manifest(data: dict[str,Any]) -> None:
    path=_cursor_permission_manifest_path(); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def _record_managed_permission(path: Path, bucket: str, token: str) -> None:
    manifest=_load_cursor_permission_manifest(); key=str(path.resolve())
    entry=manifest["files"].setdefault(key,{"allow":[],"deny":[]})
    items=entry.setdefault(bucket,[])
    if token not in items: items.append(token)
    _save_cursor_permission_manifest(manifest)


def _add_managed_permission(data: dict[str,Any], bucket: str, token: str, path: Path) -> None:
    perms=data.setdefault("permissions",{})
    items=perms.get(bucket,[]); items=items if isinstance(items,list) else []
    if token not in items:
        items.append(token)
        _record_managed_permission(path,bucket,token)
    perms[bucket]=items


def _remove_dynosai_permissions(path: Path) -> None:
    if not path.exists(): return
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except Exception: return
    manifest=_load_cursor_permission_manifest(); key=str(path.resolve())
    managed=manifest.get("files",{}).get(key,{}) if isinstance(manifest.get("files"),dict) else {}
    perms=data.get("permissions") if isinstance(data.get("permissions"),dict) else {}
    for bucket in ("allow","deny"):
        owned=set(managed.get(bucket,[]) if isinstance(managed.get(bucket),list) else [])
        items=perms.get(bucket,[]) if isinstance(perms.get(bucket),list) else []
        perms[bucket]=[x for x in items if x not in owned]
    data["permissions"]=perms
    # Heal legacy invalid metadata during in-place upgrades. Cursor config must contain only Cursor schema keys.
    data.pop("dynosai",None)
    if not perms.get("allow") and not perms.get("deny"): data.pop("permissions",None)
    files=manifest.get("files",{})
    if isinstance(files,dict): files.pop(key,None)
    _save_cursor_permission_manifest(manifest)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def _remove_toml_dynosai(path: Path) -> None:
    if not path.exists(): return
    lines=path.read_text(encoding="utf-8").splitlines(); out=[]; skipping=False
    for line in lines:
        stripped=line.strip()
        if stripped=="# DYNOSAI managed MCP server":
            continue
        if stripped.startswith("[mcp_servers.dynosai") and stripped.endswith("]"):
            skipping=True; continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping=False
        if not skipping: out.append(line)
    path.write_text("\n".join(out).rstrip()+"\n",encoding="utf-8")


class AgentConfigurator:
    def __init__(self, root: str | Path, db: Database, runtime: RuntimeBroker | None = None):
        self.root = Path(root).resolve(); self.db = db; self.runtime = runtime or RuntimeBroker()

    def _cursor_global_mcp(self) -> Path:
        path=user_home()/".cursor"/"mcp.json"
        def upd(d:dict[str,Any])->None:
            d.setdefault("mcpServers",{}).update({"dynosai":{"command":str(Path(sys.executable).absolute()),"args":["-m","dynosai_flow.mcp"],"env":{"DYNOSAI_AGENT_PROVIDER":"cursor","DYNOSAI_RUNTIME_BROKER":"1"}}})
        _merge_json(path,upd)
        self.runtime.mark_connection("cursor",True)
        return path

    def _cursor_global_permissions(self) -> Path:
        path=user_home()/".cursor"/"cli-config.json"
        def upd(d:dict[str,Any])->None:
            # Cursor rejects unknown top-level keys. DynosAI ownership metadata lives in ~/.dynosai/runtime.
            d.pop("dynosai",None)
            _add_managed_permission(d,"allow","Mcp(dynosai:*)",path)
            # Cursor is launched with --force to avoid repeated command/edit approvals.
            # Direct shell is denied wholesale; operational commands go through DynosAI MCP.
            for item in (
                "Shell(*)","Shell(git)","Shell(rm)","Shell(curl)","Shell(wget)","Shell(ssh)","Shell(scp)","Shell(chmod)",
                "Read(.env*)","Read(**/.env*)","Write(.env*)","Write(**/.env*)",
                "Read(**/secrets/**)","Write(**/secrets/**)","Read(**/*.pem)","Read(**/*.key)",
                "Write(**/*.pem)","Write(**/*.key)","Read(**/.ssh/**)","Write(**/.ssh/**)",
                "Read(**/.aws/**)","Write(**/.aws/**)","Read(**/.gnupg/**)","Write(**/.gnupg/**)",
                "Read(**/.cursor/projects/**)","Write(**/.cursor/projects/**)",
                # DynosAI/provider/runtime control planes are never agent-editable.
                "Read(.dynosai/**)","Read(**/.dynosai/**)","Write(.dynosai/**)","Write(**/.dynosai/**)",
                "Read(.cursor/**)","Read(**/.cursor/**)","Write(.cursor/**)","Write(**/.cursor/**)",
                "Write(.codex/**)","Write(**/.codex/**)","Write(.agents/**)","Write(**/.agents/**)",
                "Write(.mcp.json)","Write(**/.mcp.json)","Write(AGENTS.md)","Write(**/AGENTS.md)",
                "Read(.venv/**)","Read(**/.venv/**)","Write(.venv/**)","Write(**/.venv/**)",
                "Read(.dynosai-env/**)","Read(**/.dynosai-env/**)","Write(.dynosai-env/**)","Write(**/.dynosai-env/**)"
            ): _add_managed_permission(d,"deny",item,path)
        _merge_json(path,upd)
        return path

    def connect(self, agent: str, target_root: Path | None = None, write_scope: list[str] | None = None) -> list[Path]:
        agent = agent.lower()
        if agent not in {"codex", "cursor", "all"}: raise ValueError("Agent must be codex, cursor or all")
        root = (target_root or self.root).resolve(); created: list[Path] = []
        agents = ["codex", "cursor"] if agent == "all" else [agent]
        agents_md=root/"AGENTS.md"; _managed_markdown(agents_md,PROTOCOL); created.append(agents_md)
        def portable_server(provider: str) -> dict[str, Any]:
            return {"command":str(Path(sys.executable).absolute()),"args":["-m","dynosai_flow.mcp"],"env":{"DYNOSAI_AGENT_PROVIDER":provider}}
        if "cursor" in agents:
            cursor=root/".cursor"; (cursor/"rules").mkdir(parents=True,exist_ok=True); (cursor/"commands").mkdir(parents=True,exist_ok=True)
            # Cursor MCP is machine-global so it remains visible when DynosAI creates new Git worktrees.
            # The per-workspace files contain only rules and scoped permissions.
            if target_root is None:
                created.extend([self._cursor_global_mcp(), self._cursor_global_permissions()])
            rule=cursor/"rules"/"dynosai.mdc"; rule.write_text("---\ndescription: DynosAI workflow is mandatory\nalwaysApply: true\n---\n\n"+PROTOCOL,encoding="utf-8"); created.append(rule)
            cli=cursor/"cli.json"
            def upd(d:dict[str,Any])->None:
                d.pop("dynosai",None)  # migrate legacy invalid project config
                perms=d.setdefault("permissions",{})
                if not isinstance(perms.get("allow"),list): perms["allow"]=[]
                if not isinstance(perms.get("deny"),list): perms["deny"]=[]
                for rel in (write_scope or []):
                    _add_managed_permission(d,"allow",f"Write({str(rel).replace(chr(92), '/')})",cli)
                for item in (
                    "Shell(*)", "Shell(git)", "Shell(/usr/bin/git)", "Shell(/bin/git)", "Shell(/usr/local/bin/git)",
                    "Read(.env*)","Read(**/.env*)","Write(.env*)","Write(**/.env*)",
                    "Read(**/secrets/**)","Write(**/secrets/**)",
                    "Read(**/*.pem)","Read(**/*.key)","Write(**/*.pem)","Write(**/*.key)",
                    "Read(.dynosai/runtime/**)","Read(**/.dynosai/runtime/**)",
                    "Read(.dynosai/knowledge.db*)","Read(**/.dynosai/knowledge.db*)",
                    "Read(.dynosai/**)","Read(**/.dynosai/**)","Write(.dynosai/**)","Write(**/.dynosai/**)",
                    "Read(.cursor/**)","Read(**/.cursor/**)","Write(.cursor/**)","Write(**/.cursor/**)",
                    "Write(.codex/**)","Write(**/.codex/**)","Write(.agents/**)","Write(**/.agents/**)",
                    "Write(.mcp.json)","Write(**/.mcp.json)","Write(AGENTS.md)","Write(**/AGENTS.md)",
                    "Read(.venv/**)","Read(**/.venv/**)","Write(.venv/**)","Write(**/.venv/**)",
                    "Read(.dynosai-env/**)","Read(**/.dynosai-env/**)","Write(.dynosai-env/**)","Write(**/.dynosai-env/**)",
                    "Read(**/.cursor/projects/**)","Write(**/.cursor/projects/**)"
                ): _add_managed_permission(d,"deny",item,cli)
            _merge_json(cli,upd); created.append(cli)
            for name,content in COMMANDS.items():
                p=cursor/"commands"/f"{name}.md"; p.write_text(content+"\n",encoding="utf-8"); created.append(p)
        if "codex" in agents:
            cfg=root/".codex"/"config.toml"; _merge_toml_dynosai(cfg); created.append(cfg)
            p=root/".agents"/"skills"/"dynosai"/"SKILL.md"; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(CODEX_SKILL+"\n",encoding="utf-8"); created.append(p)
        # Materialize the provider-neutral profile after the legacy protocol so
        # Cursor and Codex receive the same semantic rules/skills/tools.
        compiled = AgentConfiguration(root).compile(providers=agents, activity="discovery", materialize_tools=False)
        for paths in compiled.get("written", {}).values():
            for raw in paths:
                path = Path(raw)
                if path not in created: created.append(path)
        if target_root is None:
            connected=set(filter(None,self.db.get_meta("connected_agents").split(",")))|set(agents)
            self.db.set_meta("connected_agents",",".join(sorted(connected)))
            def label(path:Path)->str:
                try:return str(path.resolve().relative_to(root)).replace("\\","/")
                except Exception:return str(path.resolve())
            self.db.audit("AgentConnected",agent,{"files":[label(p) for p in created]})
        return created


    def disconnect(self, agent: str) -> list[Path]:
        """Remove only DynosAI-owned integration pieces and preserve user config."""
        agent=agent.lower()
        if agent not in {"codex","cursor","all"}: raise ValueError("Agent must be codex, cursor or all")
        agents=["codex","cursor"] if agent=="all" else [agent]; touched=[]
        _remove_managed_markdown(self.root/"AGENTS.md"); touched.append(self.root/"AGENTS.md")
        if "cursor" in agents:
            # Use a global Cursor MCP entry so every generated worktree sees DynosAI.
            global_mcp=user_home()/".cursor"/"mcp.json"; _remove_json_server(global_mcp); touched.append(global_mcp)
            global_cli=user_home()/".cursor"/"cli-config.json"
            _remove_dynosai_permissions(global_cli); touched.append(global_cli)
            self.runtime.remove_connection("cursor")
            local_mcp=self.root/".cursor"/"mcp.json"
            if local_mcp.exists(): _remove_json_server(local_mcp); touched.append(local_mcp)
            local_cli=self.root/".cursor"/"cli.json"; _remove_dynosai_permissions(local_cli); touched.append(local_cli)
            for p in [self.root/".cursor"/"rules"/"dynosai.mdc",*[self.root/".cursor"/"commands"/f"{name}.md" for name in COMMANDS]]:
                if p.exists(): p.unlink(); touched.append(p)
        if "codex" in agents:
            _remove_toml_dynosai(self.root/".codex"/"config.toml"); touched.append(self.root/".codex"/"config.toml")
            p=self.root/".agents"/"skills"/"dynosai"/"SKILL.md"
            if p.exists(): p.unlink(); touched.append(p)
        connected=set(filter(None,self.db.get_meta("connected_agents").split(",")))-set(agents)
        self.db.set_meta("connected_agents",",".join(sorted(connected))); self.db.audit("AgentDisconnected",agent,{"agents":agents})
        return touched

    def ensure_worktree_protocol(self, worktree: Path, agent: str, write_scope: list[str] | None = None) -> list[Path]:
        # Only used before an agent starts. The files are management metadata and should
        # normally already exist from init/connect. Late connection is made explicit.
        return self.connect(agent, target_root=worktree, write_scope=write_scope)

    def create_session(self, agent: str, work_id: str | None, run_id: str | None, worktree: Path) -> dict[str,str]:
        sid=self.db.next_id("SESSION","agent_sessions")
        token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode()).hexdigest(); now=utc_now()
        task_ids=[]
        if run_id:
            run=self.db.one("SELECT task_ids FROM runs WHERE id=?",(run_id,))
            if run:
                try: task_ids=json.loads(run.get("task_ids") or "[]")
                except Exception: task_ids=[]
        perms={"read":"policy","write":"plan-scope","git":"read-only","worktree":str(worktree),"task_ids":task_ids}
        expires=(datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()
        self.db.execute("INSERT INTO agent_sessions(id,token_hash,agent,run_id,work_id,worktree,state,permissions,created_at,expires_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(sid,digest,agent,run_id,work_id,str(worktree),"active",json_dumps(perms),now,expires,now))
        if run_id: self.db.execute("UPDATE runs SET session_id=?,agent=? WHERE id=?",(sid,agent,run_id))
        self.runtime.register_session({"session_id":sid},self.root,worktree,agent,work_id,run_id,expires,os.getpid())
        return {"session_id":sid,"token":token,"expires_at":expires}

    def verify_session(self, token: str | None) -> dict[str,Any] | None:
        if not token: return None
        digest=hashlib.sha256(token.encode()).hexdigest(); row=self.db.one("SELECT * FROM agent_sessions WHERE token_hash=? AND state='active'",(digest,))
        if row and row.get("expires_at"):
            try:
                if datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(timezone.utc):
                    self.db.execute("UPDATE agent_sessions SET state='expired',last_seen=? WHERE id=?",(utc_now(),row["id"])); return None
            except ValueError:
                self.db.execute("UPDATE agent_sessions SET state='invalid',last_seen=? WHERE id=?",(utc_now(),row["id"])); return None
        if row: self.db.execute("UPDATE agent_sessions SET last_seen=? WHERE id=?",(utc_now(),row["id"]))
        return row

    def resolve_active_session(self, agent: str, worktree_hint: str | Path | None = None) -> dict[str,Any] | None:
        """Resolve a session through the machine-local runtime broker, then validate it in project DB."""
        agent=str(agent or "").lower().strip()
        if agent not in {"codex","cursor"}: return None
        broker=self.runtime.resolve(agent,worktree_hint or Path.cwd())
        if broker and Path(broker["project_root"]).resolve()==self.root:
            row=self.db.one("SELECT * FROM agent_sessions WHERE id=? AND state='active'",(broker["session_id"],))
            if row:
                try: expires=datetime.fromisoformat(str(row.get("expires_at") or ""))
                except Exception: expires=datetime.now(timezone.utc)
                if expires>datetime.now(timezone.utc):
                    self.db.execute("UPDATE agent_sessions SET last_seen=? WHERE id=?",(utc_now(),row["id"])); return row
        # Backwards-compatible exact worktree lookup; ambiguity is always refused.
        now=datetime.now(timezone.utc); rows=self.db.query("SELECT * FROM agent_sessions WHERE agent=? AND state='active' ORDER BY created_at DESC",(agent,)); live=[]
        for row in rows:
            try: expires=datetime.fromisoformat(str(row.get("expires_at") or ""))
            except Exception: continue
            if expires>now: live.append(row)
        hint=Path(worktree_hint or self.root).resolve(); exact=[r for r in live if Path(r.get("worktree") or self.root).resolve()==hint]
        return exact[0] if len(exact)==1 else None

    @staticmethod
    def detect() -> dict[str,bool]:
        return {"codex":shutil.which("codex") is not None,"cursor":shutil.which("cursor-agent") is not None}
