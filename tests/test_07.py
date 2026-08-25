from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.cli import parser
from dynosai_flow.debug import DebugE2ERunner, DebugE2ESuiteRunner, SCENARIOS
from dynosai_flow.debug_fixtures import (
    apply_greenfield_fibonacci,
    apply_orderflow_contract_discounts,
    brownfield_orderflow_plan,
    brownfield_orderflow_spec,
    greenfield_fibonacci_plan,
    greenfield_fibonacci_spec,
    seed_orderflow_brownfield,
)
from dynosai_flow.engine import DynosAI
from dynosai_flow.policy import ValidationProfilePolicy
from dynosai_flow.util import json_dumps, utc_now


class DynosAI07Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="dynosai-07-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp,ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.tmp.parent/".dynosai-worktrees"/self.tmp.name,ignore_errors=True))

    def _approve_unit(self, engine: DynosAI):
        parts=ValidationProfilePolicy.parse_command("python -m unittest discover -s tests")
        now=utc_now()
        engine.db.execute(
            "INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,source=excluded.source,approved=1,updated_at=excluded.updated_at",
            ("unit",json_dumps(parts),"test-07",1,now,now),
        )

    def _adopt_orderflow(self) -> DynosAI:
        seed_orderflow_brownfield(self.tmp)
        engine=DynosAI(self.tmp); engine.adopt("OrderFlow"); self._approve_unit(engine)
        return engine

    def _start_orderflow(self, engine: DynosAI) -> str:
        work=engine.start(SCENARIOS["orderflow-contract-discounts"]["description"],work_type="feature")
        wid=work["id"]
        for item in SCENARIOS["orderflow-contract-discounts"]["decisions"]:
            engine.register_decision(wid,item["question"],item["answer"])
        engine.continue_work(wid)
        engine.submit_spec(wid,brownfield_orderflow_spec(),"test-07")
        engine.continue_work(wid); engine.review(wid,"spec")
        return wid

    def test_orderflow_fixture_is_large_healthy_sqlite_brownfield(self):
        info=seed_orderflow_brownfield(self.tmp)
        self.assertGreaterEqual(info["files"],80)
        self.assertEqual(info["schema_version"],1)
        proc=subprocess.run(["python","-m","unittest","discover","-s","tests"],cwd=self.tmp,text=True,capture_output=True,timeout=30)
        self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
        self.assertEqual(subprocess.run(["git","status","--porcelain"],cwd=self.tmp,text=True,capture_output=True,check=True).stdout.strip(),"")

    def test_sql_migration_files_are_indexed_and_retrievable(self):
        engine=self._adopt_orderflow()
        sql=engine.db.query("SELECT path,language FROM code_files WHERE language='sql' ORDER BY path")
        self.assertIn({"path":"migrations/001_initial.sql","language":"sql"},sql)
        impact=engine.retrieval.impact("SQLite schema migration customers orders total_cents",limit=14)
        paths=[x["path"] for x in impact["files"]]
        self.assertIn("migrations/001_initial.sql",paths)
        self.assertIn("orderflow/migrations.py",paths)
        self.assertFalse(any("/analytics/" in p or "/exports/" in p for p in paths[:8]),paths)

    def test_data_change_plan_requires_explicit_migration_governance(self):
        engine=self._adopt_orderflow(); wid=self._start_orderflow(engine)
        spec=engine.artifacts.artifact(wid,"spec")
        plan=brownfield_orderflow_plan()
        missing=copy.deepcopy(plan); missing.pop("data_migration")
        errors=engine.artifacts.validate_plan_payload(spec,missing)
        self.assertTrue(any("data_migration.required" in x for x in errors),errors)
        no_evidence=copy.deepcopy(plan); no_evidence["tasks"][0]["evidence_required"]=["git_diff","test_result"]
        errors=engine.artifacts.validate_plan_payload(spec,no_evidence)
        self.assertTrue(any("migration_result" in x for x in errors),errors)
        no_row_semantics=copy.deepcopy(plan); no_row_semantics["data_migration"].pop("existing_row_semantics")
        errors=engine.artifacts.validate_plan_payload(spec,no_row_semantics)
        self.assertTrue(any("data_migration.existing_row_semantics" in x for x in errors),errors)
        no_verification=copy.deepcopy(plan); no_verification["data_migration"].pop("verification_strategy")
        errors=engine.artifacts.validate_plan_payload(spec,no_verification)
        self.assertTrue(any("data_migration.verification_strategy" in x for x in errors),errors)
        self.assertEqual(engine.artifacts.validate_plan_payload(spec,plan),[])

    def test_approved_orderflow_plan_renders_migration_section_and_task_evidence(self):
        engine=self._adopt_orderflow(); wid=self._start_orderflow(engine)
        created=engine.submit_plan(wid,brownfield_orderflow_plan(),"test-07")
        self.assertIn("## Migración de datos",created["plan"]["content"])
        self.assertIn("Semántica física de filas existentes",created["plan"]["content"])
        self.assertIn("Verificación:",created["plan"]["content"])
        first=engine.db.one("SELECT evidence_required FROM tasks WHERE work_id=? ORDER BY id LIMIT 1",(wid,))
        self.assertIn("migration_result",json.loads(first["evidence_required"]))
        engine.review(wid,"plan")
        contract=engine.action_contract(wid)
        # 0.10+ exposes the deterministic next transition to the managed
        # orchestrator instead of requiring a separate continue_work round-trip.
        self.assertEqual(contract["operation"], "prepare_implementation")
        self.assertEqual(contract["tool"], "dynosai_get_next_action")
        self.assertTrue(contract["deterministic_transition"])
        self.assertTrue(contract["arguments"]["execute"])

    def test_full_orderflow_migration_e2e_with_independent_oracle(self):
        runner=DebugE2ERunner(
            output=self.tmp/"orderflow-result.zip",workspace=self.tmp/"orderflow-workspace",
            scenario="orderflow-contract-discounts",timeout=40,
        )
        def fake_agent(prompt,phase):
            e=runner.engine; wid=runner.work_id
            if phase=="spec":
                e.continue_work(wid); e.submit_spec(wid,brownfield_orderflow_spec(),"deterministic-07"); e.continue_work(wid)
            elif phase=="plan":
                e.submit_plan(wid,brownfield_orderflow_plan(),"deterministic-07")
            elif phase=="implementation":
                prep=e.continue_work(wid); wt=Path(prep["worktree"])
                files=apply_orderflow_contract_discounts(wt)
                task_ids=[r["id"] for r in e.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id",(wid,))]
                reg=e.register_result(wid,"OrderFlow contractual discounts and migration implemented",files,[],task_ids)
                self.assertEqual(reg["status"],"verified",reg)
                e.continue_work(wid)
            return {"phase":phase,"simulated":True}
        runner.agent_runner=fake_agent
        with patch("dynosai_flow.engine.DynosAI.install_model",return_value={"installed":True}), \
             patch("dynosai_flow.engine.DynosAI.deep_doctor",return_value={"full_ready":True}):
            result=runner.run()
        self.assertEqual(result["status"],"passed",result)
        self.assertTrue(result["oracle"]["passed"],result.get("oracle"))
        self.assertTrue(all(result["oracle"]["checks"].values()),result["oracle"])
        self.assertEqual(result["invariants"]["final_state"],"done")
        self.assertTrue(Path(result["bundle"]).exists())


    def test_orderflow_contract_makes_historical_raw_null_semantics_explicit(self):
        spec=brownfield_orderflow_spec()
        req=next(x for x in spec["requirements"] if x["id"]=="REQ-006")
        ac=next(x for x in spec["acceptance_criteria"] if x["id"]=="AC-007")
        self.assertIn("físicamente NULL",req["text"])
        self.assertIn("físicamente NULL",ac["text"])
        decision=next(x for x in SCENARIOS["orderflow-contract-discounts"]["decisions"] if "históricos" in x["question"])
        self.assertIn("físicamente NULL",decision["answer"])

    def test_failed_child_evidence_preserves_oracle_and_efficiency(self):
        runner=DebugE2ERunner(output=self.tmp/"failed.zip",workspace=self.tmp/"failed-work",scenario="orderflow-contract-discounts")
        runner.logs.mkdir(parents=True,exist_ok=True)
        oracle={"scenario":"orderflow-contract-discounts","passed":False,"failures":["legacy_row_preserved"]}
        (runner.logs/"oracle-orderflow.json").write_text(json.dumps(oracle),encoding="utf-8")
        expected_eff={"total_usage":{"inputTokens":123},"phases":{"implementation":{"tool_calls":7}}}
        with patch.object(runner,"_efficiency_report",return_value=expected_eff):
            evidence=runner._failure_evidence()
        self.assertEqual(evidence["oracle"],oracle)
        self.assertEqual(evidence["efficiency"],expected_eff)

    def test_cli_exposes_single_green_brown_gate(self):
        args=parser().parse_args(["debug","e2e","--scenario","green-brown-gate"])
        self.assertEqual(args.scenario,"green-brown-gate")
        self.assertEqual(SCENARIOS[args.scenario]["mode"],"suite")
        self.assertEqual(SCENARIOS[args.scenario]["children"],["fibonacci","orderflow-contract-discounts"])

    def test_combined_gate_runs_greenfield_and_brownfield_in_one_bundle(self):
        suite=DebugE2ESuiteRunner(
            output=self.tmp/"combined.zip",workspace=self.tmp/"combined-workspace",
            scenario="green-brown-gate",timeout=40,
        )
        def factory(child: DebugE2ERunner, scenario: str):
            def fake(prompt,phase):
                e=child.engine; wid=child.work_id
                if scenario=="fibonacci":
                    if phase=="spec": e.continue_work(wid); e.submit_spec(wid,greenfield_fibonacci_spec(),"deterministic-07"); e.continue_work(wid)
                    elif phase=="plan": e.submit_plan(wid,greenfield_fibonacci_plan(),"deterministic-07")
                    elif phase=="implementation":
                        prep=e.continue_work(wid); wt=Path(prep["worktree"]); files=apply_greenfield_fibonacci(wt)
                        task_ids=[r["id"] for r in e.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id",(wid,))]
                        reg=e.register_result(wid,"Fibonacci implemented",files,[],task_ids); self.assertEqual(reg["status"],"verified",reg); e.continue_work(wid)
                else:
                    if phase=="spec": e.continue_work(wid); e.submit_spec(wid,brownfield_orderflow_spec(),"deterministic-07"); e.continue_work(wid)
                    elif phase=="plan": e.submit_plan(wid,brownfield_orderflow_plan(),"deterministic-07")
                    elif phase=="implementation":
                        prep=e.continue_work(wid); wt=Path(prep["worktree"]); files=apply_orderflow_contract_discounts(wt)
                        task_ids=[r["id"] for r in e.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id",(wid,))]
                        reg=e.register_result(wid,"OrderFlow implemented",files,[],task_ids); self.assertEqual(reg["status"],"verified",reg); e.continue_work(wid)
                return {"phase":phase,"simulated":True}
            return fake
        suite.child_agent_runner_factory=factory
        with patch("dynosai_flow.engine.DynosAI.install_model",return_value={"installed":True}), \
             patch("dynosai_flow.engine.DynosAI.deep_doctor",return_value={"full_ready":True}):
            result=suite.run()
        self.assertEqual(result["status"],"passed",result)
        self.assertEqual(result["coverage"],{"greenfield":True,"brownfield":True})
        self.assertTrue(all(result["gates"].values()),result["gates"])
        self.assertEqual({x["scenario"] for x in result["children"]},{"fibonacci","orderflow-contract-discounts"})
        self.assertTrue(all(x["status"]=="passed" and x["oracle_passed"] for x in result["children"]),result["children"])
        with zipfile.ZipFile(result["bundle"]) as z:
            names=set(z.namelist())
            self.assertIn("suite/summary-final.json",names)
            self.assertTrue(any("fibonacci" in n and n.endswith(".zip") for n in names),names)
            self.assertTrue(any("orderflow-contract-discounts" in n and n.endswith(".zip") for n in names),names)


if __name__=="__main__": unittest.main()
