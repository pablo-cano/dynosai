# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Model Context Protocol server, tool contracts, transport compatibility, and workflow adapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any
from dataclasses import dataclass

from .engine import DynosAI
from .version import __version__
from .runtime import RuntimeBroker
from .application import DynosAIApplication
from .providers import ProviderCapabilityService, ProviderCapabilities
from .acceptance_policy import automated_elicitation_content
from .util import utc_now
from .model_routing import activity_for_state
from .context_control import EvidenceCache, ContextCheckpointStore
from .model_control import ModelControlPlane, classify_validation_failure
from .context_handles import ContextHandleStore
from .secrets import redact_value
from .mcp_protocol import (
    CURRENT_PROTOCOL,
    NOT_INITIALIZED_CODE,
    PROTOCOL_2026,
    SUPPORTED_PROTOCOLS,
    client_identity_from_params,
    is_stateless_protocol,
    resolve_protocol_version,
    unsupported_protocol_error,
)
from . import mcp_protocol_2026 as protocol_2026
from . import mcp_protocol_legacy as protocol_legacy


def tool(name:str,description:str,properties:dict[str,Any]|None=None,required:list[str]|None=None,*,schema:dict[str,Any]|None=None)->dict[str,Any]:
    input_schema=dict(schema) if schema is not None else {"type":"object","properties":properties or {}}
    if required and schema is None: input_schema["required"]=required
    return {"name":name,"description":description,"inputSchema":input_schema}


READ_SYMBOL_SCHEMA={
    "type":"object",
    "properties":{
        "symbol_id":{"type":"string"},
        "include_body":{"type":"boolean"},
    },
    "required":["symbol_id"],
    "additionalProperties":False,
}
READ_FILE_SCHEMA={
    "type":"object",
    "properties":{
        "path":{"type":"string"},
        "start_line":{"type":"integer","minimum":1},
        "end_line":{"type":"integer","minimum":1},
    },
    "required":["path","start_line","end_line"],
    "additionalProperties":False,
}
READ_INPUT_SCHEMA={
    "type":"object",
    "oneOf":[
        READ_SYMBOL_SCHEMA,
        READ_FILE_SCHEMA,
        {
            "type":"object",
            "properties":{
                "requests":{
                    "type":"array",
                    "minItems":1,
                    "maxItems":8,
                    "items":{"oneOf":[READ_SYMBOL_SCHEMA,READ_FILE_SCHEMA]},
                }
            },
            "required":["requests"],
            "additionalProperties":False,
        },
    ],
}

TOOLS=[
 tool("dynosai_project","Provider-native project onboarding. action=detect|initialize auto-detects greenfield, brownfield, attach, no-Git, dirty-repo and monorepo states. Optional language/test_command define the project validation baseline. Risky bootstrap decisions are requested through MCP Elicitation.",{"action":{"type":"string"},"root":{"type":"string"},"name":{"type":"string"},"language":{"type":"string"},"test_command":{"type":"string"},"allow_git_init":{"type":"boolean"},"confirm_monorepo":{"type":"boolean"}},["action"]),
 tool("dynosai_work","Provider-native feature lifecycle. action=start|status|clarify|cancel. Normal users start work from Cursor/Codex rather than opening providers from the CLI. clarify creates a real human MCP Elicitation.",{"action":{"type":"string"},"description":{"type":"string"},"work_id":{"type":"string"},"workspace_strategy":{"type":"string"},"question":{"type":"string"}} ,["action"]),
 tool("dynosai_resume","Resume the active feature/provider session from authoritative DynosAI state.",{"work_id":{"type":"string"}}),
 tool("dynosai_events","Read provider-neutral application events for UI synchronization and future GUI adapters.",{"after_id":{"type":"integer"},"work_id":{"type":"string"},"limit":{"type":"integer"}}),
 tool("dynosai_project_overview","Estado del proyecto sin cargar Markdown."),
 tool("dynosai_get_next_action","Orquestador preferido. Devuelve la siguiente acción legal y su contrato. En sesiones DynosAI gestionadas, execute=true (o el modo auto-advance) ejecuta transiciones deterministas pero nunca aprueba gates humanos. No busques schemas en archivos/transcripts.",{"work_id":{"type":"string"},"execute":{"type":"boolean"}}),
 tool("dynosai_start","Registra un trabajo nuevo.",{"description":{"type":"string"},"work_type":{"type":"string"},"quick":{"type":"boolean"},"dry_run":{"type":"boolean"}},["description"]),
 tool("dynosai_continue","Ejecuta la siguiente transición gobernada.",{"work_id":{"type":"string"}}),
 tool("dynosai_board","Kanban del proyecto.",{"state":{"type":"string"},"work_type":{"type":"string"}}),
 tool("dynosai_ask","Búsqueda híbrida en código, SQL, documentación y memoria validada. Para SQL/texto usa esta herramienta; dynosai_find_symbol es solo para identificadores de código.",{"query":{"type":"string"},"limit":{"type":"integer"}},["query"]),
 tool("dynosai_context_preview","Contexto mínimo por tarea.",{"work_id":{"type":"string"},"task_id":{"type":"string"},"max_tokens":{"type":"integer"}}),
 tool("dynosai_checkpoint","Carga dirigida de secciones del checkpoint autoritativo actual. Úsalo antes de releer spec/plan/contexto ya consolidado. sections admite work|decisions|spec|plan|tasks|validations|recent_runs|scope_requests.",{"work_id":{"type":"string"},"sections":{"type":"array","minItems":1,"maxItems":4,"items":{"type":"string","enum":["work","decisions","spec","plan","tasks","validations","recent_runs","scope_requests"]}}}),
 tool("dynosai_retrieve_handle","Recupera el contenido de un context handle persistente. Usa handle_id del resultado compactado; offset/limit recortan textos grandes; section selecciona una clave si el cuerpo es un objeto.",{"handle_id":{"type":"string"},"offset":{"type":"integer"},"limit":{"type":"integer"},"section":{"type":"string"}},["handle_id"]),
 tool("dynosai_register_decision","Registra una o varias decisiones. Prefiere decisions=[{question,answer}, ...] para agrupar decisiones conocidas y reducir round-trips; question+answer sigue soportado.",{"work_id":{"type":"string"},"question":{"type":"string"},"answer":{"type":"string"},"decisions":{"type":"array","items":{"type":"object"}}},["work_id"]),
 tool("dynosai_submit_spec","Entrega spec estructurada.",{"work_id":{"type":"string"},"spec":{"type":"object"}},["work_id","spec"]),
 tool("dynosai_submit_plan","Entrega plan/tareas estructurados. En files.action usa solo read|modify|create|delete; los tests nuevos usan action=create y role=test.",{"work_id":{"type":"string"},"plan":{"type":"object"}},["work_id","plan"]),
 tool("dynosai_register_result","Declara resultado; DynosAI verifica diff/tests/evidencia. Si task_queue.execution_wave incluye varias tareas, completa y registra toda la wave en un único completed_tasks siguiendo registration_order; no hagas register_result intermedio entre tareas de la misma wave.",{"work_id":{"type":"string"},"summary":{"type":"string"},"files_changed":{"type":"array","items":{"type":"string"}},"tests":{"type":"array","items":{"type":"object"}},"completed_tasks":{"type":"array","items":{"type":"string"}}},["work_id","summary","files_changed"]),
 tool("dynosai_request_scope_extension","Solicita ampliar scope sin modificar el plan silenciosamente. action acepta únicamente read|modify|create|delete|test.",{"work_id":{"type":"string"},"task_id":{"type":"string"},"path":{"type":"string"},"action":{"type":"string","enum":["read","modify","create","delete","test"]},"reason":{"type":"string"}},["work_id","path","action","reason"]),
 tool("dynosai_find_symbol","Busca identificadores de símbolos de código por nombre/qualified_name/signature. No usar para SQL, texto o paths: usa dynosai_ask.",{"query":{"type":"string"},"limit":{"type":"integer"}},["query"]),
 tool("dynosai_read","Lectura autoritativa única. Usa exactamente uno de estos contratos: symbol_id(+include_body opcional), path+start_line+end_line, o requests con hasta 8 lecturas que cumplan uno de esos dos contratos. include_body sólo es válido con symbol_id.",schema=READ_INPUT_SCHEMA),
 tool("dynosai_git_status","Estado Git seguro de la sesión sin contenido sensible."),
 tool("dynosai_git_diff","Diff Git filtrado por PathPolicy.",{"max_chars":{"type":"integer"}}),
 tool("dynosai_refresh_overlay","Actualiza el índice overlay del run para archivos modificados."),
 tool("dynosai_analyze_impact","Analiza impacto.",{"query":{"type":"string"},"limit":{"type":"integer"}},["query"]),
 tool("dynosai_schedule","Plan de equipo gobernado: oleadas paralelas/serie, leases y techo de scope. No lanza procesos extra; un segundo worker existe solo si el host abre otra sesión gobernada.",{"work_id":{"type":"string"}}),
 tool("dynosai_semantic_status","Estado semántico.",{"probe":{"type":"boolean"}}),
 tool("dynosai_sync","Reconcilia Git/código/memoria."),
 tool("dynosai_stats","Métricas locales."),
 tool("dynosai_run_validation","Ejecuta perfiles de validación aprobados por DynosAI dentro de la sesión gestionada. Si faltan tests/artefactos que pertenecen a tareas planificadas aún pendientes, devuelve validation_precondition: completa esas tareas antes de revalidar y no solicites scope adicional por ese motivo. work_id es opcional; si se proporciona debe coincidir con el trabajo enlazado a la sesión.",{"work_id":{"type":"string"}}),
]

# Legacy read endpoints remain dispatchable for backwards compatibility but are
# intentionally not advertised in tools/list. Cursor therefore has a single
# read primitive and cannot choose between symbol-vs-file tools for the same
# intent.

# DynosAI-managed sessions expose only the tools needed by the current lifecycle
# group. Legacy/default sessions still see the full TOOLS list for compatibility.
MCP_TOOL_PROFILES = {
    "design": {
        "dynosai_project", "dynosai_work", "dynosai_resume", "dynosai_events",
        "dynosai_project_overview", "dynosai_get_next_action", "dynosai_start",
        "dynosai_continue", "dynosai_ask", "dynosai_context_preview", "dynosai_checkpoint", "dynosai_retrieve_handle",
        "dynosai_register_decision", "dynosai_submit_spec", "dynosai_submit_plan",
        "dynosai_find_symbol", "dynosai_read", "dynosai_analyze_impact",
        "dynosai_semantic_status"
    },
    "delivery": {
        "dynosai_work", "dynosai_resume", "dynosai_events", "dynosai_project_overview",
        "dynosai_get_next_action", "dynosai_continue", "dynosai_context_preview", "dynosai_checkpoint", "dynosai_retrieve_handle",
        "dynosai_register_result", "dynosai_request_scope_extension",
        "dynosai_find_symbol", "dynosai_read", "dynosai_git_status", "dynosai_git_diff",
        "dynosai_refresh_overlay", "dynosai_analyze_impact", "dynosai_run_validation",
        "dynosai_sync", "dynosai_semantic_status"
    },
    "acceptance": {
        # Keep the cross-provider acceptance surface intentionally lean. Everything below was
        # exercised by accepted green/brown baselines or is a safe escape hatch.
        # Deterministic transitions go through get_next_action auto-advance, so
        # dynosai_continue is deliberately absent from managed acceptance.
        "dynosai_project", "dynosai_work", "dynosai_get_next_action",
        "dynosai_register_decision", "dynosai_submit_spec", "dynosai_submit_plan",
        "dynosai_register_result", "dynosai_request_scope_extension",
        "dynosai_ask", "dynosai_context_preview", "dynosai_read", "dynosai_retrieve_handle",
        "dynosai_git_status", "dynosai_git_diff", "dynosai_run_validation"
    },
}

# Phase-specific advisory surfaces. Managed providers that honor MCP
# tools/list_changed can refresh to these subsets. Providers that keep the
# initial list still receive the same policy in dynosai_get_next_action, so
# correctness never depends on dynamic refresh support.
PHASE_TOOL_SURFACES: dict[str,set[str]] = {
    "discovery": {"dynosai_project","dynosai_work","dynosai_get_next_action","dynosai_register_decision","dynosai_submit_spec","dynosai_ask","dynosai_context_preview","dynosai_retrieve_handle","dynosai_read"},
    "specification": {"dynosai_get_next_action","dynosai_register_decision","dynosai_submit_spec","dynosai_ask","dynosai_context_preview","dynosai_retrieve_handle","dynosai_read"},
    "planning": {"dynosai_get_next_action","dynosai_submit_plan","dynosai_ask","dynosai_context_preview","dynosai_checkpoint","dynosai_retrieve_handle","dynosai_read","dynosai_git_status"},
    "implementation": {"dynosai_get_next_action","dynosai_register_result","dynosai_request_scope_extension","dynosai_context_preview","dynosai_checkpoint","dynosai_retrieve_handle","dynosai_read","dynosai_git_status","dynosai_git_diff","dynosai_run_validation"},
    "code_review": {"dynosai_get_next_action","dynosai_context_preview","dynosai_checkpoint","dynosai_retrieve_handle","dynosai_read","dynosai_git_status","dynosai_git_diff","dynosai_run_validation"},
    "validation": {"dynosai_get_next_action","dynosai_checkpoint","dynosai_retrieve_handle","dynosai_read","dynosai_git_status","dynosai_git_diff","dynosai_run_validation","dynosai_register_result"},
    "merge": {"dynosai_get_next_action","dynosai_checkpoint","dynosai_retrieve_handle","dynosai_git_status","dynosai_git_diff","dynosai_run_validation"},
}

def phase_tool_surface(activity:str|None)->dict[str,Any]:
    activity=str(activity or "discovery").lower()
    allowed=PHASE_TOOL_SURFACES.get(activity) or MCP_TOOL_PROFILES["acceptance"]
    ordered=[item["name"] for item in TOOLS if item.get("name") in allowed]
    return {"activity":activity,"tools":ordered,"tool_count":len(ordered),"mode":"phase_dynamic_safe" if (os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") or "").lower() in {"adaptive","dynamic"} else "phase_advisory"}

def advertised_tools(activity:str|None=None) -> list[dict[str,Any]]:
    disclosure=(os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") or "").strip().lower()
    if activity and disclosure in {"dynamic","adaptive"}:
        allowed=PHASE_TOOL_SURFACES.get(str(activity).lower())
        if allowed:
            return [item for item in TOOLS if item.get("name") in allowed]
    profile=(os.environ.get("DYNOSAI_MCP_TOOL_PROFILE") or "").strip().lower()
    allowed=MCP_TOOL_PROFILES.get(profile)
    if not allowed:
        return TOOLS
    return [item for item in TOOLS if item.get("name") in allowed]

LEGACY_TOOLS=[
 tool("dynosai_get_symbol","Legacy: use dynosai_read.",{"symbol_id":{"type":"string"},"include_body":{"type":"boolean"}},["symbol_id"]),
 tool("dynosai_get_file_slice","Legacy: use dynosai_read.",{"path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},["path","start_line","end_line"]),
]


class ToolInputError(ValueError):
    """Invalid MCP tool arguments detected before entering a tool handler."""


def _value_matches_type(value:Any, expected:str)->bool:
    if expected=="string": return isinstance(value,str)
    if expected=="boolean": return isinstance(value,bool)
    if expected=="integer": return isinstance(value,int) and not isinstance(value,bool)
    if expected=="array": return isinstance(value,list)
    if expected=="object": return isinstance(value,dict)
    return True


def _tool_schema(name:str)->dict[str,Any]|None:
    for item in [*TOOLS,*LEGACY_TOOLS]:
        if item["name"]==name: return item.get("inputSchema") or {}
    return None


def _validate_tool_arguments(name:str,args:dict[str,Any])->None:
    if name=="dynosai_read":
        if not isinstance(args,dict): raise ToolInputError("arguments must be an object")
        if "requests" in args:
            if set(args)!={"requests"}: raise ToolInputError("dynosai_read requests mode cannot be combined with symbol_id/path mode")
            requests=args.get("requests")
            if not isinstance(requests,list) or not requests or len(requests)>8: raise ToolInputError("argument requests must be a non-empty array with at most 8 reads")
            for index,item in enumerate(requests):
                if not isinstance(item,dict): raise ToolInputError(f"requests[{index}] must be object")
                _validate_tool_arguments("dynosai_read",item)
            return
        symbol_keys={"symbol_id","include_body"}; file_keys={"path","start_line","end_line"}
        has_symbol="symbol_id" in args; has_file=bool(set(args) & file_keys)
        if has_symbol and has_file: raise ToolInputError("dynosai_read accepts either symbol_id mode OR path+start_line+end_line mode, not both")
        if has_symbol:
            unknown=set(args)-symbol_keys
            if unknown: raise ToolInputError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
            if not isinstance(args.get("symbol_id"),str): raise ToolInputError("argument symbol_id must be string")
            if "include_body" in args and not isinstance(args["include_body"],bool): raise ToolInputError("argument include_body must be boolean")
            return
        missing=[key for key in ("path","start_line","end_line") if key not in args]
        if missing: raise ToolInputError("dynosai_read requires symbol_id OR path+start_line+end_line OR requests; missing: "+", ".join(missing))
        unknown=set(args)-file_keys
        if unknown: raise ToolInputError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
        if not isinstance(args.get("path"),str): raise ToolInputError("argument path must be string")
        for key in ("start_line","end_line"):
            if not isinstance(args.get(key),int) or isinstance(args.get(key),bool): raise ToolInputError(f"argument {key} must be integer")
            if args[key] < 1: raise ToolInputError(f"argument {key} must be >= 1")
        if args["end_line"] < args["start_line"]: raise ToolInputError("argument end_line must be >= start_line")
        return
    if name=="dynosai_register_decision":
        if not isinstance(args,dict): raise ToolInputError("arguments must be an object")
        if not isinstance(args.get("work_id"),str) or not args.get("work_id"): raise ToolInputError("argument work_id must be string")
        unknown=set(args)-{"work_id","question","answer","decisions"}
        if unknown: raise ToolInputError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
        if "decisions" in args:
            if "question" in args or "answer" in args: raise ToolInputError("decisions batch mode cannot be combined with question/answer")
            decisions=args.get("decisions")
            if not isinstance(decisions,list) or not decisions or len(decisions)>32: raise ToolInputError("decisions must be a non-empty array with at most 32 entries")
            for index,item in enumerate(decisions):
                if not isinstance(item,dict) or set(item)!={"question","answer"} or not all(isinstance(item.get(k),str) and item.get(k).strip() for k in ("question","answer")):
                    raise ToolInputError(f"decisions[{index}] must contain non-empty string question and answer")
            return
        if not isinstance(args.get("question"),str) or not args.get("question","").strip(): raise ToolInputError("argument question must be non-empty string")
        if not isinstance(args.get("answer"),str) or not args.get("answer","").strip(): raise ToolInputError("argument answer must be non-empty string")
        return
    schema=_tool_schema(name)
    if schema is None: raise ToolInputError(f"unknown tool: {name}")
    if not isinstance(args,dict): raise ToolInputError("arguments must be an object")
    props=schema.get("properties") or {}; required=schema.get("required") or []
    missing=[key for key in required if key not in args]
    if missing: raise ToolInputError(f"missing required argument(s): {', '.join(missing)}")
    unknown=[key for key in args if key not in props]
    if unknown: raise ToolInputError(f"unexpected argument(s): {', '.join(sorted(unknown))}")
    for key,value in args.items():
        prop=props.get(key) or {}
        expected=prop.get("type")
        if expected and not _value_matches_type(value,expected):
            raise ToolInputError(f"argument {key} must be {expected}")
        allowed=prop.get("enum")
        if allowed is not None and value not in allowed:
            raise ToolInputError(f"argument {key} must be one of: {', '.join(str(x) for x in allowed)}")


def _normalize_read_only_call(name:str,args:dict[str,Any])->tuple[str,dict[str,Any],dict[str,Any]|None]:
    # Cursor can occasionally select get_symbol while supplying the exact
    # file-slice argument shape. The intent is unambiguous and both tools are
    # read-only, so normalize this one safe mismatch instead of turning a
    # client-side selection error into an internal KeyError. All other invalid
    # argument shapes remain strict validation rejections.
    if name=="dynosai_get_symbol" and "symbol_id" not in args:
        keys=set(args)
        required={"path","start_line","end_line"}
        if required.issubset(keys) and keys.issubset(required):
            return "dynosai_get_file_slice",dict(args),{
                "requested_tool":"dynosai_get_symbol",
                "effective_tool":"dynosai_get_file_slice",
                "reason":"file-slice argument shape supplied to get_symbol",
            }
    return name,args,None

READ_ONLY_TOOLS={"dynosai_project_overview","dynosai_events","dynosai_board","dynosai_ask","dynosai_context_preview","dynosai_checkpoint","dynosai_retrieve_handle","dynosai_find_symbol","dynosai_read","dynosai_get_symbol","dynosai_get_file_slice","dynosai_git_status","dynosai_git_diff","dynosai_analyze_impact","dynosai_schedule","dynosai_semantic_status","dynosai_stats"}


def discover_project_root(explicit:str|Path|None=None)->Path:
    env=os.environ.get("DYNOSAI_PROJECT_ROOT")
    if env and (Path(env)/".dynosai"/"knowledge.db").exists(): return Path(env).resolve()
    if explicit:
        p=Path(explicit).resolve()
        if (p/".dynosai"/"knowledge.db").exists(): return p
    cwd=Path.cwd().resolve()
    for p in (cwd,*cwd.parents):
        if (p/".dynosai"/"knowledge.db").exists(): return p
    # Global Cursor MCP sessions resolve the active project through the machine-local runtime broker.
    provider=os.environ.get("DYNOSAI_AGENT_PROVIDER")
    try:
        broker=RuntimeBroker(); resolved=broker.project_for_path(cwd,provider)
        if resolved and (resolved/".dynosai"/"knowledge.db").exists(): return resolved
    except Exception: pass
    try:
        r=subprocess.run(["git","rev-parse","--path-format=absolute","--git-common-dir"],cwd=cwd,text=True,encoding="utf-8",errors="replace",capture_output=True,timeout=5)
        if r.returncode==0:
            common=Path(r.stdout.strip()).resolve(); root=common.parent
            if (root/".dynosai"/"knowledge.db").exists(): return root
    except Exception: pass
    # Provider-native onboarding must also work in an empty/new directory. The
    # high-level dynosai_project tool will initialize/adopt it before workflow tools
    # are used. Returning cwd here is safe because no project DB mutation occurs on
    # server construction.
    return cwd


@dataclass(slots=True)
class ElicitationExchange:
    tool_request_id: Any
    interaction: dict[str, Any]
    base_result: dict[str, Any]
    requested_execute: bool = False


class MCPServer:
    def __init__(self,project:Path):
        self.engine=DynosAI(project); self.app=DynosAIApplication(project)
        self.negotiated=None; self.initialized=False
        self.provider=os.environ.get("DYNOSAI_AGENT_PROVIDER") or "unknown"
        self.client_capabilities=ProviderCapabilities(provider=self.provider)
        self.session=None; self.provider_session=None
        self._managed_response_cache: dict[str,str] = {}
        self._evidence_cache = EvidenceCache()
        self.handles = ContextHandleStore(self.engine.root)
        self._last_advertised_activity: str | None = None
        if (self.engine.root/".dynosai"/"knowledge.db").exists():
            try:
                self.engine.db.initialize()
                self.session=self.engine.agents.verify_session(os.environ.get("DYNOSAI_SESSION_TOKEN"))
                if not self.session and self.provider != "unknown":
                    self.session=self.engine.agents.resolve_active_session(self.provider,Path.cwd())
                self.provider_session=self.app.sessions.active(self.provider) if self.provider != "unknown" else None
            except Exception:
                self.session=None; self.provider_session=None

    @staticmethod
    def _infer_provider(params:dict[str,Any])->str:
        env=os.environ.get("DYNOSAI_AGENT_PROVIDER")
        if env: return env.lower()
        name=str((params.get("clientInfo") or {}).get("name") or "").lower()
        if "cursor" in name: return "cursor"
        if "codex" in name or "openai" in name: return "codex"
        return "generic"

    def _rebind(self, root:Path)->None:
        self.engine=DynosAI(root); self.app=DynosAIApplication(root)
        self.engine.db.initialize()
        self.provider_session=self.app.sessions.active(self.provider)
        self.handles=ContextHandleStore(self.engine.root)
    def _compact_managed_result(self,name:str,result:Any)->Any:
        if name!="dynosai_get_next_action" or not isinstance(result,dict): return result
        if os.environ.get("DYNOSAI_MANAGED_AGENT")!="1" or os.environ.get("DYNOSAI_COMPACT_RESPONSES","1")!="1": return result
        out=dict(result); saved=0
        scope=(self.provider_session or {}).get("id") or (self.session or {}).get("id") or ((result.get("work") or {}).get("id") if isinstance(result.get("work"),dict) else None) or result.get("work_id") or "unbound"
        # Cache the *action state* separately from model-control telemetry. Usage
        # can change while the legal action/contract stays identical; replaying
        # next/tool-surface text in that case only burns provider context.
        action_payload={
            "work":result.get("work"),"next":result.get("next"),"contract":result.get("contract"),
            "checkpoint_ref":((result.get("context_checkpoint") or {}).get("ref") if isinstance(result.get("context_checkpoint"),dict) else None),
            "human_interaction":({k:(result.get("human_interaction") or {}).get(k) for k in ("id","status","kind","gate")} if isinstance(result.get("human_interaction"),dict) else None),
            "tool_surface":result.get("tool_surface"),
        }
        action_raw=json.dumps(action_payload,ensure_ascii=False,sort_keys=True,default=str,separators=(",",":"))
        action_digest=hashlib.sha256(action_raw.encode("utf-8")).hexdigest()[:16]
        action_ref=f"ACTION-{action_digest}"
        action_cache_key=f"{scope}:action_snapshot"
        previous_action=self._managed_response_cache.get(action_cache_key)
        checkpoint_saved=0
        for key in ("agent_config","contract","context_checkpoint","model_control"):
            value=out.get(key)
            if not isinstance(value,dict): continue
            if key=="context_checkpoint":
                checkpoint_saved=max(0,int(value.get("estimated_bytes_avoided") or 0))
                saved += checkpoint_saved
            raw=json.dumps(value,ensure_ascii=False,sort_keys=True,default=str)
            digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            ref=f"{key.upper()}-{digest}"
            cache_key=f"{scope}:{key}"
            previous=self._managed_response_cache.get(cache_key)
            if previous==digest:
                compact={"ref":ref,"unchanged":True}
                if key=="agent_config":
                    compact.update({k:value.get(k) for k in ("provider","activity","mode") if value.get(k) is not None})
                else:
                    compact.update({k:value.get(k) for k in ("operation","work_id") if value.get(k) is not None})
                saved += max(0,len(raw)-len(json.dumps(compact,ensure_ascii=False)))
                out[key]=compact
            else:
                full=dict(value); full["_dynosai_ref"]=ref; out[key]=full
                self._managed_response_cache[cache_key]=digest
        action_saved=0
        # Never compact away an unresolved human interaction: the MCP client must
        # still receive the actual form. For ordinary repeated polling, a stable
        # state ref is sufficient because the full action was already delivered
        # earlier in this provider session.
        if previous_action==action_digest and not result.get("human_interaction"):
            next_value=out.get("next")
            if next_value is not None:
                raw_next=json.dumps(next_value,ensure_ascii=False,default=str)
                compact_next={"ref":action_ref,"unchanged":True}
                action_saved+=max(0,len(raw_next)-len(json.dumps(compact_next,ensure_ascii=False)))
                out["next"]=compact_next
            surface=out.get("tool_surface")
            if isinstance(surface,dict):
                raw_surface=json.dumps(surface,ensure_ascii=False,default=str)
                compact_surface={"ref":action_ref,"unchanged":True,"activity":surface.get("activity"),"tool_count":surface.get("tool_count")}
                action_saved+=max(0,len(raw_surface)-len(json.dumps(compact_surface,ensure_ascii=False)))
                out["tool_surface"]=compact_surface
            if out.get("transition") is None:
                out.pop("transition",None)
            out["action_snapshot"]={"ref":action_ref,"unchanged":True}
        else:
            self._managed_response_cache[action_cache_key]=action_digest
            out["action_snapshot"]={"ref":action_ref,"unchanged":False}
        saved += action_saved
        out["response_optimization"]={
            "mode":"delta",
            "estimated_bytes_omitted":saved,
            "checkpoint_bytes_omitted":checkpoint_saved,
            "checkpoint_reference_used":bool(checkpoint_saved),
            "action_snapshot_bytes_omitted":action_saved,
            "action_snapshot_reused":bool(previous_action==action_digest and not result.get("human_interaction")),
        }
        return out

    def _current_activity(self)->str:
        try:
            wid=self._session_work_id()
            if wid:
                work=self.engine.get_work(wid)
                return activity_for_state(work.get("state"))
        except Exception:
            pass
        return "discovery"

    def _read_with_cache(self,key:str,value:Any)->Any:
        if os.environ.get("DYNOSAI_EVIDENCE_CACHE","1")!="1": return value
        scope=(self.provider_session or {}).get("id") or (self.session or {}).get("id") or self._session_work_id() or "unbound"
        compact,meta=self._evidence_cache.compact(str(scope),key,value)
        if isinstance(compact,dict): compact={**compact,"evidence_cache":meta}
        return compact

    def _authority_instructions(self)->str:
        transport_instruction=("For managed Codex tool results, structuredContent is authoritative and content.text may be only a compact compatibility envelope." if self.provider in {"codex","openai"} else "For Cursor and generic clients, content.text contains the complete authoritative tool result because some Cursor CLI streams do not expose MCP structuredContent to the model.")
        studio_gates=os.environ.get("DYNOSAI_HUMAN_GATE_TRANSPORT","").lower()=="studio"
        gate_instruction=("Human gates are returned as human_interaction tool payloads because DynosAI Studio owns the approval UI. Stop the current turn immediately when one is returned; never self-approve." if studio_gates else "Human gates are requested through MCP Elicitation when the client advertises elicitation capability; never self-approve.")
        prefix_note=" Discover remaining tools via tools/list."
        try:
            from .prompt_prefix import build_authority_prefix, persist_prefix
            prefix=build_authority_prefix(protocol=self.negotiated or CURRENT_PROTOCOL)
            if (self.engine.root/".dynosai"/"knowledge.db").exists():
                prefix=persist_prefix(self.engine.root, prefix)
                try:
                    self.engine.db.audit("PromptPrefixRecorded", prefix.get("hash"), {"claims_cache_hit": False, "schema": "PROMPT_PREFIX_1.0", "mcp_protocol": prefix.get("mcp_protocol")})
                except Exception:
                    pass
            prefix_note=f" Stable authority prefix {str(prefix.get('hash') or '')[:12]}. Discover remaining tools via tools/list. Prefix hash is cacheable structure, not provider cache telemetry."
        except Exception:
            pass
        return f"DynosAI is provider-native. Start/adopt/attach with dynosai_project and start/resume a feature with dynosai_work/dynosai_resume. Keep one provider session for the feature. {gate_instruction} {transport_instruction} Use dynosai_read for concrete reads, dynosai_ask for SQL/text and dynosai_find_symbol for code identifiers.{prefix_note}"

    def _bind_request_identity(self, params: dict[str, Any], *, protocol: str | None = None) -> None:
        identity = client_identity_from_params(params)
        selected = protocol or identity.get("protocolVersion") or self.negotiated
        if selected:
            self.negotiated = str(selected)
        info = identity.get("clientInfo") or {}
        caps = identity.get("capabilities") or {}
        if info:
            self.provider = self._infer_provider({"clientInfo": info})
        if not info and not caps:
            return
        payload = {
            "protocolVersion": self.negotiated or "",
            "clientInfo": info,
            "capabilities": caps,
        }
        provider = getattr(self, "provider", None) or "unknown"
        self.client_capabilities = ProviderCapabilityService.from_initialize(provider, payload)
        engine = getattr(self, "engine", None)
        if engine is not None and (engine.root / ".dynosai" / "knowledge.db").exists():
            try:
                ProviderCapabilityService(engine.db).record(self.client_capabilities)
            except Exception:
                pass

    def _tools_list_payload(self, protocol: str) -> dict[str, Any]:
        activity = self._current_activity()
        disclosure = (os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") or "").lower()
        # Adaptive mode is safe for clients that ignore listChanged: the first
        # list is the full lean managed surface. If they honor the notification
        # and re-list later, subsequent lists shrink to the current phase.
        first_adaptive = disclosure == "adaptive" and self._last_advertised_activity is None
        tools = advertised_tools(None if first_adaptive else activity)
        self._last_advertised_activity = activity
        meta = phase_tool_surface(activity)
        meta["initial_safe_superset"] = bool(first_adaptive)
        meta["mcp_protocol"] = protocol
        if is_stateless_protocol(protocol):
            return protocol_2026.tools_list_result(tools, meta)
        return protocol_legacy.tools_list_result(tools, meta)

    def _handle_initialize(self, rid: Any, params: dict[str, Any]) -> dict[str, Any]:
        requested = str(params.get("protocolVersion") or CURRENT_PROTOCOL)
        if requested not in SUPPORTED_PROTOCOLS:
            code, message, data = unsupported_protocol_error(requested)
            return self._error(rid, code, message, data)
        self._bind_request_identity(params, protocol=requested)
        return self._response(rid, protocol_legacy.initialize_result(protocol=requested, instructions=self._authority_instructions()))

    def _handle_discover(self, rid: Any, params: dict[str, Any]) -> dict[str, Any]:
        identity = client_identity_from_params(params)
        requested = str(identity.get("protocolVersion") or PROTOCOL_2026)
        if requested not in SUPPORTED_PROTOCOLS:
            code, message, data = unsupported_protocol_error(requested)
            return self._error(rid, code, message, data)
        self._bind_request_identity(params, protocol=requested)
        return self._response(rid, protocol_2026.discover_result(instructions=self._authority_instructions()))

    def _tool_call_response(self,rid:Any,result:Any,*,tool_name:str,protocol:str)->dict[str,Any]:
        payload=_tool_result_payload(result,provider=self.provider,tool_name=tool_name)
        if is_stateless_protocol(protocol):
            payload=protocol_2026.wrap_tool_result(payload)
        else:
            payload=protocol_legacy.wrap_tool_result(payload)
        return self._response(rid,payload)

    def handle(self,message:dict[str,Any])->dict[str,Any]|ElicitationExchange|None:
        method=message.get("method"); rid=message.get("id")
        params=message.get("params") if isinstance(message.get("params"), dict) else {}
        if method=="initialize":
            return self._handle_initialize(rid, params)
        if method=="server/discover":
            return self._handle_discover(rid, params)
        if method=="notifications/initialized": self.initialized=True; return None
        if method=="notifications/cancelled": return None
        if method=="ping":
            if params: self._bind_request_identity(params)
            return self._response(rid,{})
        protocol=resolve_protocol_version(params, self.negotiated)
        if protocol is None:
            return self._error(rid, NOT_INITIALIZED_CODE, "Server not initialized")
        if protocol not in SUPPORTED_PROTOCOLS:
            code, message, data = unsupported_protocol_error(protocol)
            return self._error(rid, code, message, data)
        self._bind_request_identity(params, protocol=protocol)
        if method=="tools/list":
            return self._response(rid, self._tools_list_payload(protocol))
        if method=="tools/call":
            params=message.get("params",{}); requested_name=params.get("name"); raw_args=params.get("arguments") or {}
            try:
                name,args,normalization=_normalize_read_only_call(str(requested_name or ""),raw_args)
                _validate_tool_arguments(name,args)
                if name not in READ_ONLY_TOOLS and not self._mutation_authorized(name,args):
                    raise PermissionError("No active governed provider session. Start/resume work through DynosAI first.")
                started=time.perf_counter()
                result=self.call_tool(name,args)
                duration_ms=int((time.perf_counter()-started)*1000)
                try:
                    result_bytes=len(json.dumps(result,ensure_ascii=False,default=str).encode("utf-8"))
                except Exception:
                    result_bytes=None
                if name in {"dynosai_git_diff","dynosai_ask","dynosai_run_validation"}:
                    try:
                        enabled=True
                        try:
                            enabled=bool(self.engine.harness_feature("context_handles"))
                        except Exception:
                            from .harness_contracts import harness_enabled
                            enabled=harness_enabled("context_handles", True)
                        result=self.handles.wrap_tool_result(name,result,work_id=self._session_work_id(),session_id=(self.session or {}).get("id"),enabled=enabled)
                    except Exception:
                        pass
                result=self._compact_managed_result(name,result)
                try:
                    result=redact_value(result)
                except Exception:
                    pass
                try:
                    if normalization:
                        self.engine.db.audit("MCPToolNormalized",str(requested_name),{**normalization,"session_id":self.session and self.session.get("id"),"work_id":self._session_work_id(),"run_id":self._run_id()})
                    self.engine.db.audit("MCPToolCalled",name,{"requested_tool":requested_name,"provider_session_id":self.provider_session and self.provider_session.get("id"),"session_id":self.session and self.session.get("id"),"work_id":self._session_work_id(),"run_id":self._run_id(),"duration_ms":duration_ms,"result_bytes":result_bytes,"mcp_protocol":protocol})
                except Exception:
                    pass
                studio_gates=os.environ.get("DYNOSAI_HUMAN_GATE_TRANSPORT","").lower()=="studio"
                if isinstance(result,dict) and result.get("human_interaction") and self.client_capabilities.elicitation_form and not studio_gates:
                    return ElicitationExchange(rid,dict(result["human_interaction"]),result,bool(args.get("execute",False)))
                return self._tool_call_response(rid,result,tool_name=name,protocol=protocol)
            except ToolInputError as exc:
                rejection={"accepted":False,"error_type":"tool_input_validation","message":str(exc),"repair_required":True,"requested_tool":requested_name}
                try:
                    self.engine.db.audit("MCPToolRejected",str(requested_name or "unknown"),{"error":"ToolInputError","message":str(exc),"session_id":self.session and self.session.get("id")})
                except Exception:
                    pass
                return self._tool_call_response(rid,rejection,tool_name=str(requested_name or "unknown"),protocol=protocol)
            except ValueError as exc:
                failed_name=str(locals().get("name") or requested_name or "unknown")
                if failed_name in {"dynosai_submit_spec","dynosai_submit_plan"}:
                    rejection={"accepted":False,"error_type":"validation","message":str(exc),"repair_required":True}
                    try:
                        self.engine.db.audit("MCPToolRejected",failed_name,{"error":"ValueError","message":str(exc),"session_id":self.session and self.session.get("id")})
                    except Exception:
                        pass
                    return self._tool_call_response(rid,rejection,tool_name=failed_name,protocol=protocol)
                try:
                    self.engine.db.audit("MCPToolFailed",failed_name,{"error":type(exc).__name__,"session_id":self.session and self.session.get("id")})
                except Exception:
                    pass
                return self._tool_call_response(rid,{"content":[{"type":"text","text":f"{type(exc).__name__}: {exc}"}],"isError":True},tool_name=failed_name,protocol=protocol)
            except Exception as exc:
                failed_name=str(locals().get("name") or requested_name or "unknown")
                try:
                    self.engine.db.audit("MCPToolFailed",failed_name,{"error":type(exc).__name__,"session_id":self.session and self.session.get("id")})
                except Exception:
                    pass
                return self._tool_call_response(rid,{"content":[{"type":"text","text":f"{type(exc).__name__}: {exc}"}],"isError":True},tool_name=failed_name,protocol=protocol)
        return self._error(rid,-32601,f"Method not found: {method}")

    def _bootstrap_orchestrator_contract(self)->dict[str,Any]|None:
        """Return the next legal bootstrap tool when no governed work exists.

        dynosai_get_next_action is the advertised orchestrator. Blocking it with
        PermissionError before dynosai_project/dynosai_work start caused live
        Cursor greenfield agents to stop with zero DYN work items.
        execute=true must not auto-initialize or auto-start; it returns the same
        contract so human gates on git/monorepo bootstrap stay mandatory.
        """
        try:
            detection=self.app.detect_project()
        except Exception:
            detection={"classification":"EMPTY_DIRECTORY","root":str(self.engine.root)}
        classification=str(detection.get("classification") or "EMPTY_DIRECTORY")
        if classification=="EXISTING_DYNOSAI_PROJECT":
            try:
                self.engine.get_work()
                return None
            except Exception:
                return {
                    "work": None,
                    "bootstrap": True,
                    "detection": detection,
                    "next": "Start a governed feature with dynosai_work action=start.",
                    "contract": {
                        "tool": "dynosai_work",
                        "arguments": {"action": "start"},
                        "deterministic_transition": False,
                        "rules": [
                            "Call dynosai_work action=start with the business goal before exploring the repository.",
                            "Human gates remain mandatory; never self-approve.",
                        ],
                    },
                    "human_interaction": None,
                    "transition": None,
                }
        return {
            "work": None,
            "bootstrap": True,
            "detection": detection,
            "next": "Initialize/adopt/attach with dynosai_project action=initialize, then start a feature with dynosai_work action=start.",
            "contract": {
                "tool": "dynosai_project",
                "arguments": {"action": "initialize"},
                "deterministic_transition": False,
                "rules": [
                    "Call dynosai_project action=initialize before exploring the repository.",
                    "Do not treat a missing session as completion.",
                    "Human gates remain mandatory; never self-approve.",
                ],
            },
            "human_interaction": None,
            "transition": None,
        }

    def _mutation_authorized(self,name:str,args:dict[str,Any])->bool:
        if name=="dynosai_project": return True
        if name=="dynosai_resume": return True
        if name=="dynosai_get_next_action": return True
        if name=="dynosai_work":
            action=str(args.get("action") or "status")
            if action in {"start","status"}: return True
            if action=="clarify":
                wid=args.get("work_id") or self._session_work_id()
                try: return bool(self.app.sessions.active(self.provider,str(wid)) if wid else self.app.sessions.active(self.provider))
                except Exception: return False
            if action=="cancel":
                wid=args.get("work_id") or self._session_work_id()
                try: return bool(self.app.sessions.active(self.provider,str(wid)) if wid else self.app.sessions.active(self.provider))
                except Exception: return False
            return False
        if self.session: return True
        if self.provider_session and self.provider_session.get("state")=="active": return True
        if (self.engine.root/".dynosai"/"knowledge.db").exists() and self.provider not in {"unknown","generic"}:
            try:
                self.provider_session=self.app.sessions.active(self.provider,self._session_work_id()) or self.app.sessions.active(self.provider)
                return bool(self.provider_session)
            except Exception: return False
        return False

    def _refresh_provider_session(self,work_id:str|None=None)->None:
        try: self.provider_session=self.app.sessions.active(self.provider,work_id) if work_id else self.app.sessions.active(self.provider)
        except Exception: self.provider_session=None

    def _session_root(self)->Path:
        if self.session and self.session.get("worktree"): return Path(self.session["worktree"]).resolve()
        if self.provider_session and self.provider_session.get("workspace"): return Path(self.provider_session["workspace"]).resolve()
        return self.engine.root
    def _run_id(self)->str|None:
        if self.session and self.session.get("run_id"): return str(self.session.get("run_id"))
        wid=self._session_work_id()
        if wid and (self.engine.root/".dynosai"/"knowledge.db").exists():
            row=self.engine.db.one("SELECT id FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(wid,))
            return str(row["id"]) if row else None
        return None
    def _session_work_id(self)->str|None:
        if self.session and self.session.get("work_id"): return str(self.session.get("work_id"))
        if self.provider_session and self.provider_session.get("work_id"): return str(self.provider_session.get("work_id"))
        return None
    def _scoped_work(self,args:dict[str,Any], *, required:bool=False)->str|None:
        supplied=args.get("work_id"); bound=self._session_work_id()
        if bound and supplied and str(supplied)!=bound: raise PermissionError(f"session is bound to {bound}, not {supplied}")
        value=str(supplied or bound) if (supplied or bound) else None
        if required and not value: raise ValueError("work_id required")
        return value
    def call_tool(self,name:str,args:dict[str,Any])->Any:
        e=self.engine
        if name=="dynosai_project":
            root=Path(args.get("root") or self.engine.root).expanduser().resolve()
            app=DynosAIApplication(root)
            action=str(args.get("action") or "detect").lower()
            if action=="detect": return app.detect_project()
            if action!="initialize": raise ValueError("dynosai_project action must be detect or initialize")
            detection=app.detect_project(); classification=detection.get("classification")
            # Project bootstrap can precede knowledge.db, so these requests are
            # deliberately ephemeral MCP Elicitations. The accepted outcome is
            # still captured by ProjectInitialized/ProjectAdopted once a project DB exists.
            if classification=="NEW_CODE_NO_GIT" and "allow_git_init" not in args:
                return {
                    "detection":detection,
                    "human_interaction":{
                        "id":"BOOTSTRAP-GIT", "work_id":None, "session_id":None,
                        "kind":"project_bootstrap", "gate":"project", "status":"pending",
                        "provider":self.provider, "ephemeral":True,
                        "message":"This directory contains code but no Git repository. DynosAI requires Git for governed implementation. Initialize Git, continue in analysis-only mode, or cancel?",
                        "requested_schema":{
                            "type":"object",
                            "properties":{"decision":{"type":"string","enum":["initialize_git","analysis_only","cancel"]}},
                            "required":["decision"],
                        },
                        "bootstrap":{"action":"initialize","root":str(root),"name":args.get("name"),"language":args.get("language"),"test_command":args.get("test_command"),"classification":classification},
                    },
                }
            if classification=="DIRTY_GIT_REPO":
                return {
                    "detection":detection,
                    "human_interaction":{
                        "id":"BOOTSTRAP-DIRTY", "work_id":None, "session_id":None,
                        "kind":"project_bootstrap", "gate":"project", "status":"pending",
                        "provider":self.provider, "ephemeral":True,
                        "message":"This repository contains uncommitted changes, so DynosAI cannot establish a reproducible governed baseline. Continue read-only/analysis-only or cancel and resolve the Git changes first?",
                        "requested_schema":{
                            "type":"object",
                            "properties":{"decision":{"type":"string","enum":["analysis_only","cancel"]}},
                            "required":["decision"],
                        },
                        "bootstrap":{"action":"initialize","root":str(root),"name":args.get("name"),"language":args.get("language"),"test_command":args.get("test_command"),"classification":classification},
                    },
                }
            if classification=="MONOREPO" and not bool(args.get("confirm_monorepo",False)):
                return {
                    "detection":detection,
                    "human_interaction":{
                        "id":"BOOTSTRAP-MONOREPO", "work_id":None, "session_id":None,
                        "kind":"project_bootstrap", "gate":"project", "status":"pending",
                        "provider":self.provider, "ephemeral":True,
                        "message":"DynosAI detected a probable monorepo. Govern the whole repository from this root, or cancel and invoke DynosAI from the intended subdirectory?",
                        "requested_schema":{
                            "type":"object",
                            "properties":{"decision":{"type":"string","enum":["whole_repository","cancel"]}},
                            "required":["decision"],
                        },
                        "bootstrap":{"action":"initialize","root":str(root),"name":args.get("name"),"language":args.get("language"),"test_command":args.get("test_command"),"classification":classification},
                    },
                }
            result=app.initialize_project(
                name=args.get("name"),
                agent=self.provider if self.provider in {"cursor","codex"} else None,
                allow_git_init=bool(args.get("allow_git_init",False)),
                language=args.get("language"),
                test_command=args.get("test_command"),
            )
            if (root/".dynosai"/"knowledge.db").exists():
                self._rebind(root)
                try: ProviderCapabilityService(self.engine.db).record(self.client_capabilities)
                except Exception: pass
            return result
        if name=="dynosai_work":
            action=str(args.get("action") or "status").lower()
            if action=="start":
                if not args.get("description"): raise ValueError("description required for dynosai_work start")
                result=self.app.start_feature(str(args["description"]),provider=self.provider,workspace_strategy=str(args.get("workspace_strategy") or "interactive_branch"),at_provider_boundary=False)
                self._refresh_provider_session(result.get("id")); return result
            if action=="status": return self.app.status(args.get("work_id"))
            if action=="clarify":
                question=str(args.get("question") or "").strip()
                if not question: raise ValueError("question required for dynosai_work clarify")
                wid=str(args.get("work_id") or self._session_work_id() or "")
                if not wid: raise ValueError("work_id required for dynosai_work clarify")
                session=self.app.sessions.active(self.provider,wid)
                interaction=self.app.interactions.clarification(wid,question,session_id=(session or {}).get("id"),provider=self.provider)
                self.app.events.emit("HumanInteractionRequested",work_id=wid,session_id=(session or {}).get("id"),payload={"interaction_id":interaction.id,"kind":"clarification","gate":None})
                return {"work_id":wid,"human_interaction":interaction.to_dict()}
            if action=="cancel":
                wid=args.get("work_id") or self._session_work_id(); work=self.engine.get_work(wid)
                self.engine.db.execute("UPDATE work_items SET active=0,updated_at=? WHERE id=?",(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),work["id"]))
                self.app.sessions.close_for_work(work["id"],"cancelled")
                self.app.events.emit("WorkCancelled",work_id=work["id"],session_id=work.get("provider_session_id"),payload={"provider":self.provider})
                self._refresh_provider_session()
                return {"cancelled":True,"work_id":work["id"]}
            raise ValueError("dynosai_work action must be start, status, clarify or cancel")
        if name=="dynosai_resume":
            result=self.app.resume(self.provider,args.get("work_id"),at_provider_boundary=False); self._refresh_provider_session(result["work"]["id"]); return result
        if name=="dynosai_events": return self.app.list_events(after_id=int(args.get("after_id",0)),work_id=args.get("work_id"),limit=int(args.get("limit",100)))
        if self.session and name=="dynosai_start": raise PermissionError("an active run session cannot start another work item")
        if name=="dynosai_project_overview":
            try:active=e.get_work()
            except Exception:active=None
            return {"project":e.db.get_meta("project_name"),"active_work":active,"git_commit":e.git.head(),"session":self.session and {"id":self.session["id"],"agent":self.session["agent"],"run_id":self.session.get("run_id"),"worktree":self.session["worktree"]},"protocol":self.negotiated}
        if name=="dynosai_get_next_action":
            wid=self._scoped_work(args)
            auto_default=os.environ.get("DYNOSAI_MANAGED_AGENT")=="1" and os.environ.get("DYNOSAI_AUTO_ADVANCE","1")=="1"
            execute=bool(args.get("execute",auto_default))
            if not wid:
                bootstrap=self._bootstrap_orchestrator_contract()
                if bootstrap is not None:
                    bootstrap["tool_surface"]=phase_tool_surface("discovery")
                    bootstrap["tool_surface"]["dynamic_refresh_requested"]=os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"}
                    return bootstrap
            result=self.app.next_action(self.provider,wid,execute)
            self._refresh_provider_session((result.get("work") or {}).get("id"))
            activity=activity_for_state(((result.get("work") or {}).get("state")))
            result["tool_surface"]=phase_tool_surface(activity)
            result["tool_surface"]["dynamic_refresh_requested"]=os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"}
            if result.get("human_interaction") and not self.client_capabilities.elicitation_form:
                result["provider_degraded_mode"]=True
                result["message"]="Client did not advertise MCP Elicitation. The human gate remains pending; resolve it from another DynosAI UI/CLI, never self-approve."
            return result
        if name=="dynosai_start":return e.start(args["description"],args.get("work_type","auto"),bool(args.get("quick",False)),bool(args.get("dry_run",False)))
        if name=="dynosai_continue":return e.continue_work(self._scoped_work(args))
        if name=="dynosai_board":return e.board(args.get("state"),args.get("work_type"))
        if name=="dynosai_ask":return e.ask(args["query"],int(args.get("limit",10)))
        if name=="dynosai_context_preview":return e.context_preview(self._scoped_work(args),int(args.get("max_tokens",3000)),args.get("task_id"))
        if name=="dynosai_checkpoint":
            wid=self._scoped_work(args,required=True)
            return ContextCheckpointStore(e.root).load_sections(wid,list(args.get("sections") or ["work","tasks","validations"]))
        if name=="dynosai_retrieve_handle":
            return self.handles.retrieve(str(args["handle_id"]),offset=int(args.get("offset") or 0),limit=args.get("limit"),section=args.get("section"))
        if name=="dynosai_register_decision":
            wid=self._scoped_work(args,required=True)
            if args.get("decisions"):
                registered=[e.register_decision(wid,str(item["question"]),str(item["answer"])) for item in args["decisions"]]
                return {"work_id":wid,"count":len(registered),"registered":registered}
            return e.register_decision(wid,args["question"],args["answer"])
        if name=="dynosai_submit_spec":return e.submit_spec(self._scoped_work(args,required=True),dict(args["spec"]),"mcp-agent")
        if name=="dynosai_submit_plan":return e.submit_plan(self._scoped_work(args,required=True),dict(args["plan"]),"mcp-agent")
        if name=="dynosai_register_result":
            wid=self._scoped_work(args,required=True)
            result=e.register_result(wid,args["summary"],list(args.get("files_changed",[])),list(args.get("tests",[])),list(args.get("completed_tasks",[])) or None,self._run_id())
            # Task completion changes the authoritative execution queue inside the
            # same workflow phase. Refresh the structured checkpoint here so the
            # next action can reuse it instead of replaying the approved plan.
            try:
                checkpoint=ContextCheckpointStore(e.root).capture(e,wid,label="task-progress",reason="task_result")
                result={**result,"context_checkpoint":checkpoint}
                e.db.audit("ContextCheckpointRefreshed",wid,{"reason":"task_result","ref":checkpoint.get("ref")})
            except Exception:
                pass
            # Completing the final execution wave is enough evidence
            # to cross the deterministic implementation -> code_review boundary.
            # Previously the model had to spend another inference just to call
            # dynosai_get_next_action and discover a transition DynosAI already
            # knew was legal. Auto-advance here and return the code gate in the
            # same MCP tool call. Human approval remains mandatory.
            if result.get("status")=="verified" and int(result.get("remaining_tasks") or 0)==0:
                advanced=self.app.next_action(self.provider,wid,True)
                try:
                    e.db.audit("ExecutionWaveAutoAdvanced",wid,{"run_id":result.get("run_id"),"from_state":"implementing","to_state":((advanced.get("work") or {}).get("state")),"remaining_tasks":0})
                except Exception:
                    pass
                advanced={**advanced,"registration_result":result,"execution_wave_auto_advanced":True}
                activity=activity_for_state(((advanced.get("work") or {}).get("state")))
                advanced["tool_surface"]=phase_tool_surface(activity)
                advanced["tool_surface"]["dynamic_refresh_requested"]=os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"}
                return self._compact_managed_result("dynosai_get_next_action",advanced)
            return result
        if name=="dynosai_request_scope_extension":
            wid=self._scoped_work(args,required=True)
            scope=e.request_scope_extension(wid,args["path"],args["action"],args["reason"],self._run_id(),args.get("task_id"))
            if scope.get("status")=="pending":
                session=self.app.sessions.active(self.provider,wid)
                interaction=self.app.interactions.scope_request(scope,session_id=(session or {}).get("id"),provider=self.provider)
                self.app.events.emit("HumanInteractionRequested",work_id=wid,session_id=(session or {}).get("id"),payload={"interaction_id":interaction.id,"kind":"scope_extension","gate":"scope"})
                return {"scope_request":scope,"human_interaction":interaction.to_dict()}
            return scope
        if name=="dynosai_find_symbol":return e.retrieval.find_symbol(args["query"],int(args.get("limit",20)),self._run_id())
        if name=="dynosai_read":
            def one_read(item:dict[str,Any])->Any:
                if "symbol_id" in item:
                    if self._run_id():
                        base=e.db.one("SELECT path FROM symbols WHERE id=?",(item["symbol_id"],))
                        if base: e.indexer.refresh_overlay(self._run_id(),self._session_root(),[base["path"]])
                    value=e.retrieval.get_symbol(item["symbol_id"],bool(item.get("include_body",True)),self._session_root(),self._run_id())
                else:
                    value=e.retrieval.file_slice(item["path"],int(item["start_line"]),int(item["end_line"]),self._session_root())
                cache_key=json.dumps(item,ensure_ascii=False,sort_keys=True,default=str,separators=(",",":"))
                return self._read_with_cache(cache_key,value)
            if args.get("requests"):
                results=[one_read(item) for item in args["requests"]]
                omitted=sum(int(((x.get("evidence_cache") or {}).get("estimated_bytes_omitted")) or 0) for x in results if isinstance(x,dict))
                return {"count":len(results),"results":results,"response_optimization":{"mode":"evidence_cache","estimated_bytes_omitted":omitted}}
            return one_read(args)
        if name=="dynosai_get_symbol":
            if self._run_id():
                base=e.db.one("SELECT path FROM symbols WHERE id=?",(args["symbol_id"],))
                if base: e.indexer.refresh_overlay(self._run_id(),self._session_root(),[base["path"]])
            return e.retrieval.get_symbol(args["symbol_id"],bool(args.get("include_body",True)),self._session_root(),self._run_id())
        if name=="dynosai_get_file_slice":return e.retrieval.file_slice(args["path"],int(args["start_line"]),int(args["end_line"]),self._session_root())
        if name=="dynosai_git_status":return e.agent_git_status(self._run_id())
        if name=="dynosai_git_diff":return e.agent_git_diff(self._run_id(),int(args.get("max_chars",30000)))
        if name=="dynosai_refresh_overlay":
            if not self._run_id(): raise ValueError("run session required")
            work=e.get_work(); actual=e.git.diff_files(self._session_root(),(e.db.one("SELECT base_commit FROM runs WHERE id=?",(self._run_id(),)) or {}).get("base_commit")); return e.indexer.refresh_overlay(self._run_id(),self._session_root(),actual)
        if name=="dynosai_analyze_impact":return e.retrieval.impact(args["query"],int(args.get("limit",12)))
        if name=="dynosai_schedule":
            wid=self._scoped_work(args)
            if not e.harness_feature("team_scheduler"):
                return {
                    "error":"feature_disabled",
                    "feature":"team_scheduler",
                    "message":"Governed team scheduling is disabled. Continue with ordinary single-session serial work.",
                    "serial_fallback":True,
                    "claimed_leases_remain_finishable":True,
                    "work_id":wid,
                }
            plan=e.schedule(wid); plan["fan_in"]=e.fan_in_report(wid); return plan
        if name=="dynosai_semantic_status":return e.model_status(bool(args.get("probe",False)))
        if name=="dynosai_sync":return e.sync()
        if name=="dynosai_stats":return e.stats()
        if name=="dynosai_run_validation":
            wid=self._scoped_work(args,required=True)
            result=e.run_validations(wid,record=True,cwd=self._session_root())
            if self.provider in {"cursor","codex"}:
                try:
                    work=e.get_work(wid)
                    outputs="\n".join(str(x.get("output") or "") for x in (result.get("results") or []))
                    exit_codes=[x.get("exit_code") for x in (result.get("results") or []) if x.get("exit_code") is not None]
                    pending_rows=e.db.query("SELECT id,files,state FROM tasks WHERE work_id=? AND state!='completed' ORDER BY id",(wid,))
                    pending_files=[]
                    for _task in pending_rows:
                        try:
                            pending_files.extend(json.loads(_task.get("files") or "[]"))
                        except Exception:
                            pass
                    failure_kind=None if result.get("passed") else classify_validation_failure(
                        outputs, exit_code=(next((x for x in exit_codes if int(x)!=0),None)), pending_planned_files=pending_files
                    )
                    fingerprint=None if result.get("passed") else hashlib.sha256((outputs+"|"+",".join(str(x) for x in exit_codes)).encode("utf-8",errors="replace")).hexdigest()[:20]
                    control=ModelControlPlane(e.root).record_outcome(self.provider,wid,success=bool(result.get("passed")),failure_kind=failure_kind,activity=activity_for_state(work.get("state")),failure_fingerprint=fingerprint)
                    result={**result,"model_control_outcome":control,"failure_kind":failure_kind,"failure_fingerprint":fingerprint}
                    if failure_kind=="validation_precondition":
                        result["validation_precondition"]={
                            "reason":"planned_test_artifacts_pending",
                            "pending_tasks":[str(x.get("id")) for x in pending_rows],
                            "pending_files":sorted({str(x) for x in pending_files}),
                            "next_action":"complete_pending_planned_tasks_before_retrying_validation",
                            "scope_extension_required":False,
                            "counts_as_model_failure":False,
                            "eligible_for_predictive_learning":False,
                        }
                except Exception:
                    pass
            return result
        raise KeyError(name)
    @staticmethod
    def _response(rid:Any,result:Any)->dict[str,Any]:return {"jsonrpc":"2.0","id":rid,"result":result}
    @staticmethod
    def _error(rid:Any,code:int,message:str,data:Any=None)->dict[str,Any]:
        error={"code":code,"message":message}
        if data is not None:
            error["data"]=data
        return {"jsonrpc":"2.0","id":rid,"error":error}


def _write_message(message:dict[str,Any])->None:
    sys.stdout.write(json.dumps(message,ensure_ascii=False,default=str)+"\n"); sys.stdout.flush()


def _result_summary(result:Any,tool_name:str|None=None)->dict[str,Any]:
    """Small human-readable MCP content block; structuredContent is authoritative.

    Codex exposes MCP structuredContent to the managed model, so repeating the
    whole JSON object in content.text doubles transport/context without adding
    information. Cursor CLI provider streams observed in RC validation may omit
    structuredContent from the model-visible result, so Cursor deliberately keeps
    the complete JSON in content.text. Generic/unknown clients also retain full text.
    """
    summary:dict[str,Any]={"dynosai":"structured_result","tool":tool_name or "dynosai","authoritative":"structuredContent"}
    if isinstance(result,dict):
        work=result.get("work")
        if isinstance(work,dict):
            if work.get("id") is not None: summary["work_id"]=work.get("id")
            if work.get("state") is not None: summary["state"]=work.get("state")
        for key in ("id","work_id","state","status","accepted","passed","count","error_type"):
            value=result.get(key)
            if value is None or isinstance(value,(dict,list)): continue
            summary.setdefault(key,value)
        interaction=result.get("human_interaction")
        if isinstance(interaction,dict):
            summary["human_interaction"]={k:interaction.get(k) for k in ("id","kind","gate","status") if interaction.get(k) is not None}
        snapshot=result.get("action_snapshot")
        if isinstance(snapshot,dict):
            summary["action_snapshot"]={k:snapshot.get(k) for k in ("ref","unchanged") if snapshot.get(k) is not None}
    return summary


def _tool_result_payload(result:Any,*,provider:str="generic",tool_name:str|None=None)->dict[str,Any]:
    is_object=isinstance(result,dict)
    structured=result if is_object else ({"items":result} if isinstance(result,list) else {"value":result})
    # Work on a shallow copy so transport telemetry never mutates engine state.
    structured=dict(structured)
    full_text=json.dumps(result,ensure_ascii=False,indent=2,default=str)
    configured=(os.environ.get("DYNOSAI_MCP_RESULT_TEXT_MODE") or "auto").strip().lower()
    structured_primary=(configured=="structured") or (configured=="auto" and str(provider).lower() in {"codex","openai"})
    if structured_primary:
        text=json.dumps(_result_summary(result,tool_name),ensure_ascii=False,separators=(",",":"),default=str)
        saved=max(0,len(full_text.encode("utf-8"))-len(text.encode("utf-8")))
        if is_object:
            optimization=dict(structured.get("response_optimization") or {})
            optimization.update({
                "transport_mode":"structured_primary",
                "transport_text_bytes_omitted":saved,
                "structured_content_authoritative":True,
            })
            structured["response_optimization"]=optimization
    else:
        text=full_text
        if is_object:
            optimization=dict(structured.get("response_optimization") or {})
            optimization.update({
                "transport_mode":"full_text_compatibility",
                "transport_text_bytes_omitted":0,
                "structured_content_authoritative":True,
            })
            structured["response_optimization"]=optimization
    return {"content":[{"type":"text","text":text}],"structuredContent":structured,"isError":False}


def _tool_result_response(rid:Any,result:Any,*,provider:str="generic",tool_name:str|None=None)->dict[str,Any]:
    return MCPServer._response(rid,_tool_result_payload(result,provider=provider,tool_name=tool_name))


def _wire_bytes(value:Any)->int:
    try:return len(json.dumps(value,ensure_ascii=False,default=str,separators=(",",":")).encode("utf-8"))
    except Exception:return 0


def _acceptance_trace(event: dict[str, Any]) -> None:
    path=os.environ.get("DYNOSAI_ACCEPTANCE_TRACE")
    if not path: return
    try:
        target=Path(path).expanduser().resolve(); target.parent.mkdir(parents=True,exist_ok=True)
        with target.open("a",encoding="utf-8") as fh:
            fh.write(json.dumps(event,ensure_ascii=False,default=str)+"\n")
    except Exception:
        # Acceptance observability must never change workflow semantics.
        pass




def _acceptance_process_trace(event: dict[str, Any]) -> None:
    paths=[os.environ.get("DYNOSAI_ACCEPTANCE_PROCESS_TRACE"),os.environ.get("DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR")]
    if not any(paths): return
    payload={"at":utc_now(),**event}
    for raw_path in paths:
        if not raw_path: continue
        try:
            target=Path(raw_path).expanduser().resolve(); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("a",encoding="utf-8") as fh:
                fh.write(json.dumps(payload,ensure_ascii=False,default=str)+"\n")
                fh.flush()
                try: os.fsync(fh.fileno())
                except OSError: pass
        except Exception:
            # Diagnostic tracing must never change MCP behavior.
            pass

def _resolve_exchange(server:MCPServer,response:ElicitationExchange,action:str,content:dict[str,Any])->dict[str,Any]:
    interaction=response.interaction
    if interaction.get("ephemeral") and interaction.get("kind")=="project_bootstrap":
        decision=str(content.get("decision") or ("cancel" if action!="accept" else "")).lower()
        bootstrap=interaction.get("bootstrap") or {}
        if action!="accept" or decision=="cancel":
            refreshed={"action":"cancel","detection":response.base_result.get("detection"),"cancelled":True}
        elif decision=="analysis_only":
            refreshed={"action":"analysis_only","detection":response.base_result.get("detection"),"message":"No project files were mutated. Resolve Git/bootstrap requirements before governed implementation."}
        elif decision=="initialize_git":
            refreshed=server.call_tool("dynosai_project",{"action":"initialize","root":bootstrap.get("root"),"name":bootstrap.get("name"),"language":bootstrap.get("language"),"test_command":bootstrap.get("test_command"),"allow_git_init":True})
        elif decision=="whole_repository":
            refreshed=server.call_tool("dynosai_project",{"action":"initialize","root":bootstrap.get("root"),"name":bootstrap.get("name"),"language":bootstrap.get("language"),"test_command":bootstrap.get("test_command"),"confirm_monorepo":True})
        else:
            raise ValueError(f"Unsupported project bootstrap decision: {decision}")
        refreshed["elicitation"]={"id":interaction["id"],"action":action,"content":content,"resolved":True,"ephemeral":True}
        return refreshed
    resolution=server.app.resolve_interaction(interaction["id"],action,content)
    wid=(resolution.get("work") or {}).get("id") or interaction.get("work_id")
    execute_after_gate=False
    if wid and action=="accept" and response.requested_execute and interaction.get("kind")=="gate_approval" and interaction.get("gate")=="plan":
        decision=str(content.get("decision") or "approve").lower()
        resolved_state=str((resolution.get("work") or {}).get("state") or "")
        execute_after_gate=(decision=="approve" and resolved_state=="ready")
    refreshed=server.app.next_action(server.provider,wid,execute_after_gate) if wid and action=="accept" else response.base_result
    if execute_after_gate and isinstance(refreshed,dict):
        refreshed["ready_to_implementation_collapsed"]=True
        try:
            server.engine.db.audit("ReadyImplementationCollapsed",wid,{"gate":interaction.get("gate"),"requested_execute":True,"to_state":((refreshed.get("work") or {}).get("state"))})
        except Exception:
            pass
    if wid and action=="accept" and isinstance(refreshed,dict):
        # The suspended tool call resumes with a fresh authoritative action. Route
        # it through the same managed delta compressor as an ordinary
        # dynosai_get_next_action call; replaying the full
        # post-gate contracts/checkpoints here.
        activity=activity_for_state(((refreshed.get("work") or {}).get("state")))
        refreshed["tool_surface"]=phase_tool_surface(activity)
        refreshed["tool_surface"]["dynamic_refresh_requested"]=os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"}
        refreshed=server._compact_managed_result("dynosai_get_next_action",refreshed)
    refreshed["elicitation"]={"id":interaction["id"],"action":action,"content":content,"resolved":True}
    return refreshed


def _workflow_state_from_result(value: Any) -> str | None:
    """Extract authoritative workflow state for diagnostics without changing MCP payloads."""
    if not isinstance(value, dict):
        return None
    for key in ("state", "work_state"):
        candidate=value.get(key)
        if isinstance(candidate,str) and candidate:
            return candidate
    work=value.get("work")
    if isinstance(work,dict) and isinstance(work.get("state"),str):
        return str(work["state"])
    for key in ("next_action", "next"):
        child=value.get(key)
        if isinstance(child,dict):
            found=_workflow_state_from_result(child)
            if found: return found
    structured=value.get("structuredContent")
    if isinstance(structured,dict):
        found=_workflow_state_from_result(structured)
        if found: return found
    result=value.get("result")
    if isinstance(result,dict):
        found=_workflow_state_from_result(result)
        if found: return found
    return None


def serve(project:Path)->int:
    server=MCPServer(project); elicitation_counter=0
    iterator=iter(sys.stdin)
    for raw in iterator:
        line=raw.strip()
        if not line: continue
        method=None; tool_name=None; message=None
        try:
            message=json.loads(line)
            method=message.get("method") if isinstance(message,dict) else None
            params=(message.get("params") or {}) if isinstance(message,dict) else {}
            tool_name=None; tool_args={}; activity_before=None
            if method=="tools/call":
                tool_name=params.get("name")
                tool_args=params.get("arguments") or {}
                if os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"}:
                    activity_before=server._current_activity()
                _acceptance_process_trace({"event":"mcp_tool_start","provider":server.provider,"request_id":message.get("id"),"tool":tool_name,"work_id":tool_args.get("work_id"),"action":tool_args.get("action"),"argument_keys":sorted(tool_args.keys())})
            response=server.handle(message)
            if isinstance(response,ElicitationExchange):
                elicitation_counter+=1
                eid=f"dynosai-elicitation-{elicitation_counter}"
                interaction=response.interaction
                request={"mode":"form","message":interaction["message"],"requestedSchema":interaction["requested_schema"]}
                # Acceptance-only unattended responder. Cursor's headless client has
                # no documented API for programmatically filling MCP Elicitation
                # forms, so the real Cursor agent may be exercised with this flag
                # while the UI transport remains a separate interactive gate.
                if os.environ.get("DYNOSAI_ACCEPTANCE_AUTORESPOND")=="1":
                    content=automated_elicitation_content(request,message=interaction.get("message") or "")
                    _acceptance_trace({"event":"elicitation/create","id":eid,"interaction_id":interaction.get("id"),"provider":server.provider,"request":request,"auto_response":{"action":"accept","content":content},"transport":"dynosai-test-autoresponder"})
                    try:
                        server.engine.db.audit("AcceptanceElicitationAutoResponded",interaction.get("id") or eid,{"provider":server.provider,"content":content})
                    except Exception:
                        pass
                    refreshed=_resolve_exchange(server,response,"accept",content)
                    wire=_tool_result_response(response.tool_request_id,refreshed,provider=server.provider,tool_name=tool_name)
                    _acceptance_process_trace({"event":"mcp_tool_end","provider":server.provider,"request_id":response.tool_request_id,"tool":tool_name,"status":"elicitation_auto_resolved","workflow_state":_workflow_state_from_result(refreshed),"response_optimization":(refreshed.get("response_optimization") if isinstance(refreshed,dict) else None),"response_bytes":_wire_bytes(wire)})
                    _write_message(wire)
                    if os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"} and activity_before and server._current_activity()!=activity_before:
                        _acceptance_process_trace({"event":"tool_surface_changed","provider":server.provider,"activity":server._current_activity(),"tool_count":phase_tool_surface(server._current_activity()).get("tool_count")}); _write_message({"jsonrpc":"2.0","method":"notifications/tools/list_changed"})
                    continue
                _write_message({"jsonrpc":"2.0","id":eid,"method":"elicitation/create","params":request})
                elicitation_response=None
                # MCP request/response is bidirectional. While the original tool call
                # is suspended, the client answers this server-originated request.
                for pending_raw in iterator:
                    pending_raw=pending_raw.strip()
                    if not pending_raw: continue
                    incoming=json.loads(pending_raw)
                    if incoming.get("id")==eid and ("result" in incoming or "error" in incoming):
                        elicitation_response=incoming; break
                    # Keep protocol notifications healthy while awaiting the human.
                    incidental=server.handle(incoming)
                    if incidental is not None and not isinstance(incidental,ElicitationExchange): _write_message(incidental)
                if not elicitation_response:
                    raise RuntimeError("MCP client disconnected while DynosAI awaited Elicitation")
                if elicitation_response.get("error"):
                    action="cancel"; content={}
                else:
                    er=elicitation_response.get("result") or {}; action=str(er.get("action") or "cancel"); content=er.get("content") or {}
                _acceptance_trace({"event":"elicitation/create","id":eid,"interaction_id":interaction.get("id"),"provider":server.provider,"request":request,"client_response":{"action":action,"content":content},"transport":"provider"})
                refreshed=_resolve_exchange(server,response,action,content)
                wire=_tool_result_response(response.tool_request_id,refreshed,provider=server.provider,tool_name=tool_name)
                _acceptance_process_trace({"event":"mcp_tool_end","provider":server.provider,"request_id":response.tool_request_id,"tool":tool_name,"status":"elicitation_resolved","action":action,"workflow_state":_workflow_state_from_result(refreshed),"response_optimization":(refreshed.get("response_optimization") if isinstance(refreshed,dict) else None),"response_bytes":_wire_bytes(wire)})
                _write_message(wire)
                if os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"} and activity_before and server._current_activity()!=activity_before:
                    _acceptance_process_trace({"event":"tool_surface_changed","provider":server.provider,"activity":server._current_activity(),"tool_count":phase_tool_surface(server._current_activity()).get("tool_count")}); _write_message({"jsonrpc":"2.0","method":"notifications/tools/list_changed"})
            elif response is not None:
                if method=="tools/call":
                    is_error=bool(isinstance(response,dict) and response.get("error"))
                    result=(response.get("result") or {}) if isinstance(response,dict) else {}
                    if isinstance(result,dict): is_error=is_error or bool(result.get("isError"))
                    structured=(result.get("structuredContent") or {}) if isinstance(result,dict) else {}
                    _acceptance_process_trace({"event":"mcp_tool_end","provider":server.provider,"request_id":message.get("id"),"tool":tool_name,"status":"error" if is_error else "ok","workflow_state":_workflow_state_from_result(response),"response_optimization":structured.get("response_optimization"),"tool_surface":structured.get("tool_surface"),"response_bytes":_wire_bytes(response)})
                _write_message(response)
                if method=="tools/call" and os.environ.get("DYNOSAI_PHASE_TOOL_DISCLOSURE") in {"dynamic","adaptive"} and activity_before and server._current_activity()!=activity_before:
                    _acceptance_process_trace({"event":"tool_surface_changed","provider":server.provider,"activity":server._current_activity(),"tool_count":phase_tool_surface(server._current_activity()).get("tool_count")}); _write_message({"jsonrpc":"2.0","method":"notifications/tools/list_changed"})
        except Exception as exc:
            try:
                if 'method' in locals() and method=="tools/call":
                    _acceptance_process_trace({"event":"mcp_tool_end","provider":server.provider,"request_id":message.get("id") if isinstance(message,dict) else None,"tool":tool_name,"status":"exception","error_type":type(exc).__name__,"message":str(exc)[:1000]})
            except Exception:
                pass
            sys.stderr.write(traceback.format_exc());sys.stderr.flush()
    return 0

def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser();p.add_argument("--project",default=None);a=p.parse_args(argv);return serve(discover_project_root(a.project))

if __name__=="__main__":raise SystemExit(main())
