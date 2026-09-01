import json
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.managed_runtime import ManagedProviderRuntime
from dynosai_flow.runtime_audit import audit_managed_runtime
from dynosai_flow.version import __version__


class StrictManagedRuntime094Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        self.project = self.tmp / "project"
        self.project.mkdir()
        AgentConfiguration(self.project).init()

    def test_version_is_094(self):
        self.assertEqual(__version__, "0.19.0")

    def test_codex_strict_config_disables_provider_extensions(self):
        effective = AgentConfiguration(self.project).resolve(provider="codex", activity="discovery")
        runtime = ManagedProviderRuntime(self.project).prepare("codex", activity="discovery", effective=effective)
        home = Path(runtime.env["CODEX_HOME"])
        config = (home / "config.toml").read_text(encoding="utf-8")
        self.assertIn("features.plugins = false", config)
        self.assertIn("features.apps = false", config)
        self.assertIn("features.recommended_plugins = false", config)
        self.assertIn("project_doc_max_bytes = 0", config)
        self.assertIn("[skills]", config)
        self.assertIn("include_instructions = false", config)
        self.assertIn("[skills.bundled]", config)
        self.assertIn("enabled = false", config)
        self.assertEqual(config.count("[mcp_servers."), 1)
        self.assertIn("[mcp_servers.dynosai]", config)
        self.assertEqual(runtime.provider_extensions_policy, "deny")
        self.assertTrue(runtime.strict_managed_runtime)

    def test_managed_prompt_contains_dynosai_skill_body_and_authority(self):
        effective = AgentConfiguration(self.project).resolve(provider="codex", activity="discovery")
        prompt = ManagedProviderRuntime.authoritative_prompt(effective, "Do the task")
        self.assertIn("DynosAI strict managed-session authority", prompt)
        self.assertIn("Ignore provider/user plugin, skill, rule, MCP", prompt)
        self.assertIn("agent_config returned by DynosAI MCP is authoritative", prompt)
        for skill in effective.get("skills") or []:
            self.assertIn(str(skill["id"]), prompt)
            self.assertIn(str(skill["body"]), prompt)
        self.assertTrue(prompt.rstrip().endswith("Do the task"))

    def test_runtime_manifest_records_strict_authority(self):
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="implementation")
        manifest = json.loads((Path(runtime.root) / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["external_defaults_policy"], "ignore")
        self.assertEqual(manifest["provider_extensions_policy"], "deny")
        self.assertTrue(manifest["strict_managed_runtime"])
        self.assertEqual(manifest["instruction_authority"], "dynosai")
        self.assertIn("prompt_capsule", manifest["instruction_delivery"])
        self.assertIn("mcp_dynamic", manifest["instruction_delivery"])

    def test_clean_cursor_runtime_audit_passes(self):
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="discovery")
        logs = self.tmp / "logs"
        logs.mkdir()
        (logs / "provider-stream.jsonl").write_text('{"type":"system","model":"Cursor"}\n', encoding="utf-8")
        audit = audit_managed_runtime("cursor", runtime.to_dict(), logs)
        self.assertTrue(audit["config_authority_verified"])
        self.assertTrue(audit["zero_provider_extension_artifacts"])
        self.assertTrue(audit["zero_external_skill_invocations"])
        self.assertTrue(audit["zero_external_plugin_invocations"])
        self.assertTrue(audit["zero_external_mcp_invocations"])
        self.assertTrue(audit["strict_managed_runtime_verified"])

    def test_codex_provider_artifacts_are_detected(self):
        runtime = ManagedProviderRuntime(self.project).prepare("codex", activity="discovery")
        home = Path(runtime.env["CODEX_HOME"])
        system_skill = home / "skills" / ".system" / "imagegen" / "SKILL.md"
        system_skill.parent.mkdir(parents=True, exist_ok=True)
        system_skill.write_text("external", encoding="utf-8")
        plugin_skill = home / "plugins" / "cache" / "remote" / "p" / "skills" / "x" / "SKILL.md"
        plugin_skill.parent.mkdir(parents=True, exist_ok=True)
        plugin_skill.write_text("external", encoding="utf-8")
        logs = self.tmp / "logs2"
        logs.mkdir()
        audit = audit_managed_runtime("codex", runtime.to_dict(), logs)
        self.assertFalse(audit["zero_provider_extension_artifacts"])
        self.assertGreaterEqual(audit["provider_extension_artifacts"], 2)
        self.assertFalse(audit["strict_managed_runtime_verified"])

    def test_external_plugin_and_mcp_invocations_are_detected(self):
        runtime = ManagedProviderRuntime(self.project).prepare("codex", activity="discovery")
        logs = self.tmp / "logs3"
        logs.mkdir()
        (logs / "codex-app-server.jsonl").write_text(
            '{"pluginId":"external-plugin","mcpServerName":"evil-server"}\n',
            encoding="utf-8",
        )
        audit = audit_managed_runtime("codex", runtime.to_dict(), logs)
        self.assertEqual(audit["external_plugin_invocations"], ["external-plugin"])
        self.assertEqual(audit["external_mcp_invocations"], ["evil-server"])
        self.assertFalse(audit["zero_external_plugin_invocations"])
        self.assertFalse(audit["zero_external_mcp_invocations"])
        self.assertFalse(audit["strict_managed_runtime_verified"])

    def test_external_system_skill_invocation_is_detected(self):
        runtime = ManagedProviderRuntime(self.project).prepare("codex", activity="discovery")
        home = Path(runtime.env["CODEX_HOME"])
        skill = home / "skills" / ".system" / "imagegen" / "SKILL.md"
        skill.parent.mkdir(parents=True, exist_ok=True)
        skill.write_text("external", encoding="utf-8")
        logs = self.tmp / "logs4"
        logs.mkdir()
        (logs / "codex-app-server.jsonl").write_text(
            '{"message":"loading skill imagegen from skills/.system/imagegen/SKILL.md"}\n',
            encoding="utf-8",
        )
        audit = audit_managed_runtime("codex", runtime.to_dict(), logs)
        self.assertIn("imagegen", audit["external_skill_invocations"])
        self.assertFalse(audit["zero_external_skill_invocations"])

    def test_only_auth_can_bridge_from_client_home(self):
        fake_home = self.tmp / "home"
        (fake_home / ".codex").mkdir(parents=True)
        (fake_home / ".codex" / "auth.json").write_text('{"token":"secret"}', encoding="utf-8")
        (fake_home / ".codex" / "config.toml").write_text('[mcp_servers.evil]\ncommand="evil"\n', encoding="utf-8")
        runtime = ManagedProviderRuntime(self.project).prepare("codex", auth_home=fake_home)
        home = Path(runtime.env["CODEX_HOME"])
        self.assertTrue((home / "auth.json").is_symlink())
        self.assertNotIn("evil", (home / "config.toml").read_text(encoding="utf-8"))

    def test_strict_environment_flags_are_exported(self):
        for provider in ("cursor", "codex"):
            runtime = ManagedProviderRuntime(self.project).prepare(provider, clean=True)
            self.assertEqual(runtime.env["DYNOSAI_EXTERNAL_DEFAULTS_POLICY"], "ignore")
            self.assertEqual(runtime.env["DYNOSAI_PROVIDER_EXTENSIONS_POLICY"], "deny")
            self.assertEqual(runtime.env["DYNOSAI_STRICT_MANAGED_RUNTIME"], "1")


if __name__ == "__main__":
    unittest.main()
