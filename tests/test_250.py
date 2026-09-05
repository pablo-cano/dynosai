import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import create_server
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.capability_manifests import load_extension_pack, provider_manifest, require_adapter
from dynosai_flow.certification_matrix import summarize_live_matrix
from dynosai_flow.db import Database
from dynosai_flow.execution_profiles import require_local_runtime
from dynosai_flow.execution_runtime import LocalExecutionRuntime
from dynosai_flow.mcp import LEGACY_TOOLS, TOOLS
from dynosai_flow.policy import GitCommandPolicy, PathPolicyEngine, PolicyError
from dynosai_flow.secrets import SecretBroker
from dynosai_flow.version import DISPLAY_VERSION, __version__


ROOT = Path(__file__).resolve().parents[1]


class DynosAI250Rc5ThreatModelFreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-250-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_threat_model_and_security_policy_are_honest(self):
        threat = (ROOT / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("docs/THREAT_MODEL.md", security)
        self.assertIn("## Enforced", threat)
        self.assertIn("## Not enforced", threat)
        for item in (
            "path roots",
            "symlink",
            "secret",
            "certified-client",
            "Git",
            "human gates",
            "execution profile",
            "process timeout",
            "loopback",
            "Host",
        ):
            self.assertIn(item, threat)
        for item in (
            "OS network sandbox",
            "Docker",
            "VM",
            "model obedience",
            "uncertified clients",
            "remote execution",
        ):
            self.assertIn(item, threat)
        self.assertIn("not a full security boundary", threat.lower())
        self.assertNotIn("loopback alone is a full security boundary", threat.lower())
        self.assertNotRegex(threat, r"(?i)os-level network (interception|sandbox).{0,40}enforced")

    def test_contributing_keeps_external_prs_closed_and_documents_certification(self):
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("not currently accepted", contributing.lower())
        self.assertIn("not currently accepted", readme.lower())
        self.assertNotIn("pull requests are welcome", contributing.lower())
        self.assertIn("GitHub Issues", contributing)
        self.assertIn("python -m pytest", contributing)
        self.assertIn("Cursor ACP", contributing)
        self.assertIn("Codex app-server", contributing)
        self.assertIn("MATRIX_1.0", contributing)
        self.assertIn("schema v6", contributing.lower())

    def test_enforced_path_secret_symlink_and_git_boundaries(self):
        root = self.tmp / "proj"
        root.mkdir()
        (root / "src").mkdir()
        (root / "src" / "ok.py").write_text("x=1\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        policy = PathPolicyEngine(root)
        self.assertFalse(policy.decision(root.parent / "outside.txt", "read").allowed)
        self.assertFalse(policy.decision(".env", "read").allowed)
        self.assertTrue(policy.decision("src/ok.py", "read").allowed)
        outside = self.tmp / "secret.txt"
        outside.write_text("token\n", encoding="utf-8")
        link = root / "inside.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks not permitted on this Windows configuration")
        self.assertFalse(PathPolicyEngine(root).decision("inside.txt", "read").allowed)
        allowed, _reason = GitCommandPolicy.allowed(["status"])
        self.assertTrue(allowed)
        blocked, reason = GitCommandPolicy.allowed(["branch", "-M", "rewrite"])
        self.assertFalse(blocked)
        self.assertIn("blocked", reason)
        broker = SecretBroker()
        with self.assertRaises(PermissionError):
            broker.materialize_for_model("secret:TOKEN")

    def test_enforced_certified_client_human_gates_timeout_and_unsupported_runtimes(self):
        with self.assertRaises(ValueError):
            provider_manifest("vscode")
        with self.assertRaises(ValueError):
            require_adapter("windsurf-acp")
        with self.assertRaises(ValueError):
            load_extension_pack("quality-pack")
        with self.assertRaises(PolicyError):
            require_local_runtime("docker")
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Freeze", allow_git_init=True)
        autonomous = app.set_execution_profile("autonomous")
        self.assertEqual(autonomous["human_gates"], "required")
        self.assertFalse(autonomous["os_network_enforcement"])
        runtime = LocalExecutionRuntime(self.tmp)
        result = runtime.run(["python", "-c", "import time; time.sleep(2)"], timeout=1)
        self.assertTrue(result.timed_out)

    def test_studio_loopback_host_and_json_post_are_enforced_but_not_a_full_boundary(self):
        with self.assertRaises(ValueError):
            create_server(self.tmp, host="0.0.0.0", port=0)
        server = create_server(self.tmp, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        host, port = server.server_address[:2]
        health_url = f"http://{host}:{port}/api/health"
        with urllib.request.urlopen(health_url, timeout=3) as response:
            self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
            self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])
        bad_host = urllib.request.Request(health_url, headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as forbidden:
            urllib.request.urlopen(bad_host, timeout=3)
        self.assertEqual(forbidden.exception.code, 403)
        post = urllib.request.Request(
            f"http://{host}:{port}/api/projects/close",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "text/plain", "Host": f"{host}:{port}"},
        )
        with self.assertRaises(urllib.error.HTTPError) as unsupported:
            urllib.request.urlopen(post, timeout=3)
        self.assertEqual(unsupported.exception.code, 415)

    def test_freeze_keeps_matrix_mcp_schema_and_studio_honesty(self):
        names = {str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}
        self.assertEqual(len(names), 31)
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)
        self.assertEqual(__version__, "1.0.0rc8")
        self.assertEqual(DISPLAY_VERSION, "1.0.0-rc.8")
        report = summarize_live_matrix()
        self.assertFalse(report["copied_from_historical"] if "copied_from_historical" in report else False)
        self.assertFalse(report["all_passed"])
        for status in report["cells"].values():
            self.assertIn(status, {"not_run", "run", "fail", "pass"})
            if status != "pass":
                self.assertNotEqual(status, "copied")
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("help.security", i18n)
        self.assertIn("not a full security boundary", i18n)
        self.assertIn("no es un límite de seguridad completo", i18n)
        self.assertIn("help.security", html)


if __name__ == "__main__":
    unittest.main()
