import json
import stat
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import CodexAppServerDriver, RealProviderAcceptanceSuite
from dynosai_flow.mcp import TOOLS
from dynosai_flow.version import __version__


class Acceptance086Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def _fake_codex(self) -> Path:
        fake = self.tmp / "codex"
        fake.write_text(r'''#!/usr/bin/env python3
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
send({"id":m["id"],"result":{"thread":{"id":"thr"}}})
m=recv(); assert m.get("method")=="turn/start",m
send({"id":m["id"],"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
send({"method":"mcpServer/elicitation/request","id":91,"params":{
  "threadId":"thr","turnId":"turn","serverName":"dynosai","mode":"form",
  "message":"Specification ready. Review before continuing.",
  "requestedSchema":{"type":"object","properties":{
    "decision":{"type":"string","enum":["approve","request_changes","cancel"]},
    "comments":{"type":"string","default":""}},"required":["decision"]}
}})
r=recv(); assert r.get("id")==91,r
assert r.get("result",{}).get("action")=="accept",r
assert r.get("result",{}).get("content",{}).get("decision")=="approve",r
send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return fake

    def test_codex_wire_elicitation_is_normalized_into_acceptance_trace(self):
        fake = self._fake_codex()
        project = self.tmp / "project"; project.mkdir()
        logs = self.tmp / "logs"
        result = CodexAppServerDriver(str(fake), 20).run(project, "prompt", logs, interaction_mode="auto")
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["elicitation_auto_responses"], 1, result)
        wire = [json.loads(x) for x in (logs / "codex-app-server.jsonl").read_text().splitlines()]
        normalized = [json.loads(x) for x in (logs / "elicitation-trace.jsonl").read_text().splitlines()]
        self.assertEqual(sum(x.get("kind") == "mcp_server_elicitation" for x in wire), 1)
        self.assertEqual(len(normalized), 1, normalized)
        self.assertEqual(normalized[0]["kind"], "mcp_server_elicitation")
        self.assertEqual(normalized[0]["content"]["decision"], "approve")

    def test_run_validation_advertises_optional_scoped_work_id(self):
        tool = next(x for x in TOOLS if x["name"] == "dynosai_run_validation")
        schema = tool["inputSchema"]
        self.assertEqual(schema["properties"]["work_id"], {"type": "string"})
        self.assertNotIn("work_id", schema.get("required", []))

    def test_acceptance_artifact_version_is_not_hardcoded(self):
        suite = RealProviderAcceptanceSuite(providers=("cursor",), scenario="fibonacci", configure=False)
        self.assertEqual(__version__, "1.0.0rc6")
        self.assertIn(__version__.replace(".", ""), suite.output.name)


if __name__ == "__main__":
    unittest.main()
