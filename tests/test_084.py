import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import CodexAppServerDriver, RealProviderAcceptanceSuite


class Acceptance084Tests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.addCleanup(self.td.cleanup)
        self.tmp=Path(self.td.name)

    def _fake_codex(self) -> Path:
        fake=self.tmp/'codex'
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys

def send(x):
    print(json.dumps(x),flush=True)

def recv():
    line=sys.stdin.readline()
    if not line:
        raise SystemExit(2)
    return json.loads(line)

m=recv()
assert m.get('method')=='initialize',m
caps=m.get('params',{}).get('capabilities',{})
assert caps.get('experimentalApi') is True,caps
assert caps.get('requestAttestation') is False,caps
assert caps.get('mcpServerOpenaiFormElicitation') is True,caps
send({'id':m['id'],'result':{'userAgent':'codex-cli 0.147.0'}})

m=recv(); assert m.get('method')=='initialized',m
m=recv(); assert m.get('method')=='thread/start',m
assert m.get('params',{}).get('sandbox')=='workspace-write',m
send({'id':m['id'],'result':{'thread':{'id':'thr'}}})

m=recv(); assert m.get('method')=='turn/start',m
turn_req=m['id']
send({'id':turn_req,'result':{'turn':{'id':'turn','status':'inProgress','items':[]}}})

# Codex tool-call approval: must not be confused with DynosAI form data.
send({'method':'mcpServer/elicitation/request','id':41,'params':{
  'threadId':'thr','turnId':'turn','serverName':'dynosai','mode':'form',
  '_meta':{'codex_approval_kind':'mcp_tool_call'},
  'message':'Allow the dynosai MCP server to run tool "dynosai_get_next_action"?',
  'requestedSchema':{'type':'object','properties':{}}
}})
m=recv(); assert m.get('id')==41,m
assert m.get('result')=={'action':'accept','content':{}},m

# True server-originated form Elicitation: must carry schema-valid gate content.
send({'method':'mcpServer/elicitation/request','id':42,'params':{
  'threadId':'thr','turnId':'turn','serverName':'dynosai','mode':'form',
  'message':'Specification ready. Review the requirements and acceptance criteria before continuing.',
  'requestedSchema':{'type':'object','properties':{
     'decision':{'type':'string','enum':['approve','request_changes','cancel']},
     'comments':{'type':'string','default':''}},'required':['decision']}
}})
m=recv(); assert m.get('id')==42,m
result=m.get('result',{})
assert result.get('action')=='accept',m
assert result.get('content',{}).get('decision')=='approve',m
assert 'comments' in result.get('content',{}),m

send({'method':'turn/completed','params':{'threadId':'thr','turn':{'id':'turn','status':'completed'}}})
''',encoding='utf-8')
        fake.chmod(fake.stat().st_mode|stat.S_IXUSR)
        return fake

    def test_codex_advertises_form_elicitation_and_distinguishes_tool_approval(self):
        fake=self._fake_codex(); root=self.tmp/'project'; root.mkdir(); logs=self.tmp/'logs'
        result=CodexAppServerDriver(str(fake),20).run(root,'prompt',logs,interaction_mode='auto')
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['sandbox_mode'],'workspace-write')
        self.assertEqual(result['elicitation_auto_responses'],1,result)
        trace=[json.loads(x) for x in (logs/'codex-app-server.jsonl').read_text(encoding='utf-8').splitlines()]
        kinds=[x.get('kind') for x in trace if x.get('event')=='auto-response']
        self.assertIn('mcp_tool_approval',kinds)
        self.assertIn('mcp_server_elicitation',kinds)

    def test_default_acceptance_workspace_is_outside_dynosai_control_plane(self):
        suite=RealProviderAcceptanceSuite(providers=('cursor',),scenario='fibonacci',configure=False,keep=True)
        parts=suite.base.parts
        self.assertNotIn('.dynosai',parts,suite.base)
        self.assertIn('dynosai-acceptance-runs',parts,suite.base)


if __name__=='__main__': unittest.main()
