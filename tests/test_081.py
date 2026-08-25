import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import CodexAppServerDriver, CursorAcceptanceDriver, probe_provider
from dynosai_flow.acceptance_policy import automated_elicitation_content
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_spec, parser


class Acceptance081Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup); self.tmp=Path(self.td.name)
        self.old_runtime=os.environ.get("DYNOSAI_RUNTIME_HOME"); os.environ["DYNOSAI_RUNTIME_HOME"]=str(self.tmp/"runtime")
        self.addCleanup(self._restore_env)
    def _restore_env(self):
        if self.old_runtime is None: os.environ.pop("DYNOSAI_RUNTIME_HOME",None)
        else: os.environ["DYNOSAI_RUNTIME_HOME"]=self.old_runtime

    def test_acceptance_policy_uses_semantic_decisions(self):
        gate={"requestedSchema":{"type":"object","properties":{"decision":{"type":"string","enum":["approve","request_changes","cancel"]},"comments":{"type":"string"}},"required":["decision"]}}
        self.assertEqual(automated_elicitation_content(gate)["decision"],"approve")
        bootstrap={"message":"This code has no Git repository","requestedSchema":{"type":"object","properties":{"decision":{"type":"string","enum":["initialize_git","analysis_only","cancel"]}},"required":["decision"]}}
        self.assertEqual(automated_elicitation_content(bootstrap)["decision"],"initialize_git")
        mono={"message":"Probable monorepo. Govern whole repository?","requestedSchema":{"type":"object","properties":{"decision":{"type":"string","enum":["whole_repository","cancel"]}},"required":["decision"]}}
        self.assertEqual(automated_elicitation_content(mono)["decision"],"whole_repository")
        scope={"message":"The agent requests modify access to payments.py. Reason: unexpected drift","requestedSchema":{"type":"object","properties":{"decision":{"type":"string","enum":["approve","request_changes","cancel"]}},"required":["decision"]}}
        self.assertEqual(automated_elicitation_content(scope)["decision"],"request_changes")

    def test_cli_exposes_real_provider_acceptance(self):
        args=parser().parse_args(["debug","acceptance","--providers","cursor,codex","--scenario","green-brown","--interaction-mode","auto","--timeout","900"])
        self.assertEqual(args.debug_command,"acceptance"); self.assertEqual(args.providers,"cursor,codex"); self.assertEqual(args.interaction_mode,"auto"); self.assertEqual(args.timeout,900)

    def test_mcp_acceptance_autoresponder_advances_same_tool_without_client_reply(self):
        root=self.tmp/"project"; root.mkdir()
        app=DynosAIApplication(root); app.initialize_project(name="Fibonacci",agent="cursor")
        work=app.start_feature("Crear Fibonacci CLI",provider="cursor")
        app.next_action("cursor",work["id"],True)
        app.engine.submit_spec(work["id"],demo_spec(),"test-081")
        app.next_action("cursor",work["id"],True)
        self.assertEqual(app.engine.get_work(work["id"])["state"],"spec_review")
        trace=self.tmp/"trace.jsonl"
        env=os.environ.copy(); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src"); env["DYNOSAI_AGENT_PROVIDER"]="cursor"; env["DYNOSAI_ACCEPTANCE_AUTORESPOND"]="1"; env["DYNOSAI_ACCEPTANCE_TRACE"]=str(trace)
        proc=subprocess.Popen([sys.executable,"-m","dynosai_flow.mcp","--project",str(root)],cwd=root,env=env,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        def send(x): proc.stdin.write(json.dumps(x)+"\n"); proc.stdin.flush()
        def recv(): return json.loads(proc.stdout.readline())
        send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor","version":"test"},"capabilities":{"elicitation":{"form":{}}}}}); recv()
        send({"jsonrpc":"2.0","method":"notifications/initialized","params":{}})
        send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"dynosai_get_next_action","arguments":{"work_id":work["id"]}}})
        final=recv()
        self.assertNotEqual(final.get("method"),"elicitation/create")
        structured=final["result"]["structuredContent"]
        self.assertEqual(structured["work"]["state"],"plan_review")
        self.assertEqual(structured["elicitation"]["action"],"accept")
        proc.terminate(); proc.wait(timeout=5)
        for stream in (proc.stdin,proc.stdout,proc.stderr):
            if stream is not None:
                stream.close()
        self.assertIn('"transport": "dynosai-test-autoresponder"',trace.read_text(encoding="utf-8"))
        check=DynosAIApplication(root); check.engine.db.initialize()
        self.assertEqual(check.engine.db.one("SELECT COUNT(*) n FROM audit WHERE event_type='AcceptanceElicitationAutoResponded'")["n"],1)

    def test_cursor_driver_uses_headless_mcp_automation_flags(self):
        script=self.tmp/"cursor-agent"
        script.write_text("#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({'argv':sys.argv[1:],'auto':os.getenv('DYNOSAI_ACCEPTANCE_AUTORESPOND')}))\n",encoding="utf-8"); script.chmod(script.stat().st_mode|stat.S_IXUSR)
        project=self.tmp/"cursor-project"; project.mkdir(); logs=self.tmp/"cursor-logs"
        result=CursorAcceptanceDriver(str(script),30).run(project,"test prompt",logs,interaction_mode="auto")
        self.assertEqual(result["exit_code"],0); self.assertFalse(result["wire_elicitation"]); self.assertTrue(result["automated_responses"])
        stream=(logs/"provider-stream.jsonl").read_text(encoding="utf-8")
        self.assertIn("--approve-mcps",stream); self.assertIn("--force",stream); self.assertIn('"auto": "1"',stream)

    def test_codex_app_server_driver_auto_answers_real_server_request_shape(self):
        fake=self.tmp/"codex"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys

def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); rid=m.get('id')
    if method=='initialize': send({'id':rid,'result':{'userAgent':'fake-codex'}})
    elif method=='initialized': pass
    elif method=='thread/start': send({'id':rid,'result':{'thread':{'id':'thr-test'}}}); send({'method':'thread/started','params':{'thread':{'id':'thr-test'}}})
    elif method=='turn/start':
        send({'id':rid,'result':{'turn':{'id':'turn-test','status':'inProgress','items':[]}}})
        send({'method':'mcpServer/elicitation/request','id':77,'params':{'threadId':'thr-test','turnId':'turn-test','serverName':'dynosai','mode':'form','message':'Specification ready','requestedSchema':{'type':'object','properties':{'decision':{'type':'string','enum':['approve','request_changes','cancel']}},'required':['decision']}}})
    elif rid==77:
        assert m['result']['action']=='accept' and m['result']['content']['decision']=='approve',m
        send({'method':'serverRequest/resolved','params':{'threadId':'thr-test','requestId':77}})
        send({'method':'turn/completed','params':{'threadId':'thr-test','turn':{'id':'turn-test','status':'completed'}}})
        break
''',encoding='utf-8'); fake.chmod(fake.stat().st_mode|stat.S_IXUSR)
        project=self.tmp/"codex-project"; project.mkdir(); logs=self.tmp/"codex-logs"
        result=CodexAppServerDriver(str(fake),30).run(project,"test prompt",logs,interaction_mode="auto")
        self.assertEqual(result["exit_code"],0,result); self.assertTrue(result["wire_elicitation"]); self.assertTrue(result["automated_responses"]); self.assertEqual(result["elicitation_auto_responses"],1)
        trace=(logs/"codex-app-server.jsonl").read_text(encoding="utf-8")
        self.assertIn("mcpServer/elicitation/request",trace); self.assertIn('"decision": "approve"',trace)


    def test_acceptance_worker_persists_structured_failure_when_provider_is_unavailable(self):
        base=self.tmp/"worker-acceptance"
        env=os.environ.copy()
        env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src")
        env["DYNOSAI_CURSOR_BIN"]=str(self.tmp/"definitely-missing-cursor")
        proc=subprocess.run([
            sys.executable,"-m","dynosai_flow.acceptance",
            "--worker","--provider","cursor","--scenario","fibonacci",
            "--base",str(base),"--interaction-mode","auto","--timeout","5",
        ],env=env,text=True,capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,1)
        summary=base/"cursor"/"fibonacci"/"logs"/"summary.json"
        self.assertTrue(summary.exists())
        payload=json.loads(summary.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"],"failed")
        self.assertEqual(payload["failure"]["type"],"ProviderUnavailable")
        self.assertFalse(payload["probe"]["installed"])

    def test_provider_probe_supports_binary_override(self):
        fake=self.tmp/"agent"; fake.write_text("#!/bin/sh\necho fake-provider-1.0\n",encoding="utf-8"); fake.chmod(fake.stat().st_mode|stat.S_IXUSR)
        old=os.environ.get("DYNOSAI_CURSOR_BIN"); os.environ["DYNOSAI_CURSOR_BIN"]=str(fake)
        try:
            p=probe_provider("cursor"); self.assertTrue(p.installed); self.assertEqual(p.executable,str(fake)); self.assertIn("fake-provider",p.version)
        finally:
            if old is None: os.environ.pop("DYNOSAI_CURSOR_BIN",None)
            else: os.environ["DYNOSAI_CURSOR_BIN"]=old


if __name__=="__main__": unittest.main()
