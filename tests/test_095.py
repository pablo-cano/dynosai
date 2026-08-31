import json
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import _strict_zero_metric
from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.managed_runtime import ManagedProviderRuntime
from dynosai_flow.runtime_audit import audit_managed_runtime
from dynosai_flow.version import __version__


class SemanticStrictRuntime095Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        self.project = self.tmp / "project"
        self.project.mkdir()
        AgentConfiguration(self.project).init()

    def _runtime(self, provider: str = "codex"):
        effective = AgentConfiguration(self.project).resolve(provider=provider, activity="discovery")
        return ManagedProviderRuntime(self.project).prepare(provider, activity="discovery", effective=effective)

    def test_version_is_095(self):
        self.assertEqual(__version__, "0.18.0")

    def test_codex_remote_control_server_name_is_not_mcp(self):
        runtime = self._runtime("codex")
        logs = self.tmp / "remote-control"
        logs.mkdir()
        event = {
            "direction": "codex->client",
            "payload": {
                "method": "remoteControl/status/changed",
                "params": {
                    "status": "disabled",
                    "serverName": "KAIN",
                    "installationId": "test-installation",
                },
            },
        }
        (logs / "codex-app-server.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        audit = audit_managed_runtime("codex", runtime.to_dict(), logs)
        self.assertEqual(audit["external_mcp_invocations"], [])
        self.assertEqual(audit["observed_mcp_servers"], [])
        self.assertTrue(audit["zero_external_mcp_invocations"])
        self.assertTrue(audit["strict_managed_runtime_verified"])

    def test_codex_actual_mcp_tool_call_is_semantically_detected(self):
        runtime = self._runtime("codex")
        logs = self.tmp / "codex-mcp"
        logs.mkdir()
        rows = [
            {
                "direction": "codex->client",
                "payload": {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "type": "mcpToolCall",
                            "server": "dynosai",
                            "tool": "dynosai_project",
                            "pluginId": None,
                        }
                    },
                },
            },
            {
                "direction": "codex->client",
                "payload": {
                    "method": "item/started",
                    "params": {
                        "item": {
                            "type": "mcpToolCall",
                            "server": "evil-server",
                            "tool": "read_secret",
                            "pluginId": "evil-plugin",
                        }
                    },
                },
            },
        ]
        (logs / "codex-app-server.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
        audit = audit_managed_runtime("codex", runtime.to_dict(), logs)
        self.assertEqual(audit["observed_mcp_servers"], ["dynosai", "evil-server"])
        self.assertEqual(audit["external_mcp_invocations"], ["evil-server"])
        self.assertEqual(audit["external_plugin_invocations"], ["evil-plugin"])
        self.assertFalse(audit["strict_managed_runtime_verified"])

    def test_codex_mcp_elicitation_server_name_is_semantically_detected(self):
        runtime = self._runtime("codex")
        logs = self.tmp / "codex-elicitation"
        logs.mkdir()
        event = {
            "direction": "codex->client",
            "payload": {
                "method": "mcpServer/elicitation/request",
                "params": {"serverName": "external-server", "message": "approve?"},
            },
        }
        (logs / "codex-app-server.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        audit = audit_managed_runtime("codex", runtime.to_dict(), logs)
        self.assertEqual(audit["external_mcp_invocations"], ["external-server"])
        self.assertFalse(audit["zero_external_mcp_invocations"])

    def test_cursor_mcp_tool_call_is_semantically_detected(self):
        runtime = self._runtime("cursor")
        logs = self.tmp / "cursor-mcp"
        logs.mkdir()
        event = {
            "type": "tool_call",
            "subtype": "completed",
            "tool_call": {
                "mcpToolCall": {
                    "args": {
                        "serverIdentifier": "evil-cursor-mcp",
                        "providerIdentifier": "evil-cursor-mcp",
                        "toolName": "x",
                    }
                }
            },
        }
        (logs / "provider-stream.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
        audit = audit_managed_runtime("cursor", runtime.to_dict(), logs)
        self.assertEqual(audit["external_mcp_invocations"], ["evil-cursor-mcp"])
        self.assertFalse(audit["strict_managed_runtime_verified"])

    def test_zero_metrics_do_not_cascade_from_unrelated_child_failure(self):
        children = [
            {"status": "failed", "mcp_failures": 0, "external_mcp_invocations": 1},
            {"status": "passed", "mcp_failures": 0, "external_mcp_invocations": 0},
        ]
        self.assertTrue(_strict_zero_metric(children, "mcp_failures"))
        self.assertFalse(_strict_zero_metric(children, "external_mcp_invocations"))

    def test_missing_metric_remains_uncertified(self):
        self.assertFalse(_strict_zero_metric([{"status": "passed"}], "mcp_failures"))


if __name__ == "__main__":
    unittest.main()
