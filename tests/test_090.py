import json
import os
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.agent_config import AgentConfiguration, ProjectAnalyzer
from dynosai_flow.cli import parser
from dynosai_flow.providers import MachineProviderSetup
from dynosai_flow.version import __version__


class AgentConfiguration091Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name) / "project"
        self.root.mkdir()

    def test_profile_is_small_provider_neutral_and_defaults_to_progressive(self):
        cfg = AgentConfiguration(self.root)
        result = cfg.init()
        self.assertTrue(result["created"])
        profile = cfg.load()
        self.assertEqual(profile["schema_version"], 1)
        self.assertEqual(profile["mode"], "recommended")
        self.assertEqual(profile["context"]["strategy"], "progressive")
        self.assertEqual(set(profile["providers"]), {"cursor", "codex"})
        self.assertLess(len(cfg.profile_path.read_text(encoding="utf-8")), 2000)

    def test_project_analyzer_detects_python_api_and_postgres(self):
        (self.root / "pyproject.toml").write_text(
            '[project]\ndependencies=["fastapi","psycopg"]\n', encoding="utf-8"
        )
        fp = ProjectAnalyzer().detect(self.root)
        self.assertEqual(fp.archetype, "python-api")
        self.assertEqual(fp.database, "postgres")
        self.assertIn("api-framework", fp.signals)

    def test_progressive_disclosure_selects_only_activity_relevant_skills(self):
        (self.root / "pyproject.toml").write_text('[project]\ndependencies=["fastapi"]\n', encoding="utf-8")
        cfg = AgentConfiguration(self.root)
        discovery = cfg.resolve(provider="cursor", activity="discovery")
        implementation = cfg.resolve(provider="cursor", activity="implementation")
        review = cfg.resolve(provider="cursor", activity="code_review")
        self.assertEqual([x["id"] for x in discovery["skills"]], ["debugging"])
        self.assertIn("api-development", [x["id"] for x in implementation["skills"]])
        self.assertIn("testing", [x["id"] for x in implementation["skills"]])
        self.assertNotIn("code-review", [x["id"] for x in implementation["skills"]])
        self.assertIn("code-review", [x["id"] for x in review["skills"]])

    def test_capabilities_decouple_skill_from_mcp_and_never_fake_missing_tool(self):
        cfg = AgentConfiguration(self.root)
        cfg.init(mode="advanced")
        profile = cfg.load()
        profile["components"]["skills"] = ["database-migrations"]
        profile["components"]["tools"] = ["postgres"]
        cfg.profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DYNOSAI_POSTGRES_MCP_COMMAND", None)
            no_tool = cfg.resolve(provider="codex", activity="implementation")
        self.assertIn("database.schema.read", no_tool["capabilities"]["missing"])
        self.assertIn("postgres", no_tool["unresolved_tools"])
        self.assertNotIn("postgres", [x["id"] for x in no_tool["tools"]])
        with patch.dict(os.environ, {"DYNOSAI_POSTGRES_MCP_COMMAND": "postgres-mcp", "DYNOSAI_POSTGRES_MCP_ARGS": "--stdio"}):
            resolved = cfg.resolve(provider="codex", activity="implementation")
        self.assertNotIn("database.schema.read", resolved["capabilities"]["missing"])
        self.assertIn("postgres", [x["id"] for x in resolved["tools"]])

    def test_compile_all_materializes_common_semantics_in_native_provider_shapes(self):
        (self.root / "pyproject.toml").write_text('[project]\ndependencies=["fastapi"]\n', encoding="utf-8")
        (self.root / "AGENTS.md").write_text("# User instructions\n", encoding="utf-8")
        cfg = AgentConfiguration(self.root)
        cfg.init()
        result = cfg.compile(providers=["cursor", "codex"], activity="implementation")
        self.assertFalse(result["dry_run"])
        self.assertTrue((self.root / ".cursor" / "skills" / "api-development" / "SKILL.md").exists())
        self.assertTrue((self.root / ".agents" / "skills" / "api-development" / "SKILL.md").exists())
        self.assertIn("# User instructions", (self.root / "AGENTS.md").read_text(encoding="utf-8"))
        cursor_mcp = json.loads((self.root / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        self.assertIn("dynosai", cursor_mcp["mcpServers"])
        tomllib.loads((self.root / ".codex" / "config.toml").read_text(encoding="utf-8"))
        effective = json.loads((self.root / ".dynosai" / "generated" / "effective-config.json").read_text(encoding="utf-8"))
        self.assertEqual(set(effective), {"cursor", "codex"})
        lock = json.loads((self.root / ".dynosai" / "generated" / "lock.json").read_text(encoding="utf-8"))
        self.assertTrue(lock["effective_sha256"])

    def test_compile_dry_run_is_observational(self):
        cfg = AgentConfiguration(self.root)
        result = cfg.compile(providers=["cursor"], activity="discovery", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse((self.root / ".cursor").exists())
        self.assertFalse((self.root / ".dynosai" / "generated").exists())

    def test_cli_exposes_agent_config_control_plane(self):
        args = parser().parse_args(["agent-config", "compile", "--provider", "all", "--activity", "implementation", "--dry-run"])
        self.assertEqual(args.command, "agent-config")
        self.assertEqual(args.provider, "all")
        self.assertTrue(args.dry_run)

    def test_agent_config_compile_in_config_only_workspace_does_not_require_git_or_create_project_db(self):
        from dynosai_flow.cli import main
        self.assertFalse((self.root / ".git").exists())
        self.assertFalse((self.root / ".dynosai" / "knowledge.db").exists())
        self.assertEqual(main(["--project", str(self.root), "--json", "agent-config", "init"]), 0)
        self.assertEqual(main(["--project", str(self.root), "--json", "agent-config", "compile", "--provider", "cursor", "--activity", "implementation"]), 0)
        self.assertEqual(main(["--project", str(self.root), "--json", "agent-config", "compile", "--provider", "codex", "--activity", "implementation"]), 0)
        self.assertFalse((self.root / ".git").exists())
        self.assertFalse((self.root / ".dynosai" / "knowledge.db").exists())
        self.assertTrue((self.root / ".cursor" / "rules" / "dynosai.mdc").exists())
        self.assertTrue((self.root / "AGENTS.md").exists())

    def test_agent_config_compile_tolerates_stale_empty_db_without_git(self):
        from dynosai_flow.cli import main
        stale = self.root / ".dynosai" / "knowledge.db"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.touch()
        self.assertEqual(main(["--project", str(self.root), "--json", "agent-config", "compile", "--provider", "cursor", "--activity", "implementation"]), 0)
        self.assertFalse((self.root / ".git").exists())
        self.assertTrue((self.root / ".cursor" / "rules" / "dynosai.mdc").exists())

    def test_machine_setup_rejects_unsupported_provider(self):
        home = Path(self.td.name) / "home"
        home.mkdir()
        with self.assertRaises(ValueError):
            MachineProviderSetup(home).setup(["unsupported-provider"], install_model=False)


    def test_clean_removes_only_dynosai_generated_semantic_files(self):
        (self.root / "AGENTS.md").write_text("# User instructions\n", encoding="utf-8")
        cfg = AgentConfiguration(self.root)
        cfg.init()
        cfg.compile(providers=["codex", "cursor"], activity="implementation")
        cursor_mcp = self.root / ".cursor" / "mcp.json"
        data = json.loads(cursor_mcp.read_text(encoding="utf-8")); data["mcpServers"]["user"]={"command":"x"}; cursor_mcp.write_text(json.dumps(data), encoding="utf-8")
        cfg.clean(providers=["codex", "cursor"])
        self.assertIn("# User instructions", (self.root / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertNotIn("DYNOSAI:AGENT-CONFIG", (self.root / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertFalse((self.root / ".agents" / "skills" / "api-development").exists())
        remaining = json.loads(cursor_mcp.read_text(encoding="utf-8"))
        self.assertIn("user", remaining["mcpServers"])
        self.assertNotIn("dynosai", remaining["mcpServers"])

    def test_version_is_091(self):
        self.assertEqual(__version__, "0.19.0")


if __name__ == "__main__":
    unittest.main()
