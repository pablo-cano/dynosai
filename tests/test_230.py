import json
import os
import shutil
import tempfile
import unittest
import zipfile
from importlib import resources
from pathlib import Path

from dynosai_flow.app_server import StudioAPI
from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cost_telemetry import aggregate_governed_change_cost, governed_change_cost
from dynosai_flow.db import Database
from dynosai_flow.eval_intelligence import MAX_CASES, load_cases
from dynosai_flow.eval_registry import EvalRegistry
from dynosai_flow.mcp import CURRENT_PROTOCOL, LEGACY_TOOLS, TOOLS, MCPServer
from dynosai_flow.prompt_prefix import build_authority_prefix, compose_prompt, persist_prefix
from dynosai_flow.util import utc_now
from dynosai_flow.version import DISPLAY_VERSION, __version__


def _acceptance_zip(path: Path, *, failures: int = 2, passed: int = 1) -> Path:
    children = []
    for index in range(failures):
        children.append({
            "provider": "cursor" if index % 2 == 0 else "codex",
            "scenario": "fibonacci" if index % 2 == 0 else "orderflow-contract-discounts",
            "status": "failed",
            "mcp_failures": 1,
            "failure_kind": "validation",
            "work_id": f"WORK-FAIL-{index}",
        })
    for index in range(passed):
        children.append({
            "provider": "codex",
            "scenario": "fibonacci",
            "status": "passed",
            "mcp_failures": 0,
        })
    summary = {
        "acceptance_version": 9,
        "status": "failed",
        "children": children,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("suite/summary-final.json", json.dumps(summary))
        archive.writestr("README.txt", "DynosAI real-provider acceptance bundle\n")
    return path


class DynosAI230Rc3EvalMaturityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-230-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def _app(self, name="EvalMature"):
        app = DynosAIApplication(self.tmp)
        app.initialize_project(name=name, allow_git_init=True)
        return app

    def test_acceptance_zip_imports_bounded_eval_registry_cases(self):
        app = self._app()
        bundle = _acceptance_zip(self.tmp / "acceptance.zip", failures=3, passed=1)
        result = app.engine.import_acceptance_bundle(str(bundle))
        self.assertFalse(result["spawn_provider"])
        self.assertFalse(result["auto_start"])
        self.assertTrue(result["bounded"])
        self.assertEqual(result["imported"], 3)
        self.assertEqual(len(result["cases"]), 3)
        self.assertTrue(all(case.get("status") == "open" for case in result["cases"]))
        self.assertTrue(all((case.get("detail") or {}).get("source") == "acceptance_zip" for case in result["cases"]))
        records = list((self.tmp / ".dynosai" / "runtime" / "eval-results").glob("*.json"))
        self.assertGreaterEqual(len(records), 3)
        for path in records:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn(payload["scenario"], {item["id"] for item in EvalRegistry(self.tmp).list_scenarios()})
        huge = _acceptance_zip(self.tmp / "huge.zip", failures=MAX_CASES + 5, passed=0)
        bounded = app.engine.import_acceptance_bundle(str(huge))
        self.assertLessEqual(bounded["imported"], MAX_CASES)
        self.assertLessEqual(len(load_cases(self.tmp)), MAX_CASES)

    def test_imported_improvement_stays_inbox_without_spawn(self):
        app = self._app("InboxOnly")
        bundle = _acceptance_zip(self.tmp / "fail.zip", failures=1, passed=0)
        imported = app.engine.import_acceptance_bundle(str(bundle))
        case_id = imported["cases"][0]["case_id"]
        proposed = app.propose_eval_improvement(case_id)
        self.assertEqual(proposed["work"]["state"], "inbox")
        self.assertFalse(proposed["spawn_provider"])
        self.assertFalse(proposed["auto_start"])
        self.assertFalse(proposed.get("auto_start_provider", False))
        overview = app.project_overview()
        self.assertTrue(overview["eval_intelligence"]["proposed_cases"])
        self.assertFalse(overview["eval_intelligence"]["auto_start_provider"])
        self.assertEqual(overview["eval_intelligence"]["predictive_routing"], "shadow")

    def test_governed_change_cost_and_aggregate_scorecard_surfaces(self):
        app = self._app("CostCard")
        work = app.engine.start("Measure a governed change")
        now = utc_now()
        app.engine.db.execute(
            "INSERT INTO validations(work_id,command,status,exit_code,output,created_at) VALUES(?,?,?,?,?,?)",
            (work["id"], "pytest", "passed", 0, "ok", now),
        )
        app.engine.db.audit("MCPToolCalled", "dynosai_git_diff", {"work_id": work["id"], "duration_ms": 12, "result_bytes": 80})
        app.engine.db.audit("MCPToolRejected", "dynosai_submit_spec", {"work_id": work["id"]})
        app.engine.db.audit("MCPToolNormalized", "dynosai_read", {"work_id": work["id"]})
        app.engine.db.execute("UPDATE work_items SET state=? WHERE id=?", ("done", work["id"]))
        cost = governed_change_cost(
            app.engine.db,
            work["id"],
            usage={
                "input_tokens": 200,
                "cached_input_tokens": 40,
                "output_tokens": 30,
                "reasoning_output_tokens": 8,
                "model_calls": 2,
            },
            provider="codex",
            model="gpt-5.6-luna",
            elapsed_seconds=9.5,
        )
        self.assertTrue(cost["success"])
        self.assertEqual(cost["validation_status"], "passed")
        self.assertEqual(cost["fresh_input_tokens"], 160)
        self.assertEqual(cost["cached_input_tokens"], 40)
        self.assertEqual(cost["reasoning_output_tokens"], 8)
        self.assertEqual(cost["mcp_calls"], 1)
        self.assertEqual(cost["mcp_failures"], 0)
        self.assertEqual(cost["mcp_rejections"], 1)
        self.assertEqual(cost["mcp_normalizations"], 1)
        self.assertEqual(cost["mcp_duration_ms"], 12)
        self.assertEqual(cost["mcp_result_bytes"], 80)
        self.assertIsNotNone(cost["estimated_list_price_usd"])
        self.assertNotIn("unused_mcp_tool_waste", cost)
        aggregate = aggregate_governed_change_cost(app.engine.db)
        self.assertEqual(aggregate["completed_work"], 1)
        self.assertEqual(aggregate["successful"], 1)
        stats = app.engine.stats()
        self.assertEqual(stats["governed_change"]["completed_work"], 1)
        self.assertIn("mcp_tool_surface_size", stats["governed_change"])
        self.assertEqual(stats["governed_change"]["mcp_tool_surface_note"], "experimental_context_overhead")
        overview = app.project_overview()
        self.assertEqual(overview["governed_change"]["completed_work"], 1)
        scorecard = app.engine.scorecard()
        self.assertEqual(scorecard["governed_change"]["completed_work"], 1)
        bundle = app.engine.diagnostic_bundle(str(self.tmp / "diag.zip"))
        with zipfile.ZipFile(bundle["bundle"]) as archive:
            payload = json.loads(archive.read("governed_change.json"))
        self.assertEqual(payload["completed_work"], 1)
        api = StudioAPI(self.tmp)
        code, body = api.get("/api/overview")
        self.assertEqual(code, 200)
        self.assertEqual(body["governed_change"]["completed_work"], 1)

    def test_unused_advertised_mcp_tool_is_not_waste(self):
        app = self._app("NoWaste")
        work = app.engine.start("Unused tools are availability")
        app.engine.db.audit("MCPToolCalled", "dynosai_stats", {"work_id": work["id"]})
        cost = governed_change_cost(app.engine.db, work["id"])
        advertised = {str(item["name"]) for item in TOOLS}
        used = {"dynosai_stats"}
        unused = advertised - used
        self.assertGreater(len(unused), 0)
        self.assertNotIn("unused_mcp_waste", cost)
        self.assertNotIn("wasted_mcp", cost)
        self.assertNotEqual(cost.get("quality_note"), "unused advertised MCP tool = wasted MCP")
        aggregate = aggregate_governed_change_cost(app.engine.db)
        self.assertNotEqual(aggregate.get("waste_metric"), "unused_advertised_mcp_tool")
        self.assertIn("mcp_tool_surface_size", aggregate)
        self.assertEqual(aggregate["mcp_tool_surface_size"], len({str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}))
        self.assertEqual(aggregate["mcp_tool_surface_note"], "experimental_context_overhead")

    def test_stable_authority_prefix_is_cacheable_structure_not_cache_hit(self):
        first = build_authority_prefix()
        second = build_authority_prefix()
        self.assertEqual(first["schema"], "PROMPT_PREFIX_1.0")
        self.assertEqual(first["hash"], second["hash"])
        self.assertFalse(first["claims_cache_hit"])
        self.assertFalse(first["tools_listed"])
        for name in {str(item["name"]) for item in TOOLS}:
            self.assertNotIn(name, first["text"])
        self.assertIn("tools/list", first["text"])
        persisted = persist_prefix(self.tmp, first)
        self.assertTrue(Path(persisted["path"]).exists())
        payload = json.loads(Path(persisted["path"]).read_text(encoding="utf-8"))
        self.assertEqual(payload["hash"], first["hash"])
        self.assertFalse(payload["claims_cache_hit"])
        composed = compose_prompt(first, "phase capsule: implementation")
        self.assertIn(first["text"], composed["prompt"])
        self.assertTrue(composed["prompt"].endswith("phase capsule: implementation") or "phase capsule: implementation" in composed["suffix"])
        self.assertNotEqual(composed["prefix_hash"], composed.get("suffix_hash"))
        self.assertFalse(composed["claims_cache_hit"])
        app = self._app("Prefix")
        stored = persist_prefix(app.engine.root, build_authority_prefix())
        app.engine.db.audit("PromptPrefixRecorded", stored["hash"], {"claims_cache_hit": False, "schema": "PROMPT_PREFIX_1.0"})
        stats = app.engine.stats()
        self.assertEqual(stats["prompt_prefix"]["schema"], "PROMPT_PREFIX_1.0")
        self.assertFalse(stats["prompt_prefix"]["claims_cache_hit"])
        self.assertEqual(stats["prompt_prefix"]["hash"], stored["hash"])
        server = MCPServer(app.engine.root)
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": CURRENT_PROTOCOL, "clientInfo": {"name": "cursor-agent"}}})
        instructions = init["result"]["instructions"]
        listed = [name for name in {str(item["name"]) for item in TOOLS} if name in instructions]
        self.assertLess(len(listed), 10)
        self.assertIn(stored["hash"][:12], instructions)
        self.assertNotIn("cache hit", instructions.lower())

    def test_mcp_schema_studio_version_and_import_api(self):
        names = {str(item["name"]) for item in [*TOOLS, *LEGACY_TOOLS]}
        self.assertEqual(len(names), 31)
        self.assertEqual(Database.CURRENT_SCHEMA_VERSION, 6)
        self.assertEqual(__version__, "1.0.0rc3")
        self.assertEqual(DISPLAY_VERSION, "1.0.0-rc.3")
        html = resources.files("dynosai_flow.studio_assets").joinpath("index.html").read_text(encoding="utf-8")
        script = resources.files("dynosai_flow.studio_assets").joinpath("app.js").read_text(encoding="utf-8")
        i18n = resources.files("dynosai_flow.studio_assets").joinpath("i18n.js").read_text(encoding="utf-8")
        self.assertIn("eval-import", html)
        self.assertIn("governed-change", html)
        self.assertIn("importAcceptanceBundle", script)
        self.assertIn("governedChangeHtml", script)
        self.assertIn("eval.import", i18n)
        self.assertIn("cost.title", i18n)
        self.assertIn("Import acceptance ZIP", i18n)
        self.assertIn("Importar ZIP de acceptance", i18n)
        self.assertIn("Governed change cost", i18n)
        self.assertIn("Coste de cambio gobernado", i18n)
        app = self._app("ApiImport")
        bundle = _acceptance_zip(self.tmp / "api.zip", failures=1, passed=0)
        api = StudioAPI(self.tmp)
        code, body = api.post("/api/eval/import", {"path": str(bundle)})
        self.assertEqual(code, 200)
        self.assertFalse(body["spawn_provider"])
        self.assertEqual(body["imported"], 1)


if __name__ == "__main__":
    unittest.main()
