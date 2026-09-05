# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Real-provider acceptance harness, scenario orchestration, retry policy, and consolidated certification."""

from __future__ import annotations

import json
import os
import hashlib
import queue
import sqlite3
import threading
import tempfile
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .acceptance_policy import automated_elicitation_content
from .application import DynosAIApplication
from .debug import DebugE2ERunner, SCENARIOS
from .debug_fixtures import seed_orderflow_brownfield
from .providers import MachineProviderSetup
from .util import executable_command, utc_now
from .version import __version__
from .model_routing import BUILTIN_DEFAULTS, ACCEPTANCE_MODEL_PROFILES, ModelRoute, ProviderModelRouting, cursor_cli_selector, env_override, model_observation, activity_for_state
from .acceptance_logging import AcceptanceLogger, acceptance_log_home, new_run_id, update_latest_pointer
from .agent_config import AgentConfiguration
from .managed_runtime import ManagedProviderRuntime
from .runtime_audit import audit_managed_runtime
from .model_control import ModelControlPlane
from .token_usage import TokenUsageRecorder, cursor_usage_from_event, cursor_estimated_output_text, codex_token_sample_from_rollout, aggregate_usage, latest_usage, runtime_metrics
from .baselines import compare_to_baseline
from .cost_telemetry import aggregate_cost


ACCEPTANCE_SCENARIOS=("fibonacci","orderflow-contract-discounts")
ACCEPTANCE_PROVIDERS=("cursor","codex")


@dataclass(slots=True)
class ProviderProbe:
    provider: str
    executable: str | None
    installed: bool
    version: str = ""
    error: str = ""

    def to_dict(self) -> dict[str,Any]:
        return {"provider":self.provider,"executable":self.executable,"installed":self.installed,"version":self.version,"error":self.error}


def probe_provider(provider:str)->ProviderProbe:
    if provider=="cursor":
        executable=os.environ.get("DYNOSAI_CURSOR_BIN") or shutil.which("cursor-agent") or shutil.which("agent")
    elif provider=="codex":
        executable=os.environ.get("DYNOSAI_CODEX_BIN") or shutil.which("codex")
    else:
        raise ValueError(provider)
    if not executable:
        return ProviderProbe(provider,None,False,error="provider executable not found")
    try:
        proc=subprocess.run(executable_command(executable,"--version"),text=True,capture_output=True,timeout=10)
        version=(proc.stdout or proc.stderr).strip().splitlines()[0][:300] if (proc.stdout or proc.stderr).strip() else ""
        return ProviderProbe(provider,executable,proc.returncode==0,version=version,error="" if proc.returncode==0 else (proc.stderr or proc.stdout).strip()[:500])
    except Exception as exc:
        return ProviderProbe(provider,executable,False,error=str(exc))


def _write_json(path:Path,value:Any)->None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")


def _append_jsonl(path:Path,value:Any,mirror_dir:Path|None=None)->None:
    payload=json.dumps(value,ensure_ascii=False,default=str)+"\n"
    targets=[path]
    if mirror_dir is not None:
        mirror=(mirror_dir/path.name).resolve()
        if mirror!=path.resolve(): targets.append(mirror)
    for target in targets:
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open("a",encoding="utf-8") as fh:
            fh.write(payload); fh.flush()
            try: os.fsync(fh.fileno())
            except OSError: pass

def _walk_optimization_nodes(value: Any):
    """Yield response_optimization objects from structured provider telemetry.

    Provider protocols sometimes wrap MCP structuredContent inside JSON strings,
    so the fallback carefully descends into both native containers and JSON-like
    string values. It never interprets arbitrary prose.
    """
    if isinstance(value, dict):
        opt = value.get("response_optimization")
        if isinstance(opt, dict):
            yield opt
        for child in value.values():
            yield from _walk_optimization_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_optimization_nodes(child)
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["} and len(stripped) <= 2_000_000:
            try:
                decoded = json.loads(stripped)
            except Exception:
                return
            yield from _walk_optimization_nodes(decoded)


def _context_optimization_metrics(mcp_trace:Path, project:Path, provider_stream:Path|None=None) -> dict[str,Any]:
    omitted=0; checkpoint_omitted=0; action_snapshot_omitted=0; action_snapshot_reuses=0; refs=0; surface_changes=0; surface_counts=[]; checkpoint_loads=0; mcp_response_bytes=0
    transport_text_bytes_omitted=0; structured_primary_results=0; full_text_compatibility_results=0
    optimization_source="normalized_trace"
    seen_opts:set[str]=set()

    def collect_record(item:dict[str,Any], *, include_surface:bool=False)->None:
        nonlocal omitted,checkpoint_omitted,action_snapshot_omitted,action_snapshot_reuses,refs,surface_changes,checkpoint_loads,mcp_response_bytes,transport_text_bytes_omitted,structured_primary_results,full_text_compatibility_results
        if include_surface and item.get("event")=="tool_surface_changed":
            surface_changes+=1
            if item.get("tool_count") is not None: surface_counts.append(int(item.get("tool_count")))
        if include_surface and item.get("event")=="mcp_tool_end" and item.get("tool")=="dynosai_checkpoint" and item.get("status") in {"ok","elicitation_resolved","elicitation_auto_resolved"}:
            checkpoint_loads+=1
        if include_surface and item.get("event")=="mcp_tool_end":
            mcp_response_bytes += int(item.get("response_bytes") or 0)
        for opt in _walk_optimization_nodes(item):
            try:key=json.dumps(opt,sort_keys=True,ensure_ascii=False)
            except Exception:key=repr(opt)
            if key in seen_opts: continue
            seen_opts.add(key)
            saved=int(opt.get("estimated_bytes_omitted") or 0)
            omitted+=saved
            checkpoint_omitted+=int(opt.get("checkpoint_bytes_omitted") or 0)
            action_snapshot_omitted+=int(opt.get("action_snapshot_bytes_omitted") or 0)
            transport_text_bytes_omitted+=int(opt.get("transport_text_bytes_omitted") or 0)
            if opt.get("transport_mode")=="structured_primary": structured_primary_results+=1
            if opt.get("transport_mode")=="full_text_compatibility": full_text_compatibility_results+=1
            if opt.get("action_snapshot_reused") is True: action_snapshot_reuses+=1
            if opt.get("mode") in {"delta","evidence_ref","evidence_cache"} and saved>0: refs+=1

    if mcp_trace.exists():
        for raw in mcp_trace.read_text(encoding="utf-8",errors="replace").splitlines():
            try:item=json.loads(raw)
            except Exception:continue
            if isinstance(item,dict): collect_record(item,include_surface=True)

    # If normalized MCP telemetry vanished, recover optimization evidence from
    # the provider protocol instead of reporting a false zero.
    if not seen_opts and provider_stream is not None and provider_stream.exists():
        optimization_source="provider_stream_fallback"
        for raw in provider_stream.read_text(encoding="utf-8",errors="replace").splitlines():
            try:item=json.loads(raw)
            except Exception:continue
            if isinstance(item,dict): collect_record(item)

    checkpoints=list((project/".dynosai"/"runtime"/"context-checkpoints").rglob("*.json")) if (project/".dynosai"/"runtime"/"context-checkpoints").exists() else []
    model_events=[]
    model_path=project/".dynosai"/"runtime"/"model-control.jsonl"
    if model_path.exists():
        for raw in model_path.read_text(encoding="utf-8",errors="replace").splitlines():
            try:model_events.append(json.loads(raw))
            except Exception:pass
    decisions=[x for x in model_events if x.get("event")=="model_route_decision"]
    candidates=[x for x in decisions if isinstance(x.get("candidate_route"),dict)]
    recommendations=[x for x in model_events if x.get("event")=="route_recommended"]
    requests=[x for x in model_events if x.get("event")=="route_requested"]
    applied=[x for x in model_events if x.get("event")=="route_applied"]
    verified=[x for x in model_events if x.get("event")=="route_verified" and x.get("verified") is True]
    initial_requests=[x for x in model_events if x.get("event")=="initial_route_requested"]
    initial_applied=[x for x in model_events if x.get("event")=="initial_route_applied"]
    initial_verified=[x for x in model_events if x.get("event")=="initial_route_verified" and x.get("verified") is True]
    applied_escalations=[x for x in applied if x.get("escalation") is True]
    predictive_used=[x for x in decisions if isinstance(x.get("prediction"),dict) and x.get("prediction",{}).get("prediction_used") is True]
    predictive_escalations=[x for x in predictive_used if x.get("prediction",{}).get("action")=="predictive_capability_escalation"]
    predictive_downshifts=[x for x in predictive_used if x.get("prediction",{}).get("action")=="predictive_cost_downshift"]
    outcomes=[x for x in model_events if x.get("event")=="model_outcome"]
    learning_eligible=[x for x in outcomes if x.get("eligible_for_learning") is True]
    learning_excluded=[x for x in outcomes if x.get("eligible_for_learning") is False]
    measurement_scopes={}
    context_modes={}
    for item in decisions:
        scope=str(((item.get("budget") or {}).get("measurement_scope") or "unknown"))
        measurement_scopes[scope]=int(measurement_scopes.get(scope) or 0)+1
        mode=str(((item.get("context_strategy") or {}).get("mode") or "unknown"))
        context_modes[mode]=int(context_modes.get(mode) or 0)+1
    context_preview_cache_hits=0; context_preview_cache_misses=0; task_checkpoint_refreshes=0
    execution_waves=0; execution_wave_tasks=[]; execution_wave_expected_round_trips_saved=0; execution_wave_auto_advances=0; ready_implementation_collapses=0
    provider_inference_chunks=0; native_file_change_actions=0
    # Provider-native inference telemetry. Codex emits one token_count event per
    # model inference chunk in its managed rollout. Count these independently of
    # token totals so efficiency work can target "think once less" directly.
    session_root=project/".dynosai"/"runtime"/"managed-agents"
    if session_root.exists():
        for rollout in session_root.rglob("rollout-*.jsonl"):
            try:
                for raw in rollout.read_text(encoding="utf-8",errors="replace").splitlines():
                    try:item=json.loads(raw)
                    except Exception:continue
                    if not isinstance(item,dict): continue
                    payload=item.get("payload") or {}
                    if item.get("type")=="event_msg" and isinstance(payload,dict) and payload.get("type")=="token_count":
                        provider_inference_chunks+=1
                    if item.get("type")=="event_msg" and isinstance(payload,dict) and payload.get("type")=="patch_apply_end" and payload.get("success") is True:
                        native_file_change_actions+=1
            except Exception:
                pass
    db_path=project/".dynosai"/"knowledge.db"
    if db_path.exists():
        try:
            con=sqlite3.connect(str(db_path))
            try:
                for event,target in (("ContextPreviewCacheHit","hit"),("ContextPreviewCacheMiss","miss"),("ContextCheckpointRefreshed","checkpoint"),("ExecutionWaveAutoAdvanced","wave_auto"),("ReadyImplementationCollapsed","ready_impl")):
                    row=con.execute("SELECT COUNT(*) FROM audit WHERE event_type=?",(event,)).fetchone()
                    n=int((row or [0])[0] or 0)
                    if target=="hit":context_preview_cache_hits=n
                    elif target=="miss":context_preview_cache_misses=n
                    elif target=="wave_auto":execution_wave_auto_advances=n
                    elif target=="ready_impl":ready_implementation_collapses=n
                    else:task_checkpoint_refreshes=n
                wave_rows=con.execute("SELECT payload FROM audit WHERE event_type='ExecutionWavePlanned' ORDER BY id").fetchall()
                execution_waves=len(wave_rows)
                for row in wave_rows:
                    try:payload=json.loads(row[0] or "{}")
                    except Exception:payload={}
                    task_ids=list(payload.get("task_ids") or []) if isinstance(payload,dict) else []
                    execution_wave_tasks.append(len(task_ids))
                    execution_wave_expected_round_trips_saved+=int((payload.get("expected_round_trips_saved") if isinstance(payload,dict) else 0) or 0)
            finally:con.close()
        except Exception:
            pass
    return {
        "response_bytes_omitted":omitted,
        "checkpoint_bytes_omitted":checkpoint_omitted,
        "action_snapshot_bytes_omitted":action_snapshot_omitted,
        "action_snapshot_reuses":action_snapshot_reuses,
        "mcp_response_bytes":mcp_response_bytes,
        "transport_text_bytes_omitted":transport_text_bytes_omitted,
        "structured_primary_results":structured_primary_results,
        "full_text_compatibility_results":full_text_compatibility_results,
        "context_preview_cache_hits":context_preview_cache_hits,
        "context_preview_cache_misses":context_preview_cache_misses,
        "task_checkpoint_refreshes":task_checkpoint_refreshes,
        "execution_waves":execution_waves,
        "execution_wave_tasks":execution_wave_tasks,
        "tasks_per_wave_avg":(round(sum(execution_wave_tasks)/len(execution_wave_tasks),3) if execution_wave_tasks else 0.0),
        "tasks_per_wave_max":(max(execution_wave_tasks) if execution_wave_tasks else 0),
        "execution_wave_expected_round_trips_saved":execution_wave_expected_round_trips_saved,
        "execution_wave_auto_advances":execution_wave_auto_advances,
        "ready_implementation_collapses":ready_implementation_collapses,
        "provider_inference_chunks":provider_inference_chunks,
        "native_file_change_actions":native_file_change_actions,
        "compacted_response_count":refs,
        "optimization_metrics_source":optimization_source if (seen_opts or mcp_trace.exists()) else "unavailable",
        "tool_surface_change_notifications":surface_changes,
        "phase_tool_counts":surface_counts,
        "context_checkpoint_files":len(checkpoints),
        "targeted_checkpoint_loads":checkpoint_loads,
        "model_control_decisions":len(decisions),
        "model_control_measurement_scopes":measurement_scopes,
        "context_strategy_modes":context_modes,
        "predictive_decisions_used":len(predictive_used),
        "predictive_capability_escalations":len(predictive_escalations),
        "predictive_cost_downshifts":len(predictive_downshifts),
        "predictive_learning_eligible_outcomes":len(learning_eligible),
        "predictive_learning_excluded_outcomes":len(learning_excluded),
        "model_route_candidates":len(candidates),
        "model_route_recommendations":len(recommendations),
        "model_route_requests":len(requests),
        "model_route_applied":len(applied),
        "model_route_verified":len(verified),
        "initial_route_requests":len(initial_requests),
        "initial_route_applied":len(initial_applied),
        "initial_route_verified":len(initial_verified),
        "model_control_transition_requests":len(requests),
        "model_control_transition_applied":len(applied),
        "model_control_transition_verified":len(verified),
        "model_escalations_applied":len(applied_escalations),
        # The legacy aggregate is interpreted as a provider-observed
        # escalation, never a recommendation.
        "model_control_escalations":len(applied_escalations),
    }


def _scenario_prompt(provider:str,scenario_name:str,model_route:ModelRoute|None=None)->str:
    scenario=SCENARIOS[scenario_name]
    decisions="\n".join(f"- {x['question']} => {x['answer']}" for x in scenario.get("decisions") or [])
    route_text=(f"Requested provider model: {model_route.model}; reasoning/effort: {model_route.effort or 'provider-default'}; activity profile: provider default.\nDo not change the provider model yourself.\n" if model_route else "")
    return f"""DynosAI {__version__} REAL-PROVIDER ACCEPTANCE RUN. This is an isolated authorized test repository.
Use the configured DynosAI MCP server as the sole authority for workflow, scope, validation, Git governance and project knowledge.
Do NOT invoke DynosAI through shell/CLI. Do NOT self-approve gates. Do NOT stop at Spec Review, Plan Review, Code Review or Merge Review: the acceptance harness resolves DynosAI Elicitations and the suspended MCP tool call will continue.

Provider under test: {provider}
{route_text}Scenario: {scenario_name}
Business goal: {scenario['description']}

Deterministic user decisions for this acceptance fixture (register them as decisions after the work item exists; do not ask the human again):
{decisions}

Required flow, in this SAME provider session and SAME workspace:
1. Call dynosai_project action=initialize for the current directory with language={scenario['language']} and test_command={json.dumps(scenario['test_command'])}. Let DynosAI auto-detect greenfield/brownfield. If dynosai_get_next_action is consulted first and returns work=null with a bootstrap contract, call that contract.tool with contract.arguments immediately; do not explore the host filesystem or external skills first.
2. Call dynosai_work action=start with the exact business goal above and workspace_strategy=interactive_branch.
3. Register ALL deterministic decisions above in ONE dynosai_register_decision call using its `decisions` array.
4. Use dynosai_get_next_action with execute=true as the orchestration primitive. DynosAI will auto-advance deterministic transitions but will never auto-approve human gates. Build and submit a complete structured spec when requested. If DynosAI returns action_snapshot.unchanged=true or a contract ref with unchanged=true, reuse the previously delivered authoritative action instead of polling again until task/repository state changes.
5. After the acceptance Elicitation result, call dynosai_get_next_action execute=true again; build and submit a complete plan/tasks when requested. Do not call dynosai_continue unless DynosAI explicitly exposes it as a fallback.
6. Implement every approved task using your native edit tools, strictly inside DynosAI scope. Treat task_queue as the complete current-task execution contract and do not reload its requirements/acceptance from spec or plan. Use dynosai_checkpoint only when a missing accepted section is actually needed. When task_queue.execution_wave contains multiple tasks, implement the full wave before registering results, validate once at the end when requested, and register completed_tasks in execution_wave.registration_order. Do not call get_next_action between tasks in the same wave. Do not use direct Git writes. When several exact file ranges are already known, batch them in one dynosai_read `requests` call instead of serial reads.
7. Use dynosai_register_result and dynosai_run_validation exactly when the contract requires them. If validation returns validation_precondition because planned test artifacts are still pending, complete those approved tasks before retrying; do not request extra scope to repair a precondition. Do not call dynosai_refresh_overlay: register_result already refreshes the authoritative overlay.
8. Continue through code review, validation and merge using dynosai_get_next_action execute=true. The harness will answer DynosAI gate Elicitations.
9. Do not finish your turn until DynosAI reports final state done.
10. Never repair the DynosAI runtime, provider configuration, permissions or virtual environments directly. Do not install packages, chmod shims, edit .dynosai/.cursor/.codex/.agents files, or write inside any virtualenv. If a required validation dependency is unavailable, report validation_environment_missing and stop rather than bypassing governance.

Do not explore agent transcripts, provider internals, .dynosai database files, virtualenvs or protocol source code to infer schemas. Use the contracts returned by DynosAI.
"""


class CursorAcceptanceDriver:
    """Real Cursor Agent CLI driver.

    Cursor's public headless interface supports tool/MCP automation, but does not
    expose a documented structured callback for filling arbitrary MCP Elicitation
    forms. In `auto` mode DynosAI therefore uses its acceptance-only transport
    responder. The real Cursor model still performs discovery/spec/plan/code/tests.
    `interactive` mode leaves Elicitation untouched for the real Cursor client.
    """
    def __init__(self, executable:str, max_runtime:int=1800, idle_timeout:int=180, slow_progress:int=60):
        self.executable=executable
        self.max_runtime=max(1,int(max_runtime))
        self.idle_timeout=max(1,int(idle_timeout))
        self.slow_progress=max(1,int(slow_progress))
        self.timeout=self.max_runtime  # compatibility for interactive subprocess timeout

    @staticmethod
    def _configure_project_mcp(project:Path, logs:Path, *, autorespond:bool, process_trace_mirror:Path|None=None) -> Path:
        """Pin a fresh project-local DynosAI MCP process for this acceptance case.

        Cursor may keep/reuse a global MCP process across CLI invocations. Acceptance
        must not depend on whatever environment that older process inherited, so the
        temporary fixture gets its own project-local server definition. The file is
        ignored by the fixture's Git baseline and contains no credentials.
        """
        mcp=project/".cursor"/"mcp.json"; mcp.parent.mkdir(parents=True,exist_ok=True)
        env={
            "DYNOSAI_AGENT_PROVIDER":"cursor",
            "DYNOSAI_RUNTIME_BROKER":"1",
            "DYNOSAI_MANAGED_AGENT":"1",
            "DYNOSAI_ACCEPTANCE_TRACE":str((logs/"elicitation-trace.jsonl").resolve()),
            "DYNOSAI_ACCEPTANCE_PROCESS_TRACE":str((logs/"mcp-activity.jsonl").resolve()),
            "DYNOSAI_PROJECT_ROOT":str(project.resolve()),
            "DYNOSAI_EXTERNAL_DEFAULTS_POLICY":"ignore",
            "DYNOSAI_MCP_TOOL_PROFILE":"acceptance",
            "DYNOSAI_PROVIDER_EXTENSIONS_POLICY":"deny",
            "DYNOSAI_STRICT_MANAGED_RUNTIME":"1",
            "DYNOSAI_AUTO_ADVANCE":"1",
            "DYNOSAI_COMPACT_RESPONSES":"1",
            "DYNOSAI_TOKEN_TELEMETRY":"1",
            "DYNOSAI_TOKEN_USAGE_FILE":str((logs/"token-usage.json").resolve()),
            "DYNOSAI_MODEL_CONTROL_POLICY":"adaptive",
            "DYNOSAI_PHASE_TOOL_DISCLOSURE":"adaptive",
            "DYNOSAI_EVIDENCE_CACHE":"1",
            "DYNOSAI_CONTEXT_CHECKPOINTS":"1",
        }
        if process_trace_mirror is not None: env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR"]=str(process_trace_mirror.resolve())
        if autorespond: env["DYNOSAI_ACCEPTANCE_AUTORESPOND"]="1"
        mcp.write_text(json.dumps({"mcpServers":{"dynosai":{"command":str(Path(sys.executable).absolute()),"args":["-m","dynosai_flow.mcp"],"env":env}}},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        # Keep acceptance-only transport configuration out of the governed source
        # baseline without dirtying an existing brownfield repository.
        if (project/".git").exists():
            ignore=project/".git"/"info"/"exclude"; ignore.parent.mkdir(parents=True,exist_ok=True)
        else:
            ignore=project/".gitignore"
        current=ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        lines=current.splitlines()
        if ".cursor/mcp.json" not in lines: lines.append(".cursor/mcp.json")
        ignore.write_text("\n".join(lines).strip()+"\n",encoding="utf-8")
        return mcp

    def run(self,project:Path,prompt:str,logs:Path,*,interaction_mode:str,model_route:ModelRoute|None=None,logger:AcceptanceLogger|None=None,usage:TokenUsageRecorder|None=None)->dict[str,Any]:
        logs.mkdir(parents=True,exist_ok=True)
        env=os.environ.copy()
        effective=AgentConfiguration(project).resolve(provider="cursor",activity="discovery")
        process_mirror=(logger.paths.root/"mcp-activity.jsonl") if logger else None
        managed_extra_env={
            "DYNOSAI_TOKEN_USAGE_FILE":str((logs/"token-usage.json").resolve()),
            "DYNOSAI_ACCEPTANCE_TRACE":str((logs/"elicitation-trace.jsonl").resolve()),
            "DYNOSAI_ACCEPTANCE_PROCESS_TRACE":str((logs/"mcp-activity.jsonl").resolve()),
        }
        if process_mirror is not None: managed_extra_env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR"]=str(process_mirror.resolve())
        managed=ManagedProviderRuntime(project).prepare("cursor",activity="discovery",effective=effective,tool_profile="acceptance",extra_env=managed_extra_env)
        prompt=ManagedProviderRuntime.authoritative_prompt(effective,prompt)
        (logs/"managed-prompt.txt").write_text(prompt,encoding="utf-8")
        if usage:
            usage.set_identity(model=(model_route.model if model_route else None),phase="discovery")
            usage.seed_estimate(input_text=prompt,event="managed_prompt_estimate")
        env.update(managed.env)
        env["DYNOSAI_AGENT_PROVIDER"]="cursor"
        env["DYNOSAI_MANAGED_AGENT"]="1"
        env["DYNOSAI_EXTERNAL_DEFAULTS_POLICY"]="ignore"
        env["DYNOSAI_MCP_TOOL_PROFILE"]="acceptance"
        env["DYNOSAI_TOKEN_USAGE_FILE"]=str((logs/"token-usage.json").resolve())
        env["DYNOSAI_ACCEPTANCE_TRACE"]=str(logs/"elicitation-trace.jsonl")
        env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE"]=str(logs/"mcp-activity.jsonl")
        if process_mirror is not None: env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR"]=str(process_mirror)
        self._configure_project_mcp(project,logs,autorespond=interaction_mode=="auto",process_trace_mirror=process_mirror)
        command=[self.executable]
        selector=cursor_cli_selector(model_route) if model_route else None
        if selector: command += ["--model",selector]
        if logger:
            logger.emit("provider_launch",phase="provider",provider="cursor",message="launching Cursor provider",model=(model_route.model if model_route else None),selector=selector)
        if interaction_mode=="interactive":
            command += [prompt]
            started=time.monotonic()
            proc=subprocess.run(executable_command(command[0], *command[1:]),cwd=project,env=env,timeout=self.timeout)
            if logger:
                logger.emit("provider_exit",phase="provider",provider="cursor",message="Cursor interactive provider exited",exit_code=proc.returncode,duration_seconds=round(time.monotonic()-started,3))
            return {"provider":"cursor","driver":"cursor-interactive-cli","interaction_mode":interaction_mode,"command":command[:-1]+["<prompt>"],"exit_code":proc.returncode,"duration_seconds":round(time.monotonic()-started,3),"real_provider":True,"wire_elicitation":True,"automated_responses":False,"model_route_requested":model_route.to_dict() if model_route else None,"model_selector":selector,"model_route_verified":None}
        env["DYNOSAI_ACCEPTANCE_AUTORESPOND"]="1"
        command += ["-p","--force","--approve-mcps","--output-format","stream-json",prompt]
        started=time.monotonic()
        stdout_path=logs/"provider-stream.jsonl"; stderr_path=logs/"provider-stderr.log"
        proc=subprocess.Popen(executable_command(command[0], *command[1:]),cwd=project,env=env,text=True,encoding="utf-8",errors="replace",stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=1)
        stdout_lines=0; stderr_lines=0; observed=None; last_progress=time.monotonic(); last_heartbeat=started; termination_reason=None
        lock=threading.Lock(); usage_pending={"input_chars":0,"output_chars":0,"reasoning_chars":0,"last_flush":time.monotonic()}
        def _flush_usage_estimate(force:bool=False):
            if not usage: return
            with lock:
                now=time.monotonic()
                chars=sum(int(usage_pending[k]) for k in ("input_chars","output_chars","reasoning_chars"))
                if not force and chars<4000 and now-float(usage_pending["last_flush"])<5: return
                inp=int(usage_pending["input_chars"]); out=int(usage_pending["output_chars"]); reason=int(usage_pending["reasoning_chars"])
                usage_pending.update({"input_chars":0,"output_chars":0,"reasoning_chars":0,"last_flush":now})
            if inp or out or reason:
                usage.add_estimate(input_tokens=(inp+3)//4,output_tokens=(out+3)//4,reasoning_output_tokens=(reason+3)//4,event="cursor_live_estimate")
        def _consume(stream,path:Path,kind:str):
            nonlocal stdout_lines,stderr_lines,observed,last_progress
            mirror=(logger.paths.root/path.name) if logger else None
            mirror_fh=None
            try:
                with path.open("a",encoding="utf-8") as fh:
                    mirror_fh=mirror.open("a",encoding="utf-8") if mirror and mirror.resolve()!=path.resolve() else None
                    for raw in iter(stream.readline,""):
                        fh.write(raw); fh.flush()
                        if mirror_fh is not None:
                            mirror_fh.write(raw); mirror_fh.flush()
                        try: os.fsync(fh.fileno())
                        except OSError: pass
                        if mirror_fh is not None:
                            try: os.fsync(mirror_fh.fileno())
                            except OSError: pass
                        with lock:
                            if kind=="stdout": stdout_lines += 1
                            else: stderr_lines += 1
                            last_progress=time.monotonic()
                        if kind=="stdout":
                            try: item=json.loads(raw)
                            except Exception: item=None
                            if isinstance(item,dict):
                                if item.get("type")=="system" and item.get("subtype")=="init" and not observed:
                                    observed=item.get("model")
                                    if usage: usage.set_identity(session_id=item.get("session_id"),model=observed,phase="discovery")
                                    if logger: logger.emit("provider_initialized",phase="provider",provider="cursor",message="Cursor stream initialized",model=observed)
                                if usage:
                                    exact_usage=cursor_usage_from_event(item)
                                    if exact_usage:
                                        snap=usage.observe_exact(exact_usage,source="cursor_result",event="cursor_provider_usage",session_id=item.get("session_id"))
                                        if logger: logger.emit("token_usage",phase="usage",provider="cursor",message="Cursor exact token usage observed",input_tokens=snap.get("input_tokens"),output_tokens=snap.get("output_tokens"),cached_input_tokens=snap.get("cached_input_tokens"),usage_quality=snap.get("quality"))
                                    else:
                                        normal_text,reason_text=cursor_estimated_output_text(item)
                                        with lock:
                                            usage_pending["output_chars"] += len(normal_text)
                                            usage_pending["reasoning_chars"] += len(reason_text)
                                        if item.get("type")=="tool_call" and item.get("subtype")=="completed":
                                            try:
                                                payload=json.dumps((item.get("tool_call") or {}).get("result") or item.get("tool_call") or {},ensure_ascii=False,default=str)
                                            except Exception: payload=""
                                            with lock: usage_pending["input_chars"] += len(payload)
                                        _flush_usage_estimate()
                                event_type=str(item.get("type") or "")
                                subtype=str(item.get("subtype") or "")
                                tool=item.get("tool_name") or item.get("name")
                                if logger and (event_type in {"tool_call","tool_result","system","result","error"} or subtype in {"tool_call","tool_result","error","complete","completed"}):
                                    logger.emit("provider_stream_event",phase="provider",provider="cursor",message=f"Cursor event {event_type or '-'} / {subtype or '-'}",tool=tool,stream_lines=stdout_lines)
            finally:
                try:
                    if mirror_fh is not None: mirror_fh.close()
                except Exception: pass
                try: stream.close()
                except Exception: pass
        assert proc.stdout is not None and proc.stderr is not None
        out_thread=threading.Thread(target=_consume,args=(proc.stdout,stdout_path,"stdout"),name="dynosai-cursor-stdout",daemon=True)
        err_thread=threading.Thread(target=_consume,args=(proc.stderr,stderr_path,"stderr"),name="dynosai-cursor-stderr",daemon=True)
        out_thread.start(); err_thread.start()
        timed_out=False
        mcp_trace=logs/"mcp-activity.jsonl"
        last_mcp_mtime=0.0
        try:
            while proc.poll() is None:
                now=time.monotonic(); elapsed=now-started
                try:
                    if mcp_trace.exists():
                        mt=mcp_trace.stat().st_mtime
                        if mt>last_mcp_mtime:
                            last_mcp_mtime=mt; last_progress=now
                except OSError:
                    pass
                idle_for=now-last_progress
                if elapsed>=self.max_runtime:
                    timed_out=True; termination_reason="max_runtime"
                elif idle_for>=self.idle_timeout:
                    timed_out=True; termination_reason="idle_timeout"
                if timed_out:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except Exception: proc.kill()
                    break
                if logger and idle_for>=self.slow_progress and now-last_heartbeat>=10:
                    logger.emit("slow_progress",phase="provider",provider="cursor",message="Cursor is still running but progress is slow",duration_seconds=round(elapsed,1),idle_seconds=round(idle_for,1),status="running")
                if logger and now-last_heartbeat>=10:
                    snap=usage.current() if usage else {}
                    logger.heartbeat(provider="cursor",phase="provider",duration_seconds=round(elapsed,1),remaining_seconds=max(0,int(self.max_runtime-elapsed)),idle_seconds=round(idle_for,1),stream_lines=stdout_lines,stderr_lines=stderr_lines,input_tokens=snap.get("input_tokens"),output_tokens=snap.get("output_tokens"),cached_input_tokens=snap.get("cached_input_tokens"),usage_quality=snap.get("quality"))
                    last_heartbeat=now
                time.sleep(0.5)
            try: proc.wait(timeout=5)
            except Exception: pass
        finally:
            out_thread.join(timeout=3); err_thread.join(timeout=3)
        _flush_usage_estimate(force=True)
        token_usage=usage.finalize(event="cursor_usage_final") if usage else None
        duration=round(time.monotonic()-started,3)
        exit_code=124 if timed_out else int(proc.returncode or 0)
        observation=model_observation(model_route,observed) if model_route else {"observed":observed,"verified":None}
        if logger:
            logger.emit("provider_timeout" if timed_out else "provider_exit",phase="provider",provider="cursor",message=(f"Cursor provider stopped: {termination_reason}" if timed_out else "Cursor provider exited"),exit_code=exit_code,duration_seconds=duration,stream_lines=stdout_lines,stderr_lines=stderr_lines,termination_reason=termination_reason)
        return {"provider":"cursor","driver":"cursor-headless-cli","interaction_mode":interaction_mode,"command":command[:-1]+["<prompt>"],"exit_code":exit_code,"timed_out":timed_out,"termination_reason":termination_reason or "provider_exit","duration_seconds":duration,"real_provider":True,"wire_elicitation":False,"automated_responses":True,"auto_response_channel":"dynosai-test-autoresponder","model_route_requested":model_route.to_dict() if model_route else None,"model_selector":selector,"model_observed":observed,"model_observation":observation,"model_route_verified":observation.get("verified"),"managed_runtime":managed.to_dict(),"provider_stream":str(stdout_path),"provider_stderr":str(stderr_path),"stream_lines":stdout_lines,"stderr_lines":stderr_lines,"token_usage":token_usage}



class CodexAppServerDriver:
    """Real Codex app-server client with programmatic Elicitation responses."""
    def __init__(self, executable:str, max_runtime:int=1800, idle_timeout:int=180, slow_progress:int=60):
        self.executable=executable
        self.max_runtime=max(1,int(max_runtime))
        self.idle_timeout=max(1,int(idle_timeout))
        self.slow_progress=max(1,int(slow_progress))
        self.timeout=self.max_runtime
        self._next_id=1

    def _id(self)->int:
        value=self._next_id; self._next_id+=1; return value

    @staticmethod
    def _send(proc:subprocess.Popen[str],payload:dict[str,Any],trace:Path,mirror_dir:Path|None=None)->None:
        _append_jsonl(trace,{"direction":"client->codex","payload":payload,"at":utc_now()},mirror_dir)
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(payload,ensure_ascii=False)+"\n"); proc.stdin.flush()

    @staticmethod
    def _elicitation_request(message:dict[str,Any])->dict[str,Any]:
        params=message.get("params") or {}
        # Current app-server uses either flattened form fields or a nested request
        # depending on protocol generation/version. Support both shapes.
        request=params.get("request") if isinstance(params.get("request"),dict) else params
        return request or {}

    def _auto_resolve_server_request(self,proc:subprocess.Popen[str],message:dict[str,Any],trace:Path,mirror_dir:Path|None=None)->bool:
        method=message.get("method"); rid=message.get("id")
        if rid is None or not method: return False
        if method=="mcpServer/elicitation/request":
            params=message.get("params") or {}
            req=self._elicitation_request(message)
            meta=(params.get("_meta") or req.get("_meta") or {}) if isinstance(params,dict) else {}
            approval_kind=str(meta.get("codex_approval_kind") or "") if isinstance(meta,dict) else ""
            if approval_kind=="mcp_tool_call":
                # This is Codex asking permission to invoke an MCP tool, not the
                # DynosAI server's own elicitation/create form. Accept the tool
                # call with an empty form payload; do not manufacture gate data.
                content={}
                kind="mcp_tool_approval"
            else:
                # True MCP-server elicitation. Fill the schema deterministically
                # so DynosAI receives approve/answer content through the wire.
                content=automated_elicitation_content(req,message=str(req.get("message") or ""))
                kind="mcp_server_elicitation"
            self._send(proc,{"id":rid,"result":{"action":"accept","content":content}},trace,mirror_dir)
            evidence={"event":"auto-response","kind":kind,"request_id":rid,"content":content,"transport":"codex-app-server","at":utc_now()}
            _append_jsonl(trace,evidence,mirror_dir)
            # Normalize true wire Elicitations into the same acceptance evidence
            # stream used by Cursor. Tool-call approvals are deliberately excluded.
            if kind=="mcp_server_elicitation":
                _append_jsonl(trace.with_name("elicitation-trace.jsonl"),evidence,mirror_dir)
            return True
        if method in {"item/commandExecution/requestApproval","item/fileChange/requestApproval"}:
            self._send(proc,{"id":rid,"result":{"decision":"acceptForSession"}},trace,mirror_dir); return True
        if method=="item/permissions/requestApproval":
            permissions=((message.get("params") or {}).get("permissions") or {})
            self._send(proc,{"id":rid,"result":{"scope":"session","permissions":permissions}},trace,mirror_dir); return True
        # Acceptance scenarios are designed not to require arbitrary user-input
        # tools. Cancel unknown server requests instead of guessing a schema.
        return False

    def run(self,project:Path,prompt:str,logs:Path,*,interaction_mode:str,model_route:ModelRoute|None=None,logger:AcceptanceLogger|None=None,usage:TokenUsageRecorder|None=None)->dict[str,Any]:
        if interaction_mode=="interactive":
            command=[self.executable]
            if model_route:
                command += ["--model",model_route.model]
                if model_route.effort: command += ["-c",f'model_reasoning_effort="{model_route.effort}"']
            command += [prompt]
            started=time.monotonic(); proc=subprocess.run(executable_command(command[0], *command[1:]),cwd=project,timeout=self.timeout)
            return {"provider":"codex","driver":"codex-interactive-cli","interaction_mode":"interactive","command":command[:-1]+["<prompt>"],"exit_code":proc.returncode,"duration_seconds":round(time.monotonic()-started,3),"real_provider":True,"wire_elicitation":True,"automated_responses":False,"model_route_requested":model_route.to_dict() if model_route else None,"model_route_verified":None}
        logs.mkdir(parents=True,exist_ok=True); trace=logs/"codex-app-server.jsonl"; mirror_dir=logger.paths.root if logger else None
        effective=AgentConfiguration(project).resolve(provider="codex",activity="discovery")
        process_mirror=(logger.paths.root/"mcp-activity.jsonl") if logger else None
        managed_extra_env={
            "DYNOSAI_TOKEN_USAGE_FILE":str((logs/"token-usage.json").resolve()),
            "DYNOSAI_ACCEPTANCE_TRACE":str((logs/"elicitation-trace.jsonl").resolve()),
            "DYNOSAI_ACCEPTANCE_PROCESS_TRACE":str((logs/"mcp-activity.jsonl").resolve()),
        }
        if process_mirror is not None: managed_extra_env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR"]=str(process_mirror.resolve())
        managed=ManagedProviderRuntime(project).prepare("codex",activity="discovery",effective=effective,tool_profile="acceptance",extra_env=managed_extra_env)
        prompt=ManagedProviderRuntime.authoritative_prompt(effective,prompt)
        (logs/"managed-prompt.txt").write_text(prompt,encoding="utf-8")
        if usage:
            usage.set_identity(model=(model_route.model if model_route else None),phase="discovery")
            usage.seed_estimate(input_text=prompt,event="managed_prompt_estimate")
        env=os.environ.copy(); env.update(managed.env); env["DYNOSAI_AGENT_PROVIDER"]="codex"; env["DYNOSAI_MANAGED_AGENT"]="1"; env["DYNOSAI_EXTERNAL_DEFAULTS_POLICY"]="ignore"; env["DYNOSAI_MCP_TOOL_PROFILE"]="acceptance"; env["DYNOSAI_TOKEN_USAGE_FILE"]=str((logs/"token-usage.json").resolve()); env["DYNOSAI_ACCEPTANCE_TRACE"]=str(logs/"elicitation-trace.jsonl"); env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE"]=str(logs/"mcp-activity.jsonl")
        if logger: env["DYNOSAI_ACCEPTANCE_PROCESS_TRACE_MIRROR"]=str(logger.paths.root/"mcp-activity.jsonl")
        if logger:
            logger.emit("provider_launch",phase="provider",provider="codex",message="launching Codex app-server",model=(model_route.model if model_route else None))
        proc=subprocess.Popen(executable_command(self.executable,"app-server","--listen","stdio://"),cwd=project,env=env,text=True,encoding="utf-8",errors="strict",stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=1)
        usage_stop=threading.Event(); usage_thread=None
        if usage:
            codex_home=Path(managed.root)/"home"
            def _watch_codex_usage():
                offsets:dict[Path,int]={}
                last_observed=-1
                while not usage_stop.is_set():
                    try:
                        sessions=codex_home/"sessions"
                        if sessions.exists():
                            for rollout in sorted(sessions.rglob("rollout*.jsonl")):
                                offset=offsets.get(rollout,0)
                                with rollout.open("r",encoding="utf-8",errors="replace") as fh:
                                    fh.seek(offset)
                                    for raw in fh:
                                        try: item=json.loads(raw)
                                        except Exception: continue
                                        sample=codex_token_sample_from_rollout(item)
                                        if not sample: continue
                                        snap=usage.observe_exact(sample["usage"],source="codex_rollout_token_count",context_window=sample.get("model_context_window"),last_usage=sample.get("last_usage"),event="codex_provider_usage")
                                        total=int(snap.get("observed_token_total") or 0)
                                        if logger and total!=last_observed:
                                            logger.emit("token_usage",phase="usage",provider="codex",message="Codex exact token usage observed",input_tokens=snap.get("input_tokens"),output_tokens=snap.get("output_tokens"),cached_input_tokens=snap.get("cached_input_tokens"),reasoning_output_tokens=snap.get("reasoning_output_tokens"),usage_quality=snap.get("quality"))
                                            last_observed=total
                                    offsets[rollout]=fh.tell()
                    except Exception as exc:
                        if logger: logger.emit("token_usage_watcher_error",phase="usage",provider="codex",message=f"Codex token watcher error: {exc}")
                    usage_stop.wait(0.5)
                # final drain on shutdown
                try:
                    sessions=codex_home/"sessions"
                    if sessions.exists():
                        for rollout in sorted(sessions.rglob("rollout*.jsonl")):
                            offset=offsets.get(rollout,0)
                            with rollout.open("r",encoding="utf-8",errors="replace") as fh:
                                fh.seek(offset)
                                for raw in fh:
                                    try: item=json.loads(raw)
                                    except Exception: continue
                                    sample=codex_token_sample_from_rollout(item)
                                    if sample: usage.observe_exact(sample["usage"],source="codex_rollout_token_count",context_window=sample.get("model_context_window"),last_usage=sample.get("last_usage"),event="codex_provider_usage")
                except Exception: pass
            usage_thread=threading.Thread(target=_watch_codex_usage,name="dynosai-codex-token-usage",daemon=True); usage_thread.start()
        started=time.monotonic(); deadline=started+self.max_runtime
        assert proc.stdout is not None
        lines: queue.Queue[str|None]=queue.Queue()
        stderr_path=logs/"provider-stderr.log"; stderr_lines=0; progress={"last":time.monotonic(),"last_heartbeat":time.monotonic()}
        def _reader():
            try:
                for raw_line in proc.stdout:
                    lines.put(raw_line)
            finally:
                lines.put(None)
        def _stderr_reader():
            nonlocal stderr_lines
            if proc.stderr is None: return
            mirror_stderr=(mirror_dir/stderr_path.name) if mirror_dir is not None else None
            mirror_fh=mirror_stderr.open("a",encoding="utf-8") if mirror_stderr and mirror_stderr.resolve()!=stderr_path.resolve() else None
            try:
                with stderr_path.open("a",encoding="utf-8") as fh:
                    for raw_line in proc.stderr:
                        fh.write(raw_line); fh.flush(); stderr_lines += 1; progress["last"]=time.monotonic()
                        if mirror_fh is not None: mirror_fh.write(raw_line); mirror_fh.flush()
                        try: os.fsync(fh.fileno())
                        except OSError: pass
                        if mirror_fh is not None:
                            try: os.fsync(mirror_fh.fileno())
                            except OSError: pass
            finally:
                if mirror_fh is not None: mirror_fh.close()
        reader=threading.Thread(target=_reader,name="dynosai-codex-appserver-reader",daemon=True); reader.start()
        stderr_reader=threading.Thread(target=_stderr_reader,name="dynosai-codex-appserver-stderr",daemon=True); stderr_reader.start()
        init_id=self._id(); thread_id_req=self._id(); turn_id_req=self._id(); thread_id=None; turn_completed=None; response_errors=[]; observed_settings=None; reroutes=[]
        sandbox_candidates=["workspace-write","workspaceWrite"]
        sandbox_attempt=0; sandbox_mode=None
        # Codex rejects non-empty MCP form elicitations under approvalPolicy=never
        # before app-server can surface them. Use the narrowest policy that
        # permits only MCP elicitations while keeping sandbox/rule/skill and
        # request-permission approval channels disabled.
        elicitation_only_policy={"granular":{
            "sandbox_approval":False,
            "rules":False,
            "skill_approval":False,
            "request_permissions":False,
            "mcp_elicitations":True,
        }}
        self._send(proc,{"method":"initialize","id":init_id,"params":{"clientInfo":{"name":"dynosai_acceptance","title":"DynosAI Acceptance Harness","version":__version__},"capabilities":{"experimentalApi":True,"requestAttestation":False,"mcpServerOpenaiFormElicitation":True}}},trace,mirror_dir)
        if logger: logger.emit("codex_initialize_sent",phase="provider",provider="codex",message="Codex initialize request sent")
        initialized_sent=False; thread_sent=False; turn_sent=False; message_count=0; termination_reason=None; mcp_trace=logs/"mcp-activity.jsonl"; last_mcp_mtime=0.0
        try:
            while time.monotonic()<deadline:
                now=time.monotonic()
                try:
                    if mcp_trace.exists():
                        mt=mcp_trace.stat().st_mtime
                        if mt>last_mcp_mtime:
                            last_mcp_mtime=mt; progress["last"]=now
                except OSError:
                    pass
                idle_for=now-progress["last"]
                if idle_for>=self.idle_timeout:
                    termination_reason="idle_timeout"
                    response_errors.append({"message":f"Codex app-server idle timeout after {self.idle_timeout}s"})
                    break
                try: raw=lines.get(timeout=min(1.0,max(0.05,deadline-time.monotonic())))
                except queue.Empty:
                    if proc.poll() is not None: break
                    now=time.monotonic(); elapsed=now-started; idle_for=now-progress["last"]
                    if logger and idle_for>=self.slow_progress and now-progress["last_heartbeat"]>=10:
                        logger.emit("slow_progress",phase="provider",provider="codex",message="Codex is still running but progress is slow",duration_seconds=round(elapsed,1),idle_seconds=round(idle_for,1),status="running")
                    if logger and now-progress["last_heartbeat"]>=10:
                        snap=usage.current() if usage else {}
                        logger.heartbeat(provider="codex",phase="provider",duration_seconds=round(elapsed,1),remaining_seconds=max(0,int(deadline-now)),idle_seconds=round(idle_for,1),stream_lines=message_count,stderr_lines=stderr_lines,input_tokens=snap.get("input_tokens"),output_tokens=snap.get("output_tokens"),cached_input_tokens=snap.get("cached_input_tokens"),reasoning_output_tokens=snap.get("reasoning_output_tokens"),usage_quality=snap.get("quality"))
                        progress["last_heartbeat"]=now
                    continue
                if raw is None:
                    if proc.poll() is not None: break
                    continue
                progress["last"]=time.monotonic()
                try: msg=json.loads(raw)
                except Exception:
                    _append_jsonl(trace,{"direction":"codex->client","raw":raw.rstrip("\n"),"at":utc_now()},mirror_dir); continue
                _append_jsonl(trace,{"direction":"codex->client","payload":msg,"at":utc_now()},mirror_dir); message_count += 1
                method=str(msg.get("method") or "")
                if logger and method in {"mcpServer/elicitation/request","thread/settings/updated","model/rerouted","turn/completed","error","item/started","item/completed"}:
                    logger.emit("codex_event",phase="provider",provider="codex",message=f"Codex event {method}",method=method,stream_lines=message_count)
                if self._auto_resolve_server_request(proc,msg,trace,mirror_dir):
                    if logger: logger.emit("codex_request_auto_resolved",phase="provider",provider="codex",message=f"auto-resolved Codex request {method}",method=method)
                    continue
                if msg.get("id")==init_id:
                    if msg.get("error"): response_errors.append(msg["error"]); break
                    self._send(proc,{"method":"initialized","params":{}},trace,mirror_dir); initialized_sent=True
                    if logger: logger.emit("codex_initialized",phase="provider",provider="codex",message="Codex initialized")
                    sandbox_mode=sandbox_candidates[sandbox_attempt]
                    thread_params={"cwd":str(project),"approvalPolicy":elicitation_only_policy,"sandbox":sandbox_mode}
                    if model_route: thread_params["model"]=model_route.model
                    self._send(proc,{"method":"thread/start","id":thread_id_req,"params":thread_params},trace,mirror_dir); thread_sent=True
                    if logger: logger.emit("codex_thread_start_sent",phase="provider",provider="codex",message="Codex thread/start sent",model=(model_route.model if model_route else None))
                    continue
                if msg.get("id")==thread_id_req:
                    if msg.get("error"):
                        error=msg.get("error") or {}
                        text=json.dumps(error,ensure_ascii=False).lower()
                        if "sandbox" in text or "unknown variant" in text:
                            sandbox_attempt += 1
                            if sandbox_attempt < len(sandbox_candidates):
                                thread_id_req=self._id(); sandbox_mode=sandbox_candidates[sandbox_attempt]
                                _append_jsonl(trace,{"event":"sandbox-compat-retry","sandbox":sandbox_mode,"previous_error":error,"at":utc_now()},mirror_dir)
                                thread_params={"cwd":str(project),"approvalPolicy":elicitation_only_policy,"sandbox":sandbox_mode}
                                if model_route: thread_params["model"]=model_route.model
                                self._send(proc,{"method":"thread/start","id":thread_id_req,"params":thread_params},trace,mirror_dir)
                                continue
                        response_errors.append(error); break
                    thread=((msg.get("result") or {}).get("thread") or {}); thread_id=thread.get("id")
                    if not thread_id: response_errors.append({"message":"thread/start returned no thread id"}); break
                    if logger: logger.emit("codex_thread_started",phase="provider",provider="codex",message="Codex thread started",thread_id=thread_id)
                    turn_params={"threadId":thread_id,"input":[{"type":"text","text":prompt}]}
                    if model_route:
                        turn_params["model"]=model_route.model
                        if model_route.effort: turn_params["effort"]=model_route.effort
                    # Work around Codex app-server sandbox inheritance: turn/start may
                    # need an explicit sandboxPolicy matching the thread request, or
                    # the provider keeps a read-only policy even when thread/start
                    # asked for workspace-write.
                    if sandbox_mode=="workspace-write": turn_params["sandboxPolicy"]={"type":"workspaceWrite"}
                    elif sandbox_mode=="workspaceWrite": turn_params["sandboxPolicy"]={"type":"workspaceWrite"}
                    elif sandbox_mode=="read-only": turn_params["sandboxPolicy"]={"type":"readOnly"}
                    self._send(proc,{"method":"turn/start","id":turn_id_req,"params":turn_params},trace,mirror_dir); turn_sent=True
                    if logger: logger.emit("codex_turn_start_sent",phase="provider",provider="codex",message="Codex turn/start sent",thread_id=thread_id)
                    continue
                if msg.get("id")==turn_id_req and msg.get("error"):
                    response_errors.append(msg["error"]); break
                if msg.get("method")=="thread/settings/updated":
                    observed_settings=msg.get("params") or {}
                if msg.get("method")=="model/rerouted":
                    reroutes.append(msg.get("params") or {})
                if msg.get("method")=="turn/completed":
                    params=msg.get("params") or {}; tid=params.get("threadId") or params.get("thread_id")
                    if not thread_id or not tid or tid==thread_id:
                        turn_completed=params; break
                if msg.get("method")=="error":
                    response_errors.append(msg.get("params") or msg)
            else:
                termination_reason="max_runtime"
                response_errors.append({"message":f"Codex app-server max runtime exhausted after {self.max_runtime}s"})
        finally:
            if proc.poll() is None:
                proc.terminate()
                try: proc.wait(timeout=5)
                except Exception: proc.kill()
            stderr_reader.join(timeout=3)
            if usage:
                usage_stop.set()
                if usage_thread is not None: usage_thread.join(timeout=3)
            stderr=""
            try:
                if stderr_path.exists(): stderr=stderr_path.read_text(encoding="utf-8")
            except Exception: pass
            for stream in (proc.stdin,proc.stdout,proc.stderr):
                try:
                    if stream: stream.close()
                except Exception: pass
        status=str((turn_completed or {}).get("turn",{}).get("status") or (turn_completed or {}).get("status") or "")
        ok=bool(turn_completed) and status.lower() not in {"failed","interrupted","cancelled","canceled"} and not response_errors
        auto_count=0
        try:
            auto_count=sum(1 for line in trace.read_text(encoding="utf-8").splitlines() if '"kind": "mcp_server_elicitation"' in line or '"kind":"mcp_server_elicitation"' in line)
        except Exception: pass
        requested=model_route.to_dict() if model_route else None
        model_verified=None
        if model_route:
            # A successful turn/start is authoritative evidence that Codex accepted
            # the requested model/effort. Any explicit backend reroute invalidates
            # that certification. thread/settings/updated is retained as extra proof.
            # Route verification is independent from feature completion. Once
            # Codex accepted thread/turn start with the requested route and did
            # not reroute it, a later workflow timeout must not erase that fact.
            model_verified=bool(turn_sent and not reroutes)
            if observed_settings:
                settings_text=json.dumps(observed_settings,ensure_ascii=False).lower()
                if model_route.model.lower() not in settings_text: model_verified=False
                if model_route.effort and model_route.effort.lower() not in settings_text: model_verified=False
        exit_code=0 if ok else 1
        token_usage=usage.finalize(event="codex_usage_final") if usage else None
        duration=round(time.monotonic()-started,3)
        if logger:
            logger.emit("provider_exit",phase="provider",provider="codex",message="Codex provider exited",exit_code=exit_code,duration_seconds=duration,stream_lines=message_count,stderr_lines=stderr_lines,status="passed" if ok else "failed")
        return {"provider":"codex","driver":"codex-app-server","interaction_mode":"auto","exit_code":exit_code,"termination_reason":termination_reason or "provider_exit","duration_seconds":duration,"real_provider":True,"wire_elicitation":True,"automated_responses":True,"auto_response_channel":"codex-app-server-client","elicitation_auto_responses":auto_count,"thread_id":thread_id,"turn_completed":turn_completed,"errors":response_errors,"sandbox_mode":sandbox_mode,"sandbox_attempts":sandbox_attempt+1,"handshake":{"initialized":initialized_sent,"thread_started":thread_sent,"turn_started":turn_sent},"model_route_requested":requested,"model_settings_observed":observed_settings,"model_reroutes":reroutes,"model_route_verified":model_verified,"managed_runtime":managed.to_dict(),"provider_stderr":str(stderr_path),"stream_messages":message_count,"stderr_lines":stderr_lines,"token_usage":token_usage}


def _runtime_bin_snapshot() -> dict[str,str]:
    """Fingerprint the installed DynosAI interpreter bin directory.

    Acceptance providers must never install/chmod/shim tools inside DynosAI's
    own virtualenv. Controller/runtime activity has no reason to mutate this
    directory during a feature run, so any delta is provider-side drift.
    """
    base=Path(sys.executable).absolute().parent
    out:dict[str,str]={}
    if not base.exists(): return out
    for path in sorted(base.iterdir(),key=lambda x:x.name):
        try:
            if path.is_symlink():
                out[path.name]="symlink:"+os.readlink(path)
            elif path.is_file():
                h=hashlib.sha256(path.read_bytes()).hexdigest()
                mode=oct(path.stat().st_mode & 0o777)
                out[path.name]=f"file:{mode}:{h}"
        except Exception as exc:
            out[path.name]=f"unreadable:{type(exc).__name__}"
    return out


def _governed_work_rows(db)->tuple[list[dict[str,Any]],list[dict[str,Any]]]:
    """Return all work rows plus the feature/bugfix work governed by acceptance.

    Brownfield adoption intentionally creates PROJECT/BASE memory rows. Those are
    context authorities, not extra user features, and must not fail single-feature
    acceptance. DYN-* is the stable governed work-item namespace.
    """
    all_rows=db.query("SELECT id,work_type,state,quality_score,provider_session_id,workspace_strategy,worktree,branch FROM work_items ORDER BY created_at")
    governed=[row for row in all_rows if str(row.get("id") or "").startswith("DYN-")]
    return all_rows,governed


def _strict_zero_metric(children:list[dict[str,Any]],key:str)->bool:
    """Certify a zero-valued metric independently from overall child status.

    A child may fail an unrelated gate while still providing valid evidence that
    this metric is exactly zero. Missing metrics remain uncertified. Overall
    pass/fail is aggregated separately by the acceptance suite.
    """
    return bool(children) and all(key in c and c.get(key)==0 for c in children)


class RealProviderAcceptanceCase:
    def __init__(self,provider:str,scenario:str,base:Path,*,interaction_mode:str="auto",timeout:int|None=None,max_runtime:int=1800,idle_timeout:int=180,slow_progress:int=60,log_dir:str|Path|None=None,run_id:str|None=None,console_progress:bool=True,model_profile:str="economy"):
        if provider not in ACCEPTANCE_PROVIDERS: raise ValueError(provider)
        if scenario not in ACCEPTANCE_SCENARIOS: raise ValueError(scenario)
        if interaction_mode not in {"auto","interactive"}: raise ValueError("interaction_mode must be auto or interactive")
        self.provider=provider; self.scenario=scenario; self.base=base; self.interaction_mode=interaction_mode
        self.max_runtime=int(timeout) if timeout is not None else int(max_runtime)
        self.idle_timeout=int(idle_timeout)
        self.slow_progress=int(slow_progress)
        self.timeout=self.max_runtime  # backwards-compatible alias
        self.case=base/provider/scenario; self.project=self.case/"project"; self.logs=self.case/"logs"
        self.progress_dir=Path(log_dir).expanduser().resolve() if log_dir else None
        self.run_id=run_id or base.name
        self.console_progress=bool(console_progress)
        self.model_profile=str(model_profile or "economy").lower()
        if self.model_profile not in ACCEPTANCE_MODEL_PROFILES: raise ValueError(f"Unknown acceptance model profile: {self.model_profile}")

    def _oracle(self,app:DynosAIApplication,wid:str)->dict[str,Any]:
        oracle_runner=DebugE2ERunner(agent="cursor",scenario=self.scenario,workspace=self.case/"oracle-holder",output=self.case/"oracle.zip")
        oracle_runner.engine=app.engine; oracle_runner.work_id=wid; oracle_runner.project=self.project; oracle_runner.logs=self.logs; oracle_runner.timeline_path=self.logs/"oracle-timeline.jsonl"
        return oracle_runner._run_fibonacci_oracle() if self.scenario=="fibonacci" else oracle_runner._run_orderflow_oracle()

    def _start_mcp_activity_watcher(self,logger:AcceptanceLogger,trace_path:Path,usage:TokenUsageRecorder|None=None)->tuple[threading.Event,threading.Thread]:
        stop=threading.Event()
        def watch():
            offset=0
            while not stop.is_set():
                try:
                    if trace_path.exists():
                        with trace_path.open("r",encoding="utf-8",errors="replace") as fh:
                            fh.seek(offset)
                            for line in fh:
                                if not line.strip(): continue
                                try: item=json.loads(line)
                                except Exception: continue
                                event=str(item.get("event") or "")
                                if event in {"mcp_tool_start","mcp_tool_end"}:
                                    tool=str(item.get("tool") or "unknown")
                                    status=item.get("status")
                                    message=f"MCP {tool} started" if event=="mcp_tool_start" else f"MCP {tool} finished ({status or 'unknown'})"
                                    workflow_state=item.get("workflow_state")
                                    workflow_activity=activity_for_state(str(workflow_state)) if workflow_state else None
                                    if usage and workflow_activity:
                                        usage.set_identity(phase=workflow_activity)
                                    logger.emit(event,phase="mcp",provider=self.provider,scenario=self.scenario,message=message,tool=tool,status=status,work_id=item.get("work_id"),action=item.get("action"),request_id=item.get("request_id"),workflow_state=workflow_state,workflow_activity=workflow_activity)
                            offset=fh.tell()
                except Exception as exc:
                    logger.emit("mcp_trace_watcher_error",phase="mcp",provider=self.provider,scenario=self.scenario,message=f"MCP trace watcher error: {exc}")
                stop.wait(0.4)
            # One final drain avoids losing the last tool completion when the provider exits.
            try:
                if trace_path.exists():
                    with trace_path.open("r",encoding="utf-8",errors="replace") as fh:
                        fh.seek(offset)
                        for line in fh:
                            if not line.strip(): continue
                            try: item=json.loads(line)
                            except Exception: continue
                            event=str(item.get("event") or "")
                            if event in {"mcp_tool_start","mcp_tool_end"}:
                                tool=str(item.get("tool") or "unknown"); status=item.get("status")
                                message=f"MCP {tool} started" if event=="mcp_tool_start" else f"MCP {tool} finished ({status or 'unknown'})"
                                workflow_state=item.get("workflow_state")
                                workflow_activity=activity_for_state(str(workflow_state)) if workflow_state else None
                                if usage and workflow_activity:
                                    usage.set_identity(phase=workflow_activity)
                                logger.emit(event,phase="mcp",provider=self.provider,scenario=self.scenario,message=message,tool=tool,status=status,work_id=item.get("work_id"),action=item.get("action"),request_id=item.get("request_id"),workflow_state=workflow_state,workflow_activity=workflow_activity)
            except Exception:
                pass
        thread=threading.Thread(target=watch,name=f"dynosai-acceptance-mcp-{self.provider}-{self.scenario}",daemon=True)
        thread.start()
        return stop,thread

    def run(self)->dict[str,Any]:
        if self.case.exists(): shutil.rmtree(self.case)
        self.project.mkdir(parents=True,exist_ok=True); self.logs.mkdir(parents=True,exist_ok=True)
        progress_root=self.progress_dir or (self.logs/"progress")
        logger=AcceptanceLogger(progress_root,run_id=self.run_id,console=self.console_progress,scope="case")
        logger.initialize(provider=self.provider,scenario=self.scenario,workspace=str(self.project),raw_logs=str(self.logs),max_runtime_seconds=self.max_runtime,idle_timeout_seconds=self.idle_timeout,slow_progress_seconds=self.slow_progress)
        logger.emit("case_start",phase="setup",provider=self.provider,scenario=self.scenario,message="acceptance case started",pid=os.getpid())
        fixture=seed_orderflow_brownfield(self.project) if self.scenario=="orderflow-contract-discounts" else None
        logger.emit("fixture_ready",phase="setup",provider=self.provider,scenario=self.scenario,message="scenario fixture ready",fixture=bool(fixture))
        probe=probe_provider(self.provider)
        _write_json(self.logs/"provider-probe.json",probe.to_dict())
        logger.emit("provider_probe",phase="setup",provider=self.provider,scenario=self.scenario,message="provider probe completed",installed=probe.installed,executable=probe.executable,version=probe.version,error=probe.error)
        result={"provider":self.provider,"scenario":self.scenario,"mode":SCENARIOS[self.scenario]["mode"],"interaction_mode":self.interaction_mode,"started_at":utc_now(),"probe":probe.to_dict(),"fixture":fixture,"status":"running","telemetry":{"log_dir":str(progress_root),"log_file":str(logger.paths.text),"events_file":str(logger.paths.events),"state_file":str(logger.paths.state),"raw_logs":str(self.logs)}}
        if not probe.installed or not probe.executable:
            result.update({"status":"failed","failure":{"type":"ProviderUnavailable","message":probe.error or "provider not installed"},"finished_at":utc_now()}); _write_json(self.logs/"summary.json",result); logger.emit("case_failed",phase="setup",provider=self.provider,scenario=self.scenario,message="provider unavailable",status="failed"); return result
        routing=ProviderModelRouting(self.project)
        if os.environ.get("DYNOSAI_ACCEPTANCE_USE_CONFIGURED_MODELS")=="1":
            model_route=routing.resolve_default(self.provider)
        else:
            default=ACCEPTANCE_MODEL_PROFILES[self.model_profile][self.provider]
            model_route=ModelRoute(self.provider,"discovery",default["model"],default.get("effort"),f"acceptance_{self.model_profile}","builtin","turn_override" if self.provider=="codex" else "session_or_resume_boundary")
        model_route=env_override(self.provider,model_route)
        result["model_route"]=model_route.to_dict()
        logger.emit("model_route_resolved",phase="routing",provider=self.provider,scenario=self.scenario,message="provider model route resolved",model=model_route.model,effort=model_route.effort,source=model_route.source)
        usage=TokenUsageRecorder(self.logs,self.provider,f"{self.run_id}:{self.provider}:{self.scenario}",model=model_route.model,scenario=self.scenario,mirror_roots=(progress_root,))
        result["telemetry"]["token_usage_file"]=str(usage.latest_path)
        result["telemetry"]["token_usage_events_file"]=str(usage.events_path)
        prompt=_scenario_prompt(self.provider,self.scenario,model_route); (self.logs/"prompt.txt").write_text(prompt,encoding="utf-8")
        logger.emit("prompt_ready",phase="routing",provider=self.provider,scenario=self.scenario,message="acceptance prompt persisted",path=str(self.logs/"prompt.txt"))
        driver=(CursorAcceptanceDriver(probe.executable,self.max_runtime,self.idle_timeout,self.slow_progress) if self.provider=="cursor" else CodexAppServerDriver(probe.executable,self.max_runtime,self.idle_timeout,self.slow_progress))
        runtime_before=_runtime_bin_snapshot()
        logger.emit("provider_run_start",phase="provider",provider=self.provider,scenario=self.scenario,message="real provider execution starting",path=str(self.logs))
        mcp_stop,mcp_watcher=self._start_mcp_activity_watcher(logger,progress_root/"mcp-activity.jsonl",usage)
        try:
            try:
                provider_result=driver.run(self.project,prompt,self.logs,interaction_mode=self.interaction_mode,model_route=model_route,logger=logger,usage=usage); result["provider_run"]=provider_result; result["token_usage"]=provider_result.get("token_usage") or usage.current()
                token_final=result.get("token_usage") or {}
                logger.emit("token_usage_final",phase="usage",provider=self.provider,scenario=self.scenario,message="provider token usage finalized",input_tokens=token_final.get("input_tokens"),cached_input_tokens=token_final.get("cached_input_tokens"),output_tokens=token_final.get("output_tokens"),reasoning_output_tokens=token_final.get("reasoning_output_tokens"),provider_total_tokens=token_final.get("provider_total_tokens"),context_read_tokens=token_final.get("context_read_tokens"),generated_tokens=token_final.get("generated_tokens"),processed_tokens=token_final.get("processed_tokens"),usage_quality=token_final.get("quality"),tokens_per_minute=token_final.get("tokens_per_minute"))
            finally:
                mcp_stop.set(); mcp_watcher.join(timeout=2)
            logger.emit("provider_run_complete",phase="provider",provider=self.provider,scenario=self.scenario,message="real provider execution returned",exit_code=provider_result.get("exit_code"),duration_seconds=provider_result.get("duration_seconds"))
            mcp_metric_path=self.logs/"mcp-activity.jsonl"
            if not mcp_metric_path.exists(): mcp_metric_path=progress_root/"mcp-activity.jsonl"
            provider_metric_stream=self.logs/("provider-stream.jsonl" if self.provider=="cursor" else "codex-app-server.jsonl")
            result["runtime_metrics"]=runtime_metrics(self.provider,provider_result.get("duration_seconds"),mcp_path=mcp_metric_path,provider_stream_path=provider_metric_stream)
            result["context_optimization"]=_context_optimization_metrics(mcp_metric_path,self.project,provider_metric_stream)
            _write_json(self.logs/"context-optimization.json",result["context_optimization"])
            result["baseline_comparison"]=compare_to_baseline(provider=self.provider,scenario=self.scenario,model=model_route.model,effort=model_route.effort,usage=result.get("token_usage"),runtime=result.get("runtime_metrics"))
            _write_json(self.logs/"runtime-metrics.json",result["runtime_metrics"])
            _write_json(self.logs/"baseline-comparison.json",result["baseline_comparison"] or {})
            logger.emit("runtime_metrics",phase="usage",provider=self.provider,scenario=self.scenario,message="runtime/token efficiency metrics finalized",workflow_elapsed_seconds=result["runtime_metrics"].get("workflow_elapsed_seconds"),dynosai_mcp_seconds=result["runtime_metrics"].get("dynosai_mcp_seconds"),mcp_calls=result["runtime_metrics"].get("mcp_calls"),provider_api_seconds=result["runtime_metrics"].get("provider_api_seconds"),reconnect_count=result["runtime_metrics"].get("reconnect_count"))
            strict_audit=audit_managed_runtime(self.provider,provider_result.get("managed_runtime"),self.logs)
            result["strict_runtime_audit"]=strict_audit
            _write_json(self.logs/"strict-runtime-audit.json",strict_audit)
            logger.emit("strict_runtime_audit",phase="verification",provider=self.provider,scenario=self.scenario,message="strict managed runtime audited",status="passed" if strict_audit.get("strict_managed_runtime_verified") else "failed",provider_extension_artifacts=strict_audit.get("provider_extension_artifacts"),external_skill_invocations=strict_audit.get("external_skill_invocation_count"),external_plugin_invocations=strict_audit.get("external_plugin_invocation_count"),external_mcp_invocations=strict_audit.get("external_mcp_invocation_count"))
            runtime_after=_runtime_bin_snapshot()
            runtime_added=sorted(set(runtime_after)-set(runtime_before)); runtime_removed=sorted(set(runtime_before)-set(runtime_after))
            runtime_changed=sorted(k for k in set(runtime_before)&set(runtime_after) if runtime_before[k]!=runtime_after[k])
            result["runtime_bin_integrity"]={"unchanged":not(runtime_added or runtime_removed or runtime_changed),"added":runtime_added,"removed":runtime_removed,"changed":runtime_changed}
            # The provider may exit successfully even after narrating an error; the
            # authoritative acceptance decision comes from DynosAI state + oracle.
            logger.emit("state_inspection_start",phase="verification",provider=self.provider,scenario=self.scenario,message="inspecting DynosAI authoritative state")
            app=DynosAIApplication(self.project); app.engine.db.initialize()
            all_works,works=_governed_work_rows(app.engine.db)
            result["work_items_total"]=len(all_works); result["governed_work_items"]=len(works)
            result["work_item_ids"]=[str(row.get("id")) for row in all_works]
            if len(works)!=1: raise AssertionError(f"expected exactly one governed DYN work item, got {len(works)} (total rows={len(all_works)})")
            work=app.engine.get_work(works[0]["id"]); wid=work["id"]
            observed_model=provider_result.get("model_observed")
            observed_effort=None
            if self.provider=="codex":
                settings=provider_result.get("model_settings_observed") or {}
                if isinstance(settings,dict):
                    observed_model=settings.get("model") or settings.get("modelName")
                    observed_effort=settings.get("effort") or settings.get("reasoningEffort")
            ModelControlPlane(self.project).observe_provider_route(
                self.provider,wid,model_route,verified=provider_result.get("model_route_verified"),
                observed_model=str(observed_model) if observed_model else None,
                observed_effort=str(observed_effort) if observed_effort else None,
                activity=activity_for_state(work.get("state")),source="acceptance_provider",initial_route=True,
            )
            # Recompute after provider-route observation so recommendation/request/
            # applied/verified counters reflect provider truth in the bundle.
            result["context_optimization"]=_context_optimization_metrics(mcp_metric_path,self.project,provider_metric_stream)
            _write_json(self.logs/"context-optimization.json",result["context_optimization"])
            logger.emit("work_state_observed",phase="verification",provider=self.provider,scenario=self.scenario,message=f"DynosAI work state: {work.get('state')}",work_id=wid,status=work.get("state"),quality_score=work.get("quality_score"))
            logger.emit("oracle_start",phase="verification",provider=self.provider,scenario=self.scenario,message="independent scenario oracle starting")
            oracle=self._oracle(app,wid) if work.get("state")=="done" else {"passed":False,"failures":["work_not_done"],"scenario":self.scenario}
            logger.emit("oracle_complete",phase="verification",provider=self.provider,scenario=self.scenario,message="independent scenario oracle completed",status="passed" if oracle.get("passed") else "failed",failures=oracle.get("failures") or [])
            sessions=app.engine.db.query("SELECT * FROM provider_sessions WHERE work_id=? ORDER BY created_at",(wid,))
            interactions=app.engine.db.query("SELECT id,kind,gate,status,provider,response_json FROM human_interactions WHERE work_id=? ORDER BY created_at",(wid,))
            audit={r["event_type"]:r["n"] for r in app.engine.db.query("SELECT event_type,COUNT(*) n FROM audit GROUP BY event_type")}
            accepted=all(str(x.get("status"))=="accepted" for x in interactions) and len(interactions)>=4
            expected_gates=["spec","plan","code","merge"]
            observed_gates=[str(x.get("gate")) for x in interactions if x.get("kind")=="gate_approval"]
            expected_interactions=(len(interactions)==4 and observed_gates==expected_gates and all(x.get("kind")=="gate_approval" for x in interactions))
            scope_rows=app.engine.db.query("SELECT id,status,path,action FROM scope_requests WHERE work_id=? ORDER BY created_at",(wid,))
            same_workspace=str(Path(work.get("worktree") or self.project).resolve())==str(self.project.resolve())
            session_ok=len(sessions)==1 and str(sessions[0].get("state"))=="completed"
            mcp_fail=int(audit.get("MCPToolFailed",0)); mcp_rej=int(audit.get("MCPToolRejected",0)); mcp_norm=int(audit.get("MCPToolNormalized",0))
            mcp_payloads=[]
            for row in app.engine.db.query("SELECT payload FROM audit WHERE event_type='MCPToolCalled' ORDER BY created_at"):
                try:
                    mcp_payloads.append(json.loads(row.get("payload") or "{}"))
                except Exception:
                    continue
            from .certification_matrix import collapse_observed_mcp_protocols, observed_mcp_protocols_from_payloads
            result.update(collapse_observed_mcp_protocols(observed_mcp_protocols_from_payloads(mcp_payloads)))
            rejection_rows=app.engine.db.query("SELECT entity_id,created_at FROM audit WHERE event_type='MCPToolRejected' ORDER BY created_at")
            call_rows=app.engine.db.query("SELECT entity_id,created_at FROM audit WHERE event_type='MCPToolCalled' ORDER BY created_at")
            unrecovered_rejections=[]
            for rejection in rejection_rows:
                entity=str(rejection.get("entity_id") or "")
                rejected_at=str(rejection.get("created_at") or "")
                recovered=any(str(call.get("entity_id") or "")==entity and str(call.get("created_at") or "")>rejected_at for call in call_rows)
                if not recovered:
                    unrecovered_rejections.append({"entity_id":entity,"created_at":rejected_at})
            rejection_recovery={
                "total":len(rejection_rows),
                "recovered":len(rejection_rows)-len(unrecovered_rejections),
                "unrecovered":len(unrecovered_rejections),
                "all_recovered":bool(rejection_rows) and not unrecovered_rejections,
                "unrecovered_items":unrecovered_rejections,
            }
            configured_test_command=app.engine.db.get_meta("test_command")
            expected_test_command=SCENARIOS[self.scenario]["test_command"]
            trace_path=self.logs/"elicitation-trace.jsonl"; trace_count=len(trace_path.read_text(encoding="utf-8").splitlines()) if trace_path.exists() else 0
            gates={
                "provider_process_ok":provider_result.get("exit_code")==0,
                "final_state_done":work.get("state")=="done",
                "quality_100":int(work.get("quality_score") or 0)==100,
                "single_provider_session":len(sessions)==1,
                "provider_session_completed":session_ok,
                "same_workspace":same_workspace,
                "human_gates_resolved":accepted,
                "expected_human_interactions_only":expected_interactions,
                "zero_scope_requests":len(scope_rows)==0,
                "validation_baseline_correct":configured_test_command==expected_test_command,
                "runtime_bin_unchanged":bool((result.get("runtime_bin_integrity") or {}).get("unchanged")),
                "provider_model_route_verified":bool(provider_result.get("model_route_verified")) if self.interaction_mode=="auto" else True,
                "token_usage_exact":bool((result.get("token_usage") or {}).get("quality")=="exact") if self.interaction_mode=="auto" else True,
                "elicitation_trace_present":trace_count>=4,
                "oracle_passed":bool(oracle.get("passed")),
                "zero_mcp_failures":mcp_fail==0,
                "zero_mcp_rejections":mcp_rej==0,
                "zero_mcp_normalizations":mcp_norm==0,
                "strict_managed_runtime_verified":bool((result.get("strict_runtime_audit") or {}).get("strict_managed_runtime_verified")),
                "zero_provider_extension_artifacts":bool((result.get("strict_runtime_audit") or {}).get("zero_provider_extension_artifacts")),
                "zero_external_skill_invocations":bool((result.get("strict_runtime_audit") or {}).get("zero_external_skill_invocations")),
                "zero_external_plugin_invocations":bool((result.get("strict_runtime_audit") or {}).get("zero_external_plugin_invocations")),
                "zero_external_mcp_invocations":bool((result.get("strict_runtime_audit") or {}).get("zero_external_mcp_invocations")),
            }
            result["cost_estimate"]=(result.get("token_usage") or {}).get("cost_estimate")
            result.update({"work_id":wid,"final_state":work.get("state"),"quality_score":work.get("quality_score"),"validation_command":configured_test_command,"oracle":oracle,"provider_sessions":len(sessions),"provider_session_state":sessions[0].get("state") if len(sessions)==1 else None,"same_workspace":same_workspace,"elicitations":interactions,"expected_elicitation_gates":expected_gates,"scope_requests":scope_rows,"elicitation_trace_count":trace_count,"audit_counts":audit,"mcp_failures":mcp_fail,"mcp_rejections":mcp_rej,"mcp_rejection_recovery":rejection_recovery,"mcp_normalizations":mcp_norm,"provider_extension_artifacts":int((result.get("strict_runtime_audit") or {}).get("provider_extension_artifacts") or 0),"external_skill_invocations":int((result.get("strict_runtime_audit") or {}).get("external_skill_invocation_count") or 0),"external_plugin_invocations":int((result.get("strict_runtime_audit") or {}).get("external_plugin_invocation_count") or 0),"external_mcp_invocations":int((result.get("strict_runtime_audit") or {}).get("external_mcp_invocation_count") or 0),"gates":gates,"status":"passed" if all(gates.values()) else "failed"})
            logger.emit("gate_evaluation",phase="verification",provider=self.provider,scenario=self.scenario,message="acceptance gates evaluated",status=result["status"],failed_gates=[k for k,v in gates.items() if not v])
        except Exception as exc:
            result.update({"status":"failed","failure":{"type":type(exc).__name__,"message":str(exc)}})
            logger.emit("case_exception",phase="verification",provider=self.provider,scenario=self.scenario,message=f"{type(exc).__name__}: {exc}",status="failed",error_type=type(exc).__name__)
        result["finished_at"]=utc_now(); _write_json(self.logs/"summary.json",result)
        logger.emit("case_complete",phase="complete",provider=self.provider,scenario=self.scenario,message=f"acceptance case {result.get('status')}",status=result.get("status"),path=str(self.logs/"summary.json"))
        return result


def certification_infrastructure_retry_allowed(
    max_infrastructure_attempts: int,
    attempt: int,
    child: dict[str, Any],
    worker_failure: dict[str, Any] | None,
) -> bool:
    """MATRIX certification uses max_infrastructure_attempts=1 (one provider execution)."""
    if int(attempt) >= max(1, int(max_infrastructure_attempts)):
        return False
    return _retryable_acceptance_infrastructure_failure(child, worker_failure, attempt)


def _retryable_acceptance_infrastructure_failure(child:dict[str,Any], worker_failure:dict[str,Any]|None, attempt:int)->bool:
    """Allow one transparent retry for a safe pre-implementation infrastructure timeout.

    A prior gate or schema rejection does not permanently poison retry eligibility when it
    was demonstrably resolved before the provider stalled.  We still never retry after
    native edits, scope activity, MCP failures, unresolved rejections, cancelled/request-
    changes governance, or after entering implementation/validation/merge.
    """
    if int(attempt)!=1 or child.get("status")=="passed":
        return False
    termination=(child.get("provider_run") or {}).get("termination_reason")
    timed_out=termination=="idle_timeout" or (worker_failure or {}).get("type")=="WorkerTimeout"
    if not timed_out:
        return False
    if child.get("scope_requests"):
        return False
    optimization=child.get("context_optimization") or {}
    if int(optimization.get("native_file_change_actions") or 0)!=0:
        return False
    if int(child.get("mcp_failures") or 0)!=0:
        return False
    interactions=child.get("elicitations") or []
    if any(str(item.get("status") or "").lower()!="accepted" for item in interactions):
        return False
    rejections=int(child.get("mcp_rejections") or 0)
    recovery=child.get("mcp_rejection_recovery") or {}
    if rejections and not bool(recovery.get("all_recovered")):
        return False
    return str(child.get("final_state") or "").lower() in {"","inbox","discovery","spec_review","planning","plan_review","ready"}


class RealProviderAcceptanceSuite:
    """Acceptance bundle intended to run on the user's own machine."""
    def __init__(self,providers:Iterable[str]=ACCEPTANCE_PROVIDERS,scenario:str="green-brown",output:str|Path|None=None,workspace:str|Path|None=None,*,interaction_mode:str="auto",timeout:int|None=None,max_runtime:int=1800,idle_timeout:int=180,slow_progress:int=60,configure:bool=True,keep:bool=True,log_dir:str|Path|None=None,heartbeat:int=10,model_profile:str="economy",max_infrastructure_attempts:int=2,certification_mode:bool=False):
        self.providers=tuple(dict.fromkeys(providers)); self.scenario=scenario; self.interaction_mode=interaction_mode
        self.max_runtime=int(timeout) if timeout is not None else int(max_runtime)
        self.idle_timeout=int(idle_timeout); self.slow_progress=int(slow_progress); self.timeout=self.max_runtime
        self.configure=configure; self.keep=keep; self.heartbeat=max(1,int(heartbeat))
        self.model_profile=str(model_profile or "economy").lower()
        if self.model_profile not in ACCEPTANCE_MODEL_PROFILES: raise ValueError(f"Unknown acceptance model profile: {self.model_profile}")
        self.max_infrastructure_attempts=1 if certification_mode else max(1,int(max_infrastructure_attempts))
        for p in self.providers:
            if p not in ACCEPTANCE_PROVIDERS: raise ValueError(p)
        scenarios=ACCEPTANCE_SCENARIOS if scenario=="green-brown" else (scenario,)
        for s in scenarios:
            if s not in ACCEPTANCE_SCENARIOS: raise ValueError(s)
        self.scenarios=tuple(scenarios)
        stamp=utc_now().replace(":","").replace("+00:00","Z")
        self.run_id=new_run_id()
        self.base=Path(workspace).expanduser().resolve() if workspace else Path(tempfile.gettempdir())/"dynosai-acceptance-runs"/f"ACCEPTANCE-{stamp}"
        self.output=Path(output).expanduser().resolve() if output else self.base/f"dynosai-acceptance-{__version__.replace('.','')}-{interaction_mode}.zip"
        self.log_dir=Path(log_dir).expanduser().resolve() if log_dir else acceptance_log_home()/self.run_id

    def _case_order(self)->list[tuple[str,str]]:
        # RC2 matrix order is scenario-first so the full acceptance runs both
        # greenfield providers before either brownfield case. This makes a single
        # consolidated run easy to read while keeping all four cases isolated.
        return [(provider,scenario) for scenario in self.scenarios for provider in self.providers]

    def run(self)->dict[str,Any]:
        started=utc_now(); self.base.mkdir(parents=True,exist_ok=True); self.log_dir.mkdir(parents=True,exist_ok=True)
        logger=AcceptanceLogger(self.log_dir,run_id=self.run_id,console=True,scope="suite")
        logger.initialize(version=__version__,providers=list(self.providers),scenario=self.scenario,interaction_mode=self.interaction_mode,model_profile=self.model_profile,max_runtime_seconds=self.max_runtime,idle_timeout_seconds=self.idle_timeout,slow_progress_seconds=self.slow_progress,workspace=str(self.base),output=str(self.output),path=str(self.log_dir))
        update_latest_pointer(self.log_dir)
        print(f"Acceptance logs: {self.log_dir}",flush=True)
        print(f"Live progress : tail -f {logger.paths.text}",flush=True)
        print(f"Status        : dynosai debug acceptance-status --log-dir {self.log_dir}",flush=True)
        print(f"Bundle logs   : dynosai debug acceptance-logs --log-dir {self.log_dir}",flush=True)
        print(f"Token usage   : dynosai usage watch --log-dir {self.log_dir}",flush=True)
        logger.emit("suite_start",phase="setup",message="real-provider acceptance suite started",pid=os.getpid(),path=str(self.log_dir))
        setup=None
        if self.configure:
            logger.emit("provider_setup_start",phase="setup",message="refreshing provider MCP configuration")
            setup=MachineProviderSetup().setup(list(self.providers),install_model=False)
            _write_json(self.base/"setup.json",setup)
            logger.emit("provider_setup_complete",phase="setup",message="provider MCP configuration refreshed",status="ready" if setup.get("ready") else "not_ready")
        probes={p:probe_provider(p).to_dict() for p in self.providers}; _write_json(self.base/"provider-probes.json",probes)
        logger.emit("provider_probes_complete",phase="setup",message="provider probes completed",probes=probes)
        children=[]
        # Every provider/scenario executes in a fresh Python interpreter.  Besides
        # isolating provider processes, this prevents semantic indexes, SQLite/Git
        # handles and model caches from accumulating across the four acceptance
        # quadrants.  The worker persists its authoritative summary under the shared
        # acceptance directory; the parent only aggregates those durable results.
        case_order=self._case_order()
        logger.emit("matrix_order",phase="setup",message="acceptance case order fixed",cases=[{"provider":p,"scenario":s} for p,s in case_order])
        case_attempts=[]
        for provider,scenario in case_order:
            retry_history=[]
            final_child=None
            for attempt in range(1, self.max_infrastructure_attempts + 1):
                case_dir=self.base/provider/scenario
                case_progress=self.log_dir/"cases"/provider/scenario
                if attempt>1:
                    archived_case=self.base/"attempts"/provider/scenario/f"attempt-{attempt-1}"
                    archived_logs=self.log_dir/"attempts"/provider/scenario/f"attempt-{attempt-1}"
                    archived_case.parent.mkdir(parents=True,exist_ok=True)
                    archived_logs.parent.mkdir(parents=True,exist_ok=True)
                    if case_dir.exists():
                        if archived_case.exists(): shutil.rmtree(archived_case)
                        shutil.move(str(case_dir),str(archived_case))
                    if case_progress.exists():
                        if archived_logs.exists(): shutil.rmtree(archived_logs)
                        shutil.move(str(case_progress),str(archived_logs))
                worker_cmd=[
                    sys.executable,
                    "-m",
                    "dynosai_flow.acceptance",
                    "--worker",
                    "--provider",provider,
                    "--scenario",scenario,
                    "--base",str(self.base),
                    "--interaction-mode",self.interaction_mode,
                    "--max-runtime",str(self.max_runtime),
                    "--idle-timeout",str(self.idle_timeout),
                    "--slow-progress",str(self.slow_progress),
                    "--model-profile",self.model_profile,
                ]
                worker_cmd += ["--log-dir",str(case_progress),"--run-id",self.run_id]
                worker_started=time.monotonic(); worker_failure=None
                logger.emit("case_worker_start",phase="case",provider=provider,scenario=scenario,message="starting isolated acceptance worker",path=str(case_progress),attempt=attempt)
                worker=None
                try:
                    worker=subprocess.Popen(worker_cmd)
                    logger.emit("case_worker_spawned",phase="case",provider=provider,scenario=scenario,message="acceptance worker spawned",pid=worker.pid,attempt=attempt)
                    next_heartbeat=time.monotonic()+self.heartbeat
                    hard_deadline=worker_started+self.max_runtime+120
                    while worker.poll() is None:
                        now=time.monotonic()
                        if now>=hard_deadline:
                            worker_failure={"type":"WorkerTimeout","message":f"acceptance worker exceeded {self.max_runtime+120}s"}
                            logger.emit("case_worker_timeout",phase="case",provider=provider,scenario=scenario,message=worker_failure["message"],duration_seconds=round(now-worker_started,1),remaining_seconds=0,attempt=attempt)
                            worker.terminate()
                            try: worker.wait(timeout=5)
                            except Exception: worker.kill()
                            break
                        if now>=next_heartbeat:
                            live_usage=latest_usage(case_progress) or {}
                            logger.heartbeat(provider=provider,scenario=scenario,phase="case",duration_seconds=round(now-worker_started,1),remaining_seconds=max(0,int(hard_deadline-now)),pid=worker.pid,input_tokens=live_usage.get("input_tokens"),cached_input_tokens=live_usage.get("cached_input_tokens"),output_tokens=live_usage.get("output_tokens"),reasoning_output_tokens=live_usage.get("reasoning_output_tokens"),usage_quality=live_usage.get("quality"),workflow_phase=live_usage.get("phase"),attempt=attempt)
                            next_heartbeat=now+self.heartbeat
                        time.sleep(0.5)
                    worker_exit=124 if worker_failure and worker_failure.get("type")=="WorkerTimeout" else int(worker.returncode or 0)
                except KeyboardInterrupt:
                    if worker is not None and worker.poll() is None:
                        worker.terminate()
                        try: worker.wait(timeout=5)
                        except Exception: worker.kill()
                    logger.emit("suite_interrupted",phase="case",provider=provider,scenario=scenario,message="acceptance interrupted by user; logs preserved",status="interrupted",path=str(self.log_dir),attempt=attempt)
                    raise
                except Exception as exc:
                    worker_exit=1
                    worker_failure={"type":type(exc).__name__,"message":str(exc)}
                    logger.emit("case_worker_exception",phase="case",provider=provider,scenario=scenario,message=f"{type(exc).__name__}: {exc}",status="failed",attempt=attempt)
                summary_path=case_dir/"logs"/"summary.json"
                if summary_path.exists():
                    try:
                        child=json.loads(summary_path.read_text(encoding="utf-8"))
                    except Exception as exc:
                        child={"provider":provider,"scenario":scenario,"mode":SCENARIOS[scenario]["mode"],"interaction_mode":self.interaction_mode,"status":"failed","failure":{"type":"InvalidWorkerSummary","message":str(exc)}}
                else:
                    child={"provider":provider,"scenario":scenario,"mode":SCENARIOS[scenario]["mode"],"interaction_mode":self.interaction_mode,"status":"failed","failure":worker_failure or {"type":"WorkerFailed","message":f"acceptance worker exited {worker_exit} without summary"}}
                child["worker"]={"exit_code":worker_exit,"duration_seconds":round(time.monotonic()-worker_started,3),"isolated_process":True,"attempt":attempt}
                if worker_exit!=0 and child.get("status")=="passed":
                    child["status"]="failed"
                    child["failure"]=worker_failure or {"type":"WorkerFailed","message":f"acceptance worker exited {worker_exit}"}
                attempt_record={
                    "provider":provider,
                    "scenario":scenario,
                    "attempt":attempt,
                    "status":child.get("status"),
                    "final_state":child.get("final_state"),
                    "quality_score":child.get("quality_score"),
                    "termination_reason":(child.get("provider_run") or {}).get("termination_reason"),
                    "worker_exit_code":worker_exit,
                    "provider_exit_code":(child.get("provider_run") or {}).get("exit_code"),
                    "mcp_failures":child.get("mcp_failures"),
                    "mcp_rejections":child.get("mcp_rejections"),
                    "mcp_rejection_recovery":child.get("mcp_rejection_recovery"),
                    "elicitations":len(child.get("elicitations") or []),
                    "scope_requests":len(child.get("scope_requests") or []),
                    "native_file_change_actions":int((child.get("context_optimization") or {}).get("native_file_change_actions") or 0),
                    "token_usage":child.get("token_usage"),
                    "cost_estimate":child.get("cost_estimate") or ((child.get("token_usage") or {}).get("cost_estimate")),
                    "duration_seconds":child["worker"]["duration_seconds"],
                }
                case_attempts.append(attempt_record)
                _write_json(summary_path,child)
                retryable=certification_infrastructure_retry_allowed(self.max_infrastructure_attempts,attempt,child,worker_failure)
                if retryable:
                    retry_history.append(attempt_record)
                    logger.emit("case_infrastructure_retry",phase="case",provider=provider,scenario=scenario,message="retrying clean case after safe pre-implementation infrastructure idle timeout",status="retrying",attempt=attempt,next_attempt=attempt+1,termination_reason=attempt_record.get("termination_reason"),path=str(case_progress))
                    continue
                final_child=child
                break
            child=final_child or child
            child["retry_history"]=retry_history
            child["infrastructure_retry_count"]=len(retry_history)
            child["recovered_after_infrastructure_retry"]=bool(retry_history and child.get("status")=="passed")
            summary_path=self.base/provider/scenario/"logs"/"summary.json"
            _write_json(summary_path,child)
            children.append(child)
            logger.emit("case_worker_complete",phase="case",provider=provider,scenario=scenario,message=f"acceptance worker {child.get('status')}",exit_code=(child.get("worker") or {}).get("exit_code"),duration_seconds=(child.get("worker") or {}).get("duration_seconds"),status=child.get("status"),path=str(summary_path),attempt=(child.get("worker") or {}).get("attempt"),infrastructure_retry_count=len(retry_history),recovered_after_infrastructure_retry=child.get("recovered_after_infrastructure_retry"))
        coverage={"cursor":any(c["provider"]=="cursor" for c in children),"codex":any(c["provider"]=="codex" for c in children),"greenfield":any(c["mode"]=="greenfield" for c in children),"brownfield":any(c["mode"]=="brownfield" for c in children)}
        gates={
            "all_children_passed":bool(children) and all(c.get("status")=="passed" for c in children),
            "all_final_done":bool(children) and all(c.get("final_state")=="done" for c in children),
            "all_oracles_passed":bool(children) and all((c.get("oracle") or {}).get("passed") for c in children),
            "all_single_session":bool(children) and all((c.get("gates") or {}).get("single_provider_session") for c in children),
            "all_sessions_completed":bool(children) and all((c.get("gates") or {}).get("provider_session_completed") for c in children),
            "all_same_workspace":bool(children) and all((c.get("gates") or {}).get("same_workspace") for c in children),
            "all_elicitations_observed":bool(children) and all((c.get("gates") or {}).get("elicitation_trace_present") for c in children),
            "all_expected_human_interactions_only":bool(children) and all((c.get("gates") or {}).get("expected_human_interactions_only") for c in children),
            "zero_scope_requests":bool(children) and all((c.get("gates") or {}).get("zero_scope_requests") for c in children),
            "all_validation_baselines_correct":bool(children) and all((c.get("gates") or {}).get("validation_baseline_correct") for c in children),
            "all_runtime_bins_unchanged":bool(children) and all((c.get("gates") or {}).get("runtime_bin_unchanged") for c in children),
            "all_provider_model_routes_verified":bool(children) and all((c.get("gates") or {}).get("provider_model_route_verified") for c in children),
            "all_token_usage_exact":bool(children) and all((c.get("gates") or {}).get("token_usage_exact") for c in children),
            "zero_mcp_failures":_strict_zero_metric(children,"mcp_failures"),
            "zero_mcp_rejections":_strict_zero_metric(children,"mcp_rejections"),
            "zero_mcp_normalizations":_strict_zero_metric(children,"mcp_normalizations"),
            "all_strict_managed_runtimes_verified":bool(children) and all((c.get("gates") or {}).get("strict_managed_runtime_verified") for c in children),
            "zero_provider_extension_artifacts":_strict_zero_metric(children,"provider_extension_artifacts"),
            "zero_external_skill_invocations":_strict_zero_metric(children,"external_skill_invocations"),
            "zero_external_plugin_invocations":_strict_zero_metric(children,"external_plugin_invocations"),
            "zero_external_mcp_invocations":_strict_zero_metric(children,"external_mcp_invocations"),
        }
        # Acceptance level is explicit so automated Cursor is never mislabeled as
        # a UI Elicitation certification.
        levels={c["provider"]:{"real_provider":bool((c.get("provider_run") or {}).get("real_provider")),"wire_elicitation":bool((c.get("provider_run") or {}).get("wire_elicitation")),"automated_responses":bool((c.get("provider_run") or {}).get("automated_responses")),"driver":(c.get("provider_run") or {}).get("driver")} for c in children}
        usage_summary=aggregate_usage([c.get("token_usage") for c in children if isinstance(c.get("token_usage"),dict)])
        actual_usage_summary=aggregate_usage([a.get("token_usage") for a in case_attempts if isinstance(a.get("token_usage"),dict)])
        usage_by_process=[{"provider":c.get("provider"),"scenario":c.get("scenario"),**(c.get("token_usage") or {})} for c in children if isinstance(c.get("token_usage"),dict)]
        usage_by_provider={p:aggregate_usage([u for u in usage_by_process if u.get("provider")==p]) for p in self.providers}
        usage_by_scenario={sc:aggregate_usage([u for u in usage_by_process if u.get("scenario")==sc]) for sc in self.scenarios}
        runtime_by_process=[{"provider":c.get("provider"),"scenario":c.get("scenario"),**(c.get("runtime_metrics") or {})} for c in children]
        optimization_by_process=[{"provider":c.get("provider"),"scenario":c.get("scenario"),**(c.get("context_optimization") or {})} for c in children]
        optimization_totals={
            "processes":optimization_by_process,
            "response_bytes_omitted":sum(int(x.get("response_bytes_omitted") or 0) for x in optimization_by_process),
            "compacted_response_count":sum(int(x.get("compacted_response_count") or 0) for x in optimization_by_process),
            "tool_surface_change_notifications":sum(int(x.get("tool_surface_change_notifications") or 0) for x in optimization_by_process),
            "context_checkpoint_files":sum(int(x.get("context_checkpoint_files") or 0) for x in optimization_by_process),
            "targeted_checkpoint_loads":sum(int(x.get("targeted_checkpoint_loads") or 0) for x in optimization_by_process),
            "checkpoint_bytes_omitted":sum(int(x.get("checkpoint_bytes_omitted") or 0) for x in optimization_by_process),
            "transport_text_bytes_omitted":sum(int(x.get("transport_text_bytes_omitted") or 0) for x in optimization_by_process),
            "structured_primary_results":sum(int(x.get("structured_primary_results") or 0) for x in optimization_by_process),
            "full_text_compatibility_results":sum(int(x.get("full_text_compatibility_results") or 0) for x in optimization_by_process),
            "execution_waves":sum(int(x.get("execution_waves") or 0) for x in optimization_by_process),
            "execution_wave_expected_round_trips_saved":sum(int(x.get("execution_wave_expected_round_trips_saved") or 0) for x in optimization_by_process),
            "execution_wave_auto_advances":sum(int(x.get("execution_wave_auto_advances") or 0) for x in optimization_by_process),
            "ready_implementation_collapses":sum(int(x.get("ready_implementation_collapses") or 0) for x in optimization_by_process),
            "provider_inference_chunks":sum(int(x.get("provider_inference_chunks") or 0) for x in optimization_by_process),
            "native_file_change_actions":sum(int(x.get("native_file_change_actions") or 0) for x in optimization_by_process),
            "model_control_decisions":sum(int(x.get("model_control_decisions") or 0) for x in optimization_by_process),
            "predictive_decisions_used":sum(int(x.get("predictive_decisions_used") or 0) for x in optimization_by_process),
            "predictive_capability_escalations":sum(int(x.get("predictive_capability_escalations") or 0) for x in optimization_by_process),
            "predictive_cost_downshifts":sum(int(x.get("predictive_cost_downshifts") or 0) for x in optimization_by_process),
            "model_route_recommendations":sum(int(x.get("model_route_recommendations") or 0) for x in optimization_by_process),
            "model_route_requests":sum(int(x.get("model_route_requests") or 0) for x in optimization_by_process),
            "model_route_applied":sum(int(x.get("model_route_applied") or 0) for x in optimization_by_process),
            "model_route_verified":sum(int(x.get("model_route_verified") or 0) for x in optimization_by_process),
            "model_escalations_applied":sum(int(x.get("model_escalations_applied") or 0) for x in optimization_by_process),
            "model_control_escalations":sum(int(x.get("model_escalations_applied") or 0) for x in optimization_by_process),
        }
        baseline_comparisons=[{"provider":c.get("provider"),"scenario":c.get("scenario"),**(c.get("baseline_comparison") or {})} for c in children if c.get("baseline_comparison")]
        cost_telemetry=aggregate_cost([(c.get("token_usage") or {}).get("cost_estimate") for c in children])
        actual_cost_telemetry=aggregate_cost([a.get("cost_estimate") for a in case_attempts if isinstance(a.get("cost_estimate"),dict)])
        retry_metrics={
            "case_attempts":case_attempts,
            "total_attempts":len(case_attempts),
            "infrastructure_retries":sum(1 for c in children if int(c.get("infrastructure_retry_count") or 0)>0),
            "recovered_cases":sum(1 for c in children if c.get("recovered_after_infrastructure_retry")),
            "unrecovered_retried_cases":sum(1 for c in children if int(c.get("infrastructure_retry_count") or 0)>0 and c.get("status")!="passed"),
            "policy":"one_retry_for_safe_preimplementation_idle_timeout_with_recovered_prior_rejections",
            "actual_mcp_rejections_including_retries":sum(int(a.get("mcp_rejections") or 0) for a in case_attempts),
            "actual_mcp_failures_including_retries":sum(int(a.get("mcp_failures") or 0) for a in case_attempts),
        }
        summary={"acceptance_version":9,"dynosai_version":__version__,"model_profile":self.model_profile,"scenario":self.scenario,"providers":list(self.providers),"matrix_order":[{"provider":p,"scenario":s} for p,s in case_order],"interaction_mode":self.interaction_mode,"started_at":started,"finished_at":utc_now(),"coverage":coverage,"gates":gates,"acceptance_levels":levels,"token_usage":{"totals":usage_summary,"actual_totals_including_retries":actual_usage_summary,"by_provider":usage_by_provider,"by_scenario":usage_by_scenario,"processes":usage_by_process},"retry_metrics":retry_metrics,"runtime_metrics":{"processes":runtime_by_process,"workflow_elapsed_seconds":round(sum(float(x.get("workflow_elapsed_seconds") or 0) for x in runtime_by_process),3),"dynosai_mcp_seconds":round(sum(float(x.get("dynosai_mcp_seconds") or 0) for x in runtime_by_process),3),"mcp_calls":sum(int(x.get("mcp_calls") or 0) for x in runtime_by_process),"reconnect_count":sum(int(x.get("reconnect_count") or 0) for x in runtime_by_process)},"baseline_comparisons":baseline_comparisons,"context_optimization":optimization_totals,"cost_telemetry":cost_telemetry,"actual_cost_telemetry_including_retries":actual_cost_telemetry,"status":"passed" if all(gates.values()) and all(coverage.get(x,False) for x in (["greenfield","brownfield"] if self.scenario=="green-brown" else [])) else "failed","children":children,"setup":setup,"telemetry":{"run_id":self.run_id,"log_dir":str(self.log_dir),"log_file":str(logger.paths.text),"events_file":str(logger.paths.events),"state_file":str(logger.paths.state)}}
        _write_json(self.base/"summary-final.json",summary)
        self.output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(self.output,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("suite/summary-final.json",json.dumps(summary,ensure_ascii=False,indent=2,default=str))
            z.writestr("README.txt",f"DynosAI {__version__} real-provider acceptance bundle. AUTO mode uses real Cursor/Codex coding providers. Codex Elicitations are answered through the official app-server request/response channel; Cursor headless uses the DynosAI acceptance-only test responder because Cursor does not document a headless form-response callback. Run --interaction-mode interactive to certify provider UI rendering.\n")
            for path in sorted(self.base.rglob("*")):
                if path.is_file() and path.resolve()!=self.output.resolve():
                    try:z.write(path,path.relative_to(self.base))
                    except Exception:pass
            for path in sorted(self.log_dir.rglob("*")):
                if path.is_file():
                    try:z.write(path,Path("telemetry")/path.relative_to(self.log_dir))
                    except Exception:pass
        summary["bundle"]=str(self.output)
        logger.emit("suite_token_usage",phase="usage",message="acceptance token usage aggregated",input_tokens=usage_summary.get("input_tokens"),cached_input_tokens=usage_summary.get("cached_input_tokens"),output_tokens=usage_summary.get("output_tokens"),reasoning_output_tokens=usage_summary.get("reasoning_output_tokens"),context_read_tokens=usage_summary.get("context_read_tokens"),processed_tokens=usage_summary.get("processed_tokens"),provider_total_tokens=usage_summary.get("provider_total_tokens"),exact_processes=usage_summary.get("exact_processes"),estimated_processes=usage_summary.get("estimated_processes"))
        logger.emit("suite_cost",phase="usage",message="acceptance public list-price estimate aggregated",estimated_list_price_usd=cost_telemetry.get("estimated_list_price_usd"),catalog_version=cost_telemetry.get("catalog_version"))
        logger.emit("suite_complete",phase="complete",message=f"acceptance suite {summary.get('status')}",status=summary.get("status"),path=str(self.output))
        _write_json(self.log_dir/"summary-final.json",summary)
        return summary


def _acceptance_worker_main(argv:list[str]|None=None)->int:
    import argparse
    parser=argparse.ArgumentParser(description="DynosAI real-provider acceptance worker")
    parser.add_argument("--worker",action="store_true",help=argparse.SUPPRESS)
    parser.add_argument("--provider",choices=ACCEPTANCE_PROVIDERS,required=True)
    parser.add_argument("--scenario",choices=ACCEPTANCE_SCENARIOS,required=True)
    parser.add_argument("--base",required=True)
    parser.add_argument("--interaction-mode",choices=("auto","interactive"),default="auto")
    parser.add_argument("--timeout",type=int,default=None,help=argparse.SUPPRESS)
    parser.add_argument("--max-runtime",type=int,default=1800)
    parser.add_argument("--idle-timeout",type=int,default=180)
    parser.add_argument("--slow-progress",type=int,default=60)
    parser.add_argument("--model-profile",choices=tuple(ACCEPTANCE_MODEL_PROFILES),default="economy")
    parser.add_argument("--log-dir")
    parser.add_argument("--run-id")
    args=parser.parse_args(argv)
    result=RealProviderAcceptanceCase(
        args.provider,
        args.scenario,
        Path(args.base).expanduser().resolve(),
        interaction_mode=args.interaction_mode,
        timeout=args.timeout,
        max_runtime=args.max_runtime,
        idle_timeout=args.idle_timeout,
        slow_progress=args.slow_progress,
        log_dir=args.log_dir,
        run_id=args.run_id,
        console_progress=True,
        model_profile=args.model_profile,
    ).run()
    return 0 if result.get("status")=="passed" else 1


def main(argv:list[str]|None=None)->int:
    # This module-level entrypoint is intentionally private to the acceptance
    # parent. End users invoke `dynosai debug acceptance`; workers are a process
    # isolation mechanism, not a second public workflow.
    return _acceptance_worker_main(argv)


if __name__=="__main__":
    raise SystemExit(main())
