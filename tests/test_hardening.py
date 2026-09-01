from __future__ import annotations

# Historical diagnostic suite. Excluded from the stable release gate
# (`python -m pytest`) unless DYNOSAI_HISTORICAL_TESTS=1.

import gzip
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.db import Database
from dynosai_flow.engine import DynosAI
from dynosai_flow.mcp import CURRENT_PROTOCOL, MCPServer


class HardeningV05Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="dynosai-v05-"))
        self.user_home=self.tmp.parent/(self.tmp.name+"-home"); self.user_home.mkdir()
        self.old_user_home=os.environ.get("DYNOSAI_USER_HOME"); os.environ["DYNOSAI_USER_HOME"]=str(self.user_home)
        def cleanup_env():
            if self.old_user_home is None: os.environ.pop("DYNOSAI_USER_HOME",None)
            else: os.environ["DYNOSAI_USER_HOME"]=self.old_user_home
        self.addCleanup(cleanup_env)
        self.addCleanup(lambda: shutil.rmtree(self.user_home,ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.tmp,ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.tmp.parent/".dynosai-worktrees"/self.tmp.name,ignore_errors=True))

    def init(self,test_command="true"):
        e=DynosAI(self.tmp); e.initialize("Hardening", "python", test_command, None); return e

    def ready(self,e:DynosAI):
        work=e.start("Crear una CLI Fibonacci que genere N términos"); wid=work["id"]
        e.continue_work(wid); e.continue_work(wid); e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        e.continue_work(wid); e.submit_plan(wid,demo_plan(),"agent"); e.review(wid,"plan"); prep=e.continue_work(wid)
        return wid,prep

    def test_git_guard_exact_read_shapes_and_write_bypasses_blocked(self):
        e=self.init(); env=e.git_guard.environment()
        allowed=[
            ["git","status"],["git","branch","--show-current"],["git","tag","--list"],
            ["git","rev-parse","HEAD"],["git","ls-files"],
        ]
        blocked=[
            ["git","branch","hacked"],["git","tag","hacked-tag"],
            ["git","config","dynosai.audit","changed"],["git","remote","add","evil","https://example.com/x.git"],
            ["git","checkout","-b","evil"],["git","reset","--hard","HEAD~1"],
            ["git","show","HEAD:.env"],["git","diff","--no-index","/etc/passwd","/etc/hosts"],
            ["git","log","-1"],["git","grep","SECRET"],
        ]
        for command in allowed:
            r=subprocess.run(command,cwd=self.tmp,env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,(command,r.stderr))
        for command in blocked:
            r=subprocess.run(command,cwd=self.tmp,env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,73,(command,r.stderr)); self.assertIn("Git Guard",r.stderr)

    def test_secret_file_is_denied_by_library_not_agent_prompt(self):
        e=self.init(); (self.tmp/".env").write_text("SECRET_TOKEN=top-secret\n",encoding="utf-8")
        with self.assertRaises(PermissionError): e.retrieval.file_slice(".env",1,2)
        # An env-like Python file is not indexed either.
        (self.tmp/"secrets").mkdir(); (self.tmp/"secrets"/"credentials.py").write_text("SECRET='x'\n")
        e.indexer.index(e.git.head())
        self.assertIsNone(e.db.one("SELECT path FROM code_files WHERE path='secrets/credentials.py'"))

    def test_agent_connect_preserves_existing_configuration(self):
        e=self.init()
        (self.tmp/"AGENTS.md").write_text("CUSTOM AGENT RULE\n",encoding="utf-8")
        (self.tmp/"CLAUDE.md").write_text("CUSTOM CLAUDE RULE\n",encoding="utf-8")
        (self.tmp/".cursor").mkdir(exist_ok=True); (self.tmp/".cursor"/"mcp.json").write_text(json.dumps({"mcpServers":{"existing":{"command":"old"}}}),encoding="utf-8")
        (self.tmp/".codex").mkdir(exist_ok=True); (self.tmp/".codex"/"config.toml").write_text('model = "keep-me"\n[mcp_servers.existing]\ncommand = "old"\n',encoding="utf-8")
        e.agents.connect("all")
        self.assertIn("CUSTOM AGENT RULE",(self.tmp/"AGENTS.md").read_text())
        self.assertIn("CUSTOM CLAUDE RULE",(self.tmp/"CLAUDE.md").read_text())
        cursor=json.loads((self.tmp/".cursor"/"mcp.json").read_text()); self.assertIn("existing",cursor["mcpServers"]); self.assertNotIn("dynosai",cursor["mcpServers"])
        global_cursor=json.loads((self.user_home/".cursor"/"mcp.json").read_text()); self.assertIn("dynosai",global_cursor["mcpServers"])
        codex=(self.tmp/".codex"/"config.toml").read_text(); self.assertIn('model = "keep-me"',codex); self.assertIn("[mcp_servers.existing]",codex); self.assertIn("[mcp_servers.dynosai]",codex)


    def test_agent_disconnect_removes_only_dynosai_managed_sections(self):
        e=self.init(); (self.tmp/"AGENTS.md").write_text("CUSTOM RULE\n",encoding="utf-8"); (self.tmp/".cursor").mkdir(exist_ok=True); (self.tmp/".cursor"/"mcp.json").write_text(json.dumps({"mcpServers":{"existing":{"command":"old"}}}),encoding="utf-8")
        e.agents.connect("cursor"); self.assertIn("DYNOSAI:START",(self.tmp/"AGENTS.md").read_text()); e.agents.disconnect("cursor")
        self.assertIn("CUSTOM RULE",(self.tmp/"AGENTS.md").read_text()); self.assertNotIn("DYNOSAI:START",(self.tmp/"AGENTS.md").read_text())
        data=json.loads((self.tmp/".cursor"/"mcp.json").read_text()); self.assertIn("existing",data["mcpServers"]); self.assertNotIn("dynosai",data["mcpServers"])
        global_data=json.loads((self.user_home/".cursor"/"mcp.json").read_text()); self.assertNotIn("dynosai",global_data.get("mcpServers",{}))

    def test_plan_scope_empty_is_denied_and_read_file_cannot_be_modified(self):
        # Existing source gives us a legitimate read-only plan entry.
        (self.tmp/"src").mkdir(); (self.tmp/"src"/"existing.py").write_text("VALUE=1\n")
        e=self.init("true")
        w=e.start("Documentar y probar el valor existente"); wid=w["id"]
        e.continue_work(wid); e.continue_work(wid)
        spec=demo_spec(); e.submit_spec(wid,spec,"agent"); e.continue_work(wid); e.review(wid,"spec"); e.continue_work(wid)
        empty=demo_plan(); empty["files"]=[]
        with self.assertRaises(ValueError): e.submit_plan(wid,empty,"agent")
        plan=demo_plan(); plan["files"]=[{"path":"src/existing.py","action":"read","reason":"Solo contexto"}]
        for task in plan["tasks"]: task["files"]=["src/existing.py"]
        e.submit_plan(wid,plan,"agent"); e.review(wid,"plan"); prep=e.continue_work(wid); wt=Path(prep["worktree"])
        (wt/"src"/"existing.py").write_text("VALUE=2\n")
        result=e.register_result(wid,"changed read-only file",["src/existing.py"],[],[f"{wid}-T01"])
        self.assertEqual(result["status"],"failed"); self.assertTrue(any(x["path"]=="src/existing.py" for x in result["scope_action_violations"]))

    def test_scope_extension_is_explicitly_reviewed(self):
        e=self.init(); wid,_=self.ready(e)
        request=e.request_scope_extension(wid,"src/extra.py","create","Implementation discovered a new adapter",task_id=f"{wid}-T01")
        self.assertEqual(request["status"],"pending")
        approved=e.approve_scope_request(request["id"],f"{wid}-T01")
        self.assertEqual(approved["status"],"approved")
        plan=e.artifacts.artifact(wid,"plan"); self.assertTrue(any(x["path"]=="src/extra.py" for x in plan["metadata"]["files"]))
        task=e.db.one("SELECT files FROM tasks WHERE id=?",(f"{wid}-T01",)); self.assertIn("src/extra.py",json.loads(task["files"]))


    def test_external_agent_process_contract_uses_worktree_session_mcp_and_git_guard(self):
        from dynosai_flow.cli import open_agent
        e=self.init(); wid,prep=self.ready(e)
        fakebin=self.tmp/"fakebin"; fakebin.mkdir()
        script=fakebin/"codex"
        script_body = """#!/usr/bin/env python3
import json, os, subprocess, sys
if not os.environ.get('DYNOSAI_SESSION_TOKEN') or not os.environ.get('DYNOSAI_PROJECT_ROOT'): sys.exit(20)
if not os.path.exists('AGENTS.md'): sys.exit(21)
g=subprocess.run(['git','branch','hacked-by-agent'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
if g.returncode != 73: sys.exit(22)
p=subprocess.Popen([sys.executable,'-m','dynosai_flow.mcp'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=os.environ.copy())
msgs=[
 {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'fake-agent','version':'1'}}},
 {'jsonrpc':'2.0','method':'notifications/initialized','params':{}},
 {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'dynosai_get_next_action','arguments':{'execute':False}}}
]
for m in msgs:
    p.stdin.write(json.dumps(m)+'\\n'); p.stdin.flush()
hello=json.loads(p.stdout.readline()); action=json.loads(p.stdout.readline()); p.terminate()
if hello.get('result',{}).get('protocolVersion')!='2025-11-25': sys.exit(23)
if action.get('result',{}).get('isError'): sys.exit(24)
sys.exit(0)
"""
        script.write_text(script_body,encoding="utf-8"); script.chmod(0o755)
        old=os.environ.get("PATH",""); os.environ["PATH"]=str(fakebin)+os.pathsep+old
        try:
            result=open_agent(e,"codex",False)
        finally:
            os.environ["PATH"]=old
        self.assertTrue(result["launched"]); self.assertEqual(result["exit_code"],0); self.assertEqual(Path(result["cwd"]),Path(prep["worktree"]))
        self.assertIsNone(e.git._git("branch","--list","hacked-by-agent",cwd=Path(prep["worktree"]),check=False).stdout.strip() or None)

    def test_mcp_negotiates_supported_version_and_session_is_work_bound(self):
        e=self.init(); wid,prep=self.ready(e); run=e.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id DESC LIMIT 1",(wid,))
        session=e.agents.create_session("codex",wid,run["id"],Path(prep["worktree"]))
        old=os.environ.get("DYNOSAI_SESSION_TOKEN"); os.environ["DYNOSAI_SESSION_TOKEN"]=session["token"]
        try:
            server=MCPServer(self.tmp)
            hello=server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2099-01-01"}})
            self.assertEqual(hello["result"]["protocolVersion"],CURRENT_PROTOCOL)
            with self.assertRaises(PermissionError): server.call_tool("dynosai_submit_spec",{"work_id":"OTHER","spec":{}})
        finally:
            if old is None: os.environ.pop("DYNOSAI_SESSION_TOKEN",None)
            else: os.environ["DYNOSAI_SESSION_TOKEN"]=old

    def test_mcp_plan_validation_rejection_is_structured_not_transport_failure(self):
        e=self.init(); work=e.start("Crear una CLI Fibonacci"); wid=work["id"]
        e.continue_work(wid); e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        session=e.agents.create_session("cursor",wid,None,self.tmp)
        old=os.environ.get("DYNOSAI_SESSION_TOKEN"); os.environ["DYNOSAI_SESSION_TOKEN"]=session["token"]
        try:
            server=MCPServer(self.tmp)
            server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":CURRENT_PROTOCOL}})
            bad=demo_plan(); bad["files"][-1]["action"]="test"
            response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_submit_plan","arguments":{"work_id":wid,"plan":bad}}})
            self.assertFalse(response["result"]["isError"],response)
            payload=response["result"]["structuredContent"]
            self.assertFalse(payload["accepted"]); self.assertEqual(payload["error_type"],"validation")
            self.assertIn("file role, not filesystem intent",payload["message"])
            self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolRejected'")["n"],1)
            self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolFailed'")["n"],0)
        finally:
            if old is None: os.environ.pop("DYNOSAI_SESSION_TOKEN",None)
            else: os.environ["DYNOSAI_SESSION_TOKEN"]=old

    def test_artifact_revisions_are_immutable_and_semantic_units_are_granular(self):
        e=self.init(); w=e.start("Crear una función sencilla"); wid=w["id"]
        e.continue_work(wid); e.continue_work(wid); first=e.submit_spec(wid,demo_spec(),"agent")["artifact"]
        changed=demo_spec(); changed["objective"]="Crear una CLI Fibonacci sencilla y completamente verificable desde la consola."
        second=e.submit_spec(wid,changed,"agent")["artifact"]
        revisions=e.db.query("SELECT revision,content FROM artifact_revisions WHERE artifact_id=? ORDER BY revision",(first["id"],))
        self.assertGreaterEqual(len(revisions),2); self.assertNotEqual(revisions[0]["content"],revisions[-1]["content"])
        types={d["entity_type"] for d in e.semantic.authoritative_documents(e.git.head())}
        self.assertIn("requirement",types); self.assertIn("acceptance",types); self.assertNotIn("spec",types); self.assertNotIn("plan",types)

    def test_restore_validation_failure_keeps_original_database(self):
        e=self.init(); work=e.start("Persistent work"); snap=e.backup("good")
        before=e.db.one("SELECT title FROM work_items WHERE id=?",(work["id"],))["title"]
        with gzip.open(snap["portable"],"rt",encoding="utf-8") as h: payload=json.load(h)
        payload["tables"]["tasks"].append({"id":"TASK-BAD","work_id":"MISSING","title":"bad","description":"bad bad bad bad bad bad","state":"ready","requirements":"[]","acceptance":"[]","files":"[]","validation_commands":"[]","evidence_required":"[]","depends_on":"[]","created_at":"x","updated_at":"x"})
        bad=self.tmp/"bad-snapshot.json.gz"
        with gzip.open(bad,"wt",encoding="utf-8") as h: json.dump(payload,h)
        with self.assertRaises(RuntimeError): e.state.restore(bad)
        self.assertEqual(e.db.one("SELECT title FROM work_items WHERE id=?",(work["id"],))["title"],before)

    def test_future_database_schema_is_refused(self):
        e=self.init(); e.db.set_meta("schema_version","999")
        with self.assertRaises(RuntimeError): Database(self.tmp).initialize()

    def test_retrieval_search_does_not_full_scan_symbol_table(self):
        e=self.init()
        original=e.db.query
        def guarded(sql,params=()):
            compact=" ".join(sql.lower().split())
            if compact.startswith("select * from symbols") and "where" not in compact:
                raise AssertionError("full symbol scan in query path")
            return original(sql,params)
        with patch.object(e.db,"query",side_effect=guarded):
            e.retrieval.search("authentication token",limit=5)

    def test_python_method_kinds_and_ambiguous_calls_are_not_invented(self):
        (self.tmp/"src").mkdir(); (self.tmp/"src"/"x.py").write_text(
            "class A:\n    def save(self): return 1\n    def second(self): return self.save()\n\nclass B:\n    def save(self): return 2\n\ndef top():\n    def nested(): return 1\n    return nested()\n",encoding="utf-8")
        e=self.init(); e.indexer.index(e.git.head())
        second=e.db.one("SELECT kind FROM symbols WHERE qualified_name='A.second'"); nested=e.db.one("SELECT kind FROM symbols WHERE qualified_name='top.nested'")
        self.assertEqual(second["kind"],"method"); self.assertEqual(nested["kind"],"function")
        edge=e.db.one("SELECT target_symbol FROM call_edges WHERE target_name='self.save'")
        self.assertTrue(edge["target_symbol"].endswith("/A.save"),edge)

    def test_multiagent_task_run_integrates_verified_task_into_feature_worktree(self):
        e=self.init("true"); wid,prep=self.ready(e)
        task_id=f"{wid}-T01"; task=e.db.one("SELECT files FROM tasks WHERE id=?",(task_id,)); task_files=json.loads(task["files"])
        task_run=e.prepare_task_run(wid,task_id,"codex"); wt=Path(task_run["worktree"])
        for rel in task_files:
            path=wt/rel; path.parent.mkdir(parents=True,exist_ok=True)
            if rel.endswith("test_fibonacci.py"): path.write_text("def test_ok(): assert True\n")
            elif rel.endswith("fibonacci.py"): path.write_text("def generate_fibonacci(n): return []\n")
            else: path.write_text("")
        result=e.register_result(wid,"task one",task_files,[],[task_id],task_run["run_id"])
        self.assertEqual(result["status"],"verified"); self.assertEqual(result["integration"]["status"],"integrated")
        self.assertEqual(e.db.one("SELECT state FROM tasks WHERE id=?",(task_id,))["state"],"completed")
        self.assertTrue((Path(prep["worktree"])/"src"/"fibonacci.py").exists())



    def test_policy_filtered_git_diff_hides_sensitive_paths(self):
        e=self.init(); wid,prep=self.ready(e); wt=Path(prep["worktree"])
        (wt/"src").mkdir(exist_ok=True); (wt/"src"/"fibonacci.py").write_text("def f(): return 1\n",encoding="utf-8")
        (wt/".env").write_text("SECRET_TOKEN=never-show\n",encoding="utf-8"); e.git._git("add","-f",".env",cwd=wt)
        view=e.agent_git_diff((e.db.one("SELECT id FROM runs WHERE work_id=? ORDER BY started_at DESC LIMIT 1",(wid,)) or {})["id"])
        self.assertIn("src/fibonacci.py",view["files"]); self.assertNotIn("SECRET_TOKEN",view["diff"]); self.assertTrue(any(x["path"]==".env" for x in view["denied"]))

    def test_late_agent_connection_is_installed_inside_existing_worktree_without_losing_source_diff(self):
        from dynosai_flow.cli import open_agent
        e=self.init(); wid,prep=self.ready(e); wt=Path(prep["worktree"]); (wt/"src").mkdir(exist_ok=True); source=wt/"src"/"pending.py"; source.write_text("VALUE=1\n",encoding="utf-8")
        before=e.git.diff_files(wt,(e.db.one("SELECT base_commit FROM runs WHERE work_id=? ORDER BY started_at DESC LIMIT 1",(wid,)) or {})["base_commit"]); self.assertIn("src/pending.py",before)
        result=open_agent(e,"codex",True)
        self.assertEqual(Path(result["cwd"]),wt); self.assertTrue((wt/"AGENTS.md").exists()); self.assertTrue((wt/".codex"/"config.toml").exists())
        after=e.git.diff_files(wt,(e.db.one("SELECT base_commit FROM runs WHERE work_id=? ORDER BY started_at DESC LIMIT 1",(wid,)) or {})["base_commit"]); self.assertIn("src/pending.py",after)

    def test_dependent_task_runs_integrate_sequentially_and_advance_after_all_evidence(self):
        e=self.init("true"); wid,prep=self.ready(e)
        first=f"{wid}-T01"; second=f"{wid}-T02"
        with self.assertRaises(ValueError): e.prepare_task_run(wid,second,"claude")
        run1=e.prepare_task_run(wid,first,"codex"); wt1=Path(run1["worktree"]); files1=json.loads(e.db.one("SELECT files FROM tasks WHERE id=?",(first,))["files"])
        for rel in files1:
            p=wt1/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text("def ok(): return True\n" if rel.endswith(".py") else "",encoding="utf-8")
        r1=e.register_result(wid,"first",files1,[],[first],run1["run_id"]); self.assertEqual(r1["status"],"verified")
        run2=e.prepare_task_run(wid,second,"claude"); wt2=Path(run2["worktree"]); files2=json.loads(e.db.one("SELECT files FROM tasks WHERE id=?",(second,))["files"])
        for rel in files2:
            p=wt2/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text("def main(): return 0\n",encoding="utf-8")
        r2=e.register_result(wid,"second",files2,[],[second],run2["run_id"]); self.assertEqual(r2["status"],"verified")
        self.assertTrue(all(x["state"]=="completed" for x in e.db.query("SELECT state FROM tasks WHERE work_id=?",(wid,))))
        advanced=e.continue_work(wid); self.assertEqual(advanced["state"],"code_review")

    def test_sync_does_not_index_uncommitted_source_as_head(self):
        (self.tmp/"src").mkdir(); (self.tmp/"src"/"a.py").write_text("VALUE=1\n")
        e=self.init(); before=e.db.one("SELECT content_hash FROM code_files WHERE path='src/a.py'")["content_hash"]
        (self.tmp/"src"/"a.py").write_text("VALUE=2\n")
        result=e.sync()
        self.assertTrue(result["index"].get("blocked")); self.assertIn("src/a.py",result["index"]["paths"])
        self.assertEqual(e.db.one("SELECT content_hash FROM code_files WHERE path='src/a.py'")["content_hash"],before)

    def test_session_expiration_is_enforced(self):
        e=self.init(); wid,prep=self.ready(e); run=e.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY started_at DESC LIMIT 1",(wid,))
        session=e.agents.create_session("codex",wid,run["id"],Path(prep["worktree"]))
        e.db.execute("UPDATE agent_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(session["session_id"],))
        self.assertIsNone(e.agents.verify_session(session["token"]))
        self.assertEqual(e.db.one("SELECT state FROM agent_sessions WHERE id=?",(session["session_id"],))["state"],"expired")


    def test_doctor_core_ready_without_connected_agent_and_full_ready_requires_external_gates(self):
        e=self.init(); d=e.doctor(); self.assertTrue(d["ready"],d); self.assertFalse(d["full_ready"]); self.assertFalse(d["provider_ready"])

    def test_scorecard_has_no_category_below_nine(self):
        e=self.init(); score=e.scorecard()
        self.assertTrue(score["goal_met"],score); self.assertGreaterEqual(score["minimum_score"],9.0)

    def test_cursor_config_is_schema_valid_and_declares_provider_without_secret(self):
        e=self.init(); e.agents.connect("cursor")
        cli=json.loads((self.tmp/".cursor"/"cli.json").read_text(encoding="utf-8"))
        self.assertIsInstance(cli["permissions"]["allow"],list)
        self.assertNotIn("dynosai",cli)
        self.assertIn("Shell(git)",cli["permissions"]["deny"])
        self.assertIn("Read(.dynosai/runtime/**)",cli["permissions"]["deny"])
        mcp=json.loads((self.user_home/".cursor"/"mcp.json").read_text(encoding="utf-8"))["mcpServers"]["dynosai"]
        self.assertEqual(mcp["env"]["DYNOSAI_AGENT_PROVIDER"],"cursor")
        self.assertNotIn("DYNOSAI_SESSION_TOKEN",mcp.get("env",{}))

    def test_cursor_mcp_binds_to_active_managed_session_without_inherited_token(self):
        e=self.init(); w=e.start("Crear CLI"); wid=w["id"]; e.continue_work(wid)
        session=e.agents.create_session("cursor",wid,None,self.tmp)
        old_token=os.environ.pop("DYNOSAI_SESSION_TOKEN",None); old_provider=os.environ.get("DYNOSAI_AGENT_PROVIDER")
        old_cwd=Path.cwd(); os.environ["DYNOSAI_AGENT_PROVIDER"]="cursor"
        try:
            os.chdir(self.tmp)
            server=MCPServer(self.tmp)
            self.assertIsNotNone(server.session)
            self.assertEqual(server.session["id"],session["session_id"])
            server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}})
            response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_next_action","arguments":{}}})
            self.assertFalse(response["result"]["isError"],response)
        finally:
            os.chdir(old_cwd)
            if old_token is not None: os.environ["DYNOSAI_SESSION_TOKEN"]=old_token
            if old_provider is None: os.environ.pop("DYNOSAI_AGENT_PROVIDER",None)
            else: os.environ["DYNOSAI_AGENT_PROVIDER"]=old_provider

    def test_runtime_broker_allows_unique_session_fallback_but_refuses_ambiguity(self):
        e=self.init(); w=e.start("Crear CLI"); wid=w["id"]; e.continue_work(wid)
        first=e.agents.create_session("cursor",wid,None,self.tmp)
        other=self.tmp.parent/(self.tmp.name+"-other"); other.mkdir(exist_ok=True); self.addCleanup(lambda: shutil.rmtree(other,ignore_errors=True))
        self.assertEqual(e.agents.resolve_active_session("cursor",other)["id"],first["session_id"])
        second_wt=self.tmp.parent/(self.tmp.name+"-second"); second_wt.mkdir(exist_ok=True); self.addCleanup(lambda: shutil.rmtree(second_wt,ignore_errors=True))
        e.agents.create_session("cursor",wid,None,second_wt)
        self.assertIsNone(e.agents.resolve_active_session("cursor",other))

    def test_nested_open_is_rejected_before_git_bootstrap(self):
        from dynosai_flow.cli import open_agent
        e=self.init(); old=os.environ.get("DYNOSAI_MANAGED_AGENT"); os.environ["DYNOSAI_MANAGED_AGENT"]="1"
        try:
            with self.assertRaisesRegex(RuntimeError,"cannot be called from inside"):
                open_agent(e,"cursor",False)
        finally:
            if old is None: os.environ.pop("DYNOSAI_MANAGED_AGENT",None)
            else: os.environ["DYNOSAI_MANAGED_AGENT"]=old

    def test_open_dry_run_does_not_mutate_managed_configuration(self):
        from dynosai_flow.cli import open_agent
        e=self.init(); before=e.git.head(); before_status=e.git.status()
        with patch("dynosai_flow.cli.shutil.which",return_value="/usr/bin/cursor-agent"):
            result=open_agent(e,"cursor",True)
        self.assertFalse(result["launched"]); self.assertEqual(e.git.head(),before); self.assertEqual(e.git.status(),before_status)

    def test_validation_can_run_against_session_task_worktree(self):
        e=self.init("python -c 'import pathlib,sys; sys.exit(0 if (pathlib.Path(\"marker.txt\").exists()) else 9)'")
        wid,prep=self.ready(e); task=f"{wid}-T01"; run=e.prepare_task_run(wid,task,"cursor"); wt=Path(run["worktree"]); (wt/"marker.txt").write_text("ok",encoding="utf-8")
        result=e.run_validations(wid,record=False,cwd=wt)
        self.assertTrue(result["passed"],result)

    def test_cursor_managed_mcp_child_recovers_session_without_parent_token(self):
        from dynosai_flow.cli import open_agent
        e=self.init(); w=e.start("Crear una CLI Fibonacci"); wid=w["id"]; e.continue_work(wid)
        fakebin=self.tmp/"fake-cursor-bin"; fakebin.mkdir(); script=fakebin/"cursor-agent"
        lines=[
            "#!/usr/bin/env python3",
            "import json, os, subprocess, sys",
            "from pathlib import Path",
            "cfg=json.loads((Path(os.environ['DYNOSAI_USER_HOME'])/'.cursor'/'mcp.json').read_text())['mcpServers']['dynosai']",
            "env=os.environ.copy(); env.pop('DYNOSAI_SESSION_TOKEN',None); env.pop('DYNOSAI_SESSION_ID',None); env.update(cfg.get('env',{}))",
            "p=subprocess.Popen([sys.executable,'-m','dynosai_flow.mcp'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env,cwd=os.getcwd())",
            "msgs=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},{'jsonrpc':'2.0','method':'notifications/initialized','params':{}},{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'dynosai_get_next_action','arguments':{}}}]",
            "for m in msgs: p.stdin.write(json.dumps(m)+'\\n'); p.stdin.flush()",
            "hello=json.loads(p.stdout.readline()); action=json.loads(p.stdout.readline()); p.terminate(); p.wait(timeout=5)",
            "sys.exit(31 if hello.get('result',{}).get('protocolVersion')!='2025-11-25' else 32 if action.get('result',{}).get('isError') else 0)",
        ]
        script.write_text("\n".join(lines)+"\n",encoding="utf-8"); script.chmod(0o755)
        old=os.environ.get("PATH",""); os.environ["PATH"]=str(fakebin)+os.pathsep+old
        try:
            result=open_agent(e,"cursor",False)
        finally:
            os.environ["PATH"]=old
        self.assertTrue(result["managed_session"]); self.assertEqual(result["exit_code"],0,result)
        self.assertEqual(e.get_work(wid)["state"],"discovery")


    def test_open_from_ready_prepares_worktree_before_cursor_launch(self):
        from dynosai_flow.cli import open_agent
        e=self.init(); work=e.start("Crear una CLI Fibonacci"); wid=work["id"]
        e.continue_work(wid); e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        e.continue_work(wid); e.submit_plan(wid,demo_plan(),"agent"); e.review(wid,"plan")
        self.assertEqual(e.get_work(wid)["state"],"ready")
        fakebin=self.tmp.parent/(self.tmp.name+"-fake-ready-cursor"); fakebin.mkdir(); self.addCleanup(lambda: shutil.rmtree(fakebin,ignore_errors=True)); script=fakebin/"cursor-agent"
        script.write_text("#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\nroot=Path(os.environ['DYNOSAI_PROJECT_ROOT']).resolve()\nif Path.cwd().resolve()==root: sys.exit(41)\nif not os.environ.get('DYNOSAI_SESSION_TOKEN'): sys.exit(42)\nsys.exit(0)\n",encoding="utf-8"); script.chmod(0o755)
        old=os.environ.get("PATH",""); os.environ["PATH"]=str(fakebin)+os.pathsep+old
        try:
            result=open_agent(e,"cursor",False)
        finally:
            os.environ["PATH"]=old
        self.assertEqual(result["exit_code"],0,result)
        self.assertTrue(result["managed_session"]); self.assertTrue(result["run_id"])
        self.assertNotEqual(Path(result["cwd"]).resolve(),self.tmp.resolve())
        self.assertEqual(e.get_work(wid)["state"],"implementing")
        self.assertEqual(Path(e.get_work(wid)["worktree"]).resolve(),Path(result["cwd"]).resolve())

    def test_cursor_global_mcp_and_scoped_worktree_writes(self):
        e=self.init(); e.agents.connect("cursor")
        gmcp=json.loads((self.user_home/".cursor"/"mcp.json").read_text(encoding="utf-8"))
        self.assertIn("dynosai",gmcp["mcpServers"]); self.assertEqual(gmcp["mcpServers"]["dynosai"]["env"]["DYNOSAI_RUNTIME_BROKER"],"1")
        cli=json.loads((self.tmp/".cursor"/"cli.json").read_text(encoding="utf-8")); self.assertIn("Shell(git)",cli["permissions"]["deny"])
        gcli=json.loads((self.user_home/".cursor"/"cli-config.json").read_text(encoding="utf-8")); self.assertNotIn("dynosai",gcli); self.assertIn("Mcp(dynosai:*)",gcli["permissions"]["allow"])
        wt=self.tmp/"scoped-wt"; wt.mkdir()
        e.agents.ensure_worktree_protocol(wt,"cursor",write_scope=["src/app.py","tests/test_app.py"])
        scoped=json.loads((wt/".cursor"/"cli.json").read_text(encoding="utf-8"))
        self.assertIn("Write(src/app.py)",scoped["permissions"]["allow"]); self.assertIn("Write(tests/test_app.py)",scoped["permissions"]["allow"]); self.assertNotIn("Write(**)",scoped["permissions"]["allow"])

    def test_dynosai_controller_git_bypasses_agent_guard_for_authenticated_mcp_transition(self):
        e=self.init(); wid,prep=self.ready(e)
        # ready() has already created the implementation worktree in older flows;
        # force a fresh ready transition scenario by moving back to ready and removing
        # worktree metadata while preserving the approved artifacts.
        work=e.get_work(wid)
        if work.get("worktree"):
            try: e.git.cleanup_worktree(work.get("branch"),Path(work["worktree"]))
            except Exception: pass
        e.db.execute("UPDATE work_items SET state='ready',worktree=NULL,branch=NULL WHERE id=?",(wid,))
        session=e.agents.create_session("cursor",wid,None,self.tmp)
        env=e.git_guard.environment(); old_path=os.environ.get("PATH"); old_token=os.environ.get("DYNOSAI_SESSION_TOKEN")
        os.environ["PATH"]=env["PATH"]; os.environ["DYNOSAI_SESSION_TOKEN"]=session["token"]
        try:
            server=MCPServer(self.tmp)
            server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25"}})
            response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_continue","arguments":{"work_id":wid}}})
            self.assertFalse(response["result"]["isError"],response)
            result=json.loads(response["result"]["content"][0]["text"]); self.assertEqual(result["state"],"implementing")
            self.assertTrue(Path(result["worktree"]).exists())
        finally:
            if old_path is None: os.environ.pop("PATH",None)
            else: os.environ["PATH"]=old_path
            if old_token is None: os.environ.pop("DYNOSAI_SESSION_TOKEN",None)
            else: os.environ["DYNOSAI_SESSION_TOKEN"]=old_token

    def test_real_git_resolver_ignores_guarded_path_for_controller(self):
        e=self.init(); e.git_guard.install(); env=e.git_guard.environment(); old=os.environ.get("PATH"); os.environ["PATH"]=env["PATH"]
        try:
            (self.tmp/"AGENTS.md").write_text("controller metadata\n",encoding="utf-8")
            commit=e.git.commit_main_changes("chore: controller privileged git")
            self.assertTrue(commit)
        finally:
            if old is None: os.environ.pop("PATH",None)
            else: os.environ["PATH"]=old


    def test_scope_extension_is_not_created_when_path_is_already_in_approved_plan(self):
        e=self.init(); wid,prep=self.ready(e)
        result=e.request_scope_extension(wid,"src/cli.py","modify","Need to adjust planned CLI",run_id=prep["run_id"],task_id=f"{wid}-T02")
        self.assertEqual(result["status"],"already_planned",result)
        self.assertIsNone(result["id"])
        self.assertIn(f"{wid}-T02",result["owner_tasks"])
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM scope_requests WHERE work_id=?",(wid,))["n"],0)
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='ScopeExtensionNotRequired'",())["n"],1)

    def test_pending_scope_request_blocks_code_approval(self):
        e=self.init(); wid,prep=self.ready(e)
        pending=e.request_scope_extension(wid,"docs/unplanned.md","create","Need an unplanned document",run_id=prep["run_id"],task_id=f"{wid}-T01")
        self.assertEqual(pending["status"],"pending")
        e.db.execute("UPDATE work_items SET state='code_review' WHERE id=?",(wid,))
        e.db.execute("UPDATE runs SET status='verified',verification_status='verified' WHERE id=?",(prep["run_id"],))
        result=e.review(wid,"code")
        self.assertFalse(result["approved"],result)
        self.assertEqual(result["state"],"code_review")
        self.assertTrue(any(x["code"]=="SCOPE.PENDING" for x in result["findings"]),result)

    def test_mcp_get_symbol_file_slice_shape_is_safely_normalized(self):
        (self.tmp/"sample.py").write_text("def alpha():\n    return 1\n",encoding="utf-8")
        e=self.init()
        server=MCPServer(self.tmp)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":CURRENT_PROTOCOL}})
        response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_symbol","arguments":{"path":"sample.py","start_line":1,"end_line":2}}})
        self.assertFalse(response["result"]["isError"],response)
        payload=response["result"]["structuredContent"]
        self.assertEqual(payload["path"],"sample.py"); self.assertTrue(payload["exists"]); self.assertIn("def alpha",payload["content"])
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolNormalized'")["n"],1)
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolFailed'")["n"],0)
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolRejected'")["n"],0)

    def test_mcp_invalid_tool_shape_is_structured_rejection_not_internal_failure(self):
        e=self.init(); server=MCPServer(self.tmp)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":CURRENT_PROTOCOL}})
        response=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_symbol","arguments":{"include_body":True}}})
        self.assertFalse(response["result"]["isError"],response)
        payload=response["result"]["structuredContent"]
        self.assertEqual(payload["error_type"],"tool_input_validation"); self.assertTrue(payload["repair_required"])
        self.assertIn("symbol_id",payload["message"])
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolRejected'")["n"],1)
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolFailed'")["n"],0)

    def test_mcp_structured_content_is_always_an_object_for_list_and_null_results(self):
        (self.tmp/"sample.py").write_text("def alpha():\n    return 1\n",encoding="utf-8")
        e=self.init()
        server=MCPServer(self.tmp)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":CURRENT_PROTOCOL}})
        found=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_find_symbol","arguments":{"query":"alpha","limit":5}}})
        self.assertFalse(found["result"]["isError"],found)
        self.assertIsInstance(found["result"]["structuredContent"],dict)
        self.assertIsInstance(found["result"]["structuredContent"].get("items"),list)
        missing=server.handle({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"dynosai_get_symbol","arguments":{"symbol_id":"does-not-exist","include_body":True}}})
        self.assertFalse(missing["result"]["isError"],missing)
        self.assertEqual(missing["result"]["structuredContent"],{"value":None})

    def test_mcp_unified_read_supports_symbol_and_file_modes_without_normalization(self):
        (self.tmp/"sample.py").write_text("def alpha():\n    return 1\n",encoding="utf-8")
        e=self.init(); server=MCPServer(self.tmp)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":CURRENT_PROTOCOL}})
        listed=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/list"})
        names={item["name"] for item in listed["result"]["tools"]}
        self.assertIn("dynosai_read",names); self.assertNotIn("dynosai_get_symbol",names); self.assertNotIn("dynosai_get_file_slice",names)
        sliced=server.handle({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"dynosai_read","arguments":{"path":"sample.py","start_line":1,"end_line":2}}})
        self.assertFalse(sliced["result"]["isError"],sliced); self.assertIn("def alpha",sliced["result"]["structuredContent"]["content"])
        found=server.handle({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"dynosai_find_symbol","arguments":{"query":"alpha","limit":5}}})
        sid=found["result"]["structuredContent"]["items"][0]["id"]
        symbol=server.handle({"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"dynosai_read","arguments":{"symbol_id":sid,"include_body":True}}})
        self.assertFalse(symbol["result"]["isError"],symbol); self.assertEqual(symbol["result"]["structuredContent"]["name"],"alpha")
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolNormalized'")["n"],0)

    def test_mcp_unified_read_rejects_ambiguous_or_incomplete_modes(self):
        e=self.init(); server=MCPServer(self.tmp)
        server.handle({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":CURRENT_PROTOCOL}})
        ambiguous=server.handle({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_read","arguments":{"symbol_id":"x","path":"a.py","start_line":1,"end_line":2}}})
        self.assertFalse(ambiguous["result"]["isError"],ambiguous); self.assertEqual(ambiguous["result"]["structuredContent"]["error_type"],"tool_input_validation")
        incomplete=server.handle({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"dynosai_read","arguments":{"path":"a.py","start_line":1}}})
        self.assertFalse(incomplete["result"]["isError"],incomplete); self.assertEqual(incomplete["result"]["structuredContent"]["error_type"],"tool_input_validation")
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolFailed'")["n"],0)
        self.assertEqual(e.db.one("SELECT COUNT(*) AS n FROM audit WHERE event_type='MCPToolNormalized'")["n"],0)


if __name__ == "__main__": unittest.main()
