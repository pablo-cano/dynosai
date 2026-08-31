import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_spec
from dynosai_flow.context_handles import ContextHandleStore
from dynosai_flow.cost_telemetry import governed_change_cost
from dynosai_flow.eval_registry import EvalRegistry, compare_records, scenario
from dynosai_flow.execution_runtime import LocalExecutionRuntime
from dynosai_flow.harness_contracts import classify_execution_state, harness_enabled
from dynosai_flow.mcp import MCPServer, phase_tool_surface
from dynosai_flow.policy import DependencyPolicy, NetworkPolicy, PathPolicyEngine, PolicyError
from dynosai_flow.secrets import SecretBroker, redact_text
from dynosai_flow.util import json_dumps, utc_now
from dynosai_flow.validation_integrity import evaluate_integrity


class DynosAI015HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-160-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_path_policy_rejects_escape_and_secrets(self):
        root = self.tmp / "proj"
        root.mkdir()
        (root / "src").mkdir()
        (root / "src" / "ok.py").write_text("x=1\n", encoding="utf-8")
        (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        policy = PathPolicyEngine(root)
        escape = policy.decision(root.parent / "outside.txt", "read")
        self.assertFalse(escape.allowed)
        self.assertIn("escapes", escape.reason)
        secret = policy.decision(".env", "read")
        self.assertFalse(secret.allowed)
        self.assertIn("sensitive", secret.reason)
        ok = policy.decision("src/ok.py", "read")
        self.assertTrue(ok.allowed)
        with self.assertRaises(PolicyError):
            policy.require(".dynosai/knowledge.db", "write")

    def test_symlink_escape_is_denied_when_supported(self):
        root = self.tmp / "proj"
        root.mkdir()
        outside = self.tmp / "secret.txt"
        outside.write_text("token\n", encoding="utf-8")
        link = root / "inside.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlinks not permitted on this Windows configuration")
        decision = PathPolicyEngine(root).decision("inside.txt", "read")
        self.assertFalse(decision.allowed)

    def test_network_and_dependency_policy(self):
        self.assertTrue(NetworkPolicy("unrestricted").decide("example.com").allowed)
        self.assertFalse(NetworkPolicy("offline").decide("example.com").allowed)
        allow = NetworkPolicy("allowlist", allowlist=("pypi.org",))
        self.assertTrue(allow.decide("pypi.org").allowed)
        self.assertFalse(allow.decide("evil.example").allowed)
        deny = NetworkPolicy("default_deny")
        self.assertFalse(deny.decide("example.com").allowed)
        self.assertEqual(DependencyPolicy.classify(["pytest", "-q"]), "use")
        self.assertEqual(DependencyPolicy.classify(["python", "-m", "pip", "install", "foo"]), "install")
        compat = DependencyPolicy("compatibility").decide(["pip", "install", "foo"])
        self.assertTrue(compat.allowed)
        governed = DependencyPolicy("governed").decide(["pip", "install", "foo"])
        self.assertFalse(governed.allowed)
        self.assertTrue(DependencyPolicy("governed").decide(["pytest"]).allowed)

    def test_secret_redaction_and_broker(self):
        text = redact_text("token=ghp_abcdefghijklmnopqrstuvwxyz1234 and sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertNotIn("ghp_", text)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", text)
        broker = SecretBroker()
        ref = broker.reference("OPENAI_API_KEY")
        self.assertEqual(ref, "secret:OPENAI_API_KEY")
        with self.assertRaises(PermissionError):
            broker.materialize_for_model(ref)
        with self.assertRaises(PermissionError):
            broker.materialize_for_runtime(ref, "local")

    def test_context_handles_reference_then_retrieve(self):
        store = ContextHandleStore(self.tmp)
        blob = "diff --git a/x.py b/x.py\n" + ("+line\n" * 3000)
        wrapped = store.wrap_tool_result(
            "dynosai_git_diff",
            {"files": ["x.py"], "diff": blob, "denied": [], "truncated": False},
            enabled=True,
            inline_limit=1024,
        )
        handle_id = wrapped["context_handle"]["id"]
        self.assertTrue(handle_id.startswith("HDL-"))
        self.assertTrue(wrapped.get("diff_omitted"))
        self.assertLess(len(wrapped["diff"]), len(blob))
        self.assertGreater(wrapped["context_handle_metrics"]["bytes_omitted"], 0)
        retrieved = store.retrieve(handle_id, offset=0, limit=40)
        self.assertTrue(retrieved["truncated"])
        self.assertEqual(retrieved["content"], blob[:40])
        full = store.retrieve(handle_id)
        self.assertEqual(full["content"], blob)
        inline = store.wrap_tool_result(
            "dynosai_git_diff",
            {"files": ["x.py"], "diff": blob, "denied": [], "truncated": False},
            enabled=False,
            inline_limit=1024,
        )
        self.assertEqual(inline["diff"], blob)
        self.assertEqual(inline["context_handle_metrics"]["bytes_omitted"], 0)

    def test_execution_runtime_enforces_roots_and_timeout(self):
        root = self.tmp / "proj"
        root.mkdir()
        (root / "ok.txt").write_text("hello\n", encoding="utf-8")
        runtime = LocalExecutionRuntime(root)
        self.assertEqual(runtime.read_text("ok.txt").replace("\r\n", "\n"), "hello\n")
        with self.assertRaises(PolicyError):
            runtime.write_text(".env", "SECRET=1\n")
        runtime.dependencies = DependencyPolicy("governed")
        with self.assertRaises(PolicyError):
            runtime.run(["pip", "install", "x"])
        result = runtime.run(["python", "-c", "print('ok')"], timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertIn("ok", result.stdout)
        timed = runtime.run(["python", "-c", "import time; time.sleep(5)"], timeout=1)
        self.assertTrue(timed.timed_out)
        worktree = root.parent / ".dynosai-worktrees" / root.name / "DYN-1"
        worktree.mkdir(parents=True)
        allowed = runtime.run(["python", "-c", "print('wt')"], cwd=worktree, timeout=30)
        self.assertEqual(allowed.returncode, 0)
        outsider = root.parent / "not-a-worktree"
        outsider.mkdir()
        with self.assertRaises(PolicyError):
            runtime.run(["python", "-c", "print('no')"], cwd=outsider, timeout=30)

    def test_loop_state_preserves_workflow_on_interrupt_and_context_exhaustion(self):
        interrupted = classify_execution_state(work_state="implementing", provider_interrupted=True)
        self.assertTrue(interrupted["can_resume"])
        self.assertFalse(interrupted["terminal"])
        exhausted = classify_execution_state(work_state="code_review", context_exhausted=True)
        self.assertTrue(exhausted["can_resume"])
        self.assertEqual(exhausted["status"], "context_exhausted")
        scope = classify_execution_state(work_state="implementing", material_scope_change=True)
        self.assertTrue(scope["requires_human"])
        self.assertFalse(scope["requires_replan"])
        done = classify_execution_state(work_state="done")
        self.assertTrue(done["terminal"])
        budget = classify_execution_state(work_state="implementing", retry_count=3, retry_budget=3)
        self.assertEqual(budget["status"], "retry_budget_exhausted")

    def test_validation_integrity_detects_requirement_gaps(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Integrity", allow_git_init=True)
        work = app.engine.start("Add a feature")
        app.engine.continue_work(work["id"])
        app.engine.submit_spec(work["id"], demo_spec(), "test-160")
        report = evaluate_integrity(app.engine, work["id"], risk_level="high")
        self.assertFalse(report["requirements_satisfied"])
        self.assertTrue(report["blocking_gaps"])
        self.assertEqual(report["completion_standard"], "dynosai_core")

    def test_eval_registry_context_handles_and_skill_toggle(self):
        registry = EvalRegistry(self.tmp)
        self.assertEqual(scenario("context_pressure")["version"], "1")
        off = registry.run_offline("context_pressure", harness={"context_handles": False})
        on = registry.run_offline("context_pressure", harness={"context_handles": True})
        self.assertTrue(off["success"])
        self.assertTrue(on["success"])
        self.assertGreater(on["context_bytes_omitted"], off["context_bytes_omitted"])
        comparison = compare_records(off, on)
        self.assertGreater(comparison["delta"]["context_bytes_omitted"], 0)
        interrupted = registry.run_offline("provider_interruption")
        self.assertTrue(interrupted["success"])
        skills = registry.skill_eval()
        self.assertEqual(skills["without_skill"]["mode"], "minimal")
        self.assertGreater(len(skills["with_skill"]["skills"]), len(skills["without_skill"]["skills"]))
        self.assertIn("on-demand", skills["claim"])

    def test_governed_change_cost_is_raw_usage(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Cost", allow_git_init=True)
        work = app.engine.start("Track cost")
        app.engine.db.audit("MCPToolCalled", "dynosai_git_diff", {"work_id": work["id"]})
        cost = governed_change_cost(
            app.engine.db, work["id"],
            usage={"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 10},
            provider="codex", model="gpt-5.6-luna",
        )
        self.assertEqual(cost["metric"], "cost_per_successful_governed_change")
        self.assertFalse(cost["success"])
        self.assertEqual(cost["tool_calls"], 1)
        self.assertEqual(cost["input_tokens"], 100)
        self.assertIsNotNone(cost["estimated_list_price_usd"])

    def test_mcp_retrieve_handle_and_phase_surface(self):
        self.assertIn("dynosai_retrieve_handle", phase_tool_surface("implementation")["tools"])
        self.assertIn("dynosai_retrieve_handle", phase_tool_surface("discovery")["tools"])
        project = self.tmp / "mcp"
        project.mkdir()
        DynosAIApplication(project).initialize_project(name="MCP", allow_git_init=True)
        server = MCPServer(project)
        meta = server.handles.put("log", "unit-test", "abcdefghijklmnopqrstuvwxyz")
        payload = server.call_tool("dynosai_retrieve_handle", {"handle_id": meta["id"], "offset": 0, "limit": 5})
        self.assertEqual(payload["content"], "abcde")
        self.assertTrue(payload["truncated"])

    def test_code_review_exposes_completion_chain(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Review", allow_git_init=True)
        work = app.engine.start("Create Fibonacci")
        app.engine.continue_work(work["id"])
        app.engine.submit_spec(work["id"], demo_spec(), "test-160")
        app.interactions.create(work_id=work["id"], kind="gate_approval", gate="code", message="Review code", requested_schema={"type": "object"})
        reviews = app.list_reviews()
        code = next(item for item in reviews if item.get("gate") == "code")
        self.assertIn("integrity", code["detail"])
        self.assertIn("diff", code["detail"])
        self.assertIn("validations", code["detail"])
        self.assertIn("requirements", code["detail"]["spec"])

    def test_validations_execute_through_local_runtime(self):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name="Hands", allow_git_init=True)
        self.assertIsInstance(app.engine.execution, LocalExecutionRuntime)
        work = app.engine.start("Probe validation")
        now = utc_now()
        app.engine.db.execute(
            "INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            ("probe", json_dumps(["python", "-c", "print('harness-ok')"]), "test-160", 1, now, now),
        )
        result = app.engine.run_validations(work["id"], record=True, profiles=["probe"], cwd=self.tmp)
        self.assertTrue(result["passed"])
        self.assertIn("harness-ok", result["results"][0]["output"])

    def test_harness_flags_are_independently_toggleable(self):
        self.assertTrue(harness_enabled("context_handles"))
        os.environ["DYNOSAI_HARNESS_SKILLS"] = "0"
        self.addCleanup(lambda: os.environ.pop("DYNOSAI_HARNESS_SKILLS", None))
        self.assertFalse(harness_enabled("skills"))
        self.assertTrue(harness_enabled("retrieval"))


if __name__ == "__main__":
    unittest.main()
