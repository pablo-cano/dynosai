from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.application import DynosAIApplication, ProjectDetector
from dynosai_flow.cli import demo_plan, demo_spec, parser
from dynosai_flow.db import Database
from dynosai_flow.interactions import HumanInteractionService
from dynosai_flow.mcp import MCPServer, ElicitationExchange, TOOLS
from dynosai_flow.provider_debug import ProviderNativeMatrixRunner
from dynosai_flow.providers import MachineProviderSetup, ProviderCapabilityService


class DynosAI08Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="dynosai-08-"))
        self.home=self.tmp/"home"; self.home.mkdir()
        self.old_home=os.environ.get("DYNOSAI_USER_HOME"); self.old_runtime=os.environ.get("DYNOSAI_RUNTIME_HOME")
        os.environ["DYNOSAI_USER_HOME"]=str(self.home)
        os.environ["DYNOSAI_RUNTIME_HOME"]=str(self.home/".dynosai"/"runtime")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        if self.old_home is None: os.environ.pop("DYNOSAI_USER_HOME",None)
        else: os.environ["DYNOSAI_USER_HOME"]=self.old_home
        if self.old_runtime is None: os.environ.pop("DYNOSAI_RUNTIME_HOME",None)
        else: os.environ["DYNOSAI_RUNTIME_HOME"]=self.old_runtime
        shutil.rmtree(self.tmp,ignore_errors=True)

    def _project(self, provider="cursor"):
        root=self.tmp/f"project-{provider}"; root.mkdir()
        app=DynosAIApplication(root); app.initialize_project(name="Demo",agent=provider)
        return root,app

    def _spec_gate(self, provider="cursor"):
        root,app=self._project(provider)
        w=app.start_feature("Crear Fibonacci CLI",provider=provider); wid=w["id"]
        app.next_action(provider,wid,True)
        app.engine.submit_spec(wid,demo_spec(),"test-08")
        app.engine.continue_work(wid)
        return root,app,wid

    def test_capability_negotiation_distinguishes_absent_from_empty_elicitation(self):
        none=ProviderCapabilityService.from_initialize("cursor",{"capabilities":{},"clientInfo":{"name":"Cursor"}})
        empty=ProviderCapabilityService.from_initialize("cursor",{"capabilities":{"elicitation":{}},"clientInfo":{"name":"Cursor"}})
        form=ProviderCapabilityService.from_initialize("codex",{"capabilities":{"elicitation":{"form":{}}},"clientInfo":{"name":"OpenAI Codex"}})
        self.assertFalse(none.elicitation_form)
        self.assertTrue(empty.elicitation_form)
        self.assertTrue(form.elicitation_form)

    def test_schema_v5_contains_provider_sessions_human_interactions_and_events(self):
        root=self.tmp/"db"; db=Database(root); db.initialize()
        self.assertEqual(db.get_meta("schema_version"),"6")
        tables={r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"provider_sessions","provider_capabilities","human_interactions","application_events"} <= tables)
        versions={r["version"] for r in db.query("SELECT version FROM migrations")}
        self.assertIn(5,versions)

    def test_project_detector_classifies_empty_code_no_git_git_and_existing_dynosai(self):
        d=ProjectDetector(); empty=self.tmp/"empty"; empty.mkdir()
        self.assertEqual(d.detect(empty)["classification"],"EMPTY_DIRECTORY")
        code=self.tmp/"code"; code.mkdir(); (code/"app.py").write_text("print('x')\n")
        self.assertEqual(d.detect(code)["classification"],"NEW_CODE_NO_GIT")
        subprocess.run(["git","init","-q"],cwd=code,check=True)
        subprocess.run(["git","config","user.email","test@example.com"],cwd=code,check=True); subprocess.run(["git","config","user.name","Test"],cwd=code,check=True)
        subprocess.run(["git","add","."],cwd=code,check=True); subprocess.run(["git","commit","-qm","baseline"],cwd=code,check=True)
        self.assertEqual(d.detect(code)["classification"],"EXISTING_GIT_REPO")
        app=DynosAIApplication(empty); app.initialize_project(name="Empty")
        self.assertEqual(d.detect(empty)["classification"],"EXISTING_DYNOSAI_PROJECT")

    def test_machine_setup_configures_cursor_and_codex_without_overwriting_user_config(self):
        (self.home/".cursor").mkdir(); (self.home/".cursor"/"mcp.json").write_text(json.dumps({"mcpServers":{"mine":{"command":"mine"}}}))
        (self.home/".codex").mkdir(); (self.home/".codex"/"config.toml").write_text('model="gpt-x"\n')
        result=MachineProviderSetup(self.home).setup(["cursor","codex"],install_model=False)
        self.assertTrue(result["ready"])
        cursor=json.loads((self.home/".cursor"/"mcp.json").read_text())
        self.assertIn("mine",cursor["mcpServers"]); self.assertEqual(cursor["mcpServers"]["dynosai"]["env"]["DYNOSAI_AGENT_PROVIDER"],"cursor")
        self.assertEqual(Path(cursor["mcpServers"]["dynosai"]["command"]),Path(sys.executable).absolute())
        self.assertEqual(cursor["mcpServers"]["dynosai"]["args"],["-m","dynosai_flow.mcp"])
        codex_path=self.home/".codex"/"config.toml"
        codex=codex_path.read_text()
        self.assertIn('model="gpt-x"',codex); self.assertIn("[mcp_servers.dynosai]",codex); self.assertIn('DYNOSAI_AGENT_PROVIDER = "codex"',codex)
        parsed=tomllib.loads(codex)
        server=parsed["mcp_servers"]["dynosai"]
        self.assertEqual(Path(server["command"]),Path(sys.executable).absolute())
        self.assertEqual(server["args"],["-m","dynosai_flow.mcp"])

    def test_provider_project_artifacts_use_cursor_commands_and_single_codex_skill(self):
        croot,capp=self._project("cursor")
        commands=sorted(p.name for p in (croot/".cursor"/"commands").glob("dynosai*.md"))
        self.assertEqual(commands,["dynosai-feature.md","dynosai-resume.md","dynosai-status.md","dynosai.md"])
        xroot,xapp=self._project("codex")
        skill=xroot/".agents"/"skills"/"dynosai"/"SKILL.md"
        self.assertTrue(skill.exists()); self.assertIn("Human decisions are MCP Elicitation",skill.read_text())
        self.assertTrue((xroot/"AGENTS.md").exists())

    def test_interactive_feature_uses_one_session_and_same_workspace_from_start(self):
        root,app=self._project("cursor")
        w=app.start_feature("Crear Fibonacci CLI",provider="cursor")
        row=app.engine.get_work(w["id"]); sessions=app.engine.db.query("SELECT * FROM provider_sessions WHERE work_id=?",(w["id"],))
        self.assertEqual(row["workspace_strategy"],"interactive_branch")
        self.assertEqual(Path(row["worktree"]).resolve(),root.resolve())
        self.assertEqual(len(sessions),1)
        self.assertTrue(app.engine.git.current_branch().startswith("dyn/dyn-0001-"))
        resumed=app.resume("cursor",w["id"])
        self.assertTrue(resumed["provider_session"]["resumed"])
        self.assertEqual(len(app.engine.db.query("SELECT * FROM provider_sessions WHERE work_id=?",(w["id"],))),1)

    def test_portable_event_journal_is_controller_runtime_not_agent_diff(self):
        root,app=self._project("cursor")
        self.assertIn(".dynosai/state/events.jsonl",(root/".gitignore").read_text().splitlines())
        w=app.start_feature("Crear Fibonacci CLI",provider="cursor")
        self.assertNotIn(".dynosai/state/events.jsonl",app.engine.git.status(root))

    def test_plan_review_only_elicits_after_plan_exists(self):
        root,app,wid=self._spec_gate("cursor")
        spec_gate=app.next_action("cursor",wid,False)["human_interaction"]
        app.resolve_interaction(spec_gate["id"],"accept",{"decision":"approve"})
        self.assertIsNone(app.next_action("cursor",wid,False)["human_interaction"])
        app.engine.submit_plan(wid,demo_plan(),"test-08")
        self.assertEqual(app.next_action("cursor",wid,False)["human_interaction"]["gate"],"plan")

    def test_cursor_and_codex_initialize_to_real_mcp_elicitation_exchange(self):
        for provider,client in (("cursor","Cursor"),("codex","OpenAI Codex")):
            with self.subTest(provider=provider):
                root,app,wid=self._spec_gate(provider)
                server=MCPServer(root)
                server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":client,"version":"test"},"capabilities":{"elicitation":{"form":{}}}}})
                response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_next_action","arguments":{"work_id":wid}}})
                self.assertIsInstance(response,ElicitationExchange)
                self.assertEqual(response.interaction["gate"],"spec")
                cap=server.engine.db.one("SELECT * FROM provider_capabilities WHERE provider=?",(provider,))
                self.assertIsNotNone(cap)

    def test_client_without_elicitation_stays_gated_and_never_autoapproves(self):
        root,app,wid=self._spec_gate("cursor")
        server=MCPServer(root); server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor"},"capabilities":{}}})
        response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_next_action","arguments":{"work_id":wid}}})
        result=response["result"]["structuredContent"]
        self.assertTrue(result["provider_degraded_mode"])
        self.assertEqual(server.engine.get_work(wid)["state"],"spec_review")
        self.assertEqual(server.engine.artifacts.artifact(wid,"spec")["status"],"draft")

    def test_stdio_mcp_elicitation_round_trip_resumes_same_tool_call(self):
        root,app,wid=self._spec_gate("cursor")
        env=os.environ.copy(); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src"); env["DYNOSAI_AGENT_PROVIDER"]="cursor"
        proc=subprocess.Popen([sys.executable,"-m","dynosai_flow.mcp","--project",str(root)],cwd=root,env=env,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        def send(obj): proc.stdin.write(json.dumps(obj)+"\n"); proc.stdin.flush()
        def recv(): return json.loads(proc.stdout.readline())
        send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor"},"capabilities":{"elicitation":{"form":{}}}}}); recv()
        send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
        send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_next_action","arguments":{"work_id":wid}}})
        request=recv(); self.assertEqual(request["method"],"elicitation/create"); self.assertEqual(request["params"]["mode"],"form")
        send({"jsonrpc":"2.0","id":request["id"],"result":{"action":"accept","content":{"decision":"approve"}}})
        final=recv()["result"]["structuredContent"]
        self.assertEqual(final["work"]["state"],"plan_review"); self.assertEqual(final["elicitation"]["action"],"accept")
        proc.terminate(); proc.wait(timeout=5); proc.stdin.close(); proc.stdout.close(); proc.stderr.close()

    def test_event_bus_is_provider_neutral_and_tauri_ready(self):
        root,app=self._project("cursor"); w=app.start_feature("Crear Fibonacci CLI",provider="cursor")
        app.next_action("cursor",w["id"],True)
        events=app.list_events(work_id=w["id"])
        self.assertIn("WorkStarted",[e["event_type"] for e in events]); self.assertIn("StateChanged",[e["event_type"] for e in events])
        self.assertTrue(all("payload" in e and "provider" not in e for e in events))


    def test_project_bootstrap_no_git_and_monorepo_require_real_elicitation(self):
        # Existing code without Git must never be mutated silently.
        no_git=self.tmp/"legacy-no-git"; no_git.mkdir(); (no_git/"app.py").write_text("print('legacy')\n")
        server=MCPServer(no_git)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor"},"capabilities":{"elicitation":{"form":{}}}}})
        response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_project","arguments":{"action":"initialize","root":str(no_git)}}})
        self.assertIsInstance(response,ElicitationExchange)
        self.assertEqual(response.interaction["kind"],"project_bootstrap")
        self.assertIn("initialize_git",response.interaction["requested_schema"]["properties"]["decision"]["enum"])
        self.assertFalse((no_git/".git").exists())

        # Probable monorepos also require explicit whole-root acceptance.
        mono=self.tmp/"mono"; mono.mkdir(); subprocess.run(["git","init","-q"],cwd=mono,check=True)
        subprocess.run(["git","config","user.email","test@example.com"],cwd=mono,check=True); subprocess.run(["git","config","user.name","Test"],cwd=mono,check=True)
        (mono/"a").mkdir(); (mono/"a"/"pyproject.toml").write_text("[project]\nname='a'\nversion='0.1'\n")
        (mono/"b").mkdir(); (mono/"b"/"package.json").write_text('{"name":"b"}\n')
        subprocess.run(["git","add","."],cwd=mono,check=True); subprocess.run(["git","commit","-qm","baseline"],cwd=mono,check=True)
        mserver=MCPServer(mono)
        mserver.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"OpenAI Codex"},"capabilities":{"elicitation":{"form":{}}}}})
        mresp=mserver.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_project","arguments":{"action":"initialize","root":str(mono)}}})
        self.assertIsInstance(mresp,ElicitationExchange)
        self.assertEqual(mresp.interaction["id"],"BOOTSTRAP-MONOREPO")
        self.assertFalse((mono/".dynosai"/"knowledge.db").exists())

    def test_stdio_project_bootstrap_elicitation_can_initialize_git_and_continue_same_call(self):
        root=self.tmp/"bootstrap-stdio"; root.mkdir(); (root/"legacy.py").write_text("VALUE=1\n")
        env=os.environ.copy(); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src"); env["DYNOSAI_AGENT_PROVIDER"]="cursor"
        proc=subprocess.Popen([sys.executable,"-m","dynosai_flow.mcp","--project",str(root)],cwd=root,env=env,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        def send(obj): proc.stdin.write(json.dumps(obj)+"\n"); proc.stdin.flush()
        def recv(): return json.loads(proc.stdout.readline())
        send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor"},"capabilities":{"elicitation":{"form":{}}}}}); recv()
        send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
        send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_project","arguments":{"action":"initialize","root":str(root),"name":"Legacy"}}})
        req=recv(); self.assertEqual(req["method"],"elicitation/create"); self.assertIn("Git",req["params"]["message"])
        send({"jsonrpc":"2.0","id":req["id"],"result":{"action":"accept","content":{"decision":"initialize_git"}}})
        final=recv()["result"]["structuredContent"]
        self.assertEqual(final["action"],"initialize"); self.assertTrue(final["elicitation"]["ephemeral"]); self.assertTrue((root/".git").exists()); self.assertTrue((root/".dynosai"/"knowledge.db").exists())
        proc.terminate(); proc.wait(timeout=10); proc.stdin.close(); proc.stdout.close(); proc.stderr.close()

    def test_clarification_and_scope_extension_are_mcp_elicitations(self):
        root,app,wid=self._spec_gate("cursor")
        sg=app.next_action("cursor",wid,False)["human_interaction"]; app.resolve_interaction(sg["id"],"accept",{"decision":"approve"})
        app.engine.submit_plan(wid,demo_plan(),"test-08")
        pg=app.next_action("cursor",wid,False)["human_interaction"]; app.resolve_interaction(pg["id"],"accept",{"decision":"approve"})
        app.next_action("cursor",wid,True)
        server=MCPServer(root); server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor"},"capabilities":{"elicitation":{"form":{}}}}})
        clarify=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_work","arguments":{"action":"clarify","work_id":wid,"question":"Should the CLI accept leading plus signs?"}}})
        self.assertIsInstance(clarify,ElicitationExchange); self.assertEqual(clarify.interaction["kind"],"clarification")
        server.app.resolve_interaction(clarify.interaction["id"],"accept",{"answer":"Yes"})
        scope=server.handle({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"dynosai_request_scope_extension","arguments":{"work_id":wid,"path":"docs/extra.md","action":"create","reason":"Document the public CLI contract"}}})
        self.assertIsInstance(scope,ElicitationExchange); self.assertEqual(scope.interaction["kind"],"scope_extension")
        resolved=server.app.resolve_interaction(scope.interaction["id"],"accept",{"decision":"approve"})
        self.assertEqual((resolved.get("result") or {}).get("status"),"approved")

    def test_provider_native_tool_surface_and_matrix_cli_are_exposed(self):
        names={x["name"] for x in TOOLS}
        self.assertTrue({"dynosai_project","dynosai_work","dynosai_resume","dynosai_events"} <= names)
        args=parser().parse_args(["debug","e2e","--agent","all","--scenario","provider-green-brown-matrix"])
        self.assertEqual(args.agent,"all"); self.assertEqual(args.scenario,"provider-green-brown-matrix")
        self.assertEqual(ProviderNativeMatrixRunner.PROVIDERS,("cursor","codex"))
        self.assertEqual(ProviderNativeMatrixRunner.SCENARIO_NAMES,("fibonacci","orderflow-contract-discounts"))


if __name__=="__main__": unittest.main()
