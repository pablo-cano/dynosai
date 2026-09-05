# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""RC8: provider runtime portability, candidate gates, append-only MATRIX."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.acceptance import CodexAppServerDriver, RealProviderAcceptanceSuite
from dynosai_flow.certification_matrix import (
    CELL_KEYS,
    candidate_certification_status,
    classify_matrix_failure,
    default_cell,
    default_matrix_workspace,
    latest_trial_for_candidate,
    load_live_matrix,
    publicize_matrix,
    validate_live_matrix,
)
from dynosai_flow.runtime_paths import (
    UNSAFE_CODEX_RUNTIME_MESSAGE,
    UnsafeCodexRuntime,
    assert_codex_home_safe,
    certification_workspace_root,
    default_acceptance_workspace,
    managed_codex_home,
    os_temp_root,
    path_is_under_repo,
    path_is_under_temp,
    user_local_state_root,
)
from dynosai_flow.version import DISPLAY_VERSION, __version__


ROOT = Path(__file__).resolve().parents[1]


def _slash(path: Path) -> str:
    return str(path).replace("\\", "/")


def _load_runner():
    path = ROOT / "scripts" / "run_matrix_1_0.py"
    spec = importlib.util.spec_from_file_location("run_matrix_1_0_rc8", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _history_matrix(counts: list[int], *, commit: str = "abc", subject: str = "def") -> dict:
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
    for (provider, mode), count in zip(CELL_KEYS, counts, strict=True):
        cell = default_cell(provider, mode)
        cell["status"] = "fail"
        cell["trials"] = []
        for attempt in range(1, count + 1):
            cell["trials"].append({
                "provider": provider,
                "mode": mode,
                "attempt": attempt,
                "final_status": "fail",
                "failure_attribution": "historical",
                "dynosai_git_commit": "other" if attempt < count else commit,
                "certification_subject_sha256": "other" if attempt < count else subject,
            })
        cell["evidence"] = {"attempt": count, "failure_attribution": "historical", "artifact_paths": [], "artifact_hashes": {}}
        matrix["cells"].append(cell)
    return validate_live_matrix(matrix)


class DynosAI280RuntimePathTests(unittest.TestCase):
    def test_windows_localappdata(self):
        root = user_local_state_root(
            environ={"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"},
            system="Windows",
            home=r"C:\Users\Test",
        )
        self.assertEqual(_slash(root), "C:/Users/Test/AppData/Local/DynosAI")

    def test_windows_userprofile_fallback(self):
        root = user_local_state_root(
            environ={"USERPROFILE": r"C:\Users\Test"},
            system="Windows",
            home=r"C:\Users\Test",
        )
        self.assertEqual(_slash(root), "C:/Users/Test/.dynosai")

    def test_linux_xdg(self):
        root = user_local_state_root(
            environ={"XDG_STATE_HOME": "/home/test/state"},
            system="Linux",
            home="/home/test",
        )
        self.assertEqual(root, Path("/home/test/state/dynosai"))

    def test_linux_fallback(self):
        root = user_local_state_root(environ={}, system="Linux", home="/home/test")
        self.assertEqual(root, Path("/home/test/.local/state/dynosai"))

    def test_macos(self):
        root = user_local_state_root(environ={}, system="Darwin", home="/Users/test")
        self.assertEqual(root, Path("/Users/test") / "Library" / "Application Support" / "DynosAI")

    def test_matrix_default_not_under_temp_or_repo(self):
        workspace = default_matrix_workspace(ROOT)
        self.assertFalse(path_is_under_temp(workspace))
        self.assertFalse(path_is_under_repo(workspace, ROOT))
        self.assertEqual(workspace, certification_workspace_root().resolve())

    def test_acceptance_default_not_under_temp(self):
        workspace = default_acceptance_workspace("ACCEPTANCE-test")
        self.assertFalse(path_is_under_temp(workspace))
        suite = RealProviderAcceptanceSuite(providers=("codex",), scenario="fibonacci", workspace=workspace)
        self.assertFalse(path_is_under_temp(suite.base))

    def test_safe_codex_home_accepted(self):
        home = user_local_state_root() / "safe-project" / ".dynosai" / "runtime" / "managed-agents" / "codex" / "home"
        self.assertFalse(path_is_under_temp(home))
        self.assertEqual(assert_codex_home_safe(home), home)

    def test_codex_home_under_temp_rejected(self):
        home = os_temp_root() / "dynosai-matrix-1.0" / ".dynosai" / "runtime" / "managed-agents" / "codex" / "home"
        with self.assertRaises(UnsafeCodexRuntime) as raised:
            assert_codex_home_safe(home)
        self.assertEqual(str(raised.exception), UNSAFE_CODEX_RUNTIME_MESSAGE)
        self.assertNotIn("C:\\Users\\", str(raised.exception))


class DynosAI280CodexGuardTests(unittest.TestCase):
    def test_unsafe_workspace_does_not_start_provider(self):
        runner = _load_runner()
        started = []

        def boom(*_args, **_kwargs):
            started.append(True)
            raise AssertionError("provider must not start")

        tmp = Path(tempfile.mkdtemp(prefix="dynosai-280-unsafe-"))
        (tmp / "matrix.json").write_text(json.dumps(_history_matrix([2, 2, 2, 2])), encoding="utf-8")
        original = runner.ROOT
        runner.ROOT = ROOT
        try:
            with patch.object(runner, "_run_live_cell", boom), patch.object(
                runner, "guard_live_certification", lambda *_a, **_k: {
                    "dynosai_git_commit": "x",
                    "certification_subject_sha256": "y",
                    "certification_dirty_paths": [],
                    "unexpected_dirty_paths": [],
                }
            ):
                code = runner.main([
                    "--live",
                    "--cells", "codex.greenfield",
                    "--workspace", str(tmp),
                    "--expected-subject-sha256", "y",
                    "--matrix", str(tmp / "matrix.json"),
                ])
        finally:
            runner.ROOT = original
        self.assertEqual(code, 2)
        self.assertEqual(started, [])

    def test_codex_driver_does_not_start_when_home_is_under_temp(self):
        project = Path(tempfile.mkdtemp(prefix="dynosai-280-codex-")) / "project"
        project.mkdir()
        logs = project.parent / "logs"
        started = []

        def boom(*_args, **_kwargs):
            started.append(True)
            raise AssertionError("provider must not start")

        with patch("dynosai_flow.acceptance.subprocess.Popen", boom), patch(
            "dynosai_flow.acceptance.subprocess.run", boom
        ):
            result = CodexAppServerDriver("codex", 5).run(project, "prompt", logs, interaction_mode="auto")
        self.assertEqual(started, [])
        self.assertEqual(result["provider_started"], False)
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["error"], UNSAFE_CODEX_RUNTIME_MESSAGE)
        self.assertNotIn("C:\\Users\\", str(result["error"]))


class DynosAI280MatrixHistoryTests(unittest.TestCase):
    def test_asymmetric_histories_are_valid(self):
        for counts in ([2, 2, 2, 2], [3, 2, 2, 2], [4, 3, 3, 3]):
            matrix = _history_matrix(counts)
            self.assertEqual([len(cell["trials"]) for cell in matrix["cells"]], counts)
            self.assertEqual(len(matrix["cells"]), 4)
            self.assertFalse(matrix["all_passed"])
            attempts = [[int(t["attempt"]) for t in cell["trials"]] for cell in matrix["cells"]]
            for series in attempts:
                self.assertEqual(series, list(range(1, len(series) + 1)))

    def test_public_live_matrix_keeps_rc7_greenfield_attempt3(self):
        matrix = load_live_matrix()
        public = publicize_matrix(matrix)
        blob = json.dumps(public)
        self.assertNotIn("OneDrive", blob)
        self.assertNotIn("C:\\Users\\", blob)
        counts = [len(cell.get("trials") or []) for cell in matrix["cells"]]
        self.assertEqual(len(counts), 4)
        self.assertEqual(counts[0], 3)
        self.assertGreaterEqual(min(counts), 2)
        self.assertFalse(matrix["all_passed"])
        green = matrix["cells"][0]
        self.assertEqual((green["provider"], green["mode"]), ("codex", "greenfield"))
        attempt3 = next(item for item in green["trials"] if int(item["attempt"]) == 3)
        self.assertEqual(attempt3["final_status"], "fail")
        self.assertEqual(attempt3["dynosai_git_commit"], "f97aa356876595638745928f26aed5dd6efb4721")
        self.assertEqual(
            attempt3["certification_subject_sha256"],
            "ad2e8a2abd44321f7ad3655a194a9c501d1bc95a6d6ad47227fef634246cf888",
        )
        self.assertIn("governed DYN work item", str(attempt3.get("failure_attribution") or ""))
        self.assertEqual(classify_matrix_failure(attempt3), "provider_runtime_layout")

    def test_candidate_gate_ignores_other_release_pass(self):
        matrix = _history_matrix([3, 2, 2, 2], commit="rc8", subject="subj8")
        matrix["cells"][0]["trials"][0]["final_status"] = "pass"
        matrix["cells"][0]["trials"][0]["dynosai_git_commit"] = "rc6"
        matrix["cells"][0]["trials"][0]["certification_subject_sha256"] = "old"
        status = candidate_certification_status("rc8", "subj8", matrix, candidate_version="1.0.0-rc.8")
        self.assertEqual(status["cells"]["codex.greenfield"]["attempt"], 3)
        self.assertEqual(status["cells"]["codex.greenfield"]["status"], "fail")
        self.assertFalse(status["all_passed"])
        latest = latest_trial_for_candidate(matrix["cells"][0], "rc8", "subj8")
        self.assertEqual(latest["attempt"], 3)

    def test_latest_matching_fail_overrides_earlier_pass(self):
        matrix = _history_matrix([2, 2, 2, 2], commit="same", subject="same")
        for cell in matrix["cells"]:
            cell["trials"][0]["final_status"] = "pass"
            cell["trials"][0]["dynosai_git_commit"] = "same"
            cell["trials"][0]["certification_subject_sha256"] = "same"
            cell["trials"][1]["final_status"] = "fail"
            cell["trials"][1]["dynosai_git_commit"] = "same"
            cell["trials"][1]["certification_subject_sha256"] = "same"
        status = candidate_certification_status("same", "subject-mismatch", matrix)
        self.assertFalse(status["all_cells_present"])
        status = candidate_certification_status("same", "same", matrix)
        self.assertTrue(status["all_cells_present"])
        self.assertFalse(status["all_passed"])
        self.assertEqual(status["cells"]["cursor.brownfield"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
