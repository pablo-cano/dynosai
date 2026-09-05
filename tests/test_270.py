# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""RC7 certification integrity: archive safety, MCP 2026 strictness, MATRIX runner."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from dynosai_flow.acceptance import (
    RealProviderAcceptanceSuite,
    certification_infrastructure_retry_allowed,
)
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.certification_matrix import (
    collapse_observed_mcp_protocols,
    default_matrix_workspace,
    empty_trial,
    load_live_matrix,
    publicize_matrix,
)
from dynosai_flow.cli import demo_spec
from dynosai_flow.mcp import ElicitationExchange, MCPServer
from dynosai_flow.mcp_human_gates import input_request_key
from dynosai_flow.mcp_protocol import (
    CURRENT_PROTOCOL,
    INVALID_PARAMS_CODE,
    METHOD_NOT_FOUND_CODE,
    META_CLIENT_CAPABILITIES,
    META_CLIENT_INFO,
    META_PROTOCOL_VERSION,
    PROTOCOL_2025_06,
    PROTOCOL_2025_11,
    PROTOCOL_2026,
    UNSUPPORTED_PROTOCOL_CODE,
)
from dynosai_flow.release_manifest import list_release_files, source_tree_identity, write_source_zip
from dynosai_flow.version import DISPLAY_VERSION, __version__


ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "RC7-FAKE-SECRET-SENTINEL-NOT-A-REAL-CREDENTIAL"


def _meta(protocol: str, name: str = "ExampleClient", capabilities: dict | None = None) -> dict:
    return {
        META_PROTOCOL_VERSION: protocol,
        META_CLIENT_INFO: {"name": name, "version": "test"},
        META_CLIENT_CAPABILITIES: {} if capabilities is None else capabilities,
    }


def _2026(name: str = "Cursor", elicitation: bool = True) -> dict:
    caps = {"elicitation": {}} if elicitation else {}
    return {"_meta": _meta(PROTOCOL_2026, name, caps)}


class DynosAI270Rc7IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-270-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _spec_gate(self, provider: str = "cursor"):
        root = Path(tempfile.mkdtemp(prefix=f"dynosai-270-{provider}-", dir=self.tmp))
        app = DynosAIApplication(root)
        app.initialize_project(name="Demo", agent=provider)
        work = app.start_feature("Crear Fibonacci CLI", provider=provider)
        wid = work["id"]
        app.next_action(provider, wid, True)
        app.engine.submit_spec(wid, demo_spec(), "test-270")
        app.engine.continue_work(wid)
        return root, app, wid

    def test_version_is_rc7(self):
        self.assertEqual(__version__, "1.0.0rc7")
        self.assertEqual(DISPLAY_VERSION, "1.0.0-rc.7")

    def test_release_manifest_excludes_secrets_and_keeps_source(self):
        tree = self.tmp / "git-tree"
        tree.mkdir()
        (tree / ".gitignore").write_text(
            "\n".join([".env", ".env.*", ".dynosai/", ".pytest_cache/", "acceptance-logs/", "dist/"]) + "\n",
            encoding="utf-8",
        )
        src = tree / "src"
        src.mkdir()
        (src / "example.py").write_text("print('ok')\n", encoding="utf-8")
        (tree / ".env").write_text(SENTINEL, encoding="utf-8")
        (tree / ".env.local").write_text(SENTINEL, encoding="utf-8")
        dyn = tree / ".dynosai"
        dyn.mkdir()
        (dyn / "knowledge.db").write_bytes(b"sqlite")
        runtime = dyn / "runtime"
        runtime.mkdir()
        (runtime / "provider.log").write_text(SENTINEL, encoding="utf-8")
        cache = tree / ".pytest_cache"
        cache.mkdir()
        (cache / "x").write_text("cache", encoding="utf-8")
        logs = tree / "acceptance-logs"
        logs.mkdir()
        (logs / "trace.jsonl").write_text("{}\n", encoding="utf-8")
        dist = tree / "dist"
        dist.mkdir()
        (dist / "foo.whl").write_bytes(b"wheel")
        subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tree, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tree, check=True)
        subprocess.run(["git", "add", "src/example.py", ".gitignore"], cwd=tree, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tree, check=True)
        files = list_release_files(tree)
        self.assertIn("src/example.py", files)
        for forbidden in (
            ".env",
            ".env.local",
            ".dynosai/knowledge.db",
            ".dynosai/runtime/provider.log",
            ".pytest_cache/x",
            "acceptance-logs/trace.jsonl",
            "dist/foo.whl",
        ):
            self.assertNotIn(forbidden, files)
        archive = self.tmp / "out.zip"
        write_source_zip(tree, archive, prefix="dynosai-test")
        with zipfile.ZipFile(archive) as zipped:
            names = zipped.namelist()
            blob = b"".join(zipped.read(name) for name in names)
        self.assertTrue(any(name.endswith("src/example.py") for name in names))
        self.assertNotIn(SENTINEL.encode("utf-8"), blob)
        self.assertFalse(any(".env" in name.split("/") for name in names if name.endswith(".env") or "/.env." in f"/{name}"))
        self.assertFalse(any(".dynosai/" in name or name.endswith(".dynosai") for name in names))

    def test_release_manifest_fallback_without_git(self):
        tree = self.tmp / "plain"
        tree.mkdir()
        (tree / "src").mkdir()
        (tree / "src" / "example.py").write_text("ok\n", encoding="utf-8")
        (tree / ".env").write_text(SENTINEL, encoding="utf-8")
        (tree / ".dynosai").mkdir()
        (tree / ".dynosai" / "knowledge.db").write_bytes(b"x")
        files = list_release_files(tree)
        self.assertEqual(files, ["src/example.py"])

    def test_source_fingerprint_ignores_secrets_and_tracks_source(self):
        tree = self.tmp / "fp"
        tree.mkdir()
        (tree / "src").mkdir()
        (tree / "src" / "a.py").write_text("a\n", encoding="utf-8")
        first = source_tree_identity(tree)
        (tree / ".env").write_text(SENTINEL, encoding="utf-8")
        (tree / ".dynosai").mkdir()
        (tree / ".dynosai" / "knowledge.db").write_bytes(b"db")
        same = source_tree_identity(tree)
        self.assertEqual(first["source_tree_sha256"], same["source_tree_sha256"])
        (tree / "src" / "b.py").write_text("b\n", encoding="utf-8")
        changed = source_tree_identity(tree)
        self.assertNotEqual(first["source_tree_sha256"], changed["source_tree_sha256"])
        self.assertEqual(changed["source_file_count"], first["source_file_count"] + 1)

    def test_2026_meta_is_per_request_and_does_not_inherit(self):
        project = self.tmp / "mcp-meta"
        project.mkdir()
        server = MCPServer(project)
        first = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "server/discover",
            "params": _2026("Cursor"),
        })
        self.assertEqual(first["result"]["resultType"], "complete")
        second = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertIn("error", second)
        missing_version = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/list",
            "params": {"_meta": {META_CLIENT_CAPABILITIES: {}}},
        })
        self.assertEqual(missing_version["error"]["code"], INVALID_PARAMS_CODE)
        missing_caps = server.handle({
            "jsonrpc": "2.0", "id": 4, "method": "tools/list",
            "params": {"_meta": {META_PROTOCOL_VERSION: PROTOCOL_2026}},
        })
        self.assertEqual(missing_caps["error"]["code"], INVALID_PARAMS_CODE)
        server.handle({
            "jsonrpc": "2.0", "id": 5, "method": "tools/list",
            "params": {"_meta": _meta(PROTOCOL_2026, "Cursor", {"elicitation": {}})},
        })
        self.assertTrue(server.client_capabilities.elicitation_form)
        server.handle({
            "jsonrpc": "2.0", "id": 6, "method": "tools/list",
            "params": {"_meta": _meta(PROTOCOL_2026, "Cursor", {})},
        })
        self.assertFalse(server.client_capabilities.elicitation_form)
        unsupported = server.handle({
            "jsonrpc": "2.0", "id": 7, "method": "server/discover",
            "params": {"_meta": {META_PROTOCOL_VERSION: "1900-01-01", META_CLIENT_CAPABILITIES: {}}},
        })
        self.assertEqual(unsupported["error"]["code"], UNSUPPORTED_PROTOCOL_CODE)

    def test_legacy_initialize_handshake_unchanged(self):
        for protocol in (PROTOCOL_2025_11, PROTOCOL_2025_06):
            project = self.tmp / f"legacy-{protocol}"
            project.mkdir()
            server = MCPServer(project)
            init = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": protocol, "clientInfo": {"name": "Cursor", "version": "x"}, "capabilities": {"elicitation": {"form": {}}}},
            })
            self.assertEqual(init["result"]["protocolVersion"], protocol)
            self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            self.assertIn("tools", listed["result"])
            ping = server.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
            self.assertEqual(ping["result"], {})

    def test_2026_lifecycle_rejects_initialize_and_ping(self):
        project = self.tmp / "life"
        project.mkdir()
        server = MCPServer(project)
        init = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_2026, "clientInfo": {"name": "x"}, "capabilities": {}},
        })
        self.assertEqual(init["error"]["code"], METHOD_NOT_FOUND_CODE)
        discover = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "server/discover",
            "params": _2026(),
        })
        self.assertEqual(discover["result"]["resultType"], "complete")
        listed = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/list",
            "params": _2026(),
        })
        self.assertEqual(listed["result"]["resultType"], "complete")
        ping = server.handle({
            "jsonrpc": "2.0", "id": 4, "method": "ping",
            "params": _2026(),
        })
        self.assertEqual(ping["error"]["code"], METHOD_NOT_FOUND_CODE)

    def test_discover_instructions_are_public_and_provider_neutral(self):
        project = self.tmp / "disc"
        project.mkdir()
        server = MCPServer(project)
        cursor = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "server/discover",
            "params": _2026("cursor-agent"),
        })
        codex = server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "server/discover",
            "params": _2026("OpenAI Codex"),
        })
        self.assertEqual(cursor["result"]["cacheScope"], "public")
        self.assertEqual(codex["result"]["cacheScope"], "public")
        self.assertEqual(cursor["result"]["instructions"], codex["result"]["instructions"])
        text = cursor["result"]["instructions"]
        self.assertNotIn("structuredContent is authoritative", text)
        self.assertNotIn("Cursor CLI streams", text)

    def test_2026_mrtr_round_trip_and_authority(self):
        root, app, wid = self._spec_gate("cursor")
        server = MCPServer(root)
        first = server.handle({
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {"name": "dynosai_get_next_action", "arguments": {"work_id": wid}, **_2026("Cursor")},
        })
        self.assertNotIsInstance(first, ElicitationExchange)
        body = first["result"]
        self.assertEqual(body["resultType"], "input_required")
        self.assertIn("inputRequests", body)
        key = next(iter(body["inputRequests"]))
        self.assertEqual(body["inputRequests"][key]["method"], "elicitation/create")
        self.assertEqual(app.engine.db.one("SELECT status FROM human_interactions WHERE work_id=?", (wid,))["status"], "pending")
        accepted = server.handle({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": wid},
                "inputResponses": {key: {"action": "accept", "content": {"decision": "approve"}}},
                **_2026("Cursor"),
            },
        })
        self.assertEqual(accepted["result"]["resultType"], "complete")
        payload = accepted["result"]["structuredContent"]
        self.assertEqual(payload["work"]["state"], "plan_review")
        self.assertEqual(app.engine.db.one("SELECT status FROM human_interactions WHERE work_id=?", (wid,))["status"], "accepted")
        replay = server.handle({
            "jsonrpc": "2.0", "id": 12, "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": wid},
                "inputResponses": {key: {"action": "accept", "content": {"decision": "approve"}}},
                **_2026("Cursor"),
            },
        })
        self.assertEqual(replay["error"]["code"], INVALID_PARAMS_CODE)

    def test_2026_mrtr_decline_cancel_and_cross_work(self):
        root, app, wid = self._spec_gate("cursor")
        server = MCPServer(root)
        first = server.handle({
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "dynosai_get_next_action", "arguments": {"work_id": wid}, **_2026("Cursor")},
        })
        key = next(iter(first["result"]["inputRequests"]))
        declined = server.handle({
            "jsonrpc": "2.0", "id": 21, "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": wid},
                "inputResponses": {key: {"action": "decline", "content": {}}},
                **_2026("Cursor"),
            },
        })
        self.assertNotIn("error", declined, declined)
        self.assertEqual(app.engine.get_work(wid)["state"], "spec_review")
        self.assertEqual(app.engine.db.one("SELECT status FROM human_interactions WHERE id=?", (key.split("dynosai-gate-", 1)[-1],))["status"], "declined")

        root2, app2, wid2 = self._spec_gate("codex")
        server2 = MCPServer(root2)
        pending = server2.handle({
            "jsonrpc": "2.0", "id": 30, "method": "tools/call",
            "params": {"name": "dynosai_get_next_action", "arguments": {"work_id": wid2}, **_2026("OpenAI Codex")},
        })
        gate_key = next(iter(pending["result"]["inputRequests"]))
        cancel = server2.handle({
            "jsonrpc": "2.0", "id": 31, "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": wid2},
                "inputResponses": {gate_key: {"action": "cancel", "content": {}}},
                **_2026("OpenAI Codex"),
            },
        })
        self.assertNotIn("error", cancel, cancel)
        cancelled_work = app2.engine.db.one("SELECT active FROM work_items WHERE id=?", (wid2,))
        self.assertIn(int(cancelled_work["active"]), {0})

        root3, app3, wid3 = self._spec_gate("cursor")
        server3 = MCPServer(root3)
        gate = server3.handle({
            "jsonrpc": "2.0", "id": 40, "method": "tools/call",
            "params": {"name": "dynosai_get_next_action", "arguments": {"work_id": wid3}, **_2026("Cursor")},
        })
        stolen = next(iter(gate["result"]["inputRequests"]))
        mismatch = server3.handle({
            "jsonrpc": "2.0", "id": 41, "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": "DYN-9999"},
                "inputResponses": {stolen: {"action": "accept", "content": {"decision": "approve"}}},
                **_2026("Cursor"),
            },
        })
        self.assertEqual(mismatch["error"]["code"], INVALID_PARAMS_CODE)
        self.assertEqual(app3.engine.get_work(wid3)["state"], "spec_review")
        bogus = server3.handle({
            "jsonrpc": "2.0", "id": 42, "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": wid3},
                "inputResponses": {input_request_key("ELIC-NOPE"): {"action": "accept", "content": {"decision": "approve"}}},
                **_2026("Cursor"),
            },
        })
        self.assertEqual(bogus["error"]["code"], INVALID_PARAMS_CODE)

    def _spawn_mcp(self, root: Path, env: dict[str, str]):
        proc = subprocess.Popen(
            [sys.executable, "-m", "dynosai_flow.mcp", "--project", str(root)],
            cwd=root,
            env=env,
            text=True,
            encoding="utf-8",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        threading.Thread(target=proc.stderr.read, daemon=True).start()

        def send(obj):
            proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            proc.stdin.flush()

        def recv(timeout: float = 20.0):
            box: list[str] = []
            worker = threading.Thread(target=lambda: box.append(proc.stdout.readline()), daemon=True)
            worker.start()
            worker.join(timeout)
            if worker.is_alive() or not box or not box[0]:
                proc.kill()
                raise AssertionError("MCP stdio did not return a JSON-RPC line")
            return json.loads(box[0])

        return send, recv, proc

    def test_stdio_wire_smoke_modern_and_legacy(self):
        modern_root = self.tmp / "wire-modern"
        modern_root.mkdir()
        DynosAIApplication(modern_root).initialize_project(name="Wire", agent="cursor")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["DYNOSAI_AGENT_PROVIDER"] = "cursor"
        send, recv, proc = self._spawn_mcp(modern_root, env)
        send({"jsonrpc": "2.0", "id": "d1", "method": "server/discover", "params": {"_meta": _meta(PROTOCOL_2026, "WireCafé", {"elicitation": {}})}})
        discover = recv()
        self.assertEqual(discover["result"]["resultType"], "complete")
        send({"jsonrpc": "2.0", "id": "l1", "method": "tools/list", "params": _2026("WireCafé")})
        listed = recv()
        self.assertEqual(listed["result"]["resultType"], "complete")
        send({"jsonrpc": "2.0", "id": "c1", "method": "tools/call", "params": {"name": "dynosai_stats", "arguments": {}, **_2026("WireCafé")}})
        called = recv()
        self.assertEqual(called["result"]["resultType"], "complete")
        send({"jsonrpc": "2.0", "id": "bad", "method": "tools/list", "params": {}})
        missing = recv()
        self.assertIn("error", missing)
        send({"jsonrpc": "2.0", "id": "unsup", "method": "server/discover", "params": {"_meta": {META_PROTOCOL_VERSION: "1900-01-01", META_CLIENT_CAPABILITIES: {}}}})
        unsup = recv()
        self.assertEqual(unsup["error"]["code"], UNSUPPORTED_PROTOCOL_CODE)
        proc.kill()

        legacy_root = self.tmp / "wire-legacy"
        legacy_root.mkdir()
        DynosAIApplication(legacy_root).initialize_project(name="Legacy", agent="cursor")
        lsend, lrecv, legacy = self._spawn_mcp(legacy_root, env)
        lsend({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_2025_11, "clientInfo": {"name": "Cursor"}, "capabilities": {"elicitation": {"form": {}}}}})
        self.assertEqual(lrecv()["result"]["protocolVersion"], PROTOCOL_2025_11)
        lsend({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        lsend({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertIn("tools", lrecv()["result"])
        lsend({"jsonrpc": "2.0", "id": 3, "method": "ping"})
        self.assertEqual(lrecv()["result"], {})
        legacy.kill()

        gated_root, _gated_app, gated_wid = self._spec_gate("cursor")
        msend, mrecv, mrtr = self._spawn_mcp(gated_root, env)
        msend({"jsonrpc": "2.0", "id": "d", "method": "server/discover", "params": _2026("Cursor")})
        self.assertNotEqual(mrecv().get("method"), "elicitation/create")
        msend({"jsonrpc": "2.0", "id": "g", "method": "tools/call", "params": {"name": "dynosai_get_next_action", "arguments": {"work_id": gated_wid}, **_2026("Cursor")}})
        gate = mrecv()
        self.assertNotEqual(gate.get("method"), "elicitation/create")
        self.assertEqual(gate["result"]["resultType"], "input_required")
        req_key = next(iter(gate["result"]["inputRequests"]))
        msend({
            "jsonrpc": "2.0", "id": "g2", "method": "tools/call",
            "params": {
                "name": "dynosai_get_next_action",
                "arguments": {"work_id": gated_wid},
                "inputResponses": {req_key: {"action": "accept", "content": {"decision": "approve"}}},
                **_2026("Cursor"),
            },
        })
        done = mrecv()
        self.assertEqual(done["result"]["resultType"], "complete")
        mrtr.kill()

    def test_matrix_default_workspace_is_outside_repo(self):
        workspace = default_matrix_workspace(ROOT)
        with self.assertRaises(ValueError):
            workspace.resolve().relative_to(ROOT.resolve())
        self.assertEqual(workspace.name, "dynosai-matrix-1.0")

    def test_matrix_certification_has_no_infrastructure_retry(self):
        child = {
            "status": "failed",
            "final_state": "inbox",
            "provider_run": {"termination_reason": "idle_timeout"},
            "elicitations": [],
            "scope_requests": [],
            "context_optimization": {"native_file_change_actions": 0},
            "mcp_failures": 0,
            "mcp_rejections": 0,
        }
        self.assertTrue(certification_infrastructure_retry_allowed(2, 1, child, None))
        self.assertFalse(certification_infrastructure_retry_allowed(1, 1, child, None))
        suite = RealProviderAcceptanceSuite(providers=("cursor",), scenario="fibonacci", certification_mode=True)
        self.assertEqual(suite.max_infrastructure_attempts, 1)
        debug = RealProviderAcceptanceSuite(providers=("cursor",), scenario="fibonacci")
        self.assertEqual(debug.max_infrastructure_attempts, 2)

    def test_observed_protocol_is_not_current_protocol_default(self):
        trial = empty_trial(provider="codex", mode="greenfield")
        self.assertIsNone(trial["mcp_protocol_version"])
        self.assertEqual(trial["mcp_protocols_observed"], [])
        self.assertNotEqual(trial["mcp_protocol_version"], CURRENT_PROTOCOL)
        none = collapse_observed_mcp_protocols([])
        self.assertIsNone(none["mcp_protocol_version"])
        one = collapse_observed_mcp_protocols([PROTOCOL_2025_11])
        self.assertEqual(one["mcp_protocol_version"], PROTOCOL_2025_11)
        many = collapse_observed_mcp_protocols([PROTOCOL_2025_11, PROTOCOL_2026])
        self.assertIsNone(many["mcp_protocol_version"])
        self.assertEqual(many["mcp_protocols_observed"], [PROTOCOL_2025_11, PROTOCOL_2026])

    def test_public_matrix_has_no_machine_local_paths(self):
        matrix = load_live_matrix()
        public = publicize_matrix(matrix)
        blob = json.dumps(public)
        self.assertNotIn("OneDrive", blob)
        self.assertNotIn("C:\\\\Users\\\\", blob)
        self.assertNotIn("C:\\Users\\", blob)
        self.assertFalse(matrix["all_passed"])
        counts = [len(cell.get("trials") or []) for cell in matrix["cells"]]
        self.assertEqual(len(counts), 4)
        self.assertGreaterEqual(min(counts), 2)
        for cell in public["cells"]:
            for trial in cell.get("trials") or []:
                for path in trial.get("artifact_paths") or []:
                    self.assertFalse(":" in str(path) or str(path).startswith("/Users/"))
                for ref in trial.get("artifact_refs") or []:
                    self.assertTrue(ref.get("local_only"))
                    self.assertIn("sha256", ref)


if __name__ == "__main__":
    unittest.main()
