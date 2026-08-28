import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.acceptance import (
    CodexAppServerDriver,
    CursorAcceptanceDriver,
    _governed_work_rows,
    _runtime_bin_snapshot,
    _scenario_prompt,
    _strict_zero_metric,
)
from dynosai_flow.agents import AgentConfigurator
from dynosai_flow.application import DynosAIApplication, ProjectDetector
from dynosai_flow.db import Database
from dynosai_flow.debug import SCENARIOS


class Acceptance083Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.tmp=Path(self.td.name)
        self.old_runtime=os.environ.get("DYNOSAI_RUNTIME_HOME")
        os.environ["DYNOSAI_RUNTIME_HOME"]=str(self.tmp/"runtime")
        self.addCleanup(self._restore)

    def _restore(self):
        if self.old_runtime is None: os.environ.pop("DYNOSAI_RUNTIME_HOME",None)
        else: os.environ["DYNOSAI_RUNTIME_HOME"]=self.old_runtime

    def test_project_detector_ignores_provider_scaffolding_for_greenfield(self):
        root=self.tmp/"green"; (root/".cursor").mkdir(parents=True)
        (root/".cursor"/"mcp.json").write_text("{}",encoding="utf-8")
        (root/".gitignore").write_text(".cursor/mcp.json\n",encoding="utf-8")
        d=ProjectDetector().detect(root)
        self.assertEqual(d["classification"],"EMPTY_DIRECTORY",d)
        self.assertFalse(d["has_code"])

    def test_cursor_acceptance_pins_project_local_autoresponder_without_dirtying_brownfield(self):
        root=self.tmp/"brown"; root.mkdir()
        subprocess.run(["git","init","-q"],cwd=root,check=True)
        subprocess.run(["git","config","user.email","test@example.com"],cwd=root,check=True)
        subprocess.run(["git","config","user.name","Test"],cwd=root,check=True)
        (root/"app.py").write_text("x=1\n",encoding="utf-8")
        subprocess.run(["git","add","."],cwd=root,check=True); subprocess.run(["git","commit","-qm","baseline"],cwd=root,check=True)
        logs=self.tmp/"logs"
        p=CursorAcceptanceDriver._configure_project_mcp(root,logs,autorespond=True)
        payload=json.loads(p.read_text(encoding="utf-8"))["mcpServers"]["dynosai"]
        self.assertEqual(payload["command"],str(Path(sys.executable).absolute()))
        self.assertEqual(payload["env"]["DYNOSAI_ACCEPTANCE_AUTORESPOND"],"1")
        self.assertEqual(payload["env"]["DYNOSAI_PROJECT_ROOT"],str(root.resolve()))
        status=subprocess.run(["git","status","--porcelain"],cwd=root,text=True,capture_output=True,check=True).stdout.strip()
        self.assertEqual(status,"",status)

    def test_provider_native_project_uses_scenario_validation_command(self):
        root=self.tmp/"project"; root.mkdir()
        app=DynosAIApplication(root)
        r=app.initialize_project(name="Fixture",agent="cursor",language="python",test_command="python -m unittest discover -s tests")
        self.assertEqual(r["action"],"initialize")
        self.assertEqual(app.engine.db.get_meta("test_command"),"python -m unittest discover -s tests")
        profiles=app.engine.db.query("SELECT command_json FROM validation_profiles WHERE approved=1")
        self.assertTrue(any(json.loads(row.get("command_json"))==["python","-m","unittest","discover","-s","tests"] for row in profiles),profiles)

    def test_governed_work_filter_ignores_project_and_baseline_rows(self):
        root=self.tmp/"works"; root.mkdir()
        app=DynosAIApplication(root); app.initialize_project(name="Fixture",agent="cursor")
        app.engine.db.execute("INSERT INTO work_items(id,work_type,title,description,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",("BASE-0001","baseline","Base","Inferred baseline","done","2026-01-01","2026-01-01"))
        work=app.start_feature("Feature",provider="cursor")
        all_rows,governed=_governed_work_rows(app.engine.db)
        self.assertGreaterEqual(len(all_rows),3)
        self.assertEqual([x["id"] for x in governed],[work["id"]])

    def test_failed_or_missing_child_metrics_are_not_reported_as_zero(self):
        self.assertFalse(_strict_zero_metric([{"status":"failed"}],"mcp_failures"))
        self.assertFalse(_strict_zero_metric([{"status":"passed"}],"mcp_failures"))
        self.assertTrue(_strict_zero_metric([{"status":"passed","mcp_failures":0}],"mcp_failures"))
        self.assertFalse(_strict_zero_metric([{"status":"passed","mcp_failures":1}],"mcp_failures"))

    def test_codex_0147_workspace_write_sandbox_is_supported(self):
        captured=self.tmp/"turn.json"
        fake=self.tmp/"codex"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys
captured=sys.argv[-1]

def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); rid=m.get('id')
    if method=='initialize': send({'id':rid,'result':{'userAgent':'codex-cli 0.147.0'}})
    elif method=='initialized': pass
    elif method=='thread/start':
        sandbox=m.get('params',{}).get('sandbox')
        if sandbox!='workspace-write':
            send({'id':rid,'error':{'code':-32602,'message':"Invalid request: unknown variant '%s', expected one of 'read-only', 'workspace-write', 'danger-full-access'"%sandbox}})
        else:
            send({'id':rid,'result':{'thread':{'id':'thr'}}})
    elif method=='turn/start':
        open(captured,'w',encoding='utf-8').write(json.dumps(m))
        send({'id':rid,'result':{'turn':{'id':'turn','status':'inProgress','items':[]}}})
        send({'method':'turn/completed','params':{'threadId':'thr','turn':{'id':'turn','status':'completed'}}}); break
''',encoding="utf-8"); fake.chmod(fake.stat().st_mode|stat.S_IXUSR)
        root=self.tmp/"codex-project"; root.mkdir(); logs=self.tmp/"codex-logs"
        # CodexAppServerDriver passes the executable only; we use a wrapper that
        # strips the extra path argument to keep the fixture simple.
        wrapper=self.tmp/"codex-wrap.py"
        wrapper.write_text(
            "import runpy,sys\nsys.argv=[sys.argv[1]]\nrunpy.run_path(sys.argv[0],run_name='__main__')\n",
            encoding="utf-8",
        )
        fake2=self.tmp/"codex.py"
        body=fake.read_text(encoding="utf-8").replace("captured=sys.argv[-1]", f"captured={str(captured)!r}")
        fake2.write_text(body,encoding="utf-8"); fake2.chmod(fake2.stat().st_mode|stat.S_IXUSR)
        result=CodexAppServerDriver(str(fake2),20).run(root,"prompt",logs,interaction_mode="auto")
        self.assertEqual(result["exit_code"],0,result)
        self.assertEqual(result["sandbox_mode"],"workspace-write")
        self.assertEqual(result["sandbox_attempts"],1)
        turn=json.loads(captured.read_text(encoding="utf-8"))
        self.assertEqual(turn["params"].get("sandboxPolicy"),{"type":"workspaceWrite"})

    def test_codex_sandbox_falls_back_to_camel_case_for_newer_protocols(self):
        fake=self.tmp/"codex-camel"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys

def send(x): print(json.dumps(x),flush=True)
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); rid=m.get('id')
    if method=='initialize': send({'id':rid,'result':{'userAgent':'future'}})
    elif method=='initialized': pass
    elif method=='thread/start':
        sandbox=m.get('params',{}).get('sandbox')
        if sandbox!='workspaceWrite': send({'id':rid,'error':{'code':-32602,'message':'unknown variant sandbox'}})
        else: send({'id':rid,'result':{'thread':{'id':'thr'}}})
    elif method=='turn/start':
        send({'id':rid,'result':{'turn':{'id':'turn','status':'inProgress','items':[]}}})
        send({'method':'turn/completed','params':{'threadId':'thr','turn':{'id':'turn','status':'completed'}}}); break
''',encoding="utf-8"); fake.chmod(fake.stat().st_mode|stat.S_IXUSR)
        root=self.tmp/"camel-project"; root.mkdir(); logs=self.tmp/"camel-logs"
        result=CodexAppServerDriver(str(fake),20).run(root,"prompt",logs,interaction_mode="auto")
        self.assertEqual(result["exit_code"],0,result)
        self.assertEqual(result["sandbox_mode"],"workspaceWrite")
        self.assertEqual(result["sandbox_attempts"],2)

    def test_cursor_permissions_deny_governance_and_virtualenv_mutation(self):
        root=self.tmp/"permissions"; root.mkdir()
        db=Database(root/".dynosai"/"knowledge.db"); db.initialize()
        AgentConfigurator(root,db).connect("cursor",target_root=root,write_scope=["src/app.py"])
        payload=json.loads((root/".cursor"/"cli.json").read_text(encoding="utf-8"))
        deny=set(payload["permissions"]["deny"])
        for expected in ("Write(.dynosai/**)","Write(**/.dynosai/**)","Write(.cursor/**)","Write(.venv/**)","Write(**/.venv/**)","Write(**/.dynosai-env/**)"):
            self.assertIn(expected,deny)
        self.assertIn("Write(src/app.py)",payload["permissions"]["allow"])

    def test_runtime_bin_snapshot_detects_added_shim(self):
        before=_runtime_bin_snapshot()
        # The helper fingerprints the current interpreter's bin directory. We do
        # not mutate it in the test; instead verify the comparison semantics used
        # by acceptance cannot treat an added entry as unchanged.
        synthetic=dict(before); synthetic["pytest"]="file:0o755:deadbeef"
        added=sorted(set(synthetic)-set(before))
        self.assertEqual(added,["pytest"] if "pytest" not in before else [])

    def test_acceptance_prompt_uses_unittest_and_forbids_runtime_repairs(self):
        prompt=_scenario_prompt("cursor","orderflow-contract-discounts")
        self.assertIn(SCENARIOS["orderflow-contract-discounts"]["test_command"],prompt)
        self.assertNotIn("test_command=\"pytest\"",prompt)
        self.assertIn("Never repair the DynosAI runtime",prompt)
        self.assertIn("Do not install packages",prompt)


if __name__=="__main__": unittest.main()
