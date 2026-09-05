import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.certification_matrix import (
    HISTORICAL_MATRIX_PATH,
    load_live_matrix,
    probe_environment,
    validate_live_matrix,
)
from dynosai_flow.db import Database
from dynosai_flow.eval_registry import CATALOG_VERSION, EvalRegistry, catalog_summary, scenario
from dynosai_flow.mcp import CURRENT_PROTOCOL, LEGACY_TOOLS, SUPPORTED_PROTOCOLS, TOOLS, MCPServer
from dynosai_flow.mcp_protocol import (
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_PROTOCOL_VERSION,
    PROTOCOL_2025_06,
    PROTOCOL_2025_11,
    PROTOCOL_2026,
    UNSUPPORTED_PROTOCOL_CODE,
)
from dynosai_flow.version import DISPLAY_VERSION, __version__


ROOT = Path(__file__).resolve().parents[1]
FROZEN_MCP_COUNT = 31


def _meta(protocol: str, name: str = "ExampleClient") -> dict:
    return {
        META_PROTOCOL_VERSION: protocol,
        META_CLIENT_INFO: {"name": name, "version": "test"},
        META_CLIENT_CAPABILITIES: {"elicitation": {}},
    }


class DynosAI260Rc6ReleaseHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-260-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_version_and_schema_and_mcp_names(self):
        self.assertEqual(__version__, "1.0.0rc8")
        self.assertEqual(DISPLAY_VERSION, "1.0.0-rc.8")
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)
        names = {str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}
        self.assertEqual(len(names), FROZEN_MCP_COUNT)
        self.assertEqual(CURRENT_PROTOCOL, PROTOCOL_2026)
        self.assertEqual(SUPPORTED_PROTOCOLS, (PROTOCOL_2026, PROTOCOL_2025_11, PROTOCOL_2025_06))

    def test_legacy_initialize_2025_revisions_keep_handshake(self):
        for protocol in (PROTOCOL_2025_11, PROTOCOL_2025_06):
            project = self.tmp / protocol
            project.mkdir()
            server = MCPServer(project)
            init = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": protocol, "clientInfo": {"name": "Cursor", "version": "x"}, "capabilities": {"elicitation": {"form": {}}}},
            })
            self.assertEqual(init["result"]["protocolVersion"], protocol)
            self.assertEqual(init["result"]["capabilities"], {"tools": {"listChanged": True}})
            self.assertEqual(init["result"]["serverInfo"]["name"], "dynosai-flow")
            self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            result = listed["result"]
            self.assertIn("tools", result)
            self.assertNotIn("resultType", result)
            self.assertNotIn("ttlMs", result)
            names = [item["name"] for item in result["tools"]]
            self.assertEqual(len(names), len(TOOLS))
            ping = server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
            self.assertEqual(ping["result"], {})

    def test_legacy_tools_list_without_initialize_is_not_initialized(self):
        server = MCPServer(self.tmp)
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertEqual(listed["error"]["code"], -32002)
        self.assertEqual(listed["error"]["message"], "Server not initialized")

    def test_2026_discover_and_stateless_tools_list(self):
        server = MCPServer(self.tmp)
        discover = server.handle({
            "jsonrpc": "2.0", "id": "discover-1", "method": "server/discover",
            "params": {"_meta": _meta(PROTOCOL_2026)},
        })
        result = discover["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"], list(SUPPORTED_PROTOCOLS))
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": True}})
        self.assertIn("ttlMs", result)
        self.assertEqual(result["cacheScope"], "public")
        listed = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            "params": {"_meta": _meta(PROTOCOL_2026)},
        })
        body = listed["result"]
        self.assertEqual(body["resultType"], "complete")
        self.assertEqual(body["cacheScope"], "private")
        self.assertIn("ttlMs", body)
        names = [item["name"] for item in body["tools"]]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(names), len(TOOLS))
        first = names[:]
        listed_again = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/list",
            "params": {"_meta": _meta(PROTOCOL_2026)},
        })
        self.assertEqual([item["name"] for item in listed_again["result"]["tools"]], first)

    def test_2026_tools_call_without_initialize_uses_meta(self):
        server = MCPServer(self.tmp)
        called = server.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "dynosai_project", "arguments": {"action": "detect"}, "_meta": _meta(PROTOCOL_2026, "cursor-agent")},
        })
        result = called["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertFalse(result["isError"])
        self.assertEqual(result["_meta"][META_PROTOCOL_VERSION], PROTOCOL_2026)
        self.assertIn("structuredContent", result)

    def test_unsupported_protocol_is_minus_32022(self):
        server = MCPServer(self.tmp)
        init = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "1900-01-01"},
        })
        self.assertEqual(init["error"]["code"], UNSUPPORTED_PROTOCOL_CODE)
        self.assertEqual(init["error"]["data"]["requested"], "1900-01-01")
        self.assertEqual(init["error"]["data"]["supported"], list(SUPPORTED_PROTOCOLS))
        discover = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "server/discover",
            "params": {"_meta": {META_PROTOCOL_VERSION: "1900-01-01"}},
        })
        self.assertEqual(discover["error"]["code"], UNSUPPORTED_PROTOCOL_CODE)

    def test_cursor_and_codex_elicitation_capability_still_binds(self):
        server = MCPServer(self.tmp)
        server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_2025_11,
                "clientInfo": {"name": "OpenAI Codex", "version": "x"},
                "capabilities": {"elicitation": {"form": {}}},
            },
        })
        self.assertTrue(server.client_capabilities.elicitation_form)
        self.assertEqual(server.provider, "codex")
        self.assertEqual(server.negotiated, PROTOCOL_2025_11)

    def test_get_next_action_returns_bootstrap_contract_without_session(self):
        server = MCPServer(self.tmp)
        server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_2025_11,
                "clientInfo": {"name": "Cursor", "version": "x"},
                "capabilities": {"elicitation": {"form": {}}},
            },
        })
        server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "dynosai_get_next_action", "arguments": {"execute": False}},
        })
        self.assertFalse(response["result"]["isError"], response)
        payload = response["result"].get("structuredContent") or json.loads(response["result"]["content"][0]["text"])
        self.assertTrue(payload.get("bootstrap"))
        self.assertIsNone(payload.get("work"))
        self.assertEqual(payload["contract"]["tool"], "dynosai_project")
        self.assertEqual(payload["contract"]["arguments"]["action"], "initialize")

    def test_nested_directory_is_not_parent_git_repo(self):
        from dynosai_flow.git_manager import GitManager
        nested = ROOT / ".dynosai" / "runtime" / "git-isolation-rc6"
        if nested.exists():
            shutil.rmtree(nested, ignore_errors=True)
        nested.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(nested, ignore_errors=True))
        git = GitManager(nested, Database(nested / "knowledge.db"))
        self.assertFalse(git.is_repo())
        git.initialize_repo()
        self.assertTrue((nested / ".git").exists())
        self.assertTrue(git.is_repo())
        self.assertEqual(git.status(), [])

    def test_release_gate_is_the_single_executable_source(self):
        gate = (ROOT / "scripts" / "release_gate.py").read_text(encoding="utf-8")
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        build = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        release_process = (ROOT / "docs" / "RELEASE_PROCESS.md").read_text(encoding="utf-8")
        python_job = ci.split("installed-wheel")[0]
        self.assertIn("python scripts/release_gate.py", python_job)
        self.assertNotIn("python -m compileall", python_job)
        self.assertNotIn("python -m pytest", python_job)
        self.assertIn("scripts/release_gate.py", build)
        self.assertNotIn('"-m", "compileall"', build)
        self.assertIn("compileall", gate)
        self.assertIn("check_repository.py", gate)
        self.assertIn("check_studio_sync.py", gate)
        self.assertIn('"-m", "pytest"', gate)
        self.assertIn("src/dynosai.egg-info", (ROOT / "scripts" / "check_repository.py").read_text(encoding="utf-8"))
        acceptance = (ROOT / "src" / "dynosai_flow" / "acceptance.py").read_text(encoding="utf-8")
        self.assertIn('app-server","--listen","stdio://"', acceptance)
        self.assertIn('text=True,encoding="utf-8",errors="strict"', acceptance.replace(" ", ""))
        self.assertIn("scripts/release_gate.py", contributing)
        self.assertIn("scripts/release_gate.py", release_process)
        self.assertIn("installed-wheel", ci)
        self.assertIn("test_07.py", gate + build + contributing + release_process + (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))

    def test_eval_catalog_has_tiered_provenance(self):
        summary = catalog_summary()
        self.assertEqual(summary["catalog_version"], CATALOG_VERSION)
        self.assertGreaterEqual(summary["count"], 20)
        self.assertLessEqual(summary["count"], 30)
        self.assertEqual(summary["tier_a"], ["greenfield_implementation", "brownfield_feature"])
        self.assertIn("scope_violation", summary["tier_b"])
        self.assertIn("secret_path_attempt", summary["created_from_real_failure"])
        registry = EvalRegistry(self.tmp)
        for case_id in ("scope_violation", "secret_path_attempt", "timeout", "malformed_provider_response", "cost_attribution", "context_handle_recovery"):
            record = registry.run_offline(case_id)
            self.assertTrue(record["success"], case_id)
            self.assertEqual(record["grader"], scenario(case_id)["grader"])
            self.assertTrue(record["provenance"])

    def test_distribution_and_plugin_adrs_exist(self):
        dist = (ROOT / "docs" / "adr" / "0001-pypi-distribution-name.md").read_text(encoding="utf-8")
        plugins = (ROOT / "docs" / "adr" / "0002-agent-plugins.md").read_text(encoding="utf-8")
        mcp = (ROOT / "docs" / "adr" / "0003-mcp-protocol-eras.md").read_text(encoding="utf-8")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("PyPI distribution: dynosai", dist)
        self.assertIn("Python import:     dynosai_flow", dist)
        self.assertIn('name = "dynosai"', pyproject)
        self.assertIn("dynosai_flow.cli:main", pyproject)
        self.assertIn("must not invent a proprietary", plugins)
        self.assertIn("DynosAI Pack", plugins)
        self.assertIn("com.dynosai", plugins)
        self.assertIn("plugin.json", plugins)
        self.assertIn("2026-07-28", mcp)
        self.assertIn("stdio", mcp)

    def test_matrix_is_honest_and_not_copied(self):
        matrix = load_live_matrix()
        validate_live_matrix(matrix)
        self.assertFalse(matrix["copied_from_historical"])
        env = probe_environment(ROOT)
        self.assertEqual(env["dynosai_version"], __version__)
        historical = json.loads((ROOT / HISTORICAL_MATRIX_PATH).read_text(encoding="utf-8"))
        self.assertTrue(historical["matrix_passed"])
        for cell in matrix["cells"]:
            self.assertNotEqual(cell.get("evidence"), HISTORICAL_MATRIX_PATH)
            if cell["status"] == "pass":
                self.assertTrue(cell.get("evidence") or cell.get("trials"))
            if cell["status"] != "pass":
                self.assertNotEqual(cell.get("quality_score"), 100)

    def test_roadmap_separates_rc6_from_future(self):
        roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("1.0.0-rc.6", roadmap)
        self.assertIn("1.0.0-rc.8", roadmap)
        self.assertIn("1.0.0-rc.8", roadmap)
        self.assertIn("Contingency", roadmap)
        self.assertIn("1.1", roadmap)
        self.assertIn("Enforced Secure Runtimes", roadmap)
        self.assertIn("1.2", roadmap)
        self.assertIn("Agent Plugins", roadmap)
        self.assertIn("1.3", roadmap)
        self.assertIn("ACP Governance Gateway", roadmap)
        self.assertIn("1.4", roadmap)
        self.assertIn("1.5", roadmap)
        quality = (ROOT / "docs" / "CODE_QUALITY.md").read_text(encoding="utf-8")
        self.assertIn("characterization", quality.lower())
        self.assertNotIn("42 Python modules", quality)

    def test_official_http_conformance_is_not_pretended_for_stdio(self):
        adr = (ROOT / "docs" / "adr" / "0003-mcp-protocol-eras.md").read_text(encoding="utf-8")
        self.assertIn("@modelcontextprotocol/conformance", adr)
        self.assertIn("HTTP-oriented", adr)


if __name__ == "__main__":
    unittest.main()
