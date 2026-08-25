# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Isolated diagnostics and end-to-end debug scenario orchestration."""

from __future__ import annotations

import json
import os
import shutil
import sys
import subprocess
import tempfile
import time
import traceback
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .engine import DynosAI
from .util import utc_now
from .debug_fixtures import seed_supportdesk_brownfield, seed_orderflow_brownfield


SCENARIOS: dict[str, dict[str, Any]] = {
    "fibonacci": {
        "name": "Fibonacci CLI",
        "mode": "greenfield",
        "description": (
            "Crear una aplicación CLI en Python que reciba un número entero N y muestre exactamente N "
            "términos de Fibonacci separados por espacios. Debe rechazar valores negativos y entradas no numéricas."
        ),
        "decisions": [
            {"question":"¿Cómo se invoca la aplicación y cómo se pasa N?","answer":"N se pasa como argumento de línea de comandos y la aplicación se ejecuta como: python -m fibonacci N."},
            {"question":"¿Cuál es el inicio de la secuencia de Fibonacci?","answer":"La secuencia empieza por 0 1 1 2 3... (F0=0, F1=1)."},
            {"question":"¿N=0 es una entrada válida y qué debe ocurrir?","answer":"N=0 es válido: stdout vacío y exit code 0."},
            {"question":"¿Cómo se tratan negativos y entradas no numéricas?","answer":"Negativos o entradas no numéricas: mensaje claro en stderr y exit code 1; el texto exacto no está fijado."},
            {"question":"¿Qué ocurre si se omite N o hay argumentos extra?","answer":"Es uso inválido: mensaje claro en stderr y código de salida 1."},
            {"question":"¿Stdout debe terminar en salto de línea cuando N>0?","answer":"Sí: un único salto de línea final si N>0; si N=0 stdout queda completamente vacío."},
            {"question":"¿Cómo se tratan literales no enteros y el signo +?","answer":"Solo enteros base 10, con signo + opcional. Literales como 3.5 son inválidos; +N equivale a N."},
        ],
        "test_command": "python -m unittest discover -s tests",
        "language": "python",
    },
    "supportdesk-premium-sla": {
        "name": "SupportDesk premium critical SLA",
        "mode": "brownfield",
        "description": (
            "En el SupportDesk existente, añadir escalado prioritario para tickets critical de clientes premium: "
            "SLA de 60 minutos y escalation_required=true. Los clientes standard y las severidades high/normal "
            "deben conservar el comportamiento actual. La API debe exponer el indicador sin enviar notificaciones."
        ),
        "decisions": [
            {"question":"¿Qué combinación activa la excepción?","answer":"Solo customer.tier=premium junto con severity=critical."},
            {"question":"¿Cuál es el SLA premium critical?","answer":"60 minutos y escalation_required=true."},
            {"question":"¿Qué ocurre con critical standard?","answer":"Conserva el SLA existente de 240 minutos y escalation_required=false."},
            {"question":"¿Qué ocurre con high y normal?","answer":"Conservan los SLA actuales: high=480 y normal=1440, sin escalado, también para premium."},
            {"question":"¿Qué cambia en la API?","answer":"El payload de creación conserva todos sus campos y expone también escalation_required; sla_minutes sigue expuesto."},
            {"question":"¿Se deben enviar notificaciones o añadir integraciones?","answer":"No. Esta feature no dispara notificaciones automáticas ni añade dependencias o persistencia externas."},
        ],
        "test_command": "python -m unittest discover -s tests",
        "language": "python",
    },
    "orderflow-contract-discounts": {
        "name": "OrderFlow contractual discounts migration",
        "mode": "brownfield",
        "description": (
            "En el OrderFlow SQLite existente, incorporar descuentos contractuales para clientes enterprise con porcentaje y fecha de vigencia. "
            "La solución debe usar una migración aditiva 002, preservar pedidos históricos, aplicar el descuento solo a pedidos nuevos cuando esté vigente, "
            "persistir original_total_cents/discount_cents/total_cents y extender la API conservando sus campos existentes."
        ),
        "decisions": [
            {"question":"¿Qué clientes pueden recibir descuento contractual?","answer":"Solo clientes con segment=enterprise; standard nunca recibe descuento contractual."},
            {"question":"¿Cómo se representa la vigencia?","answer":"Cada cliente puede tener discount_percent entero y discount_valid_from en formato ISO YYYY-MM-DD; la fecha es inclusiva."},
            {"question":"¿Cómo se calcula y redondea?","answer":"discount_cents = original_total_cents * discount_percent // 100 usando solo aritmética entera de céntimos; total_cents es el importe final y nunca negativo."},
            {"question":"¿Qué ocurre con pedidos históricos?","answer":"No se recalculan ni actualizan. Tras aplicar 002, las nuevas columnas original_total_cents y discount_cents de filas preexistentes deben permanecer físicamente NULL (sin backfill ni DEFAULT que cambie su lectura raw); el repositorio interpreta original_total_cents NULL como el total histórico y discount_cents NULL como cero."},
            {"question":"¿Qué debe cambiar en la base de datos?","answer":"Crear una migración 002 forward-only que añada configuración de descuento a customers y desglose a orders sin eliminar ni renombrar columnas; debe ser idempotente mediante schema_migrations."},
            {"question":"¿Qué compatibilidad exige la API?","answer":"Mantener id, customer_id, total_cents y status y añadir original_total_cents y discount_cents."},
            {"question":"¿Qué dominios quedan fuera?","answer":"Payments, invoices, notifications y reporting no deben modificarse; tampoco se añaden dependencias externas."},
            {"question":"¿Cómo se revierte una incidencia de despliegue?","answer":"La migración no hace downgrade destructivo; se restaura el backup SQLite previo o se revierte la aplicación manteniendo el esquema aditivo sin usar las columnas nuevas."},
        ],
        "test_command": "python -m unittest discover -s tests",
        "language": "python",
    },
    "green-brown-gate": {
        "name": "DynosAI combined greenfield+brownfield gate",
        "mode": "suite",
        "children": ["fibonacci", "orderflow-contract-discounts"],
        "description": "Gate único de certificación 0.7: debe superar un greenfield completo y un brownfield SQLite con migración y backward compatibility.",
    },

}



@dataclass
class DebugStage:
    name: str
    started_at: str
    finished_at: str | None = None
    ok: bool | None = None
    details: dict[str, Any] | None = None


class DebugE2ERunner:
    """Deterministic E2E harness for a real coding-agent integration.

    The harness uses an isolated synthetic project and deterministic business
    decisions. It is allowed to auto-approve review gates *only* inside this
    debug scenario. Normal DynosAI workflow semantics are unchanged.
    """

    def __init__(
        self,
        agent: str = "cursor",
        scenario: str = "fibonacci",
        output: str | Path | None = None,
        keep: bool = True,
        timeout: int = 600,
        workspace: str | Path | None = None,
        agent_runner: Callable[..., dict[str, Any]] | None = None,
    ):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown debug scenario: {scenario}")
        if agent != "cursor":
            raise ValueError("DynosAI 0.7 debug e2e currently certifies the real Cursor integration")
        self.agent = agent
        self.scenario_name = scenario
        self.scenario = SCENARIOS[scenario]
        self.keep = keep
        self.timeout = int(timeout)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = Path(workspace).expanduser().resolve() if workspace else Path.home() / ".dynosai" / "debug-runs" / f"DEBUG-{stamp}"
        self.base = base
        self.project = base / "project"
        self.logs = base / "logs"
        self.output = Path(output).expanduser().resolve() if output else base / f"dynosai-debug-e2e-{stamp}.zip"
        self.timeline_path = self.logs / "timeline.jsonl"
        self.summary_path = self.logs / "summary.json"
        self.stages: list[DebugStage] = []
        self.engine: DynosAI | None = None
        self.work_id: str | None = None
        self.failure: dict[str, Any] | None = None
        self.agent_runner = agent_runner or self._run_cursor_phase

    def _emit(self, event: str, **payload: Any) -> None:
        self.logs.mkdir(parents=True, exist_ok=True)
        row = {"at": utc_now(), "event": event, **payload}
        with self.timeline_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def _stage(self, name: str) -> DebugStage:
        stage = DebugStage(name=name, started_at=utc_now())
        self.stages.append(stage)
        self._emit("stage_started", stage=name)
        print(f"[DynosAI debug] {name} ...", flush=True)
        return stage

    def _finish(self, stage: DebugStage, ok: bool, **details: Any) -> None:
        stage.finished_at = utc_now(); stage.ok = ok; stage.details = details
        self._emit("stage_finished", stage=stage.name, ok=ok, details=details)
        print(f"[DynosAI debug] {stage.name}: {'PASS' if ok else 'FAIL'}", flush=True)

    def _snapshot(self, label: str) -> dict[str, Any]:
        assert self.engine is not None
        work = self.engine.get_work(self.work_id) if self.work_id else None
        snap = {
            "label": label,
            "work": work,
            "board": self.engine.board(),
            "runs": self.engine.db.query("SELECT id,work_id,agent,status,started_at,finished_at,verification_status,worktree,session_id,task_ids FROM runs ORDER BY id"),
            "tasks": self.engine.db.query("SELECT id,work_id,state,owner,claimed_run,depends_on,files,updated_at FROM tasks ORDER BY id"),
            "artifacts": self.engine.db.query("SELECT id,work_id,kind,status,version,created_at,updated_at FROM artifacts ORDER BY id"),
        }
        path = self.logs / f"snapshot-{label}.json"
        path.write_text(json.dumps(snap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return snap

    def _assert_state(self, expected: str, label: str) -> None:
        assert self.engine is not None and self.work_id
        actual = self.engine.get_work(self.work_id)["state"]
        self._emit("state_assertion", label=label, expected=expected, actual=actual, ok=actual == expected)
        if actual != expected:
            raise AssertionError(f"{label}: expected state {expected}, got {actual}")

    def _debug_approve(self, kind: str) -> dict[str, Any]:
        assert self.engine is not None and self.work_id
        before = self.engine.get_work(self.work_id)["state"]
        result = self.engine.review(self.work_id, kind)
        self.engine.db.audit("DebugHarnessApproval", self.work_id, {"kind": kind, "before": before, "result": result, "scenario": self.scenario_name})
        self._emit("debug_approval", kind=kind, before=before, result=result)
        return result

    def _prepare_agent_launch(self, prompt: str, phase: str) -> tuple[list[str], Path, dict[str, str], dict[str, Any]]:
        """Prepare the same managed session/worktree guarantees as dynosai open."""
        assert self.engine is not None
        engine = self.engine
        engine._ensure_ready()
        executable = shutil.which("cursor-agent")
        if not executable:
            raise RuntimeError("cursor-agent is not installed or not in PATH")
        engine.agents.connect("cursor")
        engine.db.set_meta("default_agent", "cursor")
        engine.git.commit_main_changes("chore: configure DynosAI agent")

        implementation_prepare = None
        try:
            work = engine.get_work(self.work_id)
            if work.get("state") == "ready":
                implementation_prepare = engine.continue_work(work["id"])
                work = engine.get_work(work["id"])
            active = work.get("state") in {"implementing", "code_review", "validating", "ready_to_merge"} and bool(work.get("worktree"))
        except Exception:
            work = None; active = False

        run = None; cwd = engine.root
        if active and work:
            cwd = Path(work["worktree"])
            run = engine.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1", (work["id"],))
            plan = engine.artifacts.artifact(work["id"], "plan")
            entries = ((plan or {}).get("metadata") or {}).get("files", [])
            write_scope = sorted({
                str(x.get("path", "")).replace("\\", "/")
                for x in entries
                if x.get("path") and str(x.get("action", "")).lower() in {"modify", "create", "delete", "test"}
            })
            generated = engine.agents.ensure_worktree_protocol(cwd, "cursor", write_scope=write_scope)
            rels = [str(x.relative_to(cwd)).replace("\\", "/") for x in generated]
            management_commit = engine.git.commit_management_worktree(cwd, rels, "chore: attach DynosAI cursor protocol")
            if run:
                engine.db.execute("UPDATE runs SET base_commit=? WHERE id=?", (management_commit, run["id"]))
                run = {**run, "base_commit": management_commit}

        session = engine.agents.create_session("cursor", work.get("id") if work else self.work_id, run.get("id") if run else None, cwd)
        env = engine.git_guard.environment({
            "DYNOSAI_PROJECT_ROOT": str(engine.root),
            "DYNOSAI_SESSION_TOKEN": session["token"],
            "DYNOSAI_SESSION_ID": session["session_id"],
            "DYNOSAI_MANAGED_AGENT": "1",
            "DYNOSAI_AGENT_PROVIDER": "cursor",
            "DYNOSAI_DEBUG_E2E": "1",
        })
        cmd = [executable, "-p", "--force", "--output-format", "stream-json", prompt]
        meta = {
            "phase": phase,
            "cwd": str(cwd),
            "session_id": session["session_id"],
            "run_id": run.get("id") if run else None,
            "work_id": work.get("id") if work else self.work_id,
            "implementation_prepare": implementation_prepare,
            "command": [Path(executable).name, "-p", "--force", "--output-format", "stream-json", "<prompt>"],
        }
        return cmd, cwd, env, meta

    def _run_cursor_phase(self, prompt: str, phase: str) -> dict[str, Any]:
        assert self.engine is not None
        cmd, cwd, env, meta = self._prepare_agent_launch(prompt, phase)
        stdout_path = self.logs / f"cursor-{phase}.stream.jsonl"
        stderr_path = self.logs / f"cursor-{phase}.stderr.log"
        started = time.monotonic()
        session_id = meta["session_id"]
        try:
            proc = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, timeout=self.timeout)
            stdout_path.write_text(proc.stdout or "", encoding="utf-8")
            stderr_path.write_text(proc.stderr or "", encoding="utf-8")
            result = {**meta, "exit_code": proc.returncode, "duration_s": round(time.monotonic() - started, 3), "stdout": str(stdout_path), "stderr": str(stderr_path)}
            self._emit("agent_phase", **result)
            if proc.returncode != 0:
                raise RuntimeError(f"Cursor phase {phase} failed with exit code {proc.returncode}")
            return result
        finally:
            self.engine.db.execute("UPDATE agent_sessions SET state='closed',last_seen=? WHERE id=?", (utc_now(), session_id))
            self.engine.runtime.close_session(session_id)

    def _cursor_stream_metrics(self, path: str | Path | None) -> dict[str, Any]:
        metrics: dict[str, Any] = {
            "path": str(path) if path else None, "usage": {}, "native_context_calls": [],
            "outside_cursor_transcript_access": [], "provider_output_spill_access": [],
            "mcp_output_spills": [], "tool_calls": 0, "mcp_result_errors": [], "client_mcp_dispatch_errors": [],
        }
        if not path or not Path(path).exists(): return metrics
        started_calls: dict[str, dict[str, Any]] = {}
        for line_no, raw in enumerate(Path(path).read_text(encoding="utf-8", errors="replace").splitlines(),1):
            try: row=json.loads(raw)
            except Exception: continue
            if row.get("type")=="result" and isinstance(row.get("usage"),dict):
                metrics["usage"] = dict(row["usage"])
                metrics["duration_ms"] = row.get("duration_ms")
            if row.get("type")!="tool_call": continue
            tool_call=row.get("tool_call") or {}
            if not isinstance(tool_call,dict) or not tool_call: continue
            call_id=str(tool_call.get("toolCallId") or row.get("call_id") or "")
            if row.get("subtype")=="started":
                metrics["tool_calls"] += 1
                mcp_call=tool_call.get("mcpToolCall") or {}
                mcp_args=mcp_call.get("args") or {}
                if call_id:
                    started_calls[call_id]={
                        "tool":mcp_args.get("toolName") or mcp_args.get("name"),
                        "arguments":mcp_args.get("args"),
                        "started_line":line_no,
                    }
                kind=next(iter(tool_call))
                if kind not in {"readToolCall","globToolCall","grepToolCall"}: continue
                args=(tool_call.get(kind) or {}).get("args") or {}
                target=args.get("path") or args.get("targetDirectory")
                item={"kind":kind,"path":target}
                metrics["native_context_calls"].append(item)
                normalized=str(target or "").replace("\\","/")
                # Only actual transcript storage is a transcript bypass. Cursor's
                # `agent-tools` directory is a different mechanism: it is where the
                # provider externalizes oversized tool results. Track it separately
                # so diagnostics name the real failure mode.
                if "/.cursor/projects/" in normalized and "/agent-transcripts/" in normalized:
                    metrics["outside_cursor_transcript_access"].append(item)
                if "/.cursor/projects/" in normalized and "/agent-tools/" in normalized:
                    metrics["provider_output_spill_access"].append(item)
                continue
            if row.get("subtype")!="completed": continue
            mcp_call=tool_call.get("mcpToolCall") or {}
            result=mcp_call.get("result") or {}
            start=started_calls.get(call_id,{})
            # Cursor replaces large MCP payloads with an outputLocation pointer.
            # Detect the spill at the transport boundary even if the agent never
            # attempts to read the provider-owned file.
            def collect_output_locations(value):
                found=[]
                if isinstance(value,dict):
                    loc=value.get("outputLocation")
                    if isinstance(loc,dict): found.append(loc)
                    for child in value.values(): found.extend(collect_output_locations(child))
                elif isinstance(value,list):
                    for child in value: found.extend(collect_output_locations(child))
                return found
            for loc in collect_output_locations(result):
                metrics["mcp_output_spills"].append({
                    "tool":start.get("tool"),"started_line":start.get("started_line"),
                    "completed_line":line_no,"path":loc.get("filePath"),
                    "size_bytes":int(loc.get("sizeBytes") or 0),"line_count":int(loc.get("lineCount") or 0),
                })
            if not isinstance(result,dict) or "error" not in result: continue
            # Cursor can fail in its *client-side MCP dispatcher* before a request
            # is associated with any server/tool. Those rows have no matching
            # started call and no server/tool identity in the completed payload.
            # They never reached DynosAI, so keep them visible as provider
            # telemetry but do not attribute them to the DynosAI MCP stream.
            completed_args=mcp_call.get("args") or {}
            completed_tool=completed_args.get("toolName") or completed_args.get("name")
            completed_server=completed_args.get("serverIdentifier") or completed_args.get("providerIdentifier")
            error_item={
                "tool":start.get("tool") or completed_tool,
                "arguments":start.get("arguments") if start else completed_args.get("args"),
                "started_line":start.get("started_line"),
                "completed_line":line_no,
                "error":result.get("error"),
            }
            attributable=bool(start) or bool(completed_tool) or bool(completed_server)
            if attributable:
                metrics["mcp_result_errors"].append(error_item)
            else:
                error_item["classification"]="client_dispatch_before_server"
                metrics["client_mcp_dispatch_errors"].append(error_item)
        return metrics

    def _efficiency_report(self) -> dict[str, Any]:
        phases: dict[str, Any] = {}
        for stage in self.stages:
            agent=(stage.details or {}).get("agent") if isinstance(stage.details,dict) else None
            if isinstance(agent,dict) and agent.get("stdout"):
                phases[stage.name]=self._cursor_stream_metrics(agent.get("stdout"))
        audit_failures=[]; audit_rejections=[]; audit_normalizations=[]; agent_decision_calls=[]
        if self.engine is not None:
            audit_failures=self.engine.db.query("SELECT id,event_type,entity_id,payload,created_at FROM audit WHERE event_type='MCPToolFailed' ORDER BY id")
            audit_rejections=self.engine.db.query("SELECT id,event_type,entity_id,payload,created_at FROM audit WHERE event_type='MCPToolRejected' ORDER BY id")
            audit_normalizations=self.engine.db.query("SELECT id,event_type,entity_id,payload,created_at FROM audit WHERE event_type='MCPToolNormalized' ORDER BY id")
            agent_decision_calls=self.engine.db.query("SELECT id,event_type,entity_id,payload,created_at FROM audit WHERE event_type='MCPToolCalled' AND entity_id='dynosai_register_decision' ORDER BY id")
        totals={"inputTokens":0,"outputTokens":0,"cacheReadTokens":0,"cacheWriteTokens":0}
        for phase in phases.values():
            usage=phase.get("usage") or {}
            for key in totals: totals[key]+=int(usage.get(key) or 0)
        stream_failures=[
            {"phase":phase_name,**item}
            for phase_name,phase in phases.items()
            for item in phase.get("mcp_result_errors",[])
        ]
        client_dispatch_errors=[
            {"phase":phase_name,**item}
            for phase_name,phase in phases.items()
            for item in phase.get("client_mcp_dispatch_errors",[])
        ]
        report={
            "phases": phases,
            "total_usage": totals,
            "mcp_failures": audit_failures,
            "mcp_stream_failures": stream_failures,
            "client_mcp_dispatch_errors": client_dispatch_errors,
            "mcp_validation_rejections": audit_rejections,
            "mcp_normalizations": audit_normalizations,
            "agent_decision_calls": agent_decision_calls,
            "outside_cursor_transcript_access": [item for phase in phases.values() for item in phase.get("outside_cursor_transcript_access",[])],
            "provider_output_spill_access": [item for phase in phases.values() for item in phase.get("provider_output_spill_access",[])],
            "mcp_output_spills": [
                {"phase":phase_name,**item}
                for phase_name,phase in phases.items()
                for item in phase.get("mcp_output_spills",[])
            ],
        }
        (self.logs / "efficiency.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        return report

    def _assert_efficiency_safety(self, report: dict[str, Any]) -> None:
        violations=[]
        if report.get("mcp_failures"):
            violations.append(f"{len(report['mcp_failures'])} audited MCP tool failure(s) occurred")
        if report.get("mcp_stream_failures"):
            violations.append(f"{len(report['mcp_stream_failures'])} MCP result/transport failure(s) occurred in Cursor stream")
        if report.get("mcp_validation_rejections"):
            violations.append(f"{len(report['mcp_validation_rejections'])} avoidable MCP payload validation rejection(s) occurred")
        if report.get("agent_decision_calls"):
            violations.append("agent registered decisions during a pre-authorized debug scenario instead of consuming harness decisions")
        if report.get("outside_cursor_transcript_access"):
            violations.append("Cursor inspected ~/.cursor/projects agent transcripts instead of DynosAI context")
        if report.get("mcp_output_spills"):
            violations.append(f"{len(report['mcp_output_spills'])} oversized MCP result(s) spilled into Cursor agent-tools")
        if report.get("provider_output_spill_access"):
            violations.append("Cursor attempted to read provider-owned agent-tools output instead of receiving an inline DynosAI contract")
        self._emit("efficiency_assertion",ok=not violations,violations=violations,report=report)
        if violations: raise AssertionError("context/protocol efficiency invariant failed: "+"; ".join(violations))

    def _phase_prompt(self, phase: str) -> str:
        decisions = "\n".join(f"- {x['question']} → {x['answer']}" for x in self.scenario["decisions"])
        common = (
            "Estás ejecutando el escenario E2E AUTORIZADO de DynosAI. Usa exclusivamente MCP DynosAI para workflow, Git, contexto y validaciones; "
            "respeta scope; no uses shell directo ni escribas Git. Consulta dynosai_get_next_action primero y usa su campo `contract` como contrato exacto. "
            "No explores Markdown, `.cursor`, agent transcripts, virtualenv ni internals para descubrir schemas. "
            "Si `repository_context.repository_context_complete=true`, no hagas exploración general del repositorio: usa el contexto entregado y solo slices/símbolos concretos cuando sean necesarios. "
            "No uses board/project-overview para reconstruir tareas cuando `task_queue` esté presente. "
            "Antes de solicitar una extensión de scope, comprueba el plan/tareas entregados: nunca solicites extensión para un path ya aprobado. Si DynosAI responde already_planned, usa la tarea propietaria y, si corresponde, completa atómicamente su cierre junto con la tarea actual respetando el cierre de dependencias. "
            "No apruebes artefactos ni intentes ejecutar comandos host de review: las aprobaciones las hará el harness externo."
        )
        if phase == "spec":
            return common + (
                "\nObjetivo: llevar DYN-0001 desde inbox/discovery hasta spec_review y DETENERTE allí. "
                "Las decisiones funcionales de este escenario ya están registradas por el harness y aparecerán en el contrato; no registres decisiones nuevas ni preguntes al usuario:\n" + decisions +
                "\nCompila una spec estructurada completa usando solo esas decisiones como autoridad, envíala y continúa solo hasta que DynosAI indique spec_review."
            )
        if phase == "plan":
            return common + (
                "\nObjetivo: DYN-0001 tiene la spec aprobada. El contrato completo del plan viene en `dynosai_get_next_action.contract`; úsalo sin buscar información de protocolo en disco. "
                "Compila y envía un plan estructurado completo con tareas, acciones de archivos y perfil unit. En brownfield declara `modify` únicamente para archivos existentes respaldados por el contexto. Continúa solo hasta que DynosAI indique plan_review y DETENTE. No apruebes el plan."
            )
        if phase == "implementation":
            return common + (
                "\nObjetivo: el plan de DYN-0001 ya está aprobado por el harness. Implementa todas las tareas en el worktree asignado. "
                "Usa las herramientas nativas de edición de Cursor dentro del scope y sigue `task_queue.execution_wave` cuando esté presente y `task_queue.current_task` en caso contrario. No lances validaciones prematuras. Si hay execution_wave, implementa todas sus tareas antes de registrar; valida una sola vez al final cuando el contrato lo pida y registra completed_tasks en registration_order. Sin wave, registra el resultado de la tarea actual normalmente. "
                "Registra el resultado mediante dynosai_register_result y continúa mediante MCP hasta que DynosAI indique code_review; DETENTE allí."
            )
        raise ValueError(phase)

    def _collect_invariants(self) -> dict[str, Any]:
        assert self.engine is not None and self.work_id
        work = self.engine.get_work(self.work_id)
        tasks = self.engine.db.query("SELECT id,state,owner,claimed_run,depends_on,files FROM tasks WHERE work_id=? ORDER BY id", (self.work_id,))
        runs = self.engine.db.query("SELECT id,status,verification_status,agent,worktree,session_id,task_ids FROM runs WHERE work_id=? ORDER BY id", (self.work_id,))
        artifacts = self.engine.db.query("SELECT id,kind,status,version FROM artifacts WHERE work_id=? ORDER BY id", (self.work_id,))
        def decode(value: Any) -> list[Any]:
            if isinstance(value, list): return value
            try: return json.loads(value or "[]")
            except Exception: return []
        return {
            "final_state": work["state"],
            "all_tasks_completed": bool(tasks) and all(t["state"] == "completed" for t in tasks),
            "task_traceability": [
                {"id": t["id"], "state": t["state"], "owner": t.get("owner"), "claimed_run": t.get("claimed_run"), "depends_on": decode(t.get("depends_on")), "files": decode(t.get("files"))}
                for t in tasks
            ],
            "runs": [{**r, "task_ids": decode(r.get("task_ids"))} for r in runs],
            "artifacts": artifacts,
            "worktree": work.get("worktree"),
            "branch": work.get("branch"),
            "quality_score": work.get("quality_score"),
        }

    def _invariant_violations(self, invariants: dict[str, Any], *, require_done: bool = True) -> list[str]:
        violations: list[str] = []
        tasks = invariants.get("task_traceability") or []
        runs = invariants.get("runs") or []
        run_map = {r["id"]: r for r in runs}
        task_ids = {t["id"] for t in tasks}
        if require_done and invariants.get("final_state") != "done":
            violations.append(f"final_state must be done, got {invariants.get('final_state')}")
        if not tasks:
            violations.append("no tasks were materialized")
        if not invariants.get("all_tasks_completed"):
            violations.append("not all tasks are completed")
        for task in tasks:
            tid = task["id"]
            owner = task.get("owner")
            claimed = task.get("claimed_run")
            if not owner:
                violations.append(f"{tid} has no owner")
            if not claimed:
                violations.append(f"{tid} has no claimed_run")
            elif claimed not in run_map:
                violations.append(f"{tid} references missing run {claimed}")
            elif tid not in set(run_map[claimed].get("task_ids") or []):
                violations.append(f"{tid} is not present in {claimed}.task_ids")
            for dep in task.get("depends_on") or []:
                if dep not in task_ids:
                    violations.append(f"{tid} references unknown dependency {dep}")
                if dep == tid:
                    violations.append(f"{tid} depends on itself")
        # Detect cycles in persisted dependencies.
        graph = {t["id"]: set(t.get("depends_on") or []) for t in tasks}
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(node: str) -> bool:
            if node in visiting: return False
            if node in visited: return True
            visiting.add(node)
            for dep in graph.get(node, set()):
                if dep in graph and not visit(dep): return False
            visiting.remove(node); visited.add(node); return True
        if any(not visit(node) for node in graph):
            violations.append("persisted task dependency graph contains a cycle")
        approved = {(a.get("kind"), a.get("status")) for a in invariants.get("artifacts") or []}
        for kind in ("spec", "plan"):
            if (kind, "approved") not in approved:
                violations.append(f"{kind} artifact is not approved")
        return violations

    def _assert_execution_traceability(self) -> None:
        invariants = self._collect_invariants()
        violations = self._invariant_violations(invariants, require_done=False)
        # At code_review all task/result traceability must already be complete.
        violations = [v for v in violations if not v.startswith("final_state")]
        self._emit("execution_traceability_assertion", ok=not violations, violations=violations, invariants=invariants)
        if violations:
            raise AssertionError("execution traceability invariant failed: " + "; ".join(violations))

    def _run_fibonacci_oracle(self) -> dict[str, Any]:
        assert self.engine is not None and self.work_id
        work = self.engine.get_work(self.work_id)
        cwd = Path(work.get("worktree") or self.project)
        cases = [
            ("n5", ["-m", "fibonacci", "5"], 0, "0 1 1 2 3", False),
            ("n0", ["-m", "fibonacci", "0"], 0, "", False),
            ("negative", ["-m", "fibonacci", "-1"], 1, None, True),
            ("non_numeric", ["-m", "fibonacci", "abc"], 1, None, True),
            ("missing", ["-m", "fibonacci"], 1, None, True),
            ("extra_args", ["-m", "fibonacci", "5", "6"], 1, None, True),
            ("fractional", ["-m", "fibonacci", "3.5"], 1, None, True),
            ("plus_four", ["-m", "fibonacci", "+4"], 0, "0 1 1 2", False),
        ]
        results: list[dict[str, Any]] = []
        failures: list[str] = []
        for name, args, expected_code, expected_stdout, require_stderr in cases:
            proc = subprocess.run([sys.executable, *args], cwd=cwd, text=True, capture_output=True, timeout=20)
            stdout = proc.stdout.rstrip("\r\n")
            stderr = proc.stderr.strip()
            ok = proc.returncode == expected_code
            if expected_stdout is not None: ok = ok and stdout == expected_stdout
            if require_stderr: ok = ok and bool(stderr)
            row = {"case": name, "command": ["python", *args], "exit_code": proc.returncode, "expected_exit_code": expected_code, "stdout": stdout, "stderr": stderr, "ok": ok}
            results.append(row)
            if not ok: failures.append(name)
        payload = {"scenario": "fibonacci", "passed": not failures, "failures": failures, "results": results}
        (self.logs / "oracle-fibonacci.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._emit("scenario_oracle", **payload)
        if failures:
            raise AssertionError("Fibonacci black-box oracle failed: " + ", ".join(failures))
        return payload

    def _run_supportdesk_oracle(self) -> dict[str, Any]:
        assert self.engine is not None and self.work_id
        work = self.engine.get_work(self.work_id)
        cwd = Path(work.get("worktree") or self.project)
        script = """
import json
from supportdesk.api.tickets import create_ticket_payload
from supportdesk.domain.customer import Customer
from supportdesk.repositories.tickets import InMemoryTicketRepository
from supportdesk.services.ticket_service import TicketService

def run(tier, severity):
    repo = InMemoryTicketRepository()
    service = TicketService(repo)
    payload = create_ticket_payload(service, Customer("C-1", tier), severity, "Need assistance")
    return {"payload": payload, "persisted": len(repo.items)}

print(json.dumps({
    "premium_critical": run("premium", "critical"),
    "standard_critical": run("standard", "critical"),
    "premium_high": run("premium", "high"),
    "premium_normal": run("premium", "normal"),
}, sort_keys=True))
"""
        proc = subprocess.run([sys.executable, "-c", script], cwd=cwd, text=True, capture_output=True, timeout=20)
        failures: list[str] = []
        data: dict[str, Any] = {}
        if proc.returncode != 0:
            failures.append("oracle_process")
        else:
            try:
                data = json.loads(proc.stdout.strip())
            except Exception:
                failures.append("oracle_json")
        expected = {
            "premium_critical": (60, True),
            "standard_critical": (240, False),
            "premium_high": (480, False),
            "premium_normal": (1440, False),
        }
        results: list[dict[str, Any]] = []
        for name, (minutes, escalation) in expected.items():
            item = data.get(name, {}) if isinstance(data, dict) else {}
            payload = item.get("payload", {}) if isinstance(item, dict) else {}
            ok = (
                payload.get("sla_minutes") == minutes
                and payload.get("escalation_required") is escalation
                and item.get("persisted") == 1
                and payload.get("id") == "T-0001"
                and payload.get("summary") == "Need assistance"
            )
            results.append({
                "case": name,
                "expected_sla": minutes,
                "expected_escalation": escalation,
                "payload": payload,
                "persisted": item.get("persisted"),
                "ok": ok,
            })
            if not ok:
                failures.append(name)
        result = {
            "scenario": "supportdesk-premium-sla",
            "passed": not failures,
            "failures": failures,
            "results": results,
            "stderr": proc.stderr.strip(),
        }
        (self.logs / "oracle-supportdesk.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self._emit("scenario_oracle", **result)
        if failures:
            raise AssertionError("SupportDesk black-box oracle failed: " + ", ".join(failures))
        return result

    def _run_orderflow_oracle(self) -> dict[str, Any]:
        """Independent migration/data/backward-compatibility oracle for OrderFlow."""
        assert self.engine is not None and self.work_id
        work = self.engine.get_work(self.work_id)
        cwd = Path(work.get("worktree") or self.project)
        script = r'''
import json, tempfile
from pathlib import Path
from orderflow.api.orders import create_order_payload
from orderflow.db import connect
from orderflow.domain.customer import Customer
from orderflow.migrations import apply_migrations
from orderflow.repositories.customers import CustomerRepository
from orderflow.repositories.orders import OrderRepository
from orderflow.services.order_service import OrderService

with tempfile.TemporaryDirectory() as d:
    conn=connect(Path(d)/"oracle.db")
    before=apply_migrations(conn,upto=1)
    conn.execute("INSERT INTO customers(id,name,segment) VALUES('C-OLD','Legacy','enterprise')")
    conn.execute("INSERT INTO orders(id,customer_id,total_cents,status,created_at) VALUES('O-OLD','C-OLD',7777,'created','2025-01-01')")
    conn.commit()
    after=apply_migrations(conn)
    repeated=apply_migrations(conn)
    legacy_raw=dict(conn.execute("SELECT id,customer_id,total_cents,status,original_total_cents,discount_cents FROM orders WHERE id='O-OLD'").fetchone())
    legacy_model=OrderRepository(conn).get("O-OLD")
    customers=CustomerRepository(conn); orders=OrderRepository(conn); service=OrderService(customers,orders)
    customers.save(Customer("C-ENT","Enterprise","enterprise",10,"2026-01-01"))
    customers.save(Customer("C-FUT","Future","enterprise",10,"2027-01-01"))
    customers.save(Customer("C-STD","Standard","standard",10,"2020-01-01"))
    active=create_order_payload(service,"C-ENT",[5000,5000],"2026-08-13")
    future=create_order_payload(service,"C-FUT",[1000],"2026-08-13")
    standard=create_order_payload(service,"C-STD",[1000],"2026-08-13")
    rounding=create_order_payload(service,"C-ENT",[999],"2026-08-13")
    columns={r[1] for r in conn.execute("PRAGMA table_info(orders)")}
    customer_columns={r[1] for r in conn.execute("PRAGMA table_info(customers)")}
    print(json.dumps({
      "versions":{"before":before,"after":after,"repeated":repeated},
      "legacy_raw":legacy_raw,
      "legacy_model":{"total_cents":legacy_model.total_cents,"original_total_cents":legacy_model.original_total_cents,"discount_cents":legacy_model.discount_cents},
      "active":active,"future":future,"standard":standard,"rounding":rounding,
      "orders_columns":sorted(columns),"customer_columns":sorted(customer_columns),
      "ledger_count":conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    },sort_keys=True))
'''
        proc = subprocess.run([sys.executable, "-c", script], cwd=cwd, text=True, capture_output=True, timeout=30)
        failures: list[str] = []
        data: dict[str, Any] = {}
        if proc.returncode != 0:
            failures.append("oracle_process")
        else:
            try:
                data=json.loads(proc.stdout.strip())
            except Exception:
                failures.append("oracle_json")
        checks = {
            "migration_versions": data.get("versions") == {"before":[1],"after":[1,2],"repeated":[1,2]},
            "migration_columns": {"original_total_cents","discount_cents"} <= set(data.get("orders_columns") or []) and {"discount_percent","discount_valid_from"} <= set(data.get("customer_columns") or []),
            "legacy_row_preserved": data.get("legacy_raw",{}).get("total_cents") == 7777 and data.get("legacy_raw",{}).get("original_total_cents") is None and data.get("legacy_raw",{}).get("discount_cents") is None,
            "legacy_read_compatible": data.get("legacy_model") == {"total_cents":7777,"original_total_cents":7777,"discount_cents":0},
            "active_discount": all(data.get("active",{}).get(k)==v for k,v in {"original_total_cents":10000,"discount_cents":1000,"total_cents":9000}.items()),
            "future_not_discounted": all(data.get("future",{}).get(k)==v for k,v in {"original_total_cents":1000,"discount_cents":0,"total_cents":1000}.items()),
            "standard_not_discounted": all(data.get("standard",{}).get(k)==v for k,v in {"original_total_cents":1000,"discount_cents":0,"total_cents":1000}.items()),
            "integer_rounding": all(data.get("rounding",{}).get(k)==v for k,v in {"original_total_cents":999,"discount_cents":99,"total_cents":900}.items()),
            "api_backward_fields": {"id","customer_id","total_cents","status"} <= set(data.get("active",{})),
            "ledger_idempotent": data.get("ledger_count") == 2,
        }
        failures.extend(name for name, ok in checks.items() if not ok)
        result={"scenario":"orderflow-contract-discounts","passed":not failures,"failures":failures,"checks":checks,"data":data,"stderr":proc.stderr.strip()}
        (self.logs / "oracle-orderflow.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        self._emit("scenario_oracle", **result)
        if failures:
            raise AssertionError("OrderFlow migration oracle failed: "+", ".join(failures))
        return result

    def _failure_evidence(self) -> dict[str, Any]:
        """Best-effort evidence capture for failed runs.

        A failed oracle or validation must not erase Cursor usage/tool metrics from
        the child result. This helper is deliberately non-throwing so diagnostics
        survive the original failure.
        """
        out: dict[str, Any] = {}
        try:
            oracle_files = {
                "fibonacci": "oracle-fibonacci.json",
                "supportdesk-premium-sla": "oracle-supportdesk.json",
                "orderflow-contract-discounts": "oracle-orderflow.json",
            }
            name = oracle_files.get(self.scenario_name)
            if name:
                opath = self.logs / name
                if opath.is_file():
                    out["oracle"] = json.loads(opath.read_text(encoding="utf-8"))
        except Exception:
            pass
        try:
            out["efficiency"] = self._efficiency_report()
        except Exception as exc:
            out["efficiency_error"] = str(exc)
        return out

    def _bundle(self, result: dict[str, Any]) -> Path:
        assert self.engine is not None
        self.logs.mkdir(parents=True, exist_ok=True)
        self.summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        # Capture authoritative diagnostics before optional cleanup.
        try:
            diag = self.engine.diagnostic_bundle(str(self.logs / "project-diagnostic.zip"))
            self._emit("diagnostic_bundle", result=diag)
        except Exception as exc:
            self._emit("diagnostic_bundle_failed", error=str(exc))
        audit = self.engine.db.query("SELECT id,event_type,entity_id,payload,created_at FROM audit ORDER BY id")
        (self.logs / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (self.logs / "runtime.json").write_text(json.dumps(self.engine.runtime.status(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        if self.work_id:
            try:
                review = self.engine.review_summary(self.work_id)
                (self.logs / "final-review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            except Exception as exc:
                self._emit("final_review_failed", error=str(exc))

        self.output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.output, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(self.logs.rglob("*")):
                if path.is_file():
                    z.write(path, path.relative_to(self.logs.parent))
            z.writestr("README.txt", (
                f"DynosAI E2E debug bundle. Scenario: {self.scenario_name}. Includes Cursor stream-json events, DynosAI state/audit and diagnostics. "
                "No session bearer tokens or environment secrets are intentionally included.\n"
            ))
        return self.output

    def run(self) -> dict[str, Any]:
        if self.base.exists(): shutil.rmtree(self.base)
        self.project.mkdir(parents=True, exist_ok=True); self.logs.mkdir(parents=True, exist_ok=True)
        result: dict[str, Any] = {"debug_version": 4, "scenario": self.scenario_name, "agent": self.agent, "started_at": utc_now(), "project": str(self.project), "status": "running"}
        try:
            st = self._stage("bootstrap")
            fixture=None
            if self.scenario.get("mode")=="brownfield":
                fixture = seed_orderflow_brownfield(self.project) if self.scenario_name == "orderflow-contract-discounts" else seed_supportdesk_brownfield(self.project)
            self.engine = DynosAI(self.project)
            if self.scenario.get("mode")=="brownfield":
                init = self.engine.adopt(self.scenario["name"], "cursor")
            else:
                init = self.engine.initialize("DynosAI Debug E2E", self.scenario["language"], self.scenario["test_command"], "cursor", mode="greenfield")
            self.engine.db.set_meta("language",self.scenario["language"]); self.engine.db.set_meta("test_command",self.scenario["test_command"])
            from .policy import ValidationProfilePolicy
            from .util import json_dumps
            parts=ValidationProfilePolicy.parse_command(self.scenario["test_command"]); now=utc_now()
            self.engine.db.execute("INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,source=excluded.source,approved=1,updated_at=excluded.updated_at",("unit",json_dumps(parts),"debug-scenario",1,now,now))
            self.engine.db.set_meta("default_agent", "cursor")
            # The semantic model is project-configured. Installation is cache-aware and deterministic if already cached.
            model = self.engine.install_model()
            sync = self.engine.sync()
            doctor = self.engine.deep_doctor()
            if not doctor.get("full_ready"):
                raise RuntimeError("deep doctor is not full_ready")
            self._finish(st, True, init=init, fixture=fixture, model=model, sync=sync, doctor_ready=doctor.get("full_ready"))

            st = self._stage("start_work")
            work = self.engine.start(self.scenario["description"])
            self.work_id = work["id"]
            if work["state"] != "inbox": raise AssertionError(f"expected inbox, got {work['state']}")
            debug_decisions=[]
            for item in self.scenario["decisions"]:
                debug_decisions.append(self.engine.register_decision(self.work_id,item["question"],item["answer"]))
            self.engine.db.audit("DebugHarnessDecisionsRegistered",self.work_id,{"count":len(debug_decisions),"scenario":self.scenario_name})
            self._finish(st, True, work=work, decisions=debug_decisions)
            self._snapshot("00-inbox")

            st = self._stage("agent_spec")
            agent_spec = self.agent_runner(self._phase_prompt("spec"), "spec")
            self._assert_state("spec_review", "agent must stop at human spec gate")
            spec_review = self.engine.review_summary(self.work_id)
            (self.logs / "review-spec.json").write_text(json.dumps(spec_review, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self._finish(st, True, agent=agent_spec, quality=spec_review.get("quality_score"), findings=spec_review.get("findings"))
            self._snapshot("01-spec-review")

            st = self._stage("approve_spec")
            approval = self._debug_approve("spec")
            self._assert_state("plan_review", "spec approval must enter plan_review")
            self._finish(st, bool(approval.get("approved")), result=approval)

            st = self._stage("agent_plan")
            agent_plan = self.agent_runner(self._phase_prompt("plan"), "plan")
            self._assert_state("plan_review", "agent must stop at human plan gate")
            plan_review = self.engine.review_summary(self.work_id)
            plan = plan_review.get("plan")
            if not plan: raise AssertionError("plan_review reached without a plan")
            if plan.get("status") == "approved": raise AssertionError("agent/harness boundary violated: plan was approved before debug approval")
            (self.logs / "review-plan.json").write_text(json.dumps(plan_review, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self._finish(st, True, agent=agent_plan, quality=plan_review.get("quality_score"), findings=plan_review.get("findings"))
            self._snapshot("02-plan-review")

            st = self._stage("approve_plan")
            approval = self._debug_approve("plan")
            self._assert_state("ready", "plan approval must enter ready")
            self._finish(st, bool(approval.get("approved")), result=approval)
            self._snapshot("03-ready")

            st = self._stage("agent_implementation")
            agent_impl = self.agent_runner(self._phase_prompt("implementation"), "implementation")
            # Some agents stop after register_result; host continuation is allowed, but only if verification is already complete.
            state = self.engine.get_work(self.work_id)["state"]
            if state == "implementing":
                cont = self.engine.continue_work(self.work_id)
                self._emit("host_continue_after_agent_result", result=cont)
            self._assert_state("code_review", "verified implementation must stop at code_review")
            code_review = self.engine.review_summary(self.work_id)
            (self.logs / "review-code.json").write_text(json.dumps(code_review, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            self._finish(st, True, agent=agent_impl, review=code_review)
            self._snapshot("04-code-review")
            self._assert_execution_traceability()
            oracle = (
                self._run_fibonacci_oracle() if self.scenario_name == "fibonacci" else
                self._run_supportdesk_oracle() if self.scenario_name == "supportdesk-premium-sla" else
                self._run_orderflow_oracle() if self.scenario_name == "orderflow-contract-discounts" else None
            )

            st = self._stage("approve_code_validate_merge")
            code_approval = self._debug_approve("code")
            self._assert_state("validating", "code approval must enter validating")
            validation = self.engine.continue_work(self.work_id)
            if validation.get("state") != "ready_to_merge":
                raise AssertionError(f"validation did not reach ready_to_merge: {validation}")
            merge = self._debug_approve("merge")
            self._assert_state("done", "merge approval must finish work")
            self._finish(st, True, code_approval=code_approval, validation=validation, merge=merge)
            self._snapshot("05-done")

            invariants = self._collect_invariants()
            violations = self._invariant_violations(invariants, require_done=True)
            if violations:
                raise AssertionError("final E2E invariants failed: " + "; ".join(violations))
            efficiency = self._efficiency_report()
            self._assert_efficiency_safety(efficiency)
            result.update({"status": "passed", "finished_at": utc_now(), "work_id": self.work_id, "invariants": invariants, "oracle": oracle, "efficiency": efficiency})
        except Exception as exc:
            self.failure = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
            self._emit("debug_failure", **self.failure)
            result.update({"status": "failed", "finished_at": utc_now(), "work_id": self.work_id, "failure": self.failure})
            if self.engine and self.work_id:
                try: result["invariants"] = self._collect_invariants()
                except Exception: pass
                # Failure evidence is still first-class evidence. Preserve oracle and
                # efficiency telemetry so a combined suite never reports a failed
                # child as zero-cost/unknown when Cursor streams already exist.
                result.update(self._failure_evidence())
        finally:
            result["stages"] = [stage.__dict__ for stage in self.stages]
            bundle = self._bundle(result) if self.engine else self.output
            result["bundle"] = str(bundle)
            # Rewrite summary with final bundle path, then refresh it inside the ZIP.
            if self.engine:
                self.summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                with zipfile.ZipFile(bundle, "a", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("logs/summary-final.json", json.dumps(result, ensure_ascii=False, indent=2, default=str))
            if not self.keep:
                # Keep the final bundle if it lives below the debug workspace by moving it first.
                if bundle.is_relative_to(self.base):
                    moved = Path(tempfile.gettempdir()) / bundle.name
                    shutil.copy2(bundle, moved); result["bundle"] = str(moved)
                shutil.rmtree(self.base, ignore_errors=True)
        return result


class DebugE2ESuiteRunner:
    """Run multiple authoritative E2E scenarios as one certification gate.

    0.7 uses this to prove that greenfield guarantees remain intact while the
    same release also handles a materially harder brownfield data migration.
    Every child keeps its own repository, knowledge DB, worktree and Cursor
    sessions; the suite merely aggregates evidence and fails atomically if any
    child fails.
    """

    def __init__(
        self,
        agent: str = "cursor",
        scenario: str = "green-brown-gate",
        output: str | Path | None = None,
        keep: bool = True,
        timeout: int = 600,
        workspace: str | Path | None = None,
        child_agent_runner_factory: Callable[[DebugE2ERunner, str], Callable[..., dict[str, Any]] | None] | None = None,
    ):
        spec = SCENARIOS.get(scenario)
        if not spec or spec.get("mode") != "suite":
            raise ValueError(f"Unknown debug suite scenario: {scenario}")
        if agent != "cursor":
            raise ValueError("DynosAI 0.7 combined gate currently certifies the real Cursor integration")
        self.agent=agent; self.scenario_name=scenario; self.scenario=spec
        self.keep=keep; self.timeout=int(timeout); self.child_agent_runner_factory=child_agent_runner_factory
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.base=Path(workspace).expanduser().resolve() if workspace else Path.home()/".dynosai"/"debug-runs"/f"DEBUG-SUITE-{stamp}"
        self.output=Path(output).expanduser().resolve() if output else self.base/f"dynosai-debug-e2e-{scenario}-{stamp}.zip"

    @staticmethod
    def _aggregate_efficiency(children: list[dict[str, Any]]) -> dict[str, Any]:
        usage={"inputTokens":0,"outputTokens":0,"cacheReadTokens":0,"cacheWriteTokens":0}
        tool_calls=0
        counters={
            "mcp_failures":0,"mcp_stream_failures":0,"client_mcp_dispatch_errors":0,"mcp_validation_rejections":0,"mcp_normalizations":0,
            "agent_decision_calls":0,"outside_cursor_transcript_access":0,"provider_output_spill_access":0,"mcp_output_spills":0,
        }
        per_scenario={}
        for child in children:
            name=child.get("scenario")
            eff=child.get("efficiency") or {}
            child_usage=eff.get("total_usage") or {}
            for key in usage: usage[key]+=int(child_usage.get(key) or 0)
            child_calls=sum(int((phase or {}).get("tool_calls") or 0) for phase in (eff.get("phases") or {}).values())
            tool_calls+=child_calls
            counts={key:len(eff.get(key) or []) for key in counters}
            for key,value in counts.items(): counters[key]+=value
            per_scenario[name]={"usage":child_usage,"tool_calls":child_calls,**counts}
        return {"total_usage":usage,"tool_calls":tool_calls,"counts":counters,"per_scenario":per_scenario}

    def run(self) -> dict[str, Any]:
        if self.base.exists(): shutil.rmtree(self.base)
        child_dir=self.base/"children"; child_dir.mkdir(parents=True,exist_ok=True)
        started=utc_now(); children=[]
        for child_name in self.scenario.get("children") or []:
            child=DebugE2ERunner(
                agent=self.agent, scenario=child_name,
                output=child_dir/f"{child_name}.zip",
                workspace=self.base/f"workspace-{child_name}",
                keep=True, timeout=self.timeout,
            )
            if self.child_agent_runner_factory:
                supplied=self.child_agent_runner_factory(child,child_name)
                if supplied is not None: child.agent_runner=supplied
            child_result=child.run()
            children.append(child_result)
        status="passed" if children and all(c.get("status")=="passed" for c in children) else "failed"
        modes={SCENARIOS.get(c.get("scenario"),{}).get("mode") for c in children}
        coverage={"greenfield":"greenfield" in modes,"brownfield":"brownfield" in modes}
        if not all(coverage.values()): status="failed"
        efficiency=self._aggregate_efficiency(children)
        gates={
            "all_children_passed":bool(children) and all(c.get("status")=="passed" for c in children),
            "all_children_done":bool(children) and all(((c.get("invariants") or {}).get("final_state"))=="done" for c in children),
            "all_oracles_passed":bool(children) and all(((c.get("oracle") or {}).get("passed")) is True for c in children),
            "greenfield_covered":coverage["greenfield"],
            "brownfield_covered":coverage["brownfield"],
            "zero_mcp_failures":efficiency["counts"]["mcp_failures"]==0,
            "zero_stream_failures":efficiency["counts"]["mcp_stream_failures"]==0,
            "zero_mcp_rejections":efficiency["counts"]["mcp_validation_rejections"]==0,
            "zero_mcp_normalizations":efficiency["counts"]["mcp_normalizations"]==0,
            "zero_agent_decisions":efficiency["counts"]["agent_decision_calls"]==0,
            "zero_spills":efficiency["counts"]["mcp_output_spills"]==0 and efficiency["counts"]["provider_output_spill_access"]==0,
            "zero_transcript_bypass":efficiency["counts"]["outside_cursor_transcript_access"]==0,
        }
        if not all(gates.values()): status="failed"
        summary={
            "debug_version":5,"scenario":self.scenario_name,"agent":self.agent,"started_at":started,"finished_at":utc_now(),
            "status":status,"coverage":coverage,"gates":gates,"efficiency":efficiency,
            "provider_warnings": {"client_mcp_dispatch_errors": efficiency["counts"]["client_mcp_dispatch_errors"]},
            "children":[{
                "scenario":c.get("scenario"),"status":c.get("status"),"work_id":c.get("work_id"),
                "final_state":((c.get("invariants") or {}).get("final_state")),
                "oracle_passed":((c.get("oracle") or {}).get("passed")),
                "bundle":Path(str(c.get("bundle"))).name if c.get("bundle") else None,
                "failure":c.get("failure"),
            } for c in children],
        }
        self.output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(self.output,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("suite/summary.json",json.dumps(summary,ensure_ascii=False,indent=2,default=str))
            z.writestr("README.txt","DynosAI 0.7 combined greenfield+brownfield certification bundle. Each child ZIP contains its complete authoritative audit, Cursor streams, oracle and project diagnostics.\n")
            for c in children:
                path=Path(str(c.get("bundle") or ""))
                if path.exists(): z.write(path,f"children/{path.name}")
        summary["bundle"]=str(self.output)
        # Refresh the summary with the final bundle path.
        with zipfile.ZipFile(self.output,"a",zipfile.ZIP_DEFLATED) as z:
            z.writestr("suite/summary-final.json",json.dumps(summary,ensure_ascii=False,indent=2,default=str))
        if not self.keep:
            if self.output.is_relative_to(self.base):
                moved=Path(tempfile.gettempdir())/self.output.name; shutil.copy2(self.output,moved); summary["bundle"]=str(moved)
            shutil.rmtree(self.base,ignore_errors=True)
        return summary
