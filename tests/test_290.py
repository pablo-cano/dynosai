# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""RC9: Codex App Server MCP preflight without a model turn."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.codex_mcp_preflight import (
    CLASSIFICATION_ACTIVITY_MISSING,
    CLASSIFICATION_CONFIG_MISSING,
    CLASSIFICATION_HOME_MISMATCH,
    CLASSIFICATION_READY_WITHOUT_TOOLS,
    CLASSIFICATION_REGISTRY_MISSING,
    CLASSIFICATION_STARTUP_FAILED,
    CLASSIFICATION_STARTUP_TIMEOUT,
    CLASSIFICATION_TOOL_CALL_FAILED,
    PREFLIGHT_TOOL,
    capture_startup_notification,
    choose_preflight_tool,
    dynosai_startup_sequence,
    dynosai_transport_verified,
    evaluate_codex_mcp_preflight,
    extract_dynosai_mcp,
    extract_initialize,
    extract_status_servers,
    extract_user_config_file,
    find_dynosai_status,
    params_override_mcp_servers,
    portable_codex_mcp_preflight,
    thread_start_params,
    tool_call_succeeded,
    tool_names_from_server,
)
from dynosai_flow.certification_matrix import default_live_matrix, load_live_matrix, save_live_matrix


ROOT = Path(__file__).resolve().parents[1]
HOME = r"C:\Users\pcano\AppData\Local\DynosAI\certification\matrix-1.0\ws\.dynosai\runtime\managed-agents\codex\home"


def _load_runner():
    path = ROOT / "scripts" / "run_matrix_1_0.py"
    spec = importlib.util.spec_from_file_location("run_matrix_1_0_rc9", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _pass_observed(**overrides):
    observed = {
        "initialize_status": "pass",
        "version_status": "pass",
        "expected_codex_home": HOME,
        "observed_codex_home": HOME,
        "codex_home_verified": True,
        "config_read": {
            "dynosai_server_present": True,
            "transport_verified": True,
            "config_source_verified": True,
            "expected_config_file": HOME + r"\config.toml",
            "observed_config_file": HOME + r"\config.toml",
            "command": r"C:\Python313\python.exe",
            "args": ["-m", "dynosai_flow.mcp"],
            "transport": "stdio",
        },
        "mcp_status": {"present": True, "status": "ready", "error": None, "failure_reason": None},
        "tools": {"count": 2, "names": ["dynosai_get_next_action", "dynosai_git_status"]},
        "direct_tool_call": {"status": "pass", "tool": PREFLIGHT_TOOL},
        "mcp_activity_present": True,
        "mcp_calls": 1,
        "model_turn_started": False,
        "model_tokens": 0,
        "startup_notifications": [
            {"server": "dynosai", "status": "starting", "error": None, "failureReason": None, "threadId": "t1", "timestamp": "2026-09-05T00:00:00+00:00"},
            {"server": "dynosai", "status": "ready", "error": None, "failureReason": None, "threadId": "t1", "timestamp": "2026-09-05T00:00:01+00:00"},
        ],
    }
    observed.update(overrides)
    return observed


class DynosAI290ProtocolParseTests(unittest.TestCase):
    def test_initialize_codex_home_fields(self):
        parsed = extract_initialize({
            "userAgent": "codex_cli/0.150.1",
            "codexHome": HOME,
            "platformFamily": "windows",
            "platformOs": "windows",
        })
        self.assertEqual(parsed["codexHome"], HOME)
        self.assertEqual(parsed["userAgent"], "codex_cli/0.150.1")

    def test_thread_start_params_do_not_override_mcp_servers(self):
        params = thread_start_params(r"C:\proj")
        self.assertFalse(params_override_mcp_servers(params))
        self.assertNotIn("mcp_servers", params)
        self.assertNotIn("mcpServers", params)
        self.assertNotIn("model", params)
        self.assertEqual(params["cwd"], r"C:\proj")

    def test_config_read_contains_dynosai(self):
        server = extract_dynosai_mcp({
            "config": {
                "mcpServers": {
                    "dynosai": {
                        "command": r"C:\Python313\python.exe",
                        "args": ["-m", "dynosai_flow.mcp"],
                        "enabled": True,
                        "transport": "stdio",
                    }
                }
            }
        })
        self.assertIsNotNone(server)
        self.assertTrue(dynosai_transport_verified(server, expected_python=r"C:\Python313\python.exe"))

    def test_config_read_misses_dynosai(self):
        self.assertIsNone(extract_dynosai_mcp({"config": {"mcpServers": {"codex": {}}}}))

    def test_config_user_layer_file(self):
        path = extract_user_config_file({
            "layers": [{"name": {"type": "user", "file": HOME + r"\config.toml"}, "config": {}}],
        })
        self.assertTrue(str(path).endswith("config.toml"))

    def test_mcp_status_list_uses_data(self):
        servers = extract_status_servers({"data": [{"name": "dynosai", "runtimeStatus": "connected", "tools": {"dynosai_git_status": {}}}]})
        found = find_dynosai_status(servers)
        self.assertIsNotNone(found)
        self.assertEqual(tool_names_from_server(found), ["dynosai_git_status"])

    def test_startup_notifications_captured(self):
        note = capture_startup_notification({
            "method": "mcpServer/startupStatus/updated",
            "params": {"threadId": "abc", "name": "dynosai", "status": "failed", "error": "boom", "failureReason": None},
        }, at="t0")
        self.assertEqual(note["server"], "dynosai")
        self.assertEqual(note["status"], "failed")
        self.assertEqual(note["error"], "boom")
        sequence = dynosai_startup_sequence([note, {"server": "codex", "status": "ready"}])
        self.assertEqual([item["server"] for item in sequence], ["dynosai"])

    def test_choose_readonly_preflight_tool(self):
        self.assertEqual(choose_preflight_tool(["dynosai_work", "dynosai_get_next_action"]), PREFLIGHT_TOOL)
        self.assertEqual(choose_preflight_tool(["dynosai_git_status"]), "dynosai_git_status")

    def test_direct_tool_call_success_and_failure(self):
        self.assertTrue(tool_call_succeeded({"result": {"content": [{"type": "text", "text": "{}"}]}}))
        self.assertFalse(tool_call_succeeded({"error": {"message": "nope"}}))
        self.assertFalse(tool_call_succeeded({"result": {"isError": True}}))


class DynosAI290EvaluateTests(unittest.TestCase):
    def test_home_match_and_mismatch(self):
        report = evaluate_codex_mcp_preflight(_pass_observed())
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["codex_home_verified"])
        mismatch = evaluate_codex_mcp_preflight(_pass_observed(
            observed_codex_home=r"C:\Users\pcano\AppData\Local\Temp\codex",
        ))
        self.assertEqual(mismatch["classification"], CLASSIFICATION_HOME_MISMATCH)
        self.assertEqual(mismatch["status"], "fail")

    def test_config_read_classifications(self):
        missing = evaluate_codex_mcp_preflight(_pass_observed(config_read={"dynosai_server_present": False}))
        self.assertEqual(missing["classification"], CLASSIFICATION_CONFIG_MISSING)
        mismatch = evaluate_codex_mcp_preflight(_pass_observed(config_read={
            "dynosai_server_present": True,
            "transport_verified": True,
            "config_source_mismatch": True,
        }))
        self.assertEqual(mismatch["classification"], "codex_config_source_mismatch")

    def test_mcp_status_absent_starting_ready_failed(self):
        absent = evaluate_codex_mcp_preflight(_pass_observed(mcp_status={"present": False}))
        self.assertEqual(absent["classification"], CLASSIFICATION_REGISTRY_MISSING)
        starting = evaluate_codex_mcp_preflight(_pass_observed(mcp_status={"present": True, "status": "starting", "timeout": True}))
        self.assertEqual(starting["classification"], CLASSIFICATION_STARTUP_TIMEOUT)
        failed = evaluate_codex_mcp_preflight(_pass_observed(mcp_status={
            "present": True, "status": "failed", "error": "spawn failed", "failure_reason": "reauthenticationRequired",
        }))
        self.assertEqual(failed["classification"], CLASSIFICATION_STARTUP_FAILED)
        self.assertEqual(failed["mcp_status"]["error"], "spawn failed")

    def test_tool_inventory_and_direct_call(self):
        empty = evaluate_codex_mcp_preflight(_pass_observed(tools={"count": 0, "names": []}))
        self.assertEqual(empty["classification"], CLASSIFICATION_READY_WITHOUT_TOOLS)
        call_fail = evaluate_codex_mcp_preflight(_pass_observed(direct_tool_call={"status": "fail", "tool": PREFLIGHT_TOOL}))
        self.assertEqual(call_fail["classification"], CLASSIFICATION_TOOL_CALL_FAILED)

    def test_activity_and_tokens_and_no_model_turn(self):
        activity = evaluate_codex_mcp_preflight(_pass_observed(mcp_activity_present=False, mcp_calls=0))
        self.assertEqual(activity["classification"], CLASSIFICATION_ACTIVITY_MISSING)
        tokens = evaluate_codex_mcp_preflight(_pass_observed(model_tokens=12))
        self.assertEqual(tokens["classification"], "model_tokens_consumed")
        turn = evaluate_codex_mcp_preflight(_pass_observed(model_turn_started=True))
        self.assertEqual(turn["classification"], "model_turn_started")

    def test_portable_report_drops_absolute_paths(self):
        portable = portable_codex_mcp_preflight(evaluate_codex_mcp_preflight(_pass_observed()))
        blob = json.dumps(portable)
        self.assertNotIn("C:\\Users\\", blob)
        self.assertTrue(portable["codex_home_verified"])
        self.assertEqual(portable["mcp_calls"], 1)
        self.assertEqual(portable["startup_statuses"], ["starting", "ready"])


class DynosAI290MatrixGateTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="dynosai-290-")
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def test_mcp_preflight_failure_does_not_start_provider_or_increment_attempts(self):
        runner = _load_runner()
        matrix_path = self.tmp / "matrix.json"
        save_live_matrix(default_live_matrix(), matrix_path)
        started = []
        original = runner.ROOT
        runner.ROOT = ROOT
        try:
            with patch.object(runner, "_run_live_cell", lambda *a, **k: started.append(True) or (_ for _ in ()).throw(AssertionError("provider must not start"))), patch.object(
                runner, "guard_live_certification", lambda *_a, **_k: {
                    "dynosai_git_commit": "x",
                    "certification_subject_sha256": "y",
                    "certification_dirty_paths": [],
                    "unexpected_dirty_paths": [],
                }
            ), patch.object(runner, "assert_codex_home_safe", lambda home, **_k: Path(home)), patch(
                "dynosai_flow.managed_runtime.run_codex_version_preflight",
                lambda *_a, **_k: {"status": "pass", "helper_binary_refusal_detected": False, "codex_version": "codex-cli test", "exit_code": 0},
            ), patch(
                "dynosai_flow.codex_mcp_preflight.run_codex_mcp_preflight",
                lambda *_a, **_k: {
                    "status": "fail",
                    "classification": CLASSIFICATION_REGISTRY_MISSING,
                    "model_turn_started": False,
                    "mcp_calls": 0,
                    "model_tokens": 0,
                    "mcp_activity_present": False,
                },
            ):
                code = runner.main([
                    "--live",
                    "--cells", "codex.greenfield",
                    "--workspace", str(self.tmp / "ws"),
                    "--expected-subject-sha256", "y",
                    "--matrix", str(matrix_path),
                ])
        finally:
            runner.ROOT = original
        self.assertEqual(code, 2)
        self.assertEqual(started, [])
        matrix = load_live_matrix(matrix_path)
        cell = next(item for item in matrix["cells"] if item["provider"] == "codex" and item["mode"] == "greenfield")
        self.assertEqual(cell.get("trials") or [], [])

    def test_rc8_attempt4_history_untouched_by_this_module(self):
        matrix = load_live_matrix()
        cell = next(item for item in matrix["cells"] if item["provider"] == "codex" and item["mode"] == "greenfield")
        attempts = [int(trial.get("attempt") or 0) for trial in cell.get("trials") or []]
        self.assertIn(4, attempts)
        trial4 = next(trial for trial in cell["trials"] if int(trial["attempt"]) == 4)
        self.assertEqual(trial4.get("candidate_version"), "1.0.0-rc.8")
        self.assertEqual(trial4.get("final_status"), "fail")
        self.assertEqual(trial4.get("dynosai_git_commit"), "181624b8df6eab3224fbfc23c44ee27b4cacef0d")


class DynosAI290AcceptanceSourceTests(unittest.TestCase):
    def test_live_codex_driver_does_not_send_mcp_server_overrides(self):
        text = (ROOT / "src" / "dynosai_flow" / "acceptance.py").read_text(encoding="utf-8")
        self.assertIn('thread_params={"cwd":str(project),"approvalPolicy":elicitation_only_policy,"sandbox":sandbox_mode}', text)
        self.assertIn('thread_params["model"]=model_route.model', text)
        self.assertNotIn('thread_params["mcp_servers"]', text)
        self.assertNotIn('thread_params["mcpServers"]', text)
        self.assertNotIn("thread_params['mcp_servers']", text)
        preflight = (ROOT / "src" / "dynosai_flow" / "codex_mcp_preflight.py").read_text(encoding="utf-8")
        self.assertIn('client.request("thread/start"', preflight)
        self.assertNotIn('client.request("turn/start"', preflight)
