import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.acceptance import RealProviderAcceptanceSuite
from dynosai_flow.mcp import MCPServer, _tool_result_payload
from dynosai_flow.version import __version__


class DynosAI127RC2CompatibilityTests(unittest.TestCase):
    def test_version_is_0127(self):
        self.assertEqual(__version__, "1.0.0rc8")

    def test_codex_auto_transport_remains_structured_primary(self):
        value={"accepted":False,"error_type":"validation","details":{"missing":["requirements"]},"payload":"x"*4000}
        payload=_tool_result_payload(value,provider="codex",tool_name="dynosai_submit_spec")
        text=json.loads(payload["content"][0]["text"])
        self.assertEqual(text["authoritative"],"structuredContent")
        self.assertEqual(payload["structuredContent"]["details"]["missing"],["requirements"])
        self.assertEqual(payload["structuredContent"]["response_optimization"]["transport_mode"],"structured_primary")
        self.assertGreater(payload["structuredContent"]["response_optimization"]["transport_text_bytes_omitted"],3000)

    def test_cursor_auto_transport_restores_full_text_compatibility(self):
        value={"accepted":False,"error_type":"validation","details":{"missing":["requirements","acceptance_criteria"]},"payload":"x"*4000}
        payload=_tool_result_payload(value,provider="cursor",tool_name="dynosai_submit_spec")
        visible=json.loads(payload["content"][0]["text"])
        self.assertEqual(visible["details"],value["details"])
        self.assertEqual(visible["payload"],value["payload"])
        self.assertEqual(payload["structuredContent"]["response_optimization"]["transport_mode"],"full_text_compatibility")
        self.assertEqual(payload["structuredContent"]["response_optimization"]["transport_text_bytes_omitted"],0)

    def test_cursor_can_be_forced_to_structured_only_for_diagnostics(self):
        value={"accepted":False,"error_type":"validation","details":{"missing":["x"]}}
        with patch.dict("os.environ",{"DYNOSAI_MCP_RESULT_TEXT_MODE":"structured"},clear=False):
            payload=_tool_result_payload(value,provider="cursor",tool_name="dynosai_submit_spec")
        self.assertEqual(payload["structuredContent"]["response_optimization"]["transport_mode"],"structured_primary")

    def test_initialize_instruction_is_provider_specific(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cursor=MCPServer(root)
            r=cursor.handle({"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"Cursor","version":"x"},"capabilities":{}}})
            self.assertIn("content.text contains the complete authoritative tool result",r["result"]["instructions"])
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            codex=MCPServer(root)
            r=codex.handle({"jsonrpc":"2.0","id":"1","method":"initialize","params":{"protocolVersion":"2025-11-25","clientInfo":{"name":"OpenAI Codex","version":"x"},"capabilities":{}}})
            self.assertIn("structuredContent is authoritative",r["result"]["instructions"])

    def test_full_green_brown_matrix_order_is_one_suite(self):
        with tempfile.TemporaryDirectory() as td:
            suite=RealProviderAcceptanceSuite(
                providers=("codex","cursor"),
                scenario="green-brown",
                output=Path(td)/"matrix.zip",
                workspace=Path(td)/"workspace",
                log_dir=Path(td)/"logs",
                configure=False,
            )
            self.assertEqual(suite._case_order(),[
                ("codex","fibonacci"),
                ("cursor","fibonacci"),
                ("codex","orderflow-contract-discounts"),
                ("cursor","orderflow-contract-discounts"),
            ])
            self.assertEqual(suite.scenarios,("fibonacci","orderflow-contract-discounts"))


if __name__ == "__main__":
    unittest.main()
