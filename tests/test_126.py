from __future__ import annotations

import copy
import gzip
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.application import DynosAIApplication
from dynosai_flow.cli import demo_plan, demo_spec
from dynosai_flow.db import Database
from dynosai_flow.engine import DynosAI
from dynosai_flow.interactions import HumanInteractionService
from dynosai_flow.model_control import ModelControlPlane, classify_validation_failure
from dynosai_flow.predictive_routing import PredictiveRoutingMemory
from dynosai_flow.util import utc_now
from dynosai_flow.version import __version__


class ReleaseCandidate0126Tests(unittest.TestCase):
    """Release-candidate hardening only; no new workflow semantics."""

    def setUp(self):
        self.td=tempfile.TemporaryDirectory(prefix="dynosai-0126-")
        self.addCleanup(self.td.cleanup)
        self.root=Path(self.td.name)/"project"
        self.root.mkdir(parents=True)
        self.user_home=Path(self.td.name)/"user-home"
        self.user_home.mkdir()
        self.old_user_home=os.environ.get("DYNOSAI_USER_HOME")
        os.environ["DYNOSAI_USER_HOME"]=str(self.user_home)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.old_user_home is None:
            os.environ.pop("DYNOSAI_USER_HOME",None)
        else:
            os.environ["DYNOSAI_USER_HOME"]=self.old_user_home
        shutil.rmtree(self.root.parent/".dynosai-worktrees"/self.root.name,ignore_errors=True)

    def init(self, test_command="true") -> DynosAI:
        e=DynosAI(self.root)
        e.initialize("RC", "python", test_command, None)
        return e

    def prepare_implementing(self, e: DynosAI):
        work=e.start("Crear una CLI Fibonacci que genere N términos")
        wid=work["id"]
        e.continue_work(wid)
        e.submit_spec(wid,demo_spec(),"rc")
        e.continue_work(wid)
        e.review(wid,"spec")
        e.submit_plan(wid,demo_plan(),"rc")
        e.review(wid,"plan")
        prep=e.continue_work(wid)
        self.assertEqual(e.get_work(wid)["state"],"implementing")
        return wid,prep

    def test_version_is_0126(self):
        self.assertEqual(__version__,"0.18.0")

    def test_v5_duplicate_pending_gate_migrates_to_single_pending_idempotently(self):
        db=Database(self.root); db.initialize()
        now=utc_now()
        db.execute(
            "INSERT INTO work_items(id,title,description,work_type,state,quick_flow,active,quality_score,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("DYN-0001","RC","x","feature","code_review",0,1,100,now,now),
        )
        db.execute("DROP INDEX IF EXISTS idx_human_interactions_one_pending")
        db.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
        for iid in ("ELIC-0001","ELIC-0002"):
            db.execute(
                "INSERT INTO human_interactions(id,work_id,kind,gate,message,schema_json,response_json,status,provider,dedupe_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (iid,"DYN-0001","gate_approval","code","review","{}","{}","pending","codex","gate:DYN-0001:code",now),
            )
        upgraded=Database(self.root); upgraded.initialize(); upgraded.initialize()
        self.assertEqual(upgraded.get_meta("schema_version"),"6")
        rows=upgraded.query("SELECT id,status FROM human_interactions WHERE dedupe_key='gate:DYN-0001:code' ORDER BY id")
        self.assertEqual(sum(1 for x in rows if x["status"]=="pending"),1,rows)
        self.assertEqual(sum(1 for x in rows if x["status"]=="cancelled"),1,rows)
        self.assertEqual((upgraded.one("SELECT COUNT(*) n FROM migrations WHERE version=6") or {})["n"],1)

    def test_restart_resume_reuses_active_provider_session_and_preserves_contract(self):
        e=self.init(); wid,prep=self.prepare_implementing(e)
        app1=DynosAIApplication(self.root); app1.engine.db.initialize()
        first=app1.resume("codex",wid)
        sid=first["provider_session"]["id"]
        op=first["next"]["operation"]
        # Simulate host/process restart: create a completely new application facade.
        app2=DynosAIApplication(self.root); app2.engine.db.initialize()
        second=app2.resume("codex",wid)
        self.assertEqual(second["provider_session"]["id"],sid)
        self.assertTrue(second["provider_session"].get("resumed"))
        self.assertEqual(second["work"]["state"],"implementing")
        self.assertEqual(second["next"]["operation"],op)
        self.assertTrue(second["permissions"]["write"])
        self.assertTrue(second["context_checkpoint"] is None or second["context_checkpoint"].get("ref"))

    def test_corrupted_snapshot_restore_is_atomic_and_keeps_live_database(self):
        e=self.init()
        work=e.start("Trabajo que debe sobrevivir")
        before=e.db.one("SELECT id,state,title FROM work_items WHERE id=?",(work["id"],))
        snapshot=Path(e.state.snapshot("rc-good")["portable"])
        with gzip.open(snapshot,"rt",encoding="utf-8") as fh:
            payload=json.load(fh)
        # Add an FK-invalid task to a copy of an otherwise valid portable state.
        bad=copy.deepcopy(payload)
        bad["tables"].setdefault("tasks",[]).append({
            "id":"DYN-9999-T01","work_id":"DYN-NOT-THERE","title":"bad","description":"bad","state":"ready",
            "requirements":"[]","acceptance":"[]","files":"[]","validation_commands":"[]","evidence_required":"[]","depends_on":"[]",
            "owner":None,"claimed_run":None,"created_at":utc_now(),"updated_at":utc_now(),
        })
        bad_path=self.root/"bad-snapshot.json.gz"
        with gzip.open(bad_path,"wt",encoding="utf-8") as fh: json.dump(bad,fh)
        with self.assertRaises(RuntimeError):
            e.state.restore(bad_path)
        after=e.db.one("SELECT id,state,title FROM work_items WHERE id=?",(work["id"],))
        self.assertEqual(after,before)
        self.assertEqual(e.db.get_meta("schema_version"),"6")
        self.assertFalse((self.root/".dynosai"/"knowledge.restore.tmp.db").exists())

    def test_missing_or_corrupted_context_checkpoint_does_not_block_resume(self):
        e=self.init(); wid,prep=self.prepare_implementing(e)
        # No checkpoint is required to reconstruct the authoritative action contract.
        checkpoint_dir=self.root/".dynosai"/"runtime"/"context-checkpoints"/wid
        shutil.rmtree(checkpoint_dir,ignore_errors=True)
        app=DynosAIApplication(self.root); app.engine.db.initialize()
        first=app.resume("codex",wid)
        self.assertIsNone(first["context_checkpoint"])
        self.assertEqual(first["work"]["state"],"implementing")
        self.assertEqual(first["next"]["operation"],"implement_tasks")
        # A torn/corrupt latest checkpoint must degrade to the same safe behavior.
        checkpoint_dir.mkdir(parents=True,exist_ok=True)
        (checkpoint_dir/"latest.json").write_text("{ torn",encoding="utf-8")
        second=DynosAIApplication(self.root); second.engine.db.initialize()
        resumed=second.resume("codex",wid)
        self.assertIsNone(resumed["context_checkpoint"])
        self.assertEqual(resumed["next"]["operation"],"implement_tasks")

    def test_corrupted_model_control_state_fails_safe_without_paid_escalation(self):
        runtime=self.root/".dynosai"/"runtime"; runtime.mkdir(parents=True)
        (runtime/"model-control.json").write_text("{ definitely-not-json",encoding="utf-8")
        (runtime/"predictive-routing.json").write_text("not-json",encoding="utf-8")
        ctl=ModelControlPlane(self.root)
        decision=ctl.decide("codex","implementation",work_id="DYN-1",usage={"phase_usage":[]},extra_signals={"repository_files":1,"task_count":1})
        self.assertEqual(decision.tier,"economy")
        self.assertFalse(decision.escalation["recommended"])
        self.assertIn("telemetry_not_comparable_no_escalation",decision.reasons)

    def test_validation_precondition_and_environment_failures_never_train_predictor(self):
        output="ImportError: Start directory is not importable: 'tests'"
        self.assertEqual(classify_validation_failure(output, exit_code=1, pending_planned_files=["tests/test_cli.py"]),"validation_precondition")
        root=self.root/"routing"; ctl=ModelControlPlane(root)
        ctl.record_outcome("codex","DYN-1",success=False,failure_kind="validation_precondition",failure_fingerprint="x",activity="implementation")
        ctl.record_outcome("codex","DYN-1",success=False,failure_kind="validation_environment",failure_fingerprint="y",activity="implementation")
        stats=PredictiveRoutingMemory(root).stats("codex","implementation","economy")
        self.assertEqual(stats["samples"],0)
        self.assertEqual(stats["model_failures"],0)
        self.assertEqual(stats["excluded_observations"],2)

    def test_scope_approve_reject_cancel_are_terminal_and_only_approve_changes_plan(self):
        e=self.init(); wid,prep=self.prepare_implementing(e)
        service=HumanInteractionService(e.db)
        plan_before=e.artifacts.artifact(wid,"plan")
        v0=plan_before["version"]
        statuses=[]
        for idx,(decision,expected) in enumerate((("request_changes","declined"),("cancel","cancelled"),("approve","approved")),1):
            path=f"extra_{idx}.py"
            req=e.request_scope_extension(wid,path,"create",f"rc {decision}")
            self.assertEqual(req["status"],"pending")
            inter=service.scope_request(req,provider="codex")
            app=DynosAIApplication(self.root); app.engine.db.initialize()
            app.interactions=service
            result=app.resolve_interaction(inter.id,"accept",{"decision":decision,"comments":"rc"})
            statuses.append((e.db.one("SELECT status FROM scope_requests WHERE id=?",(req["id"],))["status"],expected))
        self.assertTrue(all(a==b for a,b in statuses),statuses)
        self.assertEqual((e.db.one("SELECT COUNT(*) n FROM scope_requests WHERE work_id=? AND status='pending'",(wid,)) or {})["n"],0)
        plan_after=e.artifacts.artifact(wid,"plan")
        self.assertEqual(plan_after["version"],v0+1)
        paths={x.get("path") for x in (plan_after.get("metadata") or {}).get("files",[])}
        self.assertIn("extra_3.py",paths)
        self.assertNotIn("extra_1.py",paths); self.assertNotIn("extra_2.py",paths)
        self.assertEqual(e.get_work(wid)["state"],"implementing")

    def test_repeated_gate_polling_dedupes_and_request_changes_returns_to_authoring_state(self):
        e=self.init()
        work=e.start("Crear CLI"); wid=work["id"]
        e.continue_work(wid); e.submit_spec(wid,demo_spec(),"rc"); e.continue_work(wid)
        app=DynosAIApplication(self.root); app.engine.db.initialize()
        a=app.next_action("codex",wid,False)["human_interaction"]
        b=app.next_action("codex",wid,False)["human_interaction"]
        self.assertEqual(a["id"],b["id"])
        self.assertEqual((e.db.one("SELECT COUNT(*) n FROM human_interactions WHERE dedupe_key=? AND status='pending'",(a["dedupe_key"],)) or {})["n"],1)
        resolved=app.resolve_interaction(a["id"],"accept",{"decision":"request_changes","comments":"clarify acceptance"})
        self.assertEqual(resolved["work"]["state"],"discovery")
        self.assertEqual((e.db.one("SELECT status FROM human_interactions WHERE id=?",(a["id"],)) or {})["status"],"accepted")
        self.assertEqual((e.db.one("SELECT COUNT(*) n FROM human_interactions WHERE dedupe_key=? AND status='pending'",(a["dedupe_key"],)) or {})["n"],0)

    def test_runtime_broker_resolves_recreated_agent_session_and_expires_stale_one(self):
        e=self.init(); work=e.start("broker rc"); wid=work["id"]
        session=e.agents.create_session("codex",wid,None,self.root)
        fresh=DynosAI(self.root); fresh.db.initialize()
        resolved=fresh.agents.resolve_active_session("codex",self.root)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved["id"],session["session_id"])
        fresh.db.execute("UPDATE agent_sessions SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(session["session_id"],))
        self.assertIsNone(fresh.agents.resolve_active_session("codex",self.root))


if __name__=="__main__":
    unittest.main()
