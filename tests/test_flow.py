import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from dynosai_flow.benchmark import RetrievalBenchmark
from dynosai_flow.cli import demo_plan, demo_spec, run_demo, open_agent
from dynosai_flow.engine import DynosAI
from dynosai_flow.mcp import MCPServer, TOOLS


class DynosAIFlowV03Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-v03-"))
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.tmp.parent / ".dynosai-worktrees" / self.tmp.name, ignore_errors=True)

    def init(self, test_command="python -m unittest discover -s tests"):
        e=DynosAI(self.tmp); e.initialize("Test", "python", test_command, "all"); return e

    def prepare_ready(self, e: DynosAI):
        work=e.start("Crear una CLI Fibonacci que genere N términos")
        wid=work["id"]
        e.continue_work(wid)
        req=e.continue_work(wid)
        self.assertTrue(req["requires_agent"])
        e.submit_spec(wid, demo_spec(), "test-agent")
        e.continue_work(wid)
        self.assertEqual(e.get_work(wid)["state"], "spec_review")
        e.review(wid, "spec")
        plan_req=e.continue_work(wid)
        self.assertTrue(plan_req["requires_agent"])
        e.submit_plan(wid, demo_plan(), "test-agent")
        e.review(wid, "plan")
        prep=e.continue_work(wid)
        return wid, prep

    def test_init_schema_snapshot_and_git_guard(self):
        e=self.init("true")
        doctor=e.doctor()
        self.assertTrue(doctor["ready"], doctor)
        self.assertEqual(e.db.get_meta("schema_version"), "6")
        self.assertTrue((self.tmp/".dynosai/state/snapshot.json.gz").exists())
        env=e.git_guard.environment()
        ok=subprocess.run(["git","status"],cwd=self.tmp,env=env,capture_output=True,text=True)
        self.assertEqual(ok.returncode,0)
        denied=subprocess.run(["git","commit","--allow-empty","-m","forbidden"],cwd=self.tmp,env=env,capture_output=True,text=True)
        self.assertEqual(denied.returncode,73)
        self.assertIn("Git Guard", denied.stderr)

    def test_agent_driven_spec_and_plan_contract(self):
        e=self.init("true")
        work=e.start("Añadir una exportación CSV")
        wid=work["id"]; e.continue_work(wid)
        request=e.continue_work(wid)
        self.assertEqual(request["action"]["operation"],"author_specification")
        with self.assertRaises(ValueError): e.submit_spec(wid,{"objective":"x"},"bad-agent")
        spec=demo_spec(); spec["objective"]="Permitir exportar datos cumpliendo un comportamiento observable y verificable."
        e.submit_spec(wid,spec,"agent")
        e.continue_work(wid); self.assertEqual(e.get_work(wid)["state"],"spec_review")
        e.review(wid,"spec")
        request=e.continue_work(wid); self.assertEqual(request["action"]["operation"],"author_plan")
        e.submit_plan(wid,demo_plan(),"agent")
        summary=e.review_summary(wid)
        self.assertGreaterEqual(len(summary["tasks"]),2)
        self.assertTrue(all(t["requirements"] for t in summary["tasks"]))

    def test_review_summary_synchronizes_nested_work_quality_score(self):
        e=self.init("true")
        work=e.start("Añadir exportación CSV"); wid=work["id"]
        e.db.execute("UPDATE work_items SET quality_score=? WHERE id=?", (1, wid))
        summary=e.review_summary(wid)
        self.assertEqual(summary["work"]["quality_score"], summary["quality_score"])
        self.assertEqual(e.get_work(wid)["quality_score"], summary["quality_score"])


    def test_get_next_action_contract_is_structured_without_state_mutation(self):
        e=self.init("true")
        work=e.start("Añadir exportación CSV"); wid=work["id"]
        # inbox contract is pure and does not advance state
        contract=e.action_contract(wid)
        self.assertEqual(contract["operation"],"open_discovery")
        self.assertEqual(e.get_work(wid)["state"],"inbox")
        e.continue_work(wid)
        contract=e.action_contract(wid)
        self.assertEqual(contract["operation"],"author_specification")
        self.assertIn("schema",contract)
        e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        contract=e.action_contract(wid)
        self.assertEqual(contract["operation"],"author_plan")
        self.assertEqual(contract["schema"]["tasks"][0]["depends_on"],["zero-based-task-index-or-explicit-task-id"])
        self.assertEqual(e.get_work(wid)["state"],"plan_review")

    def test_plan_contract_separates_filesystem_action_from_test_role(self):
        e=self.init("true")
        work=e.start("Añadir exportación CSV"); wid=work["id"]
        e.continue_work(wid); e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        contract=e.action_contract(wid)
        self.assertEqual(contract["operation"],"author_plan")
        action_schema=contract["schema"]["files"][0]["action"]
        self.assertEqual(action_schema,"read|modify|create|delete")
        self.assertEqual(contract["schema"]["files"][0]["role"],"source|test|config|docs|other")
        self.assertTrue(any("new tests" in rule for rule in contract["rules"]))
        bad=demo_plan(); bad["files"][-1]["action"]="test"
        with self.assertRaisesRegex(ValueError,"file role, not filesystem intent"):
            e.submit_plan(wid,bad,"agent")

    def test_missing_planned_file_slice_is_structured_not_exception(self):
        e=self.init("true")
        result=e.retrieval.file_slice("future.py",1,20,self.tmp)
        self.assertFalse(result["exists"])
        self.assertEqual(result["state"],"not_created_yet")
        self.assertEqual(result["content"],"")

    def test_sync_separates_dynosai_internal_changes(self):
        e=self.init("true")
        (self.tmp/".dynosai"/"config.toml").write_text((self.tmp/".dynosai"/"config.toml").read_text()+"\n# test\n",encoding="utf-8")
        result=e.sync()
        self.assertTrue(any(".dynosai/config.toml" in x for x in result["managed_internal_changes"]))
        self.assertFalse(any(".dynosai/config.toml" in x for x in result["external_changes"]))

    def test_plan_rejects_unsafe_validation_and_managed_files(self):
        e=self.init("true")
        work=e.start("Añadir una exportación segura"); wid=work["id"]
        e.continue_work(wid); e.continue_work(wid)
        e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        bad=demo_plan(); bad["validation_commands"]=["python -c \"import os; os.remove('x')\""]; bad["files"].append({"path":".dynosai/config.toml","action":"modify","reason":"No permitido"})
        with self.assertRaises(ValueError): e.submit_plan(wid,bad,"agent")

    def test_partial_task_evidence_does_not_advance_to_code_review(self):
        e=self.init("python -m unittest discover -s tests")
        wid,prep=self.prepare_ready(e); wt=Path(prep["worktree"])
        (wt/"src").mkdir(exist_ok=True); (wt/"tests").mkdir(exist_ok=True)
        (wt/"src"/"__init__.py").write_text("",encoding="utf-8")
        (wt/"src"/"fibonacci.py").write_text("def generate_fibonacci(n):\n return []\n",encoding="utf-8")
        (wt/"tests"/"test_fibonacci.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n",encoding="utf-8")
        result=e.register_result(wid,"Primera tarea",["src/__init__.py","src/fibonacci.py","tests/test_fibonacci.py"],[],[f"{wid}-T01"])
        self.assertEqual(result["status"],"verified_partial")
        self.assertEqual(result["remaining_tasks"],1)
        follow=e.continue_work(wid)
        self.assertEqual(follow["state"],"implementing")

    def test_result_is_verified_from_real_git_and_tests(self):
        e=self.init("python -m unittest discover -s tests")
        wid,prep=self.prepare_ready(e); wt=Path(prep["worktree"])
        (wt/"src").mkdir(exist_ok=True); (wt/"tests").mkdir(exist_ok=True)
        (wt/"src"/"unexpected.py").write_text("VALUE=1\n",encoding="utf-8")
        (wt/"tests"/"test_ok.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n",encoding="utf-8")
        result=e.register_result(wid,"done",["tests/test_ok.py"],[],None)
        self.assertEqual(result["status"],"failed")
        self.assertIn("src/unexpected.py",result["undeclared_files"])
        self.assertEqual(e.get_work(wid)["state"],"implementing")
        self.assertTrue(all(t["state"]!="completed" for t in e.db.query("SELECT state FROM tasks WHERE work_id=?",(wid,))))

    def test_full_greenfield_cycle_memory_and_stats(self):
        result=run_demo(self.tmp)
        self.assertEqual(result["merged"]["state"],"done")
        self.assertEqual(result["stats"]["verified_runs"],1)
        self.assertTrue(result["doctor"]["ready"])
        e=DynosAI(self.tmp)
        self.assertTrue(e.db.one("SELECT id FROM evidence LIMIT 1"))
        self.assertTrue(e.db.one("SELECT id FROM features LIMIT 1"))

    def test_stable_python_symbols_calls_tests_and_slices(self):
        (self.tmp/"src").mkdir(parents=True); (self.tmp/"tests").mkdir()
        (self.tmp/"src"/"auth.py").write_text("class Auth:\n    def login(self, user):\n        return validate(user)\n\ndef validate(user):\n    return bool(user)\n",encoding="utf-8")
        (self.tmp/"tests"/"test_auth.py").write_text("from src.auth import validate\ndef test_validate():\n    assert validate('x')\n",encoding="utf-8")
        subprocess.run(["git","init","-b","main"],cwd=self.tmp,check=True,capture_output=True)
        subprocess.run(["git","config","user.email","x@y"],cwd=self.tmp,check=True); subprocess.run(["git","config","user.name","x"],cwd=self.tmp,check=True)
        subprocess.run(["git","add","-A"],cwd=self.tmp,check=True); subprocess.run(["git","commit","-m","base"],cwd=self.tmp,check=True,capture_output=True)
        e=DynosAI(self.tmp); e.adopt("Auth",None)
        before=e.retrieval.find_symbol("validate"); sid=[x["id"] for x in before if x["name"]=="validate" and x["path"]=="src/auth.py"][0]
        data=e.retrieval.get_symbol(sid); self.assertTrue(data["tests"])
        (self.tmp/"src"/"auth.py").write_text("\n\n"+(self.tmp/"src"/"auth.py").read_text(),encoding="utf-8")
        subprocess.run(["git","add","src/auth.py"],cwd=self.tmp,check=True); subprocess.run(["git","commit","-m","lines"],cwd=self.tmp,check=True,capture_output=True)
        e.sync(); after=e.retrieval.find_symbol("validate"); self.assertIn(sid,{x["id"] for x in after})
        sl=e.retrieval.file_slice("src/auth.py",1,8); self.assertIn("validate",sl["content"])

    def test_brownfield_routes_and_baseline_evidence(self):
        (self.tmp/"app").mkdir(); (self.tmp/"tests").mkdir()
        (self.tmp/"app"/"api.py").write_text("class Router:\n def get(self,p):\n  return lambda f:f\nrouter=Router()\n@router.get('/login')\ndef login():\n return 'ok'\n",encoding="utf-8")
        (self.tmp/"tests"/"test_api.py").write_text("from app.api import login\ndef test_login(): assert login()=='ok'\n",encoding="utf-8")
        subprocess.run(["git","init","-b","main"],cwd=self.tmp,check=True,capture_output=True); subprocess.run(["git","config","user.email","x@y"],cwd=self.tmp,check=True); subprocess.run(["git","config","user.name","x"],cwd=self.tmp,check=True); subprocess.run(["git","add","-A"],cwd=self.tmp,check=True); subprocess.run(["git","commit","-m","base"],cwd=self.tmp,check=True,capture_output=True)
        e=DynosAI(self.tmp); result=e.adopt("Brownfield",None)
        self.assertTrue(e.db.one("SELECT id FROM routes WHERE path='/login'"))
        baselines=result["baseline_specs"]; self.assertTrue(baselines)
        arts=e.db.query("SELECT metadata FROM artifacts WHERE status='inferred'")
        self.assertTrue(any("evidence" in row["metadata"] for row in arts))

    def test_backup_restore_portable_state(self):
        e=self.init("true"); work=e.start("Persistir esta idea"); snap=e.backup("test")
        e.db.execute("DELETE FROM work_items WHERE id=?",(work["id"],)); self.assertIsNone(e.db.one("SELECT id FROM work_items WHERE id=?",(work["id"],)))
        e.restore(snap["portable"]); self.assertIsNotNone(e.db.one("SELECT id FROM work_items WHERE id=?",(work["id"],)))

    def test_task_context_is_compact_and_traceable(self):
        e=self.init("true"); wid,prep=self.prepare_ready(e)
        preview=e.context_preview(wid,1000,f"{wid}-T01")
        types={x["type"] for x in preview["items"]}
        self.assertIn("task",types); self.assertIn("requirement",types); self.assertNotIn("spec",types)
        self.assertLessEqual(preview["estimated_tokens"],1000)


    def test_open_agent_dry_run_targets_worktree_and_guard(self):
        e=self.init("true"); wid,prep=self.prepare_ready(e)
        opened=open_agent(e,"codex",True)
        self.assertEqual(Path(opened["cwd"]),Path(prep["worktree"]))
        self.assertTrue(Path(opened["git_guard"]).exists())
        self.assertFalse(opened["launched"])

    def test_mcp_code_tools_and_schedule(self):
        e=self.init("true"); wid,prep=self.prepare_ready(e)
        server=MCPServer(self.tmp)
        names={x["name"] for x in TOOLS}
        for expected in {"dynosai_submit_spec","dynosai_submit_plan","dynosai_find_symbol","dynosai_read","dynosai_analyze_impact","dynosai_stats"}: self.assertIn(expected,names)
        self.assertNotIn("dynosai_get_symbol",names); self.assertNotIn("dynosai_get_file_slice",names)
        schedule=server.call_tool("dynosai_schedule",{"work_id":wid}); self.assertTrue(schedule["parallel_batches"])

    def test_migrates_v2_database_in_place(self):
        import sqlite3
        dyn=self.tmp/".dynosai"; dyn.mkdir()
        con=sqlite3.connect(dyn/"knowledge.db")
        con.executescript("""
        CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        INSERT INTO meta VALUES('schema_version','2');
        CREATE TABLE code_files(path TEXT PRIMARY KEY,language TEXT NOT NULL,content_hash TEXT NOT NULL,size INTEGER NOT NULL,indexed_commit TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE symbols(id TEXT PRIMARY KEY,path TEXT NOT NULL,name TEXT NOT NULL,kind TEXT NOT NULL,start_line INTEGER NOT NULL,end_line INTEGER NOT NULL,signature TEXT NOT NULL,docstring TEXT NOT NULL,body TEXT NOT NULL);
        CREATE TABLE work_items(id TEXT PRIMARY KEY,title TEXT NOT NULL,description TEXT NOT NULL,work_type TEXT NOT NULL,state TEXT NOT NULL,quick_flow INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,branch TEXT,worktree TEXT,quality_score INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE tasks(id TEXT PRIMARY KEY,work_id TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,state TEXT NOT NULL,requirements TEXT NOT NULL DEFAULT '[]',acceptance TEXT NOT NULL DEFAULT '[]',files TEXT NOT NULL DEFAULT '[]',validation_commands TEXT NOT NULL DEFAULT '[]',evidence_required TEXT NOT NULL DEFAULT '[]',depends_on TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE runs(id TEXT PRIMARY KEY,work_id TEXT NOT NULL,agent TEXT,status TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',files_changed TEXT NOT NULL DEFAULT '[]',tests TEXT NOT NULL DEFAULT '[]',started_at TEXT NOT NULL,finished_at TEXT);
        """)
        con.commit(); con.close()
        from dynosai_flow.db import Database
        db=Database(self.tmp); db.initialize()
        self.assertEqual(db.get_meta('schema_version'),'6')
        cols={r['name'] for r in db.query('PRAGMA table_info(symbols)')}
        self.assertIn('qualified_name',cols)
        runcols={r['name'] for r in db.query('PRAGMA table_info(runs)')}
        self.assertIn('verification_status',runcols)

    def test_benchmark_metrics(self):
        e=self.init("true")
        now=e.db.get_meta("schema_version")
        # Insert a validated memory record with a deterministic textual match.
        ts="2026-01-01T00:00:00+00:00"
        e.db.execute("INSERT INTO work_items(id,title,description,work_type,state,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",("DYN-0999","Token refresh","","feature","done",0,ts,ts))
        e.db.execute("INSERT INTO features(id,work_id,title,summary,status,commit_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",("FEATURE-0999","DYN-0999","Token refresh","renovación de token","validated",e.git.head(),ts,ts))
        e.db.execute("INSERT INTO feature_rules(feature_id,rule) VALUES(?,?)",("FEATURE-0999","El refresh token se valida antes de renovar"))
        e.db.execute("INSERT INTO feature_files(feature_id,path,role,reason,confidence,validity) VALUES(?,?,?,?,?,?)",("FEATURE-0999","src/auth.py","implementation","implementa refresh token",0.99,"current"))
        e.db.index_search("feature","FEATURE-0999","Token refresh","renovación de token")
        bench=RetrievalBenchmark(e.retrieval).run([{"query":"renovación refresh token","expected_files":["src/auth.py"]}],5)
        self.assertEqual(bench["recall_at_k"],1.0)


    def test_zero_based_plan_dependencies_materialize_exactly(self):
        e=self.init("true")
        work=e.start("Crear CLI Fibonacci con dependencias encadenadas"); wid=work["id"]
        e.continue_work(wid); e.submit_spec(wid,demo_spec(),"agent"); e.continue_work(wid); e.review(wid,"spec")
        plan=demo_plan()
        plan["files"].append({"path":"tests/test_cli.py","action":"create","reason":"Validación CLI adicional"})
        plan["tasks"].append({"title":"Validar CLI","description":"Añadir validación final del adaptador CLI con trazabilidad completa.","requirements":["REQ-001"],"acceptance":["AC-001"],"files":["tests/test_cli.py"],"depends_on":[1],"evidence_required":["git_diff","test_result"]})
        e.submit_plan(wid,plan,"agent")
        tasks=e.db.query("SELECT id,depends_on FROM tasks WHERE work_id=? ORDER BY id",(wid,))
        self.assertEqual(json.loads(tasks[0]["depends_on"]),[])
        self.assertEqual(json.loads(tasks[1]["depends_on"]),[f"{wid}-T01"])
        self.assertEqual(json.loads(tasks[2]["depends_on"]),[f"{wid}-T02"])

    def test_feature_run_binds_completed_tasks_to_owner_and_run(self):
        e=self.init("python -m unittest discover -s tests")
        wid,prep=self.prepare_ready(e); wt=Path(prep["worktree"])
        (wt/"src").mkdir(exist_ok=True); (wt/"tests").mkdir(exist_ok=True)
        (wt/"src"/"__init__.py").write_text("",encoding="utf-8")
        (wt/"src"/"fibonacci.py").write_text("def generate_fibonacci(n):\n return []\n",encoding="utf-8")
        (wt/"src"/"cli.py").write_text("def main(): return 0\n",encoding="utf-8")
        (wt/"tests"/"test_fibonacci.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n",encoding="utf-8")
        run=e.db.one("SELECT * FROM runs WHERE work_id=? ORDER BY id LIMIT 1",(wid,))
        all_tasks=[x["id"] for x in e.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id",(wid,))]
        self.assertEqual(json.loads(run["task_ids"]),all_tasks)
        result=e.register_result(wid,"done",["src/__init__.py","src/fibonacci.py","src/cli.py","tests/test_fibonacci.py"],[],all_tasks,run["id"])
        self.assertEqual(result["status"],"verified")
        rows=e.db.query("SELECT id,owner,claimed_run,state FROM tasks WHERE work_id=? ORDER BY id",(wid,))
        self.assertTrue(all(x["owner"] for x in rows),rows)
        self.assertTrue(all(x["claimed_run"]==run["id"] for x in rows),rows)
        self.assertTrue(all(x["state"]=="completed" for x in rows),rows)


if __name__ == "__main__": unittest.main()
