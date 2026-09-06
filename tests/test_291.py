# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""RC10: bounded same-session Codex continuation (synthetic, no model)."""

from __future__ import annotations

import json
import shutil
import stat
import tempfile
import unittest
import uuid
from pathlib import Path

from dynosai_flow.acceptance import CodexAppServerDriver
from dynosai_flow.certification_matrix import (
    CELL_KEYS,
    append_trial,
    candidate_certification_status,
    candidate_release_eligibility,
    default_cell,
    empty_trial,
    validate_live_matrix,
)
from dynosai_flow.runtime_paths import user_local_scratch
from dynosai_flow.session_continuity import (
    CONTINUATION_BUDGET_EXHAUSTED,
    EXTRA_DYN_WORK,
    NEW_PROVIDER_SESSION,
    PROVIDER_NO_PROGRESS,
    continuation_prompt,
    decide_continuation,
    empty_progress_snapshot,
    usage_delta,
)
from dynosai_flow.version import DISPLAY_VERSION


INITIAL_PROMPT = "INITIAL CERTIFICATION PROMPT WITH BUSINESS GOAL AND FULL SPEC"


def _snap(
    *,
    work_id: str | None = "DYN-0001",
    state: str | None = "implementing",
    tasks: int = 0,
    gates: tuple[str, ...] = (),
    governed: int = 1,
    revision: str | None = None,
    validation: str | None = None,
) -> dict:
    payload = empty_progress_snapshot()
    if governed <= 0:
        return payload
    payload.update({
        "work_id": work_id,
        "work_state": state,
        "phase": state,
        "current_action": state,
        "completed_task_count": tasks,
        "validation_state": validation,
        "observed_gates": gates,
        "git_revision": revision,
        "governed_work_items": governed,
    })
    return payload


class _QueueInspector:
    def __init__(self, snapshots: list[dict]):
        self.snapshots = [dict(item) for item in snapshots]
        self.calls = 0

    def __call__(self, project: Path) -> dict:
        self.calls += 1
        if not self.snapshots:
            raise AssertionError(f"inspect_state called too many times ({self.calls})")
        if len(self.snapshots) == 1:
            return dict(self.snapshots[0])
        return dict(self.snapshots.pop(0))


def _client_payloads(logs: Path) -> list[dict]:
    rows = []
    path = logs / "codex-app-server.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        if item.get("direction") == "client->codex":
            rows.append(item.get("payload") or {})
    return rows


def _turn_texts(payloads: list[dict]) -> list[str]:
    texts = []
    for payload in payloads:
        if payload.get("method") != "turn/start":
            continue
        items = (payload.get("params") or {}).get("input") or []
        texts.append(str((items[0] or {}).get("text") or "") if items else "")
    return texts


class SessionContinuity291Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        self.safe = user_local_scratch("unit-tests", uuid.uuid4().hex)
        self.safe.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.safe, ignore_errors=True))

    def _fake_codex(self, *, elicit: bool = False, completed_thread: str = "thr") -> Path:
        fake = self.tmp / "codex"
        fake.write_text(
            f"""#!/usr/bin/env python3
import json, sys

ELICIT = {elicit!r}
COMPLETED_THREAD = {completed_thread!r}

def send(x):
    print(json.dumps(x), flush=True)

def recv():
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)

m = recv(); assert m.get('method') == 'initialize', m
send({{'id': m['id'], 'result': {{'userAgent': 'codex-cli 0.147.0'}}}})
m = recv(); assert m.get('method') == 'initialized', m
m = recv(); assert m.get('method') == 'thread/start', m
assert m.get('params', {{}}).get('sandbox') == 'workspace-write', m
send({{'id': m['id'], 'result': {{'thread': {{'id': 'thr'}}}}}})

turn_n = 0
while True:
    m = recv()
    if m.get('method') != 'turn/start':
        continue
    turn_n += 1
    send({{'id': m['id'], 'result': {{'turn': {{'id': f'turn-{{turn_n}}', 'status': 'inProgress', 'items': []}}}}}})
    if ELICIT and turn_n == 1:
        send({{'method': 'mcpServer/elicitation/request', 'id': 42, 'params': {{
            'threadId': 'thr', 'turnId': 'turn-1', 'serverName': 'dynosai', 'mode': 'form',
            'message': 'Specification ready. Review the requirements and acceptance criteria before continuing.',
            'requestedSchema': {{'type': 'object', 'properties': {{
                'decision': {{'type': 'string', 'enum': ['approve', 'request_changes', 'cancel']}},
                'comments': {{'type': 'string', 'default': ''}}}}, 'required': ['decision']}}
        }}}})
        gate = recv()
        assert gate.get('id') == 42, gate
        assert gate.get('result', {{}}).get('action') == 'accept', gate
        assert gate.get('result', {{}}).get('content', {{}}).get('decision') == 'approve', gate
    send({{'method': 'turn/completed', 'params': {{'threadId': COMPLETED_THREAD, 'turn': {{'id': f'turn-{{turn_n}}', 'status': 'completed'}}}}}})
""",
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        return fake

    def _run(self, snapshots: list[dict], *, elicit: bool = False, completed_thread: str = "thr"):
        fake = self._fake_codex(elicit=elicit, completed_thread=completed_thread)
        root = self.safe / uuid.uuid4().hex
        root.mkdir()
        logs = self.tmp / uuid.uuid4().hex
        inspector = _QueueInspector(snapshots)
        result = CodexAppServerDriver(str(fake), 20, 8, inspect_state=inspector).run(
            root, INITIAL_PROMPT, logs, interaction_mode="auto"
        )
        payloads = _client_payloads(logs)
        return result, payloads, inspector

    def test_case_a_two_turns_implementing_then_done_pass(self):
        result, payloads, inspector = self._run([
            empty_progress_snapshot(),
            _snap(state="implementing", tasks=0),
            _snap(state="done", tasks=1),
        ])
        self.assertIsNone(result["continuation_failure"], result)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["turn_count"], 2)
        self.assertEqual(result["provider_session_count"], 1)
        self.assertEqual(result["thread_id"], "thr")
        self.assertEqual(result["provider_session_id"], "thr")
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "thread/start"), 1)
        texts = _turn_texts(payloads)
        self.assertEqual(len(texts), 2)
        self.assertIn(INITIAL_PROMPT, texts[0])
        self.assertNotIn(INITIAL_PROMPT, texts[1])
        self.assertIn("Continue the existing DynosAI", texts[1])
        self.assertLess(len(texts[1]), len(texts[0]))
        self.assertEqual(inspector.calls, 3)

    def test_case_b_three_turns_with_progress_then_done_pass(self):
        result, payloads, _ = self._run([
            empty_progress_snapshot(),
            _snap(state="implementing", tasks=0),
            _snap(state="implementing", tasks=1),
            _snap(state="done", tasks=2),
        ])
        self.assertIsNone(result["continuation_failure"], result)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["turn_count"], 3)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "turn/start"), 3)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "thread/start"), 1)

    def test_case_c_budget_exhausted(self):
        result, payloads, _ = self._run([
            empty_progress_snapshot(),
            _snap(state="implementing", tasks=0),
            _snap(state="implementing", tasks=1),
            _snap(state="implementing", tasks=2),
        ])
        self.assertEqual(result["continuation_failure"], CONTINUATION_BUDGET_EXHAUSTED)
        self.assertEqual(result["exit_code"], 1, result)
        self.assertEqual(result["turn_count"], 3)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "turn/start"), 3)
        self.assertEqual(result["termination_reason"], CONTINUATION_BUDGET_EXHAUSTED)

    def test_case_d_no_progress(self):
        stuck = _snap(state="implementing", tasks=1, gates=("spec:accepted",))
        result, payloads, _ = self._run([stuck, stuck])
        self.assertEqual(result["continuation_failure"], PROVIDER_NO_PROGRESS)
        self.assertEqual(result["exit_code"], 1, result)
        self.assertEqual(result["turn_count"], 1)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "turn/start"), 1)

    def test_case_e_gate_then_continue_same_work(self):
        result, payloads, _ = self._run(
            [
                _snap(state="implementing", tasks=0, gates=()),
                _snap(state="implementing", tasks=0, gates=("spec:accepted",)),
                _snap(state="done", tasks=1, gates=("spec:accepted", "plan:accepted")),
            ],
            elicit=True,
        )
        self.assertIsNone(result["continuation_failure"], result)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["turn_count"], 2)
        self.assertEqual(result["turns"][0]["work_state_before"], "implementing")
        self.assertEqual(result["turns"][0]["work_state_after"], "implementing")
        self.assertEqual(result["turns"][1]["work_state_after"], "done")
        self.assertGreaterEqual(result["elicitation_auto_responses"], 1)
        texts = _turn_texts(payloads)
        self.assertIn("DYN-0001", texts[1])
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "thread/start"), 1)

    def test_case_f_second_dyn_work_fails(self):
        result, payloads, _ = self._run([
            empty_progress_snapshot(),
            _snap(work_id="DYN-0001", state="implementing", governed=2),
        ])
        self.assertEqual(result["continuation_failure"], EXTRA_DYN_WORK)
        self.assertEqual(result["exit_code"], 1, result)
        self.assertEqual(result["turn_count"], 1)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "turn/start"), 1)

    def test_case_g_new_thread_fails(self):
        result, payloads, _ = self._run(
            [empty_progress_snapshot(), _snap(state="done")],
            completed_thread="other-thread",
        )
        self.assertEqual(result["continuation_failure"], NEW_PROVIDER_SESSION)
        self.assertEqual(result["exit_code"], 1, result)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "thread/start"), 1)

    def test_empty_project_stays_single_turn_without_continuation(self):
        result, payloads, _ = self._run([empty_progress_snapshot(), empty_progress_snapshot()])
        self.assertIsNone(result["continuation_failure"], result)
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(result["turn_count"], 1)
        self.assertEqual(sum(1 for item in payloads if item.get("method") == "turn/start"), 1)

    def test_usage_delta_does_not_double_count(self):
        first = {"input_tokens": 100, "cached_input_tokens": 10, "output_tokens": 20, "reasoning_output_tokens": 5, "observed_token_total": 125}
        second = {"input_tokens": 150, "cached_input_tokens": 40, "output_tokens": 35, "reasoning_output_tokens": 8, "observed_token_total": 193}
        delta1 = usage_delta(None, first)
        delta2 = usage_delta(first, second)
        session = usage_delta(None, second)
        for key in session:
            self.assertEqual(delta1[key] + delta2[key], session[key], key)

    def test_continuation_prompt_is_short(self):
        text = continuation_prompt(work_id="DYN-0001", state="implementing")
        self.assertIn("DYN-0001", text)
        self.assertIn("implementing", text)
        self.assertNotIn(INITIAL_PROMPT, text)
        self.assertLess(len(text), 800)

    def test_decide_continuation_never_issues_a_fourth_turn(self):
        before = _snap(state="implementing", tasks=1)
        after = _snap(state="implementing", tasks=2)
        decision = decide_continuation(
            turn_index=3,
            max_turns=3,
            thread_id="thr",
            completed_thread_id="thr",
            before=before,
            after=after,
        )
        self.assertEqual(decision.action, "stop")
        self.assertEqual(decision.reason, CONTINUATION_BUDGET_EXHAUSTED)


class MatrixEligibility291Tests(unittest.TestCase):
    def _matrix(self, statuses: dict[str, str], *, commit: str = "rc10", subject: str = "subj10") -> dict:
        matrix = {
            "schema": "MATRIX_1.0",
            "schema_version": 1,
            "release_line": "1.0",
            "copied_from_historical": False,
            "all_passed": False,
            "historical_baseline": {"release": "0.13.0", "path": "docs/validation/final-matrix-0.13.0.json", "note": "n"},
            "environment": {
                "dynosai_git_commit": commit,
                "certification_subject_sha256": subject,
                "dynosai_display_version": DISPLAY_VERSION,
            },
            "cells": [],
        }
        for provider, mode in CELL_KEYS:
            key = f"{provider}.{mode}"
            status = statuses[key]
            cell = default_cell(provider, mode)
            cell["status"] = status
            trial = empty_trial(
                provider=provider,
                mode=mode,
                environment={
                    "dynosai_git_commit": commit,
                    "certification_subject_sha256": subject,
                    "dynosai_display_version": DISPLAY_VERSION,
                    "dynosai_version": "1.0.0rc10",
                    "provider_client_versions": {"codex": "test", "cursor-agent": "test", "cursor": "test"},
                },
            )
            trial.update({
                "attempt": 1,
                "final_status": status if status != "not_run" else "fail",
                "dynosai_git_commit": commit,
                "certification_subject_sha256": subject,
                "retry_history": [],
                "provider_session_count": 1,
                "turn_count": 3 if provider == "codex" else 1,
                "turns": [{"turn_index": 1, "turn_id": "turn-1"}],
                "thread_id": "thr",
                "provider_session_id": "thr",
                "token_usage": {"input_tokens": 10, "output_tokens": 4},
                "estimated_cost": 0.01,
                "artifact_paths": ["logs/summary.json"],
                "artifact_hashes": {"logs/summary.json": "abc"},
            })
            if status == "pass":
                cell["evidence"] = {"attempt": 1, "artifact_paths": trial["artifact_paths"], "artifact_hashes": trial["artifact_hashes"]}
            elif status == "fail":
                cell["evidence"] = {"attempt": 1, "failure_attribution": "preview", "artifact_paths": [], "artifact_hashes": {}}
                trial["failure_attribution"] = "preview"
            cell["trials"] = [trial]
            matrix["cells"].append(cell)
        cert = candidate_certification_status(commit, subject, matrix, candidate_version=DISPLAY_VERSION)
        matrix["all_passed"] = cert["all_passed"]
        return validate_live_matrix(matrix)

    def test_codex_pass_cursor_fail_is_1_0_eligible_but_not_all_passed(self):
        matrix = self._matrix({
            "codex.greenfield": "pass",
            "codex.brownfield": "pass",
            "cursor.greenfield": "fail",
            "cursor.brownfield": "fail",
        })
        eligibility = candidate_release_eligibility("rc10", "subj10", matrix, candidate_version=DISPLAY_VERSION)
        self.assertTrue(eligibility["release_1_0_eligible"])
        self.assertFalse(eligibility["all_passed"])
        self.assertEqual(eligibility["codex_status"], "certified")
        self.assertEqual(eligibility["cursor_status"], "preview")
        self.assertEqual(eligibility["provider_preview_evidence"]["cursor.greenfield"]["status"], "fail")

    def test_single_matrix_attempt_keeps_turn_count(self):
        matrix = self._matrix({
            "codex.greenfield": "fail",
            "codex.brownfield": "fail",
            "cursor.greenfield": "fail",
            "cursor.brownfield": "fail",
        })
        trial = empty_trial(
            provider="codex",
            mode="greenfield",
            environment={"dynosai_git_commit": "rc10", "certification_subject_sha256": "subj10", "provider_client_versions": {}},
        )
        trial.update({
            "attempt": 2,
            "final_status": "fail",
            "failure_attribution": CONTINUATION_BUDGET_EXHAUSTED,
            "dynosai_git_commit": "rc10",
            "certification_subject_sha256": "subj10",
            "turn_count": 3,
            "provider_session_count": 1,
            "retry_history": [],
            "turns": [{"turn_index": 1}, {"turn_index": 2}, {"turn_index": 3}],
        })
        append_trial(matrix, "codex", "greenfield", trial)
        cell = next(item for item in matrix["cells"] if item["provider"] == "codex" and item["mode"] == "greenfield")
        self.assertEqual(len(cell["trials"]), 2)
        self.assertEqual(cell["trials"][-1]["turn_count"], 3)
        self.assertEqual(cell["trials"][-1]["attempt"], 2)
        self.assertEqual(cell["trials"][-1]["provider_session_count"], 1)
        eligibility = candidate_release_eligibility("rc10", "subj10", matrix)
        self.assertFalse(eligibility["release_1_0_eligible"])
        self.assertEqual(eligibility["codex_status"], "certification_pending")


if __name__ == "__main__":
    unittest.main()
