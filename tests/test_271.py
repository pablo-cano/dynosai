# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""RC7 pre-certification: candidate identity vs mutable MATRIX evidence."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from dynosai_flow.certification_matrix import (
    CELL_KEYS,
    CertificationAborted,
    attempt_n_same_candidate,
    classify_certification_dirty,
    default_cell,
    default_live_matrix,
    empty_trial,
    guard_live_certification,
    save_live_matrix,
)
from dynosai_flow.release_manifest import (
    certification_subject_identity,
    list_certification_subject_files,
    list_release_files,
    source_tree_identity,
    write_source_zip,
)
from dynosai_flow.version import DISPLAY_VERSION, __version__


ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = ROOT / "scripts" / "run_matrix_1_0.py"
    spec = importlib.util.spec_from_file_location("run_matrix_1_0", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DynosAI271CertificationSubjectTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="dynosai-271-")
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)

    def _product_tree(self, name: str, *, git: bool = True) -> Path:
        tree = self.tmp / name
        tree.mkdir()
        (tree / "src" / "dynosai_flow").mkdir(parents=True)
        (tree / "src" / "dynosai_flow" / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tree / "scripts").mkdir()
        (tree / "scripts" / "run_matrix_1_0.py").write_text("print('matrix-runner')\n", encoding="utf-8")
        (tree / "tests").mkdir()
        (tree / "tests" / "test_example.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (tree / "apps" / "studio").mkdir(parents=True)
        (tree / "apps" / "studio" / "i18n.js").write_text("export default {}\n", encoding="utf-8")
        (tree / "apps" / "web").mkdir(parents=True)
        (tree / "apps" / "web" / "package.json").write_text('{"name":"web"}\n', encoding="utf-8")
        (tree / "docs" / "validation").mkdir(parents=True)
        (tree / "docs" / "validation" / "matrix-1.0.json").write_text('{"schema":"MATRIX_1.0"}\n', encoding="utf-8")
        (tree / "docs" / "RELEASE_PROCESS.md").write_text("policy\n", encoding="utf-8")
        (tree / "pyproject.toml").write_text('version = "1.0.0rc7"\n', encoding="utf-8")
        (tree / "README.md").write_text("DynosAI\n", encoding="utf-8")
        (tree / ".gitignore").write_text(".env\n.dynosai/\ndist/\n", encoding="utf-8")
        if git:
            subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tree, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=tree, check=True)
            subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
            subprocess.run(["git", "commit", "-qm", "candidate"], cwd=tree, check=True)
        return tree

    def test_version_unchanged(self):
        self.assertEqual(__version__, "1.0.0rc7")
        self.assertEqual(DISPLAY_VERSION, "1.0.0-rc.7")

    def test_matrix_mutation_does_not_change_certification_subject(self):
        tree = self._product_tree("matrix-only")
        before_source = source_tree_identity(tree)
        before_subject = certification_subject_identity(tree)
        self.assertIn("docs/validation/matrix-1.0.json", list_release_files(tree))
        self.assertNotIn("docs/validation/matrix-1.0.json", list_certification_subject_files(tree))
        self.assertEqual(before_subject["certification_subject_file_count"], before_source["source_file_count"] - 1)
        (tree / "docs" / "validation" / "matrix-1.0.json").write_text('{"schema":"MATRIX_1.0","trials":[1]}\n', encoding="utf-8")
        after_source = source_tree_identity(tree)
        after_subject = certification_subject_identity(tree)
        self.assertNotEqual(before_source["source_tree_sha256"], after_source["source_tree_sha256"])
        self.assertEqual(before_subject["certification_subject_sha256"], after_subject["certification_subject_sha256"])
        self.assertEqual(before_subject["certification_subject_file_count"], after_subject["certification_subject_file_count"])
        archive = self.tmp / "still-has-matrix.zip"
        write_source_zip(tree, archive, prefix="dynosai-test")
        with zipfile.ZipFile(archive) as zipped:
            self.assertTrue(any(name.endswith("docs/validation/matrix-1.0.json") for name in zipped.namelist()))

    def test_source_script_and_pyproject_mutations_change_subject(self):
        tree = self._product_tree("mutations")
        baseline = certification_subject_identity(tree)["certification_subject_sha256"]
        (tree / "src" / "dynosai_flow" / "engine.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertNotEqual(baseline, certification_subject_identity(tree)["certification_subject_sha256"])
        (tree / "src" / "dynosai_flow" / "engine.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.assertEqual(baseline, certification_subject_identity(tree)["certification_subject_sha256"])
        (tree / "scripts" / "run_matrix_1_0.py").write_text("print('changed')\n", encoding="utf-8")
        self.assertNotEqual(baseline, certification_subject_identity(tree)["certification_subject_sha256"])
        (tree / "scripts" / "run_matrix_1_0.py").write_text("print('matrix-runner')\n", encoding="utf-8")
        self.assertEqual(baseline, certification_subject_identity(tree)["certification_subject_sha256"])
        (tree / "pyproject.toml").write_text('version = "1.0.0rc7"\nname = "dynosai"\n', encoding="utf-8")
        self.assertNotEqual(baseline, certification_subject_identity(tree)["certification_subject_sha256"])

    def test_matrix_only_dirty_tree_is_allowed_between_trials(self):
        tree = self._product_tree("dirty-matrix")
        (tree / "docs" / "validation" / "matrix-1.0.json").write_text('{"schema":"MATRIX_1.0","attempt":3}\n', encoding="utf-8")
        dirty = classify_certification_dirty(tree)
        self.assertEqual(dirty["certification_dirty_paths"], ["docs/validation/matrix-1.0.json"])
        self.assertEqual(dirty["unexpected_dirty_paths"], [])
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        env = guard_live_certification(tree, expected_subject_sha256=subject)
        self.assertEqual(env["certification_subject_sha256"], subject)
        self.assertEqual(env["certification_dirty_paths"], ["docs/validation/matrix-1.0.json"])

    def test_unexpected_dirty_source_aborts_before_provider_launch(self):
        tree = self._product_tree("dirty-src")
        (tree / "src" / "dynosai_flow" / "engine.py").write_text("VALUE = 99\n", encoding="utf-8")
        dirty = classify_certification_dirty(tree)
        self.assertIn("src/dynosai_flow/engine.py", dirty["unexpected_dirty_paths"])
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        with self.assertRaises(CertificationAborted) as raised:
            guard_live_certification(tree, expected_subject_sha256=subject)
        self.assertEqual(raised.exception.reason, "unexpected_dirty")
        self.assertIn("ABORT CERTIFICATION", str(raised.exception))
        self.assertIn("Provider execution was not started.", str(raised.exception))

    def test_wrong_expected_subject_aborts_before_provider_launch(self):
        tree = self._product_tree("wrong-sha")
        current = certification_subject_identity(tree)["certification_subject_sha256"]
        with self.assertRaises(CertificationAborted) as raised:
            guard_live_certification(tree, expected_subject_sha256="deadbeef")
        self.assertEqual(raised.exception.reason, "subject_mismatch")
        self.assertIn("Certification subject changed.", str(raised.exception))
        self.assertIn("Expected: deadbeef", str(raised.exception))
        self.assertIn(f"Current: {current}", str(raised.exception))
        self.assertIn("Provider execution was not started.", str(raised.exception))

    def _run_live_main(self, tree: Path, expected: str, runner, live_impl, *, cells: str | None = "codex.greenfield", matrix=None):
        matrix_path = tree / "docs" / "validation" / "matrix-1.0.json"
        payload = matrix if matrix is not None else default_live_matrix()
        save_live_matrix(payload, matrix_path)
        original = runner.ROOT
        runner.ROOT = tree
        argv = [
            "--live",
            "--matrix", str(matrix_path),
            "--workspace", str(self.tmp / "ws"),
            "--expected-subject-sha256", expected,
        ]
        if cells is not None:
            argv.extend(["--cells", cells])
        try:
            with patch.object(runner, "_run_live_cell", live_impl):
                return runner.main(argv), matrix_path
        finally:
            runner.ROOT = original

    def test_runner_wrong_subject_does_not_start_provider(self):
        tree = self._product_tree("runner-sha")
        runner = _load_runner()
        started = []

        def boom(*_args, **_kwargs):
            started.append(True)
            raise AssertionError("provider must not start")

        code, _path = self._run_live_main(tree, "deadbeef", runner, boom)
        self.assertEqual(code, 2)
        self.assertEqual(started, [])

    def test_runner_unexpected_dirty_does_not_start_provider(self):
        tree = self._product_tree("runner-dirty")
        (tree / "scripts" / "run_matrix_1_0.py").write_text("print('mutated')\n", encoding="utf-8")
        runner = _load_runner()
        started = []

        def boom(*_args, **_kwargs):
            started.append(True)
            raise AssertionError("provider must not start")

        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, _path = self._run_live_main(tree, subject, runner, boom)
        self.assertEqual(code, 2)
        self.assertEqual(started, [])

    def test_runner_matrix_only_dirty_reaches_live_hook(self):
        tree = self._product_tree("runner-matrix")
        (tree / "docs" / "validation" / "matrix-1.0.json").write_text('{"schema":"MATRIX_1.0","note":"trial"}\n', encoding="utf-8")
        runner = _load_runner()
        started = []

        def fake_live(provider, mode, workspace, attempt):
            started.append((provider, mode, attempt))
            return {
                "exit_code": 1,
                "started_at": "2026-09-05T00:00:00+00:00",
                "finished_at": "2026-09-05T00:00:01+00:00",
                "output": None,
                "log_dir": None,
                "scenario": "fibonacci",
            }

        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, _path = self._run_live_main(tree, subject, runner, fake_live)
        self.assertEqual(started, [("codex", "greenfield", 1)])
        self.assertNotEqual(code, 2)

    def test_four_synthetic_trials_share_candidate_identity(self):
        matrix = default_live_matrix()
        commit = "3737924aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        subject = "abc-certification-subject"
        for cell in matrix["cells"]:
            cell["status"] = "fail"
            cell["trials"] = []
            for attempt in (1, 2, 3):
                cell["trials"].append({
                    "provider": cell["provider"],
                    "mode": cell["mode"],
                    "attempt": attempt,
                    "final_status": "fail",
                    "dynosai_git_commit": commit if attempt == 3 else "83434dc-rc6",
                    "certification_subject_sha256": subject if attempt == 3 else "rc6-subject",
                    "source_tree_sha256": f"tree-{cell['provider']}-{attempt}",
                    "mcp_protocol_version": "2025-11-25" if attempt == 3 else "2026-07-28",
                    "provider_client_version": f"{cell['provider']}-cli",
                })
        report = attempt_n_same_candidate(matrix, 3)
        self.assertTrue(report["all_attempt3_same_candidate"])
        self.assertEqual(report["trial_count"], 4)
        matrix["cells"][0]["trials"][2]["certification_subject_sha256"] = "different"
        split = attempt_n_same_candidate(matrix, 3)
        self.assertFalse(split["all_attempt3_same_candidate"])

    def test_empty_trial_records_both_identities(self):
        tree = self._product_tree("empty-trial")
        from dynosai_flow.certification_matrix import probe_environment
        env = probe_environment(tree)
        trial = empty_trial(provider="codex", mode="greenfield", attempt=3, environment=env)
        self.assertEqual(trial["source_tree_sha256"], env["source_tree_sha256"])
        self.assertEqual(trial["certification_subject_sha256"], env["certification_subject_sha256"])
        self.assertEqual(trial["source_file_count"], env["source_file_count"])
        self.assertEqual(trial["certification_subject_file_count"], env["certification_subject_file_count"])
        self.assertNotEqual(trial["source_tree_sha256"], trial["certification_subject_sha256"])

    def _historical_fail_matrix(self):
        matrix = {
            "schema": "MATRIX_1.0",
            "schema_version": 1,
            "release_line": "1.0",
            "copied_from_historical": False,
            "all_passed": False,
            "historical_baseline": {
                "release": "0.13.0",
                "path": "docs/validation/final-matrix-0.13.0.json",
                "note": "Historical core evidence. Not 1.0 live certification.",
            },
            "environment": {},
            "cells": [default_cell(provider, mode) for provider, mode in CELL_KEYS],
        }
        for cell in matrix["cells"]:
            cell["status"] = "fail"
            cell["trials"] = [{
                "provider": cell["provider"],
                "mode": cell["mode"],
                "attempt": 1,
                "final_status": "fail",
                "failure_attribution": "historical",
            }]
            cell["evidence"] = {
                "attempt": 1,
                "failure_attribution": "historical",
                "artifact_paths": [],
                "artifact_hashes": {},
            }
        matrix["all_passed"] = False
        return matrix

    def _fake_outcomes(self, outcomes: dict[tuple[str, str], bool], started: list):
        def impl(provider, mode, workspace, attempt):
            started.append((provider, mode, attempt))
            workspace_path = Path(workspace)
            workspace_path.mkdir(parents=True, exist_ok=True)
            ok = bool(outcomes[(provider, mode)])
            output = None
            if ok:
                bundle = workspace_path / f"{provider}-{mode}-pass.zip"
                with zipfile.ZipFile(bundle, "w") as archive:
                    archive.writestr("summary-final.json", json.dumps({"status": "passed"}))
                output = str(bundle)
            return {
                "exit_code": 0 if ok else 1,
                "started_at": "2026-09-05T00:00:00+00:00",
                "finished_at": "2026-09-05T00:00:01+00:00",
                "output": output,
                "log_dir": None,
                "scenario": "fibonacci",
            }
        return impl

    def test_selected_greenfield_pass_exit_zero_while_matrix_still_failed(self):
        tree = self._product_tree("exit-subset-pass")
        runner = _load_runner()
        started = []
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, matrix_path = self._run_live_main(
            tree, subject, runner,
            self._fake_outcomes({("codex", "greenfield"): True}, started),
            cells="codex.greenfield",
            matrix=self._historical_fail_matrix(),
        )
        payload = json.loads(matrix_path.read_text(encoding="utf-8"))
        self.assertEqual(started, [("codex", "greenfield", 2)])
        self.assertEqual(code, 0)
        self.assertFalse(payload["all_passed"])
        by_key = {(cell["provider"], cell["mode"]): cell for cell in payload["cells"]}
        self.assertEqual(by_key[("codex", "greenfield")]["status"], "pass")
        self.assertEqual(by_key[("codex", "brownfield")]["status"], "fail")
        self.assertEqual(by_key[("cursor", "greenfield")]["status"], "fail")
        self.assertEqual(by_key[("cursor", "brownfield")]["status"], "fail")

    def test_selected_cell_fail_exits_one(self):
        tree = self._product_tree("exit-subset-fail")
        runner = _load_runner()
        started = []
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, _path = self._run_live_main(
            tree, subject, runner,
            self._fake_outcomes({("codex", "greenfield"): False}, started),
            cells="codex.greenfield",
            matrix=self._historical_fail_matrix(),
        )
        self.assertEqual(code, 1)
        self.assertEqual(started, [("codex", "greenfield", 2)])

    def test_two_selected_cells_both_pass_exit_zero(self):
        tree = self._product_tree("exit-two-pass")
        runner = _load_runner()
        started = []
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, payload_path = self._run_live_main(
            tree, subject, runner,
            self._fake_outcomes({("codex", "greenfield"): True, ("cursor", "greenfield"): True}, started),
            cells="codex.greenfield,cursor.greenfield",
            matrix=self._historical_fail_matrix(),
        )
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertFalse(payload["all_passed"])
        self.assertEqual([item[:2] for item in started], [("codex", "greenfield"), ("cursor", "greenfield")])

    def test_two_selected_cells_one_fail_exit_one(self):
        tree = self._product_tree("exit-two-mixed")
        runner = _load_runner()
        started = []
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, _path = self._run_live_main(
            tree, subject, runner,
            self._fake_outcomes({("codex", "greenfield"): True, ("cursor", "greenfield"): False}, started),
            cells="codex.greenfield,cursor.greenfield",
            matrix=self._historical_fail_matrix(),
        )
        self.assertEqual(code, 1)
        self.assertEqual([item[:2] for item in started], [("codex", "greenfield"), ("cursor", "greenfield")])

    def test_all_cells_one_fail_exit_one(self):
        tree = self._product_tree("exit-all-one-fail")
        runner = _load_runner()
        started = []
        outcomes = {key: key != ("cursor", "brownfield") for key in CELL_KEYS}
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, payload_path = self._run_live_main(
            tree, subject, runner,
            self._fake_outcomes(outcomes, started),
            cells=None,
            matrix=self._historical_fail_matrix(),
        )
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        self.assertEqual(code, 1)
        self.assertFalse(payload["all_passed"])
        self.assertEqual(len(started), 4)

    def test_all_cells_pass_exit_zero(self):
        tree = self._product_tree("exit-all-pass")
        runner = _load_runner()
        started = []
        outcomes = {key: True for key in CELL_KEYS}
        subject = certification_subject_identity(tree)["certification_subject_sha256"]
        code, payload_path = self._run_live_main(
            tree, subject, runner,
            self._fake_outcomes(outcomes, started),
            cells=None,
            matrix=self._historical_fail_matrix(),
        )
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertTrue(payload["all_passed"])
        self.assertEqual(len(started), 4)


if __name__ == "__main__":
    unittest.main()
