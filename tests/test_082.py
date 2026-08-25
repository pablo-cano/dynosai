import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.cli import main
from dynosai_flow.providers import MachineProviderSetup
from dynosai_flow.semantic import default_cache_dir


class MachineDoctor082Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.tmp=Path(self.td.name); self.home=self.tmp/'home'; self.home.mkdir()
        self.bin=self.tmp/'bin'; self.bin.mkdir()
        self.venv=self.tmp/'venv'; self.venv.mkdir()
        self.python=Path(os.sys.executable).absolute()

    def _exe(self,name,body):
        p=self.bin/name; p.write_text('#!/bin/sh\n'+body+'\n',encoding='utf-8'); p.chmod(p.stat().st_mode|stat.S_IXUSR); return str(p)

    def _configs(self):
        c=self.home/'.cursor'/'mcp.json'; c.parent.mkdir(parents=True); c.write_text(json.dumps({'mcpServers':{'dynosai':{'command':str(self.python),'args':['-m','dynosai_flow.mcp']}}}))
        x=self.home/'.codex'/'config.toml'; x.parent.mkdir(parents=True); x.write_text('[mcp_servers.dynosai]\ncommand = '+json.dumps(str(self.python))+'\nargs = ["-m", "dynosai_flow.mcp"]\nenabled = true\n')

    def test_machine_doctor_does_not_require_project_database(self):
        self._configs()
        cursor=self._exe('cursor-agent','echo 2026.08.11')
        codex=self._exe('codex','echo codex-cli 0.147.0')
        marker=self.tmp/'models'/'.dynosai-model.json'; marker.parent.mkdir(); marker.write_text('{}')
        with patch('dynosai_flow.providers.shutil.which',side_effect=lambda n: cursor if n=='cursor-agent' else codex if n=='codex' else None), \
             patch('dynosai_flow.semantic.default_cache_dir',return_value=marker.parent):
            d=MachineProviderSetup(self.home).doctor()
        self.assertEqual(d['scope'],'machine'); self.assertFalse(d['project_initialized']); self.assertTrue(d['ready'],d)

    def test_machine_deep_doctor_checks_live_auth_and_mcp(self):
        self._configs()
        cursor=self._exe('cursor-agent','case "$1" in status) echo "Logged in";; --version) echo "2026.08.11";; mcp) if [ "$2" = "list-tools" ]; then i=0; while [ $i -lt 20 ]; do echo dynosai_tool_$i; i=$((i+1)); done; else echo "dynosai ready"; fi;; esac')
        codex=self._exe('codex','if [ "$1" = "login" ]; then echo "Logged in using ChatGPT"; elif [ "$1" = "mcp" ]; then echo "dynosai enabled"; else echo "codex-cli 0.147.0"; fi')
        marker=self.tmp/'models'/'.dynosai-model.json'; marker.parent.mkdir(); marker.write_text('{}')
        with patch('dynosai_flow.providers.shutil.which',side_effect=lambda n: cursor if n=='cursor-agent' else codex if n=='codex' else None), \
             patch('dynosai_flow.semantic.default_cache_dir',return_value=marker.parent):
            d=MachineProviderSetup(self.home).doctor(deep=True)
        self.assertTrue(d['full_ready'],d)
        self.assertTrue(d['checks']['cursor_authenticated']); self.assertTrue(d['checks']['codex_authenticated'])
        self.assertTrue(d['checks']['cursor_tools_visible']); self.assertTrue(d['checks']['codex_mcp_visible'])

    def test_cli_global_doctor_no_longer_opens_project_sqlite(self):
        cwd=os.getcwd(); os.chdir(self.tmp)
        try:
            with patch('dynosai_flow.cli.MachineProviderSetup.doctor',return_value={'scope':'machine','ready':True}) as md:
                rc=main(['doctor','--deep'])
            self.assertEqual(rc,0); md.assert_called_once_with(deep=True)
            self.assertFalse((self.tmp/'.dynosai'/'knowledge.db').exists())
        finally: os.chdir(cwd)

if __name__=='__main__': unittest.main()
