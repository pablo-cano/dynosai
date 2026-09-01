from __future__ import annotations

# Historical diagnostic suite. Excluded from the stable release gate
# (`python -m pytest`) unless DYNOSAI_HISTORICAL_TESTS=1.

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.cli import demo_plan, demo_spec, open_agent
from dynosai_flow.engine import DynosAI
from dynosai_flow.mcp import discover_project_root


class RuntimeV05Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="dynosai-v05-runtime-"))
        self.home=Path(tempfile.mkdtemp(prefix="dynosai-v05-home-"))
        self.old=os.environ.get("DYNOSAI_USER_HOME"); os.environ["DYNOSAI_USER_HOME"]=str(self.home)
        self.addCleanup(lambda: shutil.rmtree(self.tmp,ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.home,ignore_errors=True))
        self.addCleanup(lambda: os.environ.pop("DYNOSAI_USER_HOME",None) if self.old is None else os.environ.__setitem__("DYNOSAI_USER_HOME",self.old))
        self.addCleanup(lambda: shutil.rmtree(self.tmp.parent/".dynosai-worktrees"/self.tmp.name,ignore_errors=True))

    def init(self):
        e=DynosAI(self.tmp); e.initialize("Runtime 0.5", "python", "true", None); return e

    def ready(self,e:DynosAI):
        w=e.start("Crear CLI Fibonacci"); wid=w["id"]; e.continue_work(wid); e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec"); e.continue_work(wid); e.submit_plan(wid,demo_plan(),"agent"); e.review(wid,"plan"); return wid

    def test_cursor_connection_uses_global_mcp_and_versioned_global_config(self):
        e=self.init(); e.agents.connect("cursor")
        gmcp=json.loads((self.home/".cursor"/"mcp.json").read_text())
        self.assertIn("dynosai",gmcp["mcpServers"]); self.assertEqual(gmcp["mcpServers"]["dynosai"]["env"]["DYNOSAI_RUNTIME_BROKER"],"1")
        gcli=json.loads((self.home/".cursor"/"cli-config.json").read_text())
        self.assertNotIn("dynosai",gcli); self.assertIn("Shell(*)",gcli["permissions"]["deny"]); self.assertIn("Mcp(dynosai:*)",gcli["permissions"]["allow"]); self.assertEqual(e.runtime.status()["connections"][0]["config_version"],1)
        local=self.tmp/".cursor"/"mcp.json"
        self.assertFalse(local.exists() and "dynosai" in json.loads(local.read_text()).get("mcpServers",{}))

    def test_cursor_cli_configs_contain_only_cursor_schema_no_dynosai_metadata(self):
        e=self.init(); e.agents.connect("cursor")
        local=json.loads((self.tmp/".cursor"/"cli.json").read_text())
        global_cli=json.loads((self.home/".cursor"/"cli-config.json").read_text())
        self.assertNotIn("dynosai",local)
        self.assertNotIn("dynosai",global_cli)
        self.assertEqual(set(local),{"permissions"})
        self.assertEqual(set(global_cli),{"permissions"})
        manifest=json.loads((self.home/".dynosai"/"runtime"/"cursor-managed-permissions.json").read_text())
        self.assertEqual(manifest["configVersion"],1)
        self.assertIn(str((self.tmp/".cursor"/"cli.json").resolve()),manifest["files"])

    def test_cursor_connect_migrates_invalid_050_metadata_out_of_cursor_json(self):
        e=self.init()
        local=self.tmp/".cursor"/"cli.json"; local.parent.mkdir(parents=True,exist_ok=True); local.write_text(json.dumps({"dynosai":{"configVersion":1},"permissions":{"allow":[],"deny":[]}}))
        global_cli=self.home/".cursor"/"cli-config.json"; global_cli.parent.mkdir(parents=True,exist_ok=True); global_cli.write_text(json.dumps({"dynosai":{"configVersion":1},"permissions":{"allow":[],"deny":[]}}))
        e.agents.connect("cursor")
        self.assertNotIn("dynosai",json.loads(local.read_text()))
        self.assertNotIn("dynosai",json.loads(global_cli.read_text()))

    def test_clean_bootstrap_cursor_schema_probe_matches_real_cli_contract(self):
        e=self.init()
        fakebin=self.tmp.parent/(self.tmp.name+"-cursor-schema-bin"); fakebin.mkdir(); self.addCleanup(lambda: shutil.rmtree(fakebin,ignore_errors=True))
        script=fakebin/"cursor-agent"
        script.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
home=Path(os.environ['DYNOSAI_USER_HOME'])
local=Path.cwd()/'.cursor'/'cli.json'
global_cli=home/'.cursor'/'cli-config.json'
def validate(path):
    if not path.exists(): return
    data=json.loads(path.read_text())
    allowed={'permissions'}
    bad=set(data)-allowed
    if bad:
        print(f\"Invalid project config at {path}: schema validation failed. unrecognized_keys={sorted(bad)}\")
        sys.exit(19)
for path in (global_cli,local): validate(path)
args=sys.argv[1:]
if args==['status']:
    print('✓ Logged in as clean-bootstrap@example.test'); sys.exit(0)
if args[:2]==['mcp','enable']:
    print('✓ Enabled and approved MCP server: dynosai'); sys.exit(0)
if args==['mcp','list']:
    cfg=json.loads((home/'.cursor'/'mcp.json').read_text())
    print('dynosai: ready' if 'dynosai' in cfg.get('mcpServers',{}) else 'dynosai: missing'); sys.exit(0)
if args==['mcp','list-tools','dynosai']:
    print('Tools for dynosai (24):')
    [print(f'- dynosai_tool_{i} ()') for i in range(24)]; sys.exit(0)
print('2026.08-test'); sys.exit(0)
""",encoding='utf-8'); script.chmod(0o755)
        old=os.environ.get('PATH',''); os.environ['PATH']=str(fakebin)+os.pathsep+old
        try:
            # Re-connect after init must remain schema-clean and the real-style probes must pass.
            e.agents.connect('cursor')
            from dynosai_flow.cli import _cursor_health
            health=_cursor_health()
        finally:
            os.environ['PATH']=old
        self.assertTrue(health['ready'],health)
        self.assertTrue(health['authenticated'],health)
        self.assertTrue(health['mcp'],health)
        self.assertEqual(health['tools'],24)
        self.assertNotIn('dynosai',json.loads((self.tmp/'.cursor'/'cli.json').read_text()))
        self.assertNotIn('dynosai',json.loads((self.home/'.cursor'/'cli-config.json').read_text()))

    def test_runtime_broker_discovers_project_from_generated_worktree(self):
        e=self.init(); wid=self.ready(e); prep=e.continue_work(wid); wt=Path(prep["worktree"])
        session=e.agents.create_session("cursor",wid,prep["run_id"],wt)
        old_provider=os.environ.get("DYNOSAI_AGENT_PROVIDER"); old_cwd=Path.cwd(); os.environ["DYNOSAI_AGENT_PROVIDER"]="cursor"
        try:
            os.chdir(wt)
            self.assertEqual(discover_project_root(),self.tmp.resolve())
            resolved=e.runtime.resolve("cursor",wt); self.assertEqual(resolved["session_id"],session["session_id"])
        finally:
            os.chdir(old_cwd)
            if old_provider is None: os.environ.pop("DYNOSAI_AGENT_PROVIDER",None)
            else: os.environ["DYNOSAI_AGENT_PROVIDER"]=old_provider

    def test_cursor_open_is_autonomous_and_starts_inside_worktree_from_ready(self):
        e=self.init(); wid=self.ready(e)
        fake=self.tmp.parent/(self.tmp.name+"-cursor"); fake.write_text("#!/bin/sh\nexit 0\n"); fake.chmod(0o755); self.addCleanup(lambda: fake.unlink(missing_ok=True))
        original_which=shutil.which
        with patch("dynosai_flow.cli.shutil.which", side_effect=lambda name: str(fake) if name=="cursor-agent" else original_which(name)):
            result=open_agent(e,"cursor",False)
        self.assertEqual(result["exit_code"],0); self.assertEqual(e.get_work(wid)["state"],"implementing"); self.assertNotEqual(Path(result["cwd"]),self.tmp)
        # --force removes repetitive Cursor command/MCP confirmations; DynosAI still verifies scope/Git independently.
        # We assert the launch shape directly via dry-run after the worktree exists.
        with patch("dynosai_flow.cli.shutil.which", return_value=str(fake)):
            dry=open_agent(e,"cursor",True)
        self.assertIn("--force",dry["command"])

    def test_diagnostic_bundle_is_sanitized_and_contains_no_source(self):
        e=self.init(); (self.tmp/".env").write_text("SECRET=never-include\n"); (self.tmp/"private.py").write_text("TOKEN='secret-source'\n")
        result=e.diagnostic_bundle(); z=Path(result["bundle"]); self.assertTrue(z.exists()); self.assertTrue(result["sanitized"])
        import zipfile
        with zipfile.ZipFile(z) as archive:
            names=archive.namelist(); self.assertNotIn(".env",names); self.assertNotIn("private.py",names)
            combined="".join(archive.read(n).decode("utf-8",errors="ignore") for n in names)
            self.assertNotIn("never-include",combined); self.assertNotIn("secret-source",combined)

    def test_deep_doctor_proves_mcp_from_temporary_git_worktree_without_cursor_requirement(self):
        e=self.init(); d=e.deep_doctor()
        self.assertTrue(d["checks"]["runtime_broker"],d)
        self.assertTrue(d["checks"]["mcp_root_handshake"],d)
        self.assertTrue(d["checks"]["mcp_worktree_handshake"],d)


if __name__ == "__main__": unittest.main()
