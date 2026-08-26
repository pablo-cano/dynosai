# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Command-line interface for setup, workflow operations, diagnostics, and administration."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .benchmark import RetrievalBenchmark
from .engine import DynosAI
from .version import __version__
from .debug import DebugE2ERunner, DebugE2ESuiteRunner
from .providers import MachineProviderSetup
from .application import DynosAIApplication
from .provider_debug import ProviderNativeMatrixRunner
from .acceptance import RealProviderAcceptanceSuite
from .acceptance_logging import acceptance_status, acceptance_usage, bundle_acceptance_logs
from .model_routing import ProviderModelRouting, activity_for_state, cursor_cli_selector, normalize_provider
from .agent_config import AgentConfiguration, PROVIDERS as AGENT_CONFIG_PROVIDERS, MODES as AGENT_CONFIG_MODES
from .managed_runtime import ManagedProviderRuntime
from .model_control import ModelControlPlane
from .model_benchmark import ModelBenchmarkMatrix
from .context_control import ContextCheckpointStore
from .predictive_validation import PredictiveRouterValidator
from .app_server import serve_studio


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(prog="dynosai",description=f"DynosAI {__version__}")
    root.add_argument("--version", action="version", version=f"DynosAI {__version__}")
    root.add_argument("--project",default="."); root.add_argument("--json",action="store_true")
    sub=root.add_subparsers(dest="command",required=True)
    setup=sub.add_parser("setup",help="One-time machine setup for provider-native DynosAI")
    setup.add_argument("--provider",choices=["cursor","codex","claude","all"],default="all")
    setup.add_argument("--skip-model",action="store_true",help="Configure providers without downloading/probing the semantic model")
    pr=sub.add_parser("project",help="Headless project detect/initialize adapter")
    pr.add_argument("action",choices=["detect","initialize"]); pr.add_argument("path",nargs="?",default="."); pr.add_argument("--name"); pr.add_argument("--agent",choices=["cursor","codex","claude"])
    git_group=pr.add_mutually_exclusive_group()
    git_group.add_argument("--init-git",action="store_true",help="Explicitly allow Git initialization for existing code without Git")
    git_group.add_argument("--no-git-init",action="store_true",help=argparse.SUPPRESS)
    stat=sub.add_parser("status",help="Provider-neutral active work status"); stat.add_argument("work_id",nargs="?")
    resume=sub.add_parser("resume",help="Resume a persistent provider feature session"); resume.add_argument("--provider",choices=["cursor","codex","claude"],required=True); resume.add_argument("work_id",nargs="?")
    studio=sub.add_parser("studio",help="Launch the local DynosAI graphical control plane")
    studio.add_argument("--host",choices=["127.0.0.1","localhost"],default="127.0.0.1")
    studio.add_argument("--port",type=int,default=8765)
    studio.add_argument("--no-browser",action="store_true",help="Start Studio without opening a browser")
    app_server=sub.add_parser("app-server",help="Run the local DynosAI App Server without opening Studio")
    app_server.add_argument("--host",choices=["127.0.0.1","localhost"],default="127.0.0.1")
    app_server.add_argument("--port",type=int,default=8765)
    init=sub.add_parser("init"); init.add_argument("path",nargs="?",default="."); init.add_argument("--name"); init.add_argument("--language",default="python"); init.add_argument("--test-command",default="pytest"); init.add_argument("--agent",choices=["claude","codex","cursor","all"]); init.add_argument("--install-model",action="store_true")
    adopt=sub.add_parser("adopt"); adopt.add_argument("path",nargs="?",default="."); adopt.add_argument("--name"); adopt.add_argument("--agent",choices=["claude","codex","cursor","all"]); adopt.add_argument("--install-model",action="store_true")
    c=sub.add_parser("connect"); c.add_argument("agent",choices=["claude","codex","cursor","all"]); dc=sub.add_parser("disconnect"); dc.add_argument("agent",choices=["claude","codex","cursor","all"])
    o=sub.add_parser("open"); o.add_argument("--agent",choices=["claude","codex","cursor"],required=True); o.add_argument("--task"); o.add_argument("--dry-run",action="store_true")
    d=sub.add_parser("doctor"); d.add_argument("--deep",action="store_true")
    sub.add_parser("env")
    dg=sub.add_parser("diagnose"); dg.add_argument("--bundle",action="store_true"); dg.add_argument("--output")
    m=sub.add_parser("model"); m.add_argument("action",choices=["install","status","remove"]); m.add_argument("--probe",action="store_true")
    pm=sub.add_parser("provider-model",help="Configure provider model routing by activity")
    pm.add_argument("action",choices=["show","set","reset"])
    pm.add_argument("--provider",choices=["cursor","codex","openai"])
    pm.add_argument("--model")
    pm.add_argument("--effort")
    pm.add_argument("--activity",help="discovery, spec, plan, implementation, code_review, validation or merge")
    pm.add_argument("--scope",choices=["machine","project"],default="machine")
    mc=sub.add_parser("model-control",help="Inspect adaptive model routing, budgets and escalation state")
    mc.add_argument("action",choices=["show","outcome","validate-history"],default="show")
    mc.add_argument("--provider",choices=["cursor","codex"])
    mc.add_argument("--work-id")
    mc.add_argument("--policy",choices=["adaptive","pinned","economy","standard","strong"],default=None)
    mc.add_argument("--success",action="store_true")
    mc.add_argument("--failure-kind",choices=["syntax","test","spec","scope","provider","tool","unknown"])
    mc.add_argument("files",nargs="*",help="Acceptance ZIPs for validate-history (offline; launches no models)")
    mc.add_argument("--output",dest="validation_output",help="Optional JSON report path for validate-history")
    ac=sub.add_parser("agent-config",help="Provider-neutral rules, skills, tools/MCP, agents and hooks")
    ac.add_argument("action",choices=["init","show","compile","clean","doctor"])
    ac.add_argument("--provider",choices=["cursor","codex","claude","all"],default="all")
    ac.add_argument("--activity",default="discovery")
    ac.add_argument("--mode",choices=list(AGENT_CONFIG_MODES),default="recommended")
    ac.add_argument("--force",action="store_true")
    ac.add_argument("--dry-run",action="store_true")
    ix=sub.add_parser("index"); ix.add_argument("--rebuild",action="store_true")
    se=sub.add_parser("search"); se.add_argument("query"); se.add_argument("--limit",type=int,default=10)
    st=sub.add_parser("start"); st.add_argument("description"); st.add_argument("--type",default="auto",dest="work_type"); st.add_argument("--quick",action="store_true"); st.add_argument("--dry-run",action="store_true")
    co=sub.add_parser("continue"); co.add_argument("work_id",nargs="?")
    rv=sub.add_parser("review"); rv.add_argument("work_id",nargs="?"); rv.add_argument("--approve",choices=["spec","plan","code","merge"]); rv.add_argument("--changes"); rv.add_argument("--approve-scope",dest="approve_scope")
    bo=sub.add_parser("board"); bo.add_argument("--state"); bo.add_argument("--type",dest="work_type")
    ask=sub.add_parser("ask"); ask.add_argument("query"); ask.add_argument("--limit",type=int,default=10); ask.add_argument("--save")
    cx=sub.add_parser("context"); cx.add_argument("work_id",nargs="?"); cx.add_argument("--task"); cx.add_argument("--max-tokens",type=int,default=3000)
    sub.add_parser("sync")
    rb=sub.add_parser("rollback"); rb.add_argument("work_id"); rb.add_argument("--commit")
    sub.add_parser("stats"); sub.add_parser("scorecard")
    usage=sub.add_parser("usage",help="Show or watch live provider token consumption from persisted DynosAI telemetry")
    usage.add_argument("action",choices=["show","watch"],nargs="?",default="show")
    usage.add_argument("--log-dir",help="Acceptance telemetry directory; default is latest")
    usage.add_argument("--interval",type=float,default=2.0,help="Refresh interval for watch mode")
    sch=sub.add_parser("schedule"); sch.add_argument("work_id",nargs="?")
    bk=sub.add_parser("backup"); bk.add_argument("--reason",default="manual")
    rs=sub.add_parser("restore"); rs.add_argument("snapshot",nargs="?")
    bm=sub.add_parser("benchmark"); bm.add_argument("file"); bm.add_argument("--k",type=int,default=5)
    mb=sub.add_parser("model-benchmark",help="Rank existing acceptance bundles by quality, cost, time and tokens without running new models")
    mb.add_argument("files",nargs="+",help="Acceptance ZIP or summary-final.json files")
    # Low-level commands exist for automation/MCP, not normal day-to-day UX.
    ss=sub.add_parser("submit-spec",help=argparse.SUPPRESS); ss.add_argument("work_id"); ss.add_argument("json_file")
    sp=sub.add_parser("submit-plan",help=argparse.SUPPRESS); sp.add_argument("work_id"); sp.add_argument("json_file")
    ar=sub.add_parser("agent-result",help=argparse.SUPPRESS); ar.add_argument("work_id"); ar.add_argument("--summary",required=True); ar.add_argument("--files",nargs="*",default=[]); ar.add_argument("--task",action="append",default=[])
    de=sub.add_parser("demo"); de.add_argument("path",nargs="?",default="./dynosai-demo")
    dbg=sub.add_parser("debug",help="Run isolated diagnostic scenarios")
    dbg_sub=dbg.add_subparsers(dest="debug_command",required=True)
    e2e=dbg_sub.add_parser("e2e",help="Run a full isolated E2E scenario with a real coding agent")
    e2e.add_argument("--agent",choices=["cursor","codex","all"],default="cursor")
    e2e.add_argument("--scenario",choices=["fibonacci","supportdesk-premium-sla","orderflow-contract-discounts","green-brown-gate","provider-green-brown-matrix"],default="fibonacci")
    e2e.add_argument("--output")
    e2e.add_argument("--workspace")
    e2e.add_argument("--timeout",type=int,default=600)
    e2e.add_argument("--cleanup",action="store_true",help="Remove isolated workspace after creating the bundle")
    acc=dbg_sub.add_parser("acceptance",help="Run real-provider Cursor/Codex acceptance in the current machine")
    acc.add_argument("--providers",default="cursor,codex",help="Comma-separated: cursor,codex")
    acc.add_argument("--scenario",choices=["green-brown","fibonacci","orderflow-contract-discounts"],default="green-brown")
    acc.add_argument("--interaction-mode",choices=["auto","interactive"],default="auto",help="auto answers acceptance gates; interactive certifies the provider UI")
    acc.add_argument("--output")
    acc.add_argument("--workspace")
    acc.add_argument("--timeout",type=int,default=None,help="Legacy alias for --max-runtime")
    acc.add_argument("--max-runtime",type=int,default=1800,help="Hard ceiling per provider/scenario child")
    acc.add_argument("--idle-timeout",type=int,default=180,help="Abort only after this many seconds without provider/MCP progress")
    acc.add_argument("--slow-progress",type=int,default=60,help="Emit a warning after this many idle seconds without failing")
    acc.add_argument("--log-dir",help="Persistent telemetry directory. Default: ~/.dynosai/logs/acceptance/<run-id>")
    acc.add_argument("--heartbeat",type=int,default=10,help="Progress heartbeat interval in seconds")
    acc.add_argument("--model-profile",choices=["economy","baseline"],default="economy",help="Acceptance model budget profile; economy uses GPT-5.6 Luna / medium on both providers")
    acc.add_argument("--configure-machine",action="store_true",help="Also refresh user-level provider MCP configuration (not required for managed acceptance)")
    acc.add_argument("--no-configure",action="store_true",help=argparse.SUPPRESS)
    acc.add_argument("--cleanup",action="store_true")
    acc_status=dbg_sub.add_parser("acceptance-status",help="Show the latest persisted acceptance progress")
    acc_status.add_argument("--log-dir",help="Acceptance telemetry directory; default is latest")
    acc_logs=dbg_sub.add_parser("acceptance-logs",help="Create a portable ZIP from persisted acceptance logs, even while a run is active")
    acc_logs.add_argument("--log-dir",help="Acceptance telemetry directory; default is latest")
    acc_logs.add_argument("--output",help="Output ZIP path")
    return root


def _display_path(path: Path, root: Path) -> str:
    try: return str(path.resolve().relative_to(root.resolve())).replace("\\","/")
    except Exception: return str(path.resolve())

def _cursor_health() -> dict[str,Any]:
    exe=shutil.which("cursor-agent")
    if not exe: return {"installed":False,"authenticated":False,"mcp":False,"tools":0,"ready":False}
    out={"installed":True,"executable":exe}
    try:
        status=subprocess.run([exe,"status"],text=True,capture_output=True,timeout=8)
        out["authenticated"]=status.returncode==0 and "logged in" in ((status.stdout+status.stderr).lower())
        out["status"]=(status.stdout or status.stderr).strip()[:500]
    except Exception as exc: out["authenticated"]=False; out["status_error"]=str(exc)
    try:
        en=subprocess.run([exe,"mcp","enable","dynosai"],text=True,capture_output=True,timeout=10)
        out["approval"]=(en.stdout or en.stderr).strip()[:500]; out["approval_ok"]=en.returncode==0
    except Exception as exc: out["approval_ok"]=False; out["approval_error"]=str(exc)
    try:
        ls=subprocess.run([exe,"mcp","list"],text=True,capture_output=True,timeout=10)
        txt=(ls.stdout or ls.stderr); out["mcp"]=ls.returncode==0 and "dynosai" in txt.lower() and "ready" in txt.lower(); out["mcp_list"]=txt.strip()[:1000]
        tools=subprocess.run([exe,"mcp","list-tools","dynosai"],text=True,capture_output=True,timeout=10)
        t=(tools.stdout or tools.stderr); import re as _re
        m=_re.search(r"Tools for dynosai \((\d+)\)",t); out["tools"]=int(m.group(1)) if m else t.count("dynosai_")
        out["tools_ok"]=tools.returncode==0 and out["tools"]>=20
    except Exception as exc: out["mcp"]=False; out["tools"]=0; out["mcp_error"]=str(exc)
    out["ready"]=bool(out.get("authenticated") and out.get("mcp") and out.get("tools_ok"))
    return out



def _initialized_project(engine: DynosAI) -> bool:
    """Return True only for an initialized DynosAI project backed by Git.

    A mere `.dynosai/knowledge.db` file is not sufficient: older/read-only code
    paths could leave an empty SQLite file behind. Config-only workspaces are
    valid and must not fail because there is no Git repository to checkpoint.
    """
    if not engine.db.path.exists():
        return False
    try:
        marker = engine.db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='meta'")
    except Exception:
        return False
    if not marker:
        return False
    try:
        return engine.git.is_repo()
    except Exception:
        return False


def main(argv:list[str]|None=None)->int:
    args=parser().parse_args(argv); project=Path(getattr(args,"path",None) or args.project).resolve(); engine=DynosAI(project)
    try:
        cmd=args.command
        if cmd in {"studio","app-server"}:
            serve_studio(project, host=args.host, port=args.port, open_browser=(cmd=="studio" and not getattr(args,"no_browser",False)))
            return 0
        if cmd=="setup":
            providers=["cursor","codex","claude"] if args.provider=="all" else [args.provider]
            result=MachineProviderSetup().setup(providers,install_model=not args.skip_model)
            output(result,args.json); return 0
        if cmd=="project":
            app=DynosAIApplication(Path(args.path).resolve())
            result=app.detect_project() if args.action=="detect" else app.initialize_project(name=args.name,agent=args.agent,allow_git_init=bool(args.init_git and not args.no_git_init))
            output(result,args.json); return 0
        if cmd=="status":
            app=DynosAIApplication(project); app.engine.db.initialize(); result=app.status(args.work_id); output(result,args.json); return 0
        if cmd=="resume":
            app=DynosAIApplication(project); app.engine.db.initialize(); result=app.resume(args.provider,args.work_id); output(result,args.json); return 0
        # A managed coding agent must use authenticated MCP for process mutations.
        # Otherwise it could invoke the privileged DynosAI controller through shell
        # and bypass work/run/session authorization.
        if os.environ.get("DYNOSAI_MANAGED_AGENT") == "1":
            mutating={"init","adopt","connect","disconnect","open","model","index","start","continue","review","sync","rollback","backup","restore","submit-spec","submit-plan","agent-result","demo"}
            if cmd in mutating or (cmd=="provider-model" and args.action in {"set","reset"}) or (cmd=="agent-config" and args.action in {"init","compile","clean"}):
                raise RuntimeError(f"managed agents must use DynosAI MCP for `{cmd}`; return to the host terminal for human/admin commands")
        if cmd=="init":
            result=engine.initialize(args.name,args.language,args.test_command,args.agent); result["model_install"]=engine.install_model() if args.install_model else None
            if args.agent in {"cursor","all"}: result["cursor_health"]=_cursor_health()
        elif cmd=="adopt":
            result=engine.adopt(args.name,args.agent); result["model_install"]=engine.install_model() if args.install_model else None
            if args.agent in {"cursor","all"}: result["cursor_health"]=_cursor_health()
        elif cmd=="connect":
            created=engine.agents.connect(args.agent); engine.db.set_meta("default_agent",args.agent if args.agent!="all" else ""); engine.git.commit_main_changes("chore: configure DynosAI agents")
            result={"agent":args.agent,"created":[_display_path(x,engine.root) for x in created]}
            if args.agent in {"cursor","all"}:
                result["cursor_health"]=_cursor_health()
                if args.agent=="cursor" and not result["cursor_health"].get("ready"):
                    raise RuntimeError("Cursor integration is configured but not fully ready; run `dynosai doctor --deep` for details")
        elif cmd=="disconnect":
            touched=engine.agents.disconnect(args.agent); engine.git.commit_main_changes("chore: disconnect DynosAI agents"); result={"agent":args.agent,"disconnected":True,"touched":[_display_path(x,engine.root) for x in touched if x.exists()]}
        elif cmd=="open": result=open_agent(engine,args.agent,args.dry_run,args.task)
        elif cmd=="doctor":
            result=((engine.deep_doctor() if args.deep else engine.doctor()) if _initialized_project(engine) else MachineProviderSetup().doctor(deep=args.deep))
        elif cmd=="env": result=engine.environment_report()
        elif cmd=="diagnose": result=engine.diagnostic_bundle(args.output) if args.bundle else {"environment":engine.environment_report(),"doctor":engine.doctor(),"runtime":engine.runtime.status()}
        elif cmd=="model": result=engine.install_model() if args.action=="install" else engine.remove_model() if args.action=="remove" else engine.model_status(args.probe)
        elif cmd=="provider-model":
            routing=ProviderModelRouting(project)
            if args.action=="show":
                result=routing.show(args.provider)
            elif args.action=="set":
                if not args.provider or not args.model: raise ValueError("provider-model set requires --provider and --model")
                result=routing.set(args.provider,args.model,effort=args.effort,activity=args.activity,scope=args.scope)
                if args.scope=="project" and _initialized_project(engine): engine.git.commit_main_changes("chore: configure DynosAI provider model routing")
            else:
                if not args.provider: raise ValueError("provider-model reset requires --provider")
                result=routing.reset(args.provider,activity=args.activity,scope=args.scope)
                if args.scope=="project" and _initialized_project(engine): engine.git.commit_main_changes("chore: reset DynosAI provider model routing")
        elif cmd=="model-control":
            control=ModelControlPlane(project,policy=args.policy)
            if args.action=="show":
                result=control.show(args.provider,args.work_id)
            elif args.action=="validate-history":
                if not args.files: raise ValueError("model-control validate-history requires one or more acceptance ZIPs")
                result=PredictiveRouterValidator.validate(args.files)
                if args.validation_output:
                    target=Path(args.validation_output).expanduser().resolve(); target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
            else:
                if not args.provider or not args.work_id: raise ValueError("model-control outcome requires --provider and --work-id")
                result=control.record_outcome(args.provider,args.work_id,success=bool(args.success),failure_kind=args.failure_kind)
        elif cmd=="agent-config":
            config=AgentConfiguration(project)
            providers=list(AGENT_CONFIG_PROVIDERS) if args.provider=="all" else [args.provider]
            if args.action=="init":
                result=config.init(mode=args.mode,force=args.force)
            elif args.action=="show":
                result={"profile":config.load(),"effective":{p:config.resolve(provider=p,activity=args.activity) for p in providers}}
            elif args.action=="compile":
                if not config.profile_path.exists(): config.init(mode=args.mode)
                result=config.compile(providers=providers,activity=args.activity,dry_run=args.dry_run)
                if not args.dry_run and _initialized_project(engine): engine.git.commit_main_changes("chore: compile DynosAI agent configuration")
            elif args.action=="clean":
                result=config.clean(providers=providers)
                if _initialized_project(engine): engine.git.commit_main_changes("chore: clean DynosAI agent configuration")
            else:
                result=config.doctor()
        elif cmd=="index": result=engine.semantic_index(args.rebuild)
        elif cmd=="search": result=engine.ask(args.query,args.limit)
        elif cmd=="start": result=engine.start(args.description,args.work_type,args.quick,args.dry_run)
        elif cmd=="continue": result=engine.continue_work(args.work_id)
        elif cmd=="review":
            result=engine.approve_scope_request(args.approve_scope) if args.approve_scope else engine.review(args.work_id,args.approve,args.changes)
        elif cmd=="board": result=engine.board(args.state,args.work_type)
        elif cmd=="ask":
            result=engine.ask(args.query,args.limit)
            if args.save: engine.db.execute("INSERT OR REPLACE INTO saved_searches(name,query,created_at) VALUES(?,?,datetime('now'))",(args.save,args.query)); result["saved_as"]=args.save
        elif cmd=="context": result=engine.context_preview(args.work_id,args.max_tokens,args.task)
        elif cmd=="sync": result=engine.sync()
        elif cmd=="rollback": result=engine.rollback(args.work_id,args.commit)
        elif cmd=="stats": result=engine.stats()
        elif cmd=="scorecard": result=engine.scorecard()
        elif cmd=="usage":
            if args.action=="show":
                result=acceptance_usage(args.log_dir)
            else:
                interval=max(0.5,float(args.interval))
                try:
                    while True:
                        current=acceptance_usage(args.log_dir)
                        if args.json:
                            print(json.dumps(current,ensure_ascii=False,default=str),flush=True)
                        else:
                            active=current.get("active_case") or {}; u=active.get("token_usage") or {}; totals=current.get("totals") or {}; rt=active.get("runtime_metrics") or {}
                            target="/".join(str(x) for x in (active.get("provider"),active.get("scenario")) if x) or "-"
                            quality=str(u.get("quality") or "-").lower(); phase=u.get("phase") or active.get("phase") or "-"
                            if quality=="exact":
                                reasoning=u.get("reasoning_output_tokens")
                                reasoning_text="n/a" if reasoning is None else f"{int(reasoning):,}"
                                provider_total=u.get("provider_total_tokens")
                                provider_total_text="n/a" if provider_total is None else f"{int(provider_total):,}"
                                print(
                                    f"{target} phase={phase} FINAL/EXACT | "
                                    f"input={int(u.get('input_tokens') or 0):,} cached={int(u.get('cached_input_tokens') or 0):,} "
                                    f"generated={int(u.get('generated_tokens') or u.get('output_tokens') or 0):,} reasoning={reasoning_text} "
                                    f"context_read={int(u.get('context_read_tokens') or 0):,} processed={int(u.get('processed_tokens') or 0):,} provider_total={provider_total_text}",
                                    flush=True,
                                )
                            else:
                                live=u.get("live_estimate") or {}; calibrated=u.get("calibrated_live_estimate") or {}
                                calibration_note=(f" calibrated_context~={int(calibrated.get('context_read_tokens') or 0):,} calibrated_generated~={int(calibrated.get('generated_tokens') or 0):,}" if calibrated.get("quality")=="calibrated_estimate" else "")
                                print(
                                    f"{target} phase={phase} LIVE/ESTIMATED | "
                                    f"context~={int(live.get('context_read_tokens') or live.get('observed_context_tokens_estimate') or 0):,} "
                                    f"generated~={int(live.get('generated_tokens') or live.get('output_tokens') or 0):,} "
                                    f"thinking_observed~={int(live.get('observed_thinking_tokens_estimate') or 0):,} "
                                    f"processed~={int(live.get('processed_tokens') or u.get('processed_tokens') or 0):,}{calibration_note}",
                                    flush=True,
                                )
                            print(
                                f"runtime elapsed={float(rt.get('workflow_elapsed_seconds') or u.get('elapsed_seconds') or 0):.1f}s "
                                f"mcp={float(rt.get('dynosai_mcp_seconds') or 0):.1f}s/{int(rt.get('mcp_calls') or 0)}calls "
                                f"reconnects={int(rt.get('reconnect_count') or 0)} | "
                                f"suite_context={int(totals.get('context_read_tokens') or 0):,} suite_generated={int(totals.get('generated_tokens') or totals.get('output_tokens') or 0):,} "
                                f"list_cost~=${float((u.get('cost_estimate') or {}).get('estimated_list_price_usd') or 0):.4f}",
                                flush=True,
                            )
                        time.sleep(interval)
                except KeyboardInterrupt:
                    return 0
        elif cmd=="schedule": result=engine.schedule(args.work_id)
        elif cmd=="backup": result=engine.backup(args.reason)
        elif cmd=="restore": result=engine.restore(args.snapshot)
        elif cmd=="benchmark": result=RetrievalBenchmark(engine.retrieval).run(RetrievalBenchmark.load(args.file),args.k)
        elif cmd=="model-benchmark": result=ModelBenchmarkMatrix.rank(args.files)
        elif cmd=="submit-spec": result=engine.submit_spec(args.work_id,json.loads(Path(args.json_file).read_text(encoding="utf-8")),"cli-automation")
        elif cmd=="submit-plan": result=engine.submit_plan(args.work_id,json.loads(Path(args.json_file).read_text(encoding="utf-8")),"cli-automation")
        elif cmd=="agent-result": result=engine.register_result(args.work_id,args.summary,args.files,[],args.task or None)
        elif cmd=="demo": result=run_demo(project)
        elif cmd=="debug":
            if args.debug_command=="acceptance":
                providers=[x.strip().lower() for x in args.providers.split(",") if x.strip()]
                if not providers: raise ValueError("--providers must include cursor and/or codex")
                result=RealProviderAcceptanceSuite(providers=providers,scenario=args.scenario,output=args.output,workspace=args.workspace,interaction_mode=args.interaction_mode,timeout=args.timeout,max_runtime=args.max_runtime,idle_timeout=args.idle_timeout,slow_progress=args.slow_progress,configure=bool(args.configure_machine and not args.no_configure),keep=not args.cleanup,log_dir=args.log_dir,heartbeat=args.heartbeat,model_profile=args.model_profile).run()
            elif args.debug_command=="acceptance-status":
                result=acceptance_status(args.log_dir)
            elif args.debug_command=="acceptance-logs":
                result=bundle_acceptance_logs(args.log_dir,args.output)
            elif args.debug_command=="e2e":
                if args.scenario=="provider-green-brown-matrix":
                    result=ProviderNativeMatrixRunner(output=args.output,workspace=args.workspace,keep=not args.cleanup).run()
                else:
                    if args.agent=="all": raise ValueError("--agent all is only valid with provider-green-brown-matrix")
                    if args.agent=="codex": raise ValueError("Use `dynosai debug acceptance --providers codex` for real Codex acceptance. `codex exec` auto-cancels server Elicitations; DynosAI uses Codex app-server instead.")
                    runner_cls = DebugE2ESuiteRunner if args.scenario == "green-brown-gate" else DebugE2ERunner
                    result=runner_cls(agent=args.agent,scenario=args.scenario,output=args.output,workspace=args.workspace,timeout=args.timeout,keep=not args.cleanup).run()
            else: raise ValueError("Unknown debug command")
        else: return 2
        output(result,args.json); return 0
    except Exception as exc:
        if args.json: print(json.dumps({"error":type(exc).__name__,"message":str(exc)},ensure_ascii=False,indent=2))
        else: print(f"ERROR: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 1


def open_agent(engine:DynosAI,agent:str,dry_run:bool,task_id:str|None=None)->dict[str,Any]:
    engine._ensure_ready(); commands={"claude":"claude","codex":"codex","cursor":"cursor-agent"}; executable=shutil.which(commands[agent])
    if os.environ.get("DYNOSAI_MANAGED_AGENT")=="1":
        raise RuntimeError("dynosai open cannot be called from inside a managed agent session; return to the host terminal")
    # Dry-run is observational only. A real launch refreshes portable integration before creating the session.
    if not dry_run:
        engine.agents.connect(agent); engine.db.set_meta("default_agent",agent); engine.git.commit_main_changes("chore: configure DynosAI agent")
    implementation_prepare=None
    try:
        work=engine.get_work()
        # `ready` means all human approvals required before implementation already
        # exist. Prepare Git/run/worktree on the trusted host controller *before*
        # launching the coding agent so its cwd and MCP session are correct from
        # the first instruction. A dry-run remains observational.
        if work.get("state")=="ready" and not dry_run:
            implementation_prepare=engine.continue_work(work["id"])
            work=engine.get_work(work["id"])
        active=work.get("state") in {"implementing","code_review","validating","ready_to_merge"} and bool(work.get("worktree"))
    except Exception:
        work=None; active=False
    run=None; cwd=engine.root; task_prepare=None
    if active and work:
        if task_id:
            if dry_run:
                task=engine.db.one("SELECT * FROM tasks WHERE id=? AND work_id=?",(task_id,work["id"]))
                if not task: raise KeyError(task_id)
                cwd=Path(work["worktree"]); task_prepare={"dry_run":True,"task_id":task_id,"message":"Real launch will create an isolated task worktree."}
            else:
                task_prepare=engine.prepare_task_run(work["id"],task_id,agent); cwd=Path(task_prepare["worktree"]); run=engine.db.one("SELECT * FROM runs WHERE id=?",(task_prepare["run_id"],))
        else:
            cwd=Path(work["worktree"]); run=engine.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(work["id"],))
    if active and not (task_id and dry_run):
        write_scope=[]
        plan=engine.artifacts.artifact(work["id"],"plan") if work else None
        entries=((plan or {}).get("metadata") or {}).get("files",[])
        allowed_by_plan={str(x.get("path","")).replace("\\","/") for x in entries if x.get("path") and str(x.get("action","")).lower() in {"modify","create","delete","test"}}
        if run:
            try:
                import json as _json
                task_ids=_json.loads(run.get("task_ids") or "[]")
            except Exception:
                task_ids=[]
            if task_ids:
                task_files=set()
                for tid in task_ids:
                    row=engine.db.one("SELECT files FROM tasks WHERE id=? AND work_id=?",(tid,work["id"]))
                    if row:
                        try: task_files.update(_json.loads(row.get("files") or "[]"))
                        except Exception: pass
                allowed_by_plan &= task_files
        write_scope=sorted(allowed_by_plan)
        generated=engine.agents.ensure_worktree_protocol(cwd,agent,write_scope=write_scope)
        rels=[str(x.relative_to(cwd)).replace("\\","/") for x in generated]
        management_commit=engine.git.commit_management_worktree(cwd,rels,f"chore: attach DynosAI {agent} protocol")
        if run: engine.db.execute("UPDATE runs SET base_commit=? WHERE id=?",(management_commit,run["id"])); run={**run,"base_commit":management_commit}
    prompt="Proyecto gobernado por DynosAI. Consulta dynosai_get_next_action antes de explorar; usa MCP para workflow, Git y validaciones; respeta scope; no uses shell directo ni escribas Git."
    if task_id: prompt+=f" Trabaja únicamente en la tarea {task_id}."
    if work:
        checkpoint_capsule=ContextCheckpointStore(engine.root).prompt_capsule(work["id"])
        if checkpoint_capsule:
            prompt += "\n\n" + checkpoint_capsule + "\nUsa este checkpoint como contexto de arranque y recarga por MCP sólo lo que necesites."
    state=(work or {}).get("state") or "discovery"
    activity=activity_for_state(state)
    agent_configuration=AgentConfiguration(cwd if (cwd/".dynosai").exists() else engine.root)
    effective_agent_config=agent_configuration.resolve(provider=agent,activity=activity)
    managed_runtime=None
    if not dry_run:
        # Managed sessions use a DynosAI-owned provider runtime. Do not import
        # global/user provider rules, skills, MCPs or settings. Authentication
        # remains provider-owned and may be bridged read-only by the runtime.
        managed_runtime=ManagedProviderRuntime(engine.root).prepare(
            agent, activity=activity, effective=effective_agent_config
        )
    model_route=None
    model_args=[]
    if agent in {"cursor","codex"}:
        if work:
            control_decision=ModelControlPlane(engine.root).for_work(normalize_provider(agent),work,at_provider_boundary=True)
        else:
            control_decision=ModelControlPlane(engine.root).decide(normalize_provider(agent),activity,at_provider_boundary=True)
        route=control_decision.route
        model_route=route.to_dict(); model_route["model_control"]=control_decision.to_dict()
        if agent=="cursor":
            selector=cursor_cli_selector(route)
            if selector: model_args=["--model",selector]
            model_route["provider_selector"]=selector
        else:
            # Codex CLI accepts model + reasoning effort at a launch/turn boundary.
            model_args=["--model",route.model]
            if route.effort: model_args += ["-c",f'model_reasoning_effort="{route.effort}"']
    cmd=[executable or commands[agent]] + (["--force"] if agent=="cursor" else []) + model_args + [prompt]
    if dry_run or not executable:
        return {"agent":agent,"available":bool(executable),"command":cmd,"cwd":str(cwd),"task":task_id,"task_prepare":task_prepare,"implementation_prepare":implementation_prepare,"will_prepare_implementation":bool(work and work.get("state")=="ready"),"git_guard":str(engine.git_guard.guard_dir),"model_route":model_route,"agent_config":effective_agent_config,"managed_runtime":None,"managed_session":False,"launched":False}
    session=engine.agents.create_session(agent,work.get("id") if work else None,run.get("id") if run else None,cwd)
    runtime_env=(managed_runtime.env if managed_runtime is not None else {})
    env=engine.git_guard.environment({
        **runtime_env,
        "DYNOSAI_PROJECT_ROOT":str(engine.root),
        "DYNOSAI_SESSION_TOKEN":session["token"],
        "DYNOSAI_SESSION_ID":session["session_id"],
        "DYNOSAI_MANAGED_AGENT":"1",
        "DYNOSAI_AGENT_PROVIDER":agent,
        "DYNOSAI_EXTERNAL_DEFAULTS_POLICY":"ignore",
    })
    try:
        process=subprocess.run(cmd,cwd=cwd,env=env)
        return {"agent":agent,"available":True,"exit_code":process.returncode,"cwd":str(cwd),"task":task_id,"implementation_prepare":implementation_prepare,"session_id":session["session_id"],"run_id":run.get("id") if run else None,"work_id":work.get("id") if work else None,"model_route":model_route,"agent_config":effective_agent_config,"managed_runtime":managed_runtime.to_dict() if managed_runtime else None,"managed_session":True,"launched":True}
    finally:
        engine.db.execute("UPDATE agent_sessions SET state='closed',last_seen=? WHERE id=?",(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),session["session_id"]))
        engine.runtime.close_session(session["session_id"])


def demo_spec()->dict[str,Any]:
    return {"objective":"Crear una CLI que reciba N y muestre exactamente N términos de Fibonacci.","scope":["Entrada por argumento CLI","Generación de la secuencia","Errores de entrada"],"out_of_scope":["Interfaz gráfica"],"requirements":[{"id":"REQ-001","text":"La aplicación debe aceptar N como argumento entero de línea de comandos.","priority":"must"},{"id":"REQ-002","text":"La aplicación debe generar exactamente N términos de Fibonacci para N mayor o igual que cero.","priority":"must"},{"id":"REQ-003","text":"La aplicación debe rechazar entradas negativas o no enteras con un error observable.","priority":"must"}],"acceptance_criteria":[{"id":"AC-001","requirement":"REQ-001","text":"Dado el comando con argumento 5, cuando se ejecuta, entonces la aplicación interpreta N como entero."},{"id":"AC-002","requirement":"REQ-002","text":"Dado N=5, cuando se genera la secuencia, entonces la salida es 0 1 1 2 3."},{"id":"AC-003","requirement":"REQ-003","text":"Dado N=-1 o texto no numérico, cuando se ejecuta la CLI, entonces devuelve error y código distinto de cero."}],"business_rules":[{"id":"RULE-001","text":"N representa la cantidad de términos, no el índice final."}],"unknowns":[],"affected_features":[]}

def demo_plan()->dict[str,Any]:
    return {"approach":"Separar el cálculo puro de Fibonacci de la adaptación CLI y cubrir ambos comportamientos con unittest.","architecture_delta":["Añadir módulo src/fibonacci.py y adaptador src/cli.py"],"files":[{"path":"src/__init__.py","action":"create","reason":"Paquete Python"},{"path":"src/fibonacci.py","action":"create","reason":"Cálculo puro"},{"path":"src/cli.py","action":"create","reason":"Entrada y salida CLI"},{"path":"tests/test_fibonacci.py","action":"create","reason":"Cobertura de reglas"}],"risks":["Errores de conversión de argumentos"],"validation_profiles":["unit"],"tasks":[{"title":"Implementar núcleo Fibonacci","description":"Crear la función pura que genera exactamente N términos y valida N no negativo.","requirements":["REQ-002","REQ-003"],"acceptance":["AC-002","AC-003"],"files":["src/__init__.py","src/fibonacci.py","tests/test_fibonacci.py"],"depends_on":[],"evidence_required":["git_diff","test_result"]},{"title":"Implementar adaptador CLI","description":"Leer N desde argv, mostrar la secuencia separada por espacios y propagar errores como código no cero.","requirements":["REQ-001","REQ-003"],"acceptance":["AC-001","AC-003"],"files":["src/cli.py"],"depends_on":[0],"evidence_required":["git_diff","test_result"]}]}


def run_demo(project:Path)->dict[str,Any]:
    if project.exists(): shutil.rmtree(project)
    e=DynosAI(project); e.initialize("Fibonacci CLI","python","python -m unittest discover -s tests","all")
    w=e.start("Crear una aplicación CLI que reciba N y muestre exactamente N términos de Fibonacci separados por espacios"); wid=w["id"]; e.continue_work(wid); e.register_decision(wid,"¿Qué representa N?","La cantidad exacta de términos"); e.submit_spec(wid,demo_spec(),"demo-agent"); e.continue_work(wid); e.review(wid,"spec"); e.continue_work(wid); e.submit_plan(wid,demo_plan(),"demo-agent"); e.review(wid,"plan"); prep=e.continue_work(wid); wt=Path(prep["worktree"]); (wt/"src").mkdir(exist_ok=True); (wt/"tests").mkdir(exist_ok=True); (wt/"src"/"__init__.py").write_text("",encoding="utf-8")
    (wt/"src"/"fibonacci.py").write_text("def generate_fibonacci(n: int) -> list[int]:\n    if not isinstance(n, int) or n < 0:\n        raise ValueError('N must be a non-negative integer')\n    out=[]\n    a,b=0,1\n    for _ in range(n):\n        out.append(a); a,b=b,a+b\n    return out\n",encoding="utf-8")
    (wt/"src"/"cli.py").write_text("import sys\nfrom .fibonacci import generate_fibonacci\ndef main(argv=None):\n    argv=list(sys.argv[1:] if argv is None else argv)\n    try:\n        n=int(argv[0]); print(' '.join(map(str,generate_fibonacci(n)))); return 0\n    except (ValueError,IndexError) as exc:\n        print(f'Error: {exc}',file=sys.stderr); return 2\nif __name__=='__main__': raise SystemExit(main())\n",encoding="utf-8")
    (wt/"tests"/"test_fibonacci.py").write_text("import unittest\nfrom src.fibonacci import generate_fibonacci\nclass T(unittest.TestCase):\n def test_five(self): self.assertEqual(generate_fibonacci(5),[0,1,1,2,3])\n def test_zero(self): self.assertEqual(generate_fibonacci(0),[])\n def test_negative(self):\n  with self.assertRaises(ValueError): generate_fibonacci(-1)\n",encoding="utf-8")
    reg=e.register_result(wid,"Fibonacci implementado",["src/__init__.py","src/fibonacci.py","src/cli.py","tests/test_fibonacci.py"],[],[f"{wid}-T01",f"{wid}-T02"]); e.continue_work(wid); e.review(wid,"code"); val=e.continue_work(wid); merged=e.review(wid,"merge") if val["validation"]["passed"] else val
    return {"project":str(project),"work":wid,"registration":reg,"merged":merged,"board":e.board(),"ask":e.ask("poner un máximo a la cantidad de números producidos"),"stats":e.stats(),"doctor":e.doctor()}


def output(value:Any,as_json:bool)->None:
    if as_json or not isinstance(value,list): print(json.dumps(value,ensure_ascii=False,indent=2,default=str)); return
    for row in value: print(f"{row['state']:15} {row['id']:10} {row['title']} [{row.get('tasks',{}).get('completed',0)}/{row.get('tasks',{}).get('total',0)}]")

if __name__=="__main__": raise SystemExit(main())
