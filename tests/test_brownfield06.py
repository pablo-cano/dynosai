from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.cli import parser
from dynosai_flow.debug import DebugE2ERunner, SCENARIOS
from dynosai_flow.debug_fixtures import (
    brownfield_supportdesk_plan,
    brownfield_supportdesk_spec,
    seed_supportdesk_brownfield,
)
from dynosai_flow.engine import DynosAI


class Brownfield06Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dynosai-06-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.tmp.parent / ".dynosai-worktrees" / self.tmp.name, ignore_errors=True))

    def _adopt_supportdesk(self) -> DynosAI:
        seed_supportdesk_brownfield(self.tmp)
        engine = DynosAI(self.tmp)
        engine.adopt("SupportDesk")
        # The fixture intentionally uses unittest rather than the adopt() default pytest profile.
        from dynosai_flow.policy import ValidationProfilePolicy
        from dynosai_flow.util import json_dumps, utc_now
        parts = ValidationProfilePolicy.parse_command("python -m unittest discover -s tests")
        now = utc_now()
        engine.db.execute(
            "INSERT INTO validation_profiles(name,command_json,source,approved,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,source=excluded.source,approved=1,updated_at=excluded.updated_at",
            ("unit", json_dumps(parts), "test", 1, now, now),
        )
        return engine

    def _prepare_ready(self, engine: DynosAI) -> str:
        work = engine.start(SCENARIOS["supportdesk-premium-sla"]["description"], work_type="feature")
        wid = work["id"]
        for item in SCENARIOS["supportdesk-premium-sla"]["decisions"]:
            engine.register_decision(wid, item["question"], item["answer"])
        engine.continue_work(wid)
        engine.submit_spec(wid, brownfield_supportdesk_spec(), "test-agent")
        engine.continue_work(wid)
        engine.review(wid, "spec")
        engine.submit_plan(wid, brownfield_supportdesk_plan(), "test-agent")
        engine.review(wid, "plan")
        return wid

    def test_supportdesk_fixture_is_real_brownfield_and_green_before_adoption(self):
        seed = seed_supportdesk_brownfield(self.tmp)
        self.assertGreaterEqual(seed["files"], 50)
        result = subprocess.run(
            ["python", "-m", "unittest", "discover", "-s", "tests"],
            cwd=self.tmp,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.tmp, text=True, capture_output=True, check=True)
        self.assertEqual(status.stdout.strip(), "")

    def test_brownfield_retrieval_finds_relevant_existing_code_without_distractors(self):
        engine = self._adopt_supportdesk()
        impact = engine.retrieval.impact("tickets critical clientes premium SLA escalation_required API", limit=12)
        files = [item["path"] for item in impact["files"]]
        tests = [item["path"] for item in impact["tests"]]
        expected = {
            "supportdesk/services/sla.py",
            "supportdesk/services/ticket_service.py",
            "supportdesk/domain/ticket.py",
            "supportdesk/api/tickets.py",
        }
        self.assertTrue(expected.issubset(set(files)), files)
        self.assertIn("tests/test_sla.py", tests)
        self.assertIn("tests/test_ticket_service.py", tests)
        self.assertFalse(any("/analytics/" in path or "/exports/" in path or "/imports/" in path for path in files[:8]), files)
        self.assertFalse(any(path.startswith("tests/") for path in files), files)

    def test_plan_contract_marks_repository_context_complete(self):
        engine = self._adopt_supportdesk()
        work = engine.start(SCENARIOS["supportdesk-premium-sla"]["description"], work_type="feature")
        wid = work["id"]
        for item in SCENARIOS["supportdesk-premium-sla"]["decisions"]:
            engine.register_decision(wid, item["question"], item["answer"])
        engine.continue_work(wid)
        engine.submit_spec(wid, brownfield_supportdesk_spec(), "test-agent")
        engine.continue_work(wid)
        engine.review(wid, "spec")
        contract = engine.action_contract(wid)
        self.assertEqual(contract["operation"], "author_plan")
        repo = contract["repository_context"]
        self.assertEqual(repo["mode"], "brownfield")
        self.assertTrue(repo["repository_context_complete"], repo)
        self.assertFalse(repo["additional_discovery_required"], repo)
        paths = {x["path"] for x in contract["related_context"]["files"]}
        self.assertIn("supportdesk/services/sla.py", paths)

    def test_implementation_contract_contains_authoritative_task_queue(self):
        engine = self._adopt_supportdesk()
        wid = self._prepare_ready(engine)
        prep = engine.continue_work(wid)
        self.assertEqual(prep["state"], "implementing")
        contract = engine.action_contract(wid)
        queue = contract["task_queue"]
        self.assertEqual(queue["current_task"]["id"], f"{wid}-T01")
        self.assertEqual(queue["remaining_count"], 3)
        self.assertTrue(queue["current_task"]["dependencies_satisfied"])
        self.assertEqual(queue["blocked_tasks"][0]["id"], f"{wid}-T02")
        self.assertIn("supportdesk/services/sla.py", queue["current_task"]["files"])

    def test_partial_task_without_test_evidence_skips_premature_work_validation(self):
        engine = self._adopt_supportdesk()
        plan = brownfield_supportdesk_plan()
        plan["tasks"][0]["evidence_required"] = ["git_diff"]
        # Keep the first task isolated to one source file so evidence can be completed independently.
        plan["tasks"][0]["files"] = ["supportdesk/services/sla.py"]
        plan["files"] = [x for x in plan["files"] if x["path"] != "tests/test_sla.py"]
        wid = engine.start(SCENARIOS["supportdesk-premium-sla"]["description"], work_type="feature")["id"]
        for item in SCENARIOS["supportdesk-premium-sla"]["decisions"]:
            engine.register_decision(wid, item["question"], item["answer"])
        engine.continue_work(wid)
        engine.submit_spec(wid, brownfield_supportdesk_spec(), "test-agent")
        engine.continue_work(wid); engine.review(wid, "spec")
        engine.submit_plan(wid, plan, "test-agent"); engine.review(wid, "plan")
        prep = engine.continue_work(wid); wt = Path(prep["worktree"])
        path = wt / "supportdesk/services/sla.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# task-only evidence\n", encoding="utf-8")
        result = engine.register_result(wid, "partial", ["supportdesk/services/sla.py"], [], [f"{wid}-T01"])
        self.assertEqual(result["status"], "verified_partial", result)
        self.assertTrue(result["validation"]["skipped"], result["validation"])
        self.assertEqual(result["validation"]["scope"], "task")
        self.assertEqual(engine.db.one("SELECT COUNT(*) n FROM validations")["n"], 0)

    def test_brownfield_baseline_and_feature_artifacts_cannot_collide(self):
        engine = self._adopt_supportdesk()
        inferred_before = engine.db.query(
            "SELECT id,work_id,status,version FROM artifacts WHERE kind='spec' AND work_id LIKE 'BASE-%' ORDER BY id"
        )
        self.assertEqual([row["id"] for row in inferred_before], ["SPEC-BASE-0001", "SPEC-BASE-0002"])
        self.assertTrue(all(row["status"] == "inferred" and row["version"] == 1 for row in inferred_before))

        work = engine.start(SCENARIOS["supportdesk-premium-sla"]["description"], work_type="feature")
        wid = work["id"]
        for item in SCENARIOS["supportdesk-premium-sla"]["decisions"]:
            engine.register_decision(wid, item["question"], item["answer"])
        engine.continue_work(wid)
        created = engine.submit_spec(wid, brownfield_supportdesk_spec(), "test-agent")

        self.assertEqual(created["artifact"]["id"], f"SPEC-{wid}")
        inferred_after = engine.db.query(
            "SELECT id,work_id,status,version FROM artifacts WHERE kind='spec' AND work_id LIKE 'BASE-%' ORDER BY id"
        )
        self.assertEqual(inferred_after, inferred_before)
        self.assertEqual(engine.get_work(wid)["state"], "discovery")

    def test_implementation_context_uses_task_spec_query_not_broad_project_title(self):
        engine = self._adopt_supportdesk()
        wid = self._prepare_ready(engine)
        engine.continue_work(wid)
        preview = engine.context_preview(wid, max_tokens=3000)
        paths = [item["path"] for item in preview["impact"]["files"]]
        self.assertIn("supportdesk/services/sla.py", paths)
        self.assertFalse(any("/analytics/" in path or "/exports/" in path or "/imports/" in path for path in paths[:8]), paths)
        self.assertIn(f"{wid}-T01", [item["id"] for item in preview["items"] if item["type"] == "task"])


    def test_get_next_action_execute_is_compact_and_returns_post_transition_contract(self):
        import json
        from dynosai_flow.mcp import MCPServer
        engine = self._adopt_supportdesk()
        wid = self._prepare_ready(engine)
        server = MCPServer(self.tmp)
        result = server.call_tool("dynosai_get_next_action", {"work_id": wid, "execute": True})
        self.assertEqual(result["work"]["state"], "implementing")
        self.assertEqual(result["transition"]["from_state"], "ready")
        self.assertEqual(result["transition"]["to_state"], "implementing")
        self.assertEqual(result["contract"]["operation"], "implement_tasks")
        self.assertEqual(result["contract"]["task_queue"]["current_task"]["id"], f"{wid}-T01")
        self.assertNotIn("continue_result", result)
        self.assertNotIn("ready_tasks", result["contract"]["task_queue"])
        # Keep enough headroom below Cursor's provider-output spill threshold.
        logical_size=len(json.dumps(result, ensure_ascii=False).encode())
        wire={"content":[{"type":"text","text":json.dumps(result,ensure_ascii=False,indent=2)}],"structuredContent":result,"isError":False}
        wire_size=len(json.dumps(wire,ensure_ascii=False).encode())
        self.assertLess(logical_size, 20000)
        self.assertLess(wire_size, 30000)

    def test_cli_exposes_brownfield_debug_scenario(self):
        args = parser().parse_args(["debug", "e2e", "--scenario", "supportdesk-premium-sla"])
        self.assertEqual(args.scenario, "supportdesk-premium-sla")
        self.assertEqual(SCENARIOS[args.scenario]["mode"], "brownfield")

    def test_full_brownfield_e2e_with_deterministic_agent_and_independent_oracle(self):
        runner = DebugE2ERunner(
            output=self.tmp / "brownfield-result.zip",
            workspace=self.tmp / "debug-workspace",
            scenario="supportdesk-premium-sla",
            timeout=30,
        )

        def fake_agent(prompt: str, phase: str):
            e = runner.engine; wid = runner.work_id
            self.assertIsNotNone(e); self.assertIsNotNone(wid)
            if phase == "spec":
                e.continue_work(wid)
                contract = e.action_contract(wid)
                self.assertTrue(contract["repository_context"]["repository_context_complete"])
                e.submit_spec(wid, brownfield_supportdesk_spec(), "deterministic-agent")
                e.continue_work(wid)
            elif phase == "plan":
                contract = e.action_contract(wid)
                self.assertEqual(contract["operation"], "author_plan")
                e.submit_plan(wid, brownfield_supportdesk_plan(), "deterministic-agent")
            elif phase == "implementation":
                prep = e.continue_work(wid)
                wt = Path(prep["worktree"])
                (wt / "supportdesk/services/sla.py").write_text(
                    '''SLA_MINUTES = {"critical": 240, "high": 480, "normal": 1440}\n\ndef sla_policy(customer, severity: str) -> tuple[int, bool]:\n    """Return SLA minutes and whether immediate escalation is required."""\n    try:\n        base = SLA_MINUTES[severity]\n    except KeyError as exc:\n        raise ValueError(f"unsupported severity: {severity}") from exc\n    if customer.is_premium() and severity == "critical":\n        return 60, True\n    return base, False\n\ndef sla_minutes_for(severity: str) -> int:\n    """Backward-compatible severity-only SLA lookup."""\n    try:\n        return SLA_MINUTES[severity]\n    except KeyError as exc:\n        raise ValueError(f"unsupported severity: {severity}") from exc\n''', encoding="utf-8")
                (wt / "supportdesk/domain/ticket.py").write_text(
                    '''from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass Ticket:\n    """Persisted support ticket with SLA and escalation decision chosen at creation time."""\n    id: str\n    customer_id: str\n    severity: str\n    summary: str\n    sla_minutes: int\n    escalation_required: bool = False\n''', encoding="utf-8")
                (wt / "supportdesk/services/ticket_service.py").write_text(
                    '''from supportdesk.domain.customer import Customer\nfrom supportdesk.domain.ticket import Ticket\nfrom supportdesk.services.sla import sla_policy\n\nclass TicketService:\n    """Create tickets using the customer-aware SLA policy."""\n    def __init__(self, repository):\n        self.repository = repository\n\n    def create_ticket(self, customer: Customer, severity: str, summary: str) -> Ticket:\n        if not summary.strip():\n            raise ValueError("summary is required")\n        minutes, escalation = sla_policy(customer, severity)\n        ticket = Ticket(\n            id=self.repository.next_id(), customer_id=customer.id, severity=severity,\n            summary=summary.strip(), sla_minutes=minutes, escalation_required=escalation,\n        )\n        self.repository.save(ticket)\n        return ticket\n''', encoding="utf-8")
                (wt / "supportdesk/api/tickets.py").write_text(
                    '''def create_ticket_payload(service, customer, severity: str, summary: str) -> dict:\n    """Application API adapter for ticket creation."""\n    ticket = service.create_ticket(customer, severity, summary)\n    return {\n        "id": ticket.id, "customer_id": ticket.customer_id, "severity": ticket.severity,\n        "summary": ticket.summary, "sla_minutes": ticket.sla_minutes,\n        "escalation_required": ticket.escalation_required,\n    }\n''', encoding="utf-8")
                (wt / "tests/test_sla.py").write_text(
                    '''import unittest\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.services.sla import sla_minutes_for, sla_policy\n\nclass SlaTests(unittest.TestCase):\n    def test_current_sla_values(self):\n        self.assertEqual(sla_minutes_for("critical"),240); self.assertEqual(sla_minutes_for("high"),480); self.assertEqual(sla_minutes_for("normal"),1440)\n    def test_premium_critical_policy(self):\n        self.assertEqual(sla_policy(Customer("1","premium"),"critical"),(60,True))\n        self.assertEqual(sla_policy(Customer("2"),"critical"),(240,False))\n        self.assertEqual(sla_policy(Customer("3","premium"),"high"),(480,False))\n        self.assertEqual(sla_policy(Customer("4","premium"),"normal"),(1440,False))\n    def test_unknown_severity_is_rejected(self):\n        with self.assertRaises(ValueError): sla_policy(Customer("1"),"urgent-ish")\n''', encoding="utf-8")
                (wt / "tests/test_ticket_service.py").write_text(
                    '''import unittest\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\nfrom supportdesk.services.ticket_service import TicketService\n\nclass TicketServiceTests(unittest.TestCase):\n    def setUp(self): self.repo=InMemoryTicketRepository(); self.service=TicketService(self.repo)\n    def test_create_ticket_persists_ticket(self):\n        t=self.service.create_ticket(Customer("C-1"),"high","Need help"); self.assertEqual((t.sla_minutes,t.escalation_required),(480,False)); self.assertIs(self.repo.get(t.id),t)\n    def test_premium_critical_is_escalated(self):\n        t=self.service.create_ticket(Customer("C-2","premium"),"critical","Outage"); self.assertEqual((t.sla_minutes,t.escalation_required),(60,True)); self.assertEqual(len(self.repo.items),1)\n    def test_summary_is_required(self):\n        with self.assertRaises(ValueError): self.service.create_ticket(Customer("C-1"),"normal","   ")\n''', encoding="utf-8")
                (wt / "tests/test_ticket_api.py").write_text(
                    '''import unittest\nfrom supportdesk.api.tickets import create_ticket_payload\nfrom supportdesk.domain.customer import Customer\nfrom supportdesk.repositories.tickets import InMemoryTicketRepository\nfrom supportdesk.services.ticket_service import TicketService\n\nclass TicketApiTests(unittest.TestCase):\n    def test_standard_payload_contract(self):\n        p=create_ticket_payload(TicketService(InMemoryTicketRepository()),Customer("C-9"),"normal","Question"); self.assertEqual(p,{"id":"T-0001","customer_id":"C-9","severity":"normal","summary":"Question","sla_minutes":1440,"escalation_required":False})\n    def test_premium_critical_payload(self):\n        p=create_ticket_payload(TicketService(InMemoryTicketRepository()),Customer("C-8","premium"),"critical","Outage"); self.assertEqual(p["sla_minutes"],60); self.assertTrue(p["escalation_required"])\n''', encoding="utf-8")
                task_ids = [x["id"] for x in e.db.query("SELECT id FROM tasks WHERE work_id=? ORDER BY id", (wid,))]
                files = [x["path"] for x in brownfield_supportdesk_plan()["files"]]
                result = e.register_result(wid, "premium critical SLA implemented", files, [], task_ids)
                self.assertEqual(result["status"], "verified", result)
                e.continue_work(wid)
            return {"phase": phase, "simulated": True}

        runner.agent_runner = fake_agent
        with patch("dynosai_flow.engine.DynosAI.install_model", return_value={"installed": True}), \
             patch("dynosai_flow.engine.DynosAI.deep_doctor", return_value={"full_ready": True}):
            result = runner.run()
        self.assertEqual(result["status"], "passed", result)
        self.assertTrue(result["oracle"]["passed"])
        self.assertEqual(result["invariants"]["final_state"], "done")
        self.assertTrue(Path(result["bundle"]).exists())


if __name__ == "__main__":
    unittest.main()
