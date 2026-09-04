import json
import stat
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.acceptance import CodexAppServerDriver, CursorAcceptanceDriver
from dynosai_flow.cli import open_agent, parser
from dynosai_flow.engine import DynosAI
from dynosai_flow.model_routing import (
    ACTIVITIES,
    BUILTIN_DEFAULTS,
    ModelRoute,
    ProviderModelRouting,
    activity_for_state,
    cursor_cli_selector,
    normalize_provider,
)
from dynosai_flow.version import __version__


class ModelRouting087Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        self.home = self.tmp / "home"
        self.project = self.tmp / "project"
        self.project.mkdir()

    def test_current_builtin_provider_defaults_and_aliases(self):
        routing = ProviderModelRouting(self.project, self.home)
        codex = routing.resolve_default("openai")
        cursor = routing.resolve_default("cursor")
        self.assertEqual(normalize_provider("openai"), "codex")
        self.assertEqual((codex.model, codex.effort), ("gpt-5.6-sol", "medium"))
        self.assertEqual((cursor.model, cursor.effort), ("grok-4.6", "medium"))
        self.assertEqual(BUILTIN_DEFAULTS["codex"]["model"], "gpt-5.6-sol")
        self.assertIn("planning", ACTIVITIES)
        self.assertEqual(activity_for_state("spec_review"), "specification")

    def test_precedence_project_activity_project_default_machine_activity_machine_default_builtin(self):
        routing = ProviderModelRouting(self.project, self.home)
        routing.set("codex", "machine-default", effort="low", scope="machine")
        routing.set("codex", "machine-plan", effort="high", activity="plan", scope="machine")
        routing.set("openai", "project-default", effort="medium", scope="project")
        routing.set("codex", "project-impl", effort="xhigh", activity="implementation", scope="project")

        self.assertEqual(routing.resolve("codex", "implementation").model, "project-impl")
        # Project default outranks a machine activity override by design.
        plan = routing.resolve("codex", "plan")
        self.assertEqual(plan.model, "project-default")
        self.assertEqual(plan.source, "project_default")
        self.assertEqual(routing.resolve("codex", "validation").model, "project-default")

        routing.reset("codex", scope="project")
        plan = routing.resolve("codex", "planning")
        self.assertEqual(plan.model, "machine-plan")
        self.assertEqual(plan.source, "machine_activity")
        routing.reset("codex", activity="plan", scope="machine")
        self.assertEqual(routing.resolve("codex", "planning").model, "machine-default")

    def test_cli_exposes_provider_model_configuration(self):
        args = parser().parse_args([
            "provider-model", "set", "--provider", "openai", "--model", "gpt-5.6-sol",
            "--effort", "medium", "--activity", "plan", "--scope", "project",
        ])
        self.assertEqual(args.command, "provider-model")
        self.assertEqual(args.provider, "openai")
        self.assertEqual(args.activity, "plan")
        show = ProviderModelRouting(self.project, self.home).show()
        self.assertIn("recommended_models", show["providers"]["cursor"])
        self.assertEqual(show["providers"]["cursor"]["recommended_models"][0]["model"], "grok-4.6")

    def test_cursor_selector_compiles_effort_for_deterministic_automation(self):
        route = ModelRoute("cursor", "implementation", "grok-4.6", "medium", "test", "test", "session_or_resume_boundary")
        self.assertEqual(cursor_cli_selector(route), "cursor-grok-4.6-medium")
        auto = ModelRoute("cursor", "implementation", "auto", None, "test", "test", "session_or_resume_boundary")
        self.assertEqual(cursor_cli_selector(auto), "auto")

    def test_cursor_driver_pins_and_verifies_requested_model(self):
        script = self.tmp / "cursor-agent"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json,sys\n"
            "a=sys.argv[1:]\n"
            "m=a[a.index('--model')+1] if '--model' in a else 'Auto'\n"
            "print(json.dumps({'type':'system','subtype':'init','model':m}))\n",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        route = ModelRoute("cursor", "discovery", "grok-4.6", "medium", "test", "test", "session_or_resume_boundary")
        logs = self.tmp / "cursor-logs"
        result = CursorAcceptanceDriver(str(script), 20).run(self.project, "prompt", logs, interaction_mode="auto", model_route=route)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["model_selector"], "cursor-grok-4.6-medium")
        self.assertEqual(result["model_observed"], "cursor-grok-4.6-medium")
        self.assertTrue(result["model_route_verified"], result)

    def test_codex_app_server_applies_model_and_effort_on_turn(self):
        fake = self.tmp / "codex"
        fake.write_text(r'''#!/usr/bin/env python3
import json,sys

def send(x): print(json.dumps(x),flush=True)
def recv():
    line=sys.stdin.readline()
    if not line: raise SystemExit(2)
    return json.loads(line)

m=recv(); assert m.get("method")=="initialize",m
send({"id":m["id"],"result":{"userAgent":"codex-cli test"}})
m=recv(); assert m.get("method")=="initialized",m
m=recv(); assert m.get("method")=="thread/start",m
assert m["params"].get("model")=="gpt-5.6-sol",m
send({"id":m["id"],"result":{"thread":{"id":"thr"}}})
m=recv(); assert m.get("method")=="turn/start",m
assert m["params"].get("model")=="gpt-5.6-sol",m
assert m["params"].get("effort")=="medium",m
send({"id":m["id"],"result":{"turn":{"id":"turn","status":"inProgress","items":[]}}})
send({"method":"thread/settings/updated","params":{"threadId":"thr","model":"gpt-5.6-sol","reasoningEffort":"medium"}})
send({"method":"turn/completed","params":{"threadId":"thr","turn":{"id":"turn","status":"completed"}}})
''', encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        route = ModelRoute("codex", "discovery", "gpt-5.6-sol", "medium", "test", "test", "turn_override")
        result = CodexAppServerDriver(str(fake), 20).run(self.project, "prompt", self.tmp / "codex-logs", interaction_mode="auto", model_route=route)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertTrue(result["model_route_verified"], result)
        self.assertEqual(result["model_settings_observed"]["model"], "gpt-5.6-sol")

    def test_open_agent_dry_run_uses_activity_route(self):
        root = self.tmp / "open-project"
        engine = DynosAI(root)
        engine.initialize("Routing", "python", "python -m unittest", None)
        routing = ProviderModelRouting(root, self.home)
        # open_agent uses the actual user home; project-scoped route is sufficient.
        project_routing = ProviderModelRouting(root)
        project_routing.set("cursor", "grok-4.6", effort="medium", activity="discovery", scope="project")
        result = open_agent(engine, "cursor", True)
        self.assertIn("--model", result["command"])
        self.assertIn("cursor-grok-4.6-medium", result["command"])
        self.assertEqual(result["model_route"]["activity"], "discovery")

    def test_version_is_087(self):
        self.assertEqual(__version__, "1.0.0rc5")


if __name__ == "__main__":
    unittest.main()
