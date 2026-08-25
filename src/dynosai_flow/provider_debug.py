# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider-native debug driver helpers and deterministic certification adapters."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .application import DynosAIApplication
from .debug import DebugE2ERunner, SCENARIOS
from .debug_fixtures import (
    apply_greenfield_fibonacci,
    apply_orderflow_contract_discounts,
    brownfield_orderflow_plan,
    brownfield_orderflow_spec,
    greenfield_fibonacci_plan,
    greenfield_fibonacci_spec,
    seed_orderflow_brownfield,
)
from .mcp import MCPServer, ElicitationExchange, _resolve_exchange
from .policy import ValidationProfilePolicy
from .util import json_dumps, utc_now


class ProviderNativeMatrixRunner:
    """Deterministic provider-native certification matrix.

    This is intentionally not a fake Cursor/Codex coding model. It certifies the
    DynosAI side of the integration contract using real MCP initialize/tool calls,
    advertised Elicitation capability, persistent provider sessions, interactive
    branches, human gate round-trips and the same independent business oracles used
    by the 0.7 certification. Real provider UI runs remain an external acceptance
    layer because they require authenticated interactive clients.
    """

    PROVIDERS=("cursor","codex")
    SCENARIO_NAMES=("fibonacci","orderflow-contract-discounts")

    def __init__(self, output: str|Path|None=None, workspace: str|Path|None=None, keep: bool=True):
        stamp=utc_now().replace(":","").replace("+00:00","Z")
        self.base=Path(workspace).resolve() if workspace else Path(tempfile.gettempdir())/f"dynosai-provider-matrix-{stamp}"
        self.output=Path(output).resolve() if output else self.base/"dynosai-debug-e2e-provider-matrix-080.zip"
        self.keep=keep

    @staticmethod
    def _tool_result(response: dict[str,Any]) -> Any:
        if "error" in response: raise RuntimeError(response["error"])
        result=response.get("result") or {}
        if result.get("isError"): raise RuntimeError(result.get("content"))
        return result.get("structuredContent")

    def _call(self, server:MCPServer, name:str, arguments:dict[str,Any]|None=None) -> Any:
        response=server.handle({"jsonrpc":"2.0","id":f"call-{name}","method":"tools/call","params":{"name":name,"arguments":arguments or {}}})
        if isinstance(response,ElicitationExchange): return response
        assert isinstance(response,dict), response
        return self._tool_result(response)

    def _accept_gate(self, server:MCPServer, wid:str, expected_gate:str, *, execute:bool=False) -> dict[str,Any]:
        exchange=self._call(server,"dynosai_get_next_action",{"work_id":wid,"execute":execute})
        if not isinstance(exchange,ElicitationExchange):
            raise AssertionError(f"expected MCP Elicitation for {expected_gate}, got {exchange}")
        if exchange.interaction.get("gate")!=expected_gate:
            raise AssertionError(f"expected {expected_gate} gate, got {exchange.interaction.get('gate')}")
        content={"decision":"approve","comments":"provider-matrix certification"}
        refreshed=_resolve_exchange(server,exchange,"accept",content)
        return {"interaction":exchange.interaction,"resolved":True,"refreshed":refreshed}

    @staticmethod
    def _approve_unit(app:DynosAIApplication) -> None:
        parts=ValidationProfilePolicy.parse_command("python -m unittest discover -s tests")
        now=utc_now()
        app.engine.db.set_meta("test_command","python -m unittest discover -s tests")
        app.engine.db.execute(
            "INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,source=excluded.source,approved=1,updated_at=excluded.updated_at",
            ("unit",json_dumps(parts),"provider-native-matrix",1,now,now),
        )

    def _run_case(self, provider:str, scenario_name:str) -> dict[str,Any]:
        case=self.base/provider/scenario_name
        if case.exists(): shutil.rmtree(case)
        project=case/"project"; logs=case/"logs"; home=case/"home"
        project.mkdir(parents=True,exist_ok=True); logs.mkdir(parents=True,exist_ok=True); home.mkdir(parents=True,exist_ok=True)
        old_home=os.environ.get("DYNOSAI_USER_HOME"); old_runtime=os.environ.get("DYNOSAI_RUNTIME_HOME")
        os.environ["DYNOSAI_USER_HOME"]=str(home); os.environ["DYNOSAI_RUNTIME_HOME"]=str(home/".dynosai"/"runtime")
        try:
            fixture=None
            if scenario_name=="orderflow-contract-discounts": fixture=seed_orderflow_brownfield(project)
            app=DynosAIApplication(project)
            init=app.initialize_project(name=SCENARIOS[scenario_name]["name"],agent=provider,allow_git_init=(scenario_name=="orderflow-contract-discounts"))
            self._approve_unit(app)
            start=app.start_feature(SCENARIOS[scenario_name]["description"],provider=provider,workspace_strategy="interactive_branch")
            wid=start["id"]
            for item in SCENARIOS[scenario_name]["decisions"]:
                app.engine.register_decision(wid,item["question"],item["answer"])
            server=MCPServer(project)
            init_response=server.handle({"jsonrpc":"2.0","id":"init","method":"initialize","params":{
                "protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor" if provider=="cursor" else "OpenAI Codex","version":"provider-matrix"},
                "capabilities":{"tools":{},"elicitation":{"form":{}},"roots":{},"prompts":{}},
            }})
            assert isinstance(init_response,dict)
            server.handle({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
            # inbox -> discovery
            first=self._call(server,"dynosai_get_next_action",{"work_id":wid,"execute":True})
            if isinstance(first,ElicitationExchange): raise AssertionError("unexpected gate before spec")
            # Agent authors spec.
            spec=greenfield_fibonacci_spec() if scenario_name=="fibonacci" else brownfield_orderflow_spec()
            self._call(server,"dynosai_submit_spec",{"work_id":wid,"spec":spec})
            spec_gate=self._accept_gate(server,wid,"spec",execute=True)
            # Agent authors plan; plan_review before a plan must not elicit.
            pre_plan=self._call(server,"dynosai_get_next_action",{"work_id":wid,"execute":False})
            if isinstance(pre_plan,ElicitationExchange): raise AssertionError("plan approval requested before plan exists")
            plan=greenfield_fibonacci_plan() if scenario_name=="fibonacci" else brownfield_orderflow_plan()
            self._call(server,"dynosai_submit_plan",{"work_id":wid,"plan":plan})
            # When execute=true on the plan-gate call, the deterministic ready boundary collapses
            # READY -> IMPLEMENTING when the human approves the plan. No second
            # orchestration poll is required.
            plan_gate=self._accept_gate(server,wid,"plan",execute=True)
            implementing=plan_gate.get("refreshed") or {}
            if isinstance(implementing,ElicitationExchange): raise AssertionError("unexpected implementation gate")
            if ((implementing.get("work") or {}).get("state"))!="implementing": raise AssertionError(implementing)
            if implementing.get("ready_to_implementation_collapsed") is not True: raise AssertionError("READY->IMPLEMENTING was not collapsed")
            certified_wave=((implementing.get("contract") or {}).get("task_queue") or {}).get("execution_wave") if isinstance(implementing,dict) else None
            if scenario_name=="fibonacci":
                if not isinstance(certified_wave,dict) or len(certified_wave.get("task_ids") or [])!=2:
                    raise AssertionError(f"expected deterministic two-task fibonacci execution wave, got {certified_wave}")
                if certified_wave.get("atomic_execution_allowed") is not True:
                    raise AssertionError(f"expected atomic fibonacci execution wave, got {certified_wave}")
            work=server.engine.get_work(wid); workspace=Path(work["worktree"])
            if workspace.resolve()!=project.resolve(): raise AssertionError("interactive provider session changed workspace")
            files=apply_greenfield_fibonacci(workspace) if scenario_name=="fibonacci" else apply_orderflow_contract_discounts(workspace)
            task_ids=[r["id"] for r in server.engine.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id",(wid,))]
            reg=self._call(server,"dynosai_register_result",{"work_id":wid,"summary":f"{scenario_name} implemented by deterministic provider harness","files_changed":files,"tests":[],"completed_tasks":task_ids})
            if isinstance(reg,ElicitationExchange):
                if reg.interaction.get("gate")!="code": raise AssertionError(f"expected auto-advanced code gate, got {reg.interaction}")
                registration=(reg.base_result.get("registration_result") or {}) if isinstance(reg.base_result,dict) else {}
                if registration.get("status")!="verified": raise AssertionError(reg.base_result)
                resolved=server.app.resolve_interaction(reg.interaction["id"],"accept",{"decision":"approve","comments":"provider-matrix certification"})
                code_gate={"interaction":reg.interaction,"resolved":resolved,"auto_advanced":True}
            else:
                if reg.get("status")!="verified": raise AssertionError(reg)
                code_gate=self._accept_gate(server,wid,"code",execute=True)
            merge_gate=self._accept_gate(server,wid,"merge",execute=True)
            final=server.engine.get_work(wid)
            if final["state"]!="done": raise AssertionError(final)
            # Independent oracle against the merged code.
            oracle_runner=DebugE2ERunner(agent="cursor",scenario=scenario_name,workspace=case/"oracle-holder",output=case/"oracle.zip")
            oracle_runner.engine=server.engine; oracle_runner.work_id=wid; oracle_runner.logs=logs; oracle_runner.timeline_path=logs/"oracle-timeline.jsonl"
            oracle=oracle_runner._run_fibonacci_oracle() if scenario_name=="fibonacci" else oracle_runner._run_orderflow_oracle()
            sessions=server.engine.db.query("SELECT * FROM provider_sessions WHERE work_id=? ORDER BY created_at",(wid,))
            interactions=server.engine.db.query("SELECT id,kind,gate,status,provider FROM human_interactions WHERE work_id=? ORDER BY created_at",(wid,))
            caps=server.engine.db.one("SELECT * FROM provider_capabilities WHERE provider=?",(provider,))
            audit_counts={row["event_type"]:row["n"] for row in server.engine.db.query("SELECT event_type,COUNT(*) n FROM audit GROUP BY event_type")}
            events=server.app.list_events(work_id=wid)
            provider_files={
                "cursor_commands": sorted(str(p.relative_to(project)) for p in (project/".cursor"/"commands").glob("dynosai*.md")) if provider=="cursor" else [],
                "codex_skill": (project/".agents"/"skills"/"dynosai"/"SKILL.md").exists() if provider=="codex" else False,
            }
            case_result={
                "provider":provider,"scenario":scenario_name,"mode":SCENARIOS[scenario_name]["mode"],"status":"passed",
                "work_id":wid,"final_state":final["state"],"quality_score":final["quality_score"],"oracle":oracle,
                "provider_sessions":len(sessions),"provider_session_state":sessions[0].get("state") if len(sessions)==1 else None,"same_workspace":str(workspace.resolve())==str(project.resolve()),
                "elicitation_count":len(interactions),"elicitations":interactions,"capabilities_recorded":bool(caps),
                "certified_execution_wave_task_ids":((certified_wave or {}).get("task_ids") if isinstance(certified_wave,dict) else []),
                "certified_execution_wave_atomic":bool((certified_wave or {}).get("atomic_execution_allowed")) if isinstance(certified_wave,dict) else False,
                "ready_to_implementation_collapsed":bool(implementing.get("ready_to_implementation_collapsed")) if isinstance(implementing,dict) else False,
                "mcp_failures":audit_counts.get("MCPToolFailed",0),"mcp_rejections":audit_counts.get("MCPToolRejected",0),"mcp_normalizations":audit_counts.get("MCPToolNormalized",0),
                "events":[e["event_type"] for e in events],"provider_files":provider_files,"fixture":fixture,
                "gates":{
                    "single_provider_session":len(sessions)==1,
                    "provider_session_closed":len(sessions)==1 and sessions[0].get("state")=="completed",
                    "all_human_gates_via_elicitation":len(interactions)==4 and {x.get("gate") for x in interactions}=={"spec","plan","code","merge"} and all(x.get("status")=="accepted" for x in interactions),
                    "elicitation_capability_recorded":bool(caps),"same_workspace":str(workspace.resolve())==str(project.resolve()),
                    "final_done":final["state"]=="done","oracle_passed":oracle.get("passed") is True,
                    "zero_mcp_failures":audit_counts.get("MCPToolFailed",0)==0,"zero_mcp_rejections":audit_counts.get("MCPToolRejected",0)==0,"zero_mcp_normalizations":audit_counts.get("MCPToolNormalized",0)==0,
                },
            }
            case_result["status"]="passed" if all(case_result["gates"].values()) else "failed"
            (logs/"summary.json").write_text(json.dumps(case_result,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
            return case_result
        finally:
            if old_home is None: os.environ.pop("DYNOSAI_USER_HOME",None)
            else: os.environ["DYNOSAI_USER_HOME"]=old_home
            if old_runtime is None: os.environ.pop("DYNOSAI_RUNTIME_HOME",None)
            else: os.environ["DYNOSAI_RUNTIME_HOME"]=old_runtime

    def _run_case_subprocess(self, provider:str, scenario:str) -> dict[str,Any]:
        timeout=300 if scenario=="orderflow-contract-discounts" else 240
        command=[sys.executable,"-m","dynosai_flow.provider_debug","--case-provider",provider,"--case-scenario",scenario,"--workspace",str(self.base)]
        completed=subprocess.run(command,text=True,capture_output=True,timeout=timeout)
        if completed.returncode!=0:
            raise RuntimeError(f"provider case failed: {provider}/{scenario}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")
        lines=[line for line in completed.stdout.splitlines() if line.strip()]
        if not lines: raise RuntimeError(f"provider case produced no summary: {provider}/{scenario}")
        return json.loads(lines[-1])

    def run(self) -> dict[str,Any]:
        if self.base.exists(): shutil.rmtree(self.base)
        self.base.mkdir(parents=True,exist_ok=True); started=utc_now(); children=[]
        # Each heavy adoption/indexing case runs in a fresh interpreter. This avoids
        # cumulative native/model state and makes the matrix representative of four
        # independent provider sessions rather than one long-lived test process.
        for provider in self.PROVIDERS:
            for scenario in self.SCENARIO_NAMES:
                children.append(self._run_case_subprocess(provider,scenario))
        coverage={"cursor":False,"codex":False,"greenfield":False,"brownfield":False}
        for c in children:
            coverage[c["provider"]]=True; coverage[c["mode"]]=True
        gates={
            "cursor_green_passed":next(c for c in children if c["provider"]=="cursor" and c["mode"]=="greenfield")["status"]=="passed",
            "cursor_brown_passed":next(c for c in children if c["provider"]=="cursor" and c["mode"]=="brownfield")["status"]=="passed",
            "codex_green_passed":next(c for c in children if c["provider"]=="codex" and c["mode"]=="greenfield")["status"]=="passed",
            "codex_brown_passed":next(c for c in children if c["provider"]=="codex" and c["mode"]=="brownfield")["status"]=="passed",
            "all_single_session":all(c["gates"]["single_provider_session"] for c in children),
            "all_sessions_closed":all(c["gates"]["provider_session_closed"] for c in children),
            "all_elicitations":all(c["gates"]["all_human_gates_via_elicitation"] for c in children),
            "all_oracles":all(c["oracle"]["passed"] for c in children),
            "zero_mcp_failures":all(c["mcp_failures"]==0 for c in children),
            "zero_mcp_rejections":all(c["mcp_rejections"]==0 for c in children),
            "zero_mcp_normalizations":all(c["mcp_normalizations"]==0 for c in children),
        }
        summary={"debug_version":8,"scenario":"provider-green-brown-matrix","started_at":started,"finished_at":utc_now(),"coverage":coverage,"gates":gates,"status":"passed" if all(gates.values()) and all(coverage.values()) else "failed","children":children}
        self.output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(self.output,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("suite/summary-final.json",json.dumps(summary,ensure_ascii=False,indent=2,default=str))
            z.writestr("README.txt","DynosAI 0.8 provider-native deterministic certification: Cursor + Codex, each against greenfield Fibonacci and brownfield OrderFlow, with persistent provider sessions and MCP Elicitation gates. This certifies DynosAI protocol/core; authenticated real-provider UI acceptance is separate.\n")
            for path in sorted(self.base.rglob("*")):
                if path.is_file() and path.resolve()!=self.output.resolve():
                    try: z.write(path,path.relative_to(self.base))
                    except Exception: pass
        summary["bundle"]=str(self.output)
        if not self.keep:
            moved=Path(tempfile.gettempdir())/self.output.name
            if self.output.resolve()!=moved.resolve(): shutil.copy2(self.output,moved)
            shutil.rmtree(self.base,ignore_errors=True); summary["bundle"]=str(moved)
        return summary


def main(argv:list[str]|None=None)->int:
    import argparse
    parser=argparse.ArgumentParser(description="DynosAI provider-native matrix worker")
    parser.add_argument("--case-provider",choices=list(ProviderNativeMatrixRunner.PROVIDERS))
    parser.add_argument("--case-scenario",choices=list(ProviderNativeMatrixRunner.SCENARIO_NAMES))
    parser.add_argument("--workspace")
    args=parser.parse_args(argv)
    if not args.case_provider or not args.case_scenario or not args.workspace:
        parser.error("--case-provider, --case-scenario and --workspace are required")
    result=ProviderNativeMatrixRunner(workspace=args.workspace,keep=True)._run_case(args.case_provider,args.case_scenario)
    print(json.dumps(result,ensure_ascii=False,default=str,separators=(",",":")))
    return 0 if result.get("status")=="passed" else 1


if __name__=="__main__":
    raise SystemExit(main())
