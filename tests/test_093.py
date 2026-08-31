import json
import stat
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.agent_config import AgentConfiguration
from dynosai_flow.acceptance import CursorAcceptanceDriver
from dynosai_flow.cli import parser
from dynosai_flow.managed_runtime import ManagedProviderRuntime, tool_profile_for_activity
from dynosai_flow.mcp import MCP_TOOL_PROFILES, TOOLS, advertised_tools
from dynosai_flow.model_routing import ModelRoute
from dynosai_flow.version import __version__


class ManagedAgentRuntime093Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        self.project = self.tmp / "project"
        self.project.mkdir()
        AgentConfiguration(self.project).init()

    def test_version_is_093(self):
        self.assertEqual(__version__, "0.15.0")

    def test_tool_profiles_are_progressive_and_smaller_than_legacy_surface(self):
        names = {x["name"] for x in TOOLS}
        self.assertGreater(len(names), len(MCP_TOOL_PROFILES["design"]))
        self.assertGreater(len(names), len(MCP_TOOL_PROFILES["delivery"]))
        self.assertNotIn("dynosai_stats", MCP_TOOL_PROFILES["design"])
        self.assertNotIn("dynosai_board", MCP_TOOL_PROFILES["delivery"])
        self.assertEqual(tool_profile_for_activity("planning"), "design")
        self.assertEqual(tool_profile_for_activity("implementation"), "delivery")

    def test_cursor_runtime_ignores_external_user_configuration(self):
        fake_home = self.tmp / "home"
        (fake_home / ".cursor").mkdir(parents=True)
        (fake_home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers":{"evil":{"command":"evil"}}}), encoding="utf-8")
        (fake_home / ".cursor" / "skills" / "evil").mkdir(parents=True)
        (fake_home / ".cursor" / "skills" / "evil" / "SKILL.md").write_text("evil", encoding="utf-8")
        effective = AgentConfiguration(self.project).resolve(provider="cursor", activity="implementation")
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="implementation", effective=effective)
        cfg = Path(runtime.env["CURSOR_CONFIG_DIR"])
        mcp = json.loads((cfg / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(set(mcp["mcpServers"]), {"dynosai"})
        self.assertFalse((cfg / "skills" / "evil").exists())
        self.assertEqual(runtime.external_defaults_policy, "ignore")
        self.assertEqual(runtime.tool_profile, "delivery")

    def test_codex_runtime_ignores_settings_and_bridges_only_auth(self):
        fake_home = self.tmp / "home"
        (fake_home / ".codex").mkdir(parents=True)
        (fake_home / ".codex" / "config.toml").write_text('[mcp_servers.evil]\ncommand="evil"\n', encoding="utf-8")
        auth = fake_home / ".codex" / "auth.json"
        auth.write_text('{"token":"do-not-copy"}', encoding="utf-8")
        effective = AgentConfiguration(self.project).resolve(provider="codex", activity="planning")
        runtime = ManagedProviderRuntime(self.project).prepare("codex", activity="planning", effective=effective, auth_home=fake_home)
        home = Path(runtime.env["CODEX_HOME"])
        cfg = (home / "config.toml").read_text(encoding="utf-8")
        self.assertIn("project_doc_max_bytes = 0", cfg)
        self.assertIn("[mcp_servers.dynosai]", cfg)
        self.assertNotIn("mcp_servers.evil", cfg)
        self.assertTrue((home / "auth.json").is_symlink())
        self.assertEqual((home / "auth.json").resolve(), auth.resolve())
        self.assertEqual(runtime.auth_bridge, str(auth.resolve()))

    def test_runtime_manifest_records_ignored_defaults(self):
        effective = AgentConfiguration(self.project).resolve(provider="cursor", activity="discovery")
        runtime = ManagedProviderRuntime(self.project).prepare("cursor", activity="discovery", effective=effective)
        manifest = json.loads((Path(runtime.root) / "runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["external_defaults_policy"], "ignore")
        self.assertEqual(manifest["tool_profile"], "design")

    def test_acceptance_cli_uses_agentic_timeouts_and_no_machine_config_by_default(self):
        args = parser().parse_args(["debug","acceptance","--providers","cursor","--scenario","fibonacci"])
        self.assertIsNone(args.timeout)
        self.assertEqual(args.max_runtime, 1800)
        self.assertEqual(args.idle_timeout, 180)
        self.assertEqual(args.slow_progress, 60)
        self.assertFalse(args.configure_machine)

    def test_cursor_model_evidence_survives_max_runtime(self):
        fake = self.tmp / "cursor-agent"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json,time\n"
            "print(json.dumps({'type':'system','subtype':'init','model':'Cursor Grok 4.6 Medium'}), flush=True)\n"
            "time.sleep(3)\n",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        route = ModelRoute("cursor","discovery","grok-4.6","medium","test","test","session_or_resume_boundary")
        result = CursorAcceptanceDriver(str(fake), max_runtime=1, idle_timeout=10).run(
            self.project, "prompt", self.tmp / "logs", interaction_mode="auto", model_route=route
        )
        self.assertEqual(result["termination_reason"], "max_runtime")
        self.assertTrue(result["model_route_verified"])
        self.assertEqual(result["managed_runtime"]["external_defaults_policy"], "ignore")

    def test_acceptance_profile_exposes_only_curated_tools(self):
        import os
        old = os.environ.get("DYNOSAI_MCP_TOOL_PROFILE")
        try:
            os.environ["DYNOSAI_MCP_TOOL_PROFILE"] = "acceptance"
            visible = {x["name"] for x in advertised_tools()}
        finally:
            if old is None: os.environ.pop("DYNOSAI_MCP_TOOL_PROFILE", None)
            else: os.environ["DYNOSAI_MCP_TOOL_PROFILE"] = old
        self.assertEqual(visible, MCP_TOOL_PROFILES["acceptance"])
        self.assertNotIn("dynosai_stats", visible)

    def test_unknown_profile_keeps_legacy_compatibility(self):
        import os
        old = os.environ.get("DYNOSAI_MCP_TOOL_PROFILE")
        try:
            os.environ["DYNOSAI_MCP_TOOL_PROFILE"] = "unknown"
            self.assertEqual(len(advertised_tools()), len(TOOLS))
        finally:
            if old is None: os.environ.pop("DYNOSAI_MCP_TOOL_PROFILE", None)
            else: os.environ["DYNOSAI_MCP_TOOL_PROFILE"] = old

    def test_managed_runtime_does_not_copy_auth_secret(self):
        fake_home = self.tmp / "home2"
        (fake_home / ".codex").mkdir(parents=True)
        auth = fake_home / ".codex" / "auth.json"
        auth.write_text("SECRET-MARKER", encoding="utf-8")
        runtime = ManagedProviderRuntime(self.project).prepare("codex", activity="discovery", auth_home=fake_home)
        root = Path(runtime.root)
        files = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
        self.assertFalse(any("SECRET-MARKER" in p.read_text(encoding="utf-8", errors="ignore") for p in files))


if __name__ == "__main__":
    unittest.main()
