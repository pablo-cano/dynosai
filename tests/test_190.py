import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_spec
from dynosai_flow.db import Database
from dynosai_flow.execution_profiles import require_local_runtime
from dynosai_flow.policy import PolicyError
from dynosai_flow.secrets import SecretBroker
from dynosai_flow.version import __version__


class DynosAI018ExecutionProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-190-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_strict_denies_dependency_install_and_keeps_human_gates(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Strict", allow_git_init=True)
        evidence = app.set_execution_profile("strict")
        self.assertEqual(evidence["profile"], "strict")
        self.assertEqual(evidence["network"], "offline")
        self.assertEqual(evidence["human_gates"], "required")
        self.assertFalse(evidence["os_network_enforcement"])
        self.assertEqual(evidence["enforcement"], "decision_only")
        with self.assertRaises(PolicyError):
            app.engine.execution.run(["pip", "install", "x"])
        autonomous = app.set_execution_profile("autonomous")
        self.assertEqual(autonomous["network"], "allowlist")
        self.assertEqual(autonomous["human_gates"], "required")
        self.assertNotEqual(autonomous["network"], "unrestricted")

    def test_vault_materializes_to_local_runtime_never_to_model(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Vault", allow_git_init=True)
        vault = self.tmp / ".dynosai" / "runtime"
        vault.mkdir(parents=True, exist_ok=True)
        (vault / "secret-vault.json").write_text(
            json.dumps({"secrets": {"TOKEN": "super-secret-value-018"}}),
            encoding="utf-8",
        )
        evidence = app.set_execution_profile("balanced")
        self.assertTrue(evidence["vault_configured"])
        self.assertEqual(evidence["secret_materialization"], "runtime_only")
        dumped = json.dumps(evidence)
        self.assertNotIn("super-secret-value-018", dumped)
        value = app.engine.execution.secrets.materialize_for_runtime("secret:TOKEN", "local")
        self.assertEqual(value, "super-secret-value-018")
        with self.assertRaises(PermissionError):
            app.engine.execution.secrets.materialize_for_model("secret:TOKEN")
        with self.assertRaises(PermissionError):
            app.engine.execution.secrets.materialize_for_runtime("secret:TOKEN", "docker")
        closed = SecretBroker()
        with self.assertRaises(PermissionError):
            closed.materialize_for_runtime("secret:TOKEN", "local")
        overview = json.dumps(app.project_overview())
        self.assertNotIn("super-secret-value-018", overview)

    def test_docker_and_vm_runtimes_are_refused(self):
        with self.assertRaises(PolicyError) as docker:
            require_local_runtime("docker")
        self.assertIn("not shipped", str(docker.exception))
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Runtime", allow_git_init=True)
        with self.assertRaises(PolicyError):
            app.engine.require_execution_runtime("vm")
        self.assertEqual(app.engine.require_execution_runtime("local"), "local")

    def test_code_review_and_bundle_include_policy_without_secrets(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Review", allow_git_init=True)
        work = app.engine.start("Create Fibonacci")
        app.engine.continue_work(work["id"])
        app.engine.submit_spec(work["id"], demo_spec(), "test-190")
        app.interactions.create(work_id=work["id"], kind="gate_approval", gate="code", message="Review code", requested_schema={"type": "object"})
        reviews = app.list_reviews()
        code = next(item for item in reviews if item.get("gate") == "code")
        policy = code["detail"]["execution_policy"]
        self.assertEqual(policy["profile"], "balanced")
        self.assertFalse(policy["os_network_enforcement"])
        bundle = app.engine.diagnostic_bundle()
        self.assertIn("execution_policy.json", bundle["files"])
        stats = app.engine.stats()
        self.assertEqual(stats["execution_policy"]["human_gates"], "required")
        self.assertEqual(stats["execution_policy"]["enforcement"], "decision_only")

    def test_schema_and_version_remain_governed(self):
        self.assertEqual(__version__, "1.0.0rc6")
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)


if __name__ == "__main__":
    unittest.main()
