import json
import stat
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import CodexAppServerDriver


class Acceptance085Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def _fake_codex(self) -> Path:
        fake = self.tmp / "codex"
        script = '''#!/usr/bin/env python3
import json,sys

def send(x): print(json.dumps(x), flush=True)
def recv():
    line=sys.stdin.readline()
    if not line: raise SystemExit(2)
    return json.loads(line)

m=recv(); assert m.get("method")=="initialize",m
send({"id":m["id"],"result":{"userAgent":"codex-cli 0.147.0"}})
m=recv(); assert m.get("method")=="initialized",m
m=recv(); assert m.get("method")=="thread/start",m
policy=m.get("params",{}).get("approvalPolicy")
assert isinstance(policy,dict),policy
g=policy.get("granular",{})
assert g=={
  "sandbox_approval":False,
  "rules":False,
  "skill_approval":False,
  "request_permissions":False,
  "mcp_elicitations":True,
},g
assert m.get("params",{}).get("sandbox")=="workspace-write",m
send({"id":m["id"],"result":{"thread":{"id":"thr"}}})
m=recv(); assert m.get("method")=="turn/start",m
send({"id":m["id"],"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
send({"method":"mcpServer/elicitation/request","id":"codex-mcp-elicitation-1","params":{
  "threadId":"thr","turnId":"turn","serverName":"dynosai","mode":"form",
  "message":"Specification ready. Review the requirements and acceptance criteria before continuing.",
  "requestedSchema":{"type":"object","properties":{
    "decision":{"type":"string","enum":["approve","request_changes","cancel"]},
    "comments":{"type":"string","default":""}},"required":["decision"]}
}})
r=recv(); assert r.get("id")=="codex-mcp-elicitation-1",r
assert r.get("result",{}).get("action")=="accept",r
assert r.get("result",{}).get("content",{}).get("decision")=="approve",r
send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
'''
        fake.write_text(script, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return fake

    def test_codex_uses_elicitation_only_granular_policy(self):
        fake = self._fake_codex()
        project = self.tmp / "project"
        project.mkdir()
        logs = self.tmp / "logs"
        result = CodexAppServerDriver(str(fake), 20).run(project, "prompt", logs, interaction_mode="auto")
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["elicitation_auto_responses"], 1, result)
        trace = [json.loads(x) for x in (logs / "codex-app-server.jsonl").read_text().splitlines()]
        thread = [x["payload"] for x in trace if x.get("direction") == "client->codex" and x.get("payload", {}).get("method") == "thread/start"]
        self.assertEqual(len(thread), 1, thread)
        granular = thread[0]["params"]["approvalPolicy"]["granular"]
        self.assertTrue(granular["mcp_elicitations"])
        self.assertFalse(granular["sandbox_approval"])
        self.assertFalse(granular["rules"])
        self.assertFalse(granular["skill_approval"])
        self.assertFalse(granular["request_permissions"])


if __name__ == "__main__":
    unittest.main()
