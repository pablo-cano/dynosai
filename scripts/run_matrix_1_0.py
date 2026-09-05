#!/usr/bin/env python3
"""Record MATRIX_1.0 environment probes and optional live provider trials.

Default mode is probe-only: cells stay not_run. --live runs exactly one new
attempt per selected cell and keeps the full trial history. Silent retries
that rewrite a failure into PASS are not implemented.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dynosai_flow.certification_matrix import (  # noqa: E402
    CELL_KEYS,
    SCENARIO_FOR_MODE,
    CertificationAborted,
    append_trial,
    collapse_observed_mcp_protocols,
    default_matrix_workspace,
    empty_trial,
    guard_live_certification,
    load_live_matrix,
    observed_mcp_protocols_from_payloads,
    probe_environment,
    save_live_matrix,
)
from dynosai_flow.util import utc_now  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _select_cells(raw: str | None) -> list[tuple[str, str]]:
    if not raw:
        return list(CELL_KEYS)
    selected = []
    for item in raw.split(","):
        provider, _, mode = item.strip().partition(".")
        pair = (provider, mode)
        if pair not in CELL_KEYS:
            raise SystemExit(f"unknown cell {item!r}; expected provider.mode from {CELL_KEYS}")
        selected.append(pair)
    return selected


def live_exit_code(matrix: dict, selected_cells: list[tuple[str, str]], *, cells_specified: bool) -> int:
    """Command exit code. Subset --cells ignores historical status of other cells."""
    if cells_specified:
        for provider, mode in selected_cells:
            cell = next(item for item in matrix["cells"] if item["provider"] == provider and item["mode"] == mode)
            trials = cell.get("trials") or []
            latest = trials[-1] if trials else {}
            if str(latest.get("final_status") or "") != "pass":
                return 1
        return 0
    return 0 if all(cell.get("status") == "pass" for cell in matrix["cells"]) else 1


def _run_live_cell(provider: str, mode: str, workspace: Path, attempt: int) -> dict:
    from dynosai_flow.cli import main
    scenario = SCENARIO_FOR_MODE[mode]
    workspace.mkdir(parents=True, exist_ok=True)
    tag = f"{provider}-{mode}-attempt{attempt}"
    output = workspace / f"{tag}.zip"
    log_dir = workspace / f"{tag}-logs"
    argv = [
        "debug", "acceptance",
        "--providers", provider,
        "--scenario", scenario,
        "--interaction-mode", "auto",
        "--model-profile", "economy",
        "--output", str(output),
        "--workspace", str(workspace / f"{tag}-ws"),
        "--log-dir", str(log_dir),
        "--max-runtime", "1800",
        "--certification-mode",
        "--max-infrastructure-attempts", "1",
    ]
    started = utc_now()
    code = main(argv)
    finished = utc_now()
    return {
        "exit_code": code,
        "started_at": started,
        "finished_at": finished,
        "output": str(output) if output.exists() else None,
        "log_dir": str(log_dir) if log_dir.exists() else None,
        "scenario": scenario,
    }


def _failure_from_payload(payload: dict) -> str | None:
    failure = payload.get("failure")
    if isinstance(failure, dict):
        kind = str(failure.get("type") or "error")
        message = str(failure.get("message") or "").strip()
        if message:
            return f"{kind}: {message}"[:500]
    for key in ("children", "cases", "results"):
        items = payload.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    found = _failure_from_payload(item)
                    if found:
                        return found
    return None


def _summarize_bundle(path: str | None) -> dict:
    empty = {
        "oracle_result": None,
        "token_usage": None,
        "final_status": "fail",
        "failure_attribution": "missing_acceptance_bundle",
        "model": None,
        "mcp_protocol_version": None,
        "mcp_protocols_observed": [],
        "mcp_protocol_note": "no MCP tool calls observed",
        "validation_commands": None,
        "validation_results": None,
        "scope_result": None,
        "git_evidence": None,
        "human_gate_evidence": None,
        "execution_profile": None,
        "harness_feature_state": None,
        "acp_app_server_capabilities": None,
        "retry_history": None,
    }
    if not path or not Path(path).is_file():
        return empty
    import zipfile
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        summary_name = next((name for name in names if name.endswith("summary-final.json")), None)
        if summary_name is None:
            missing = dict(empty)
            missing["failure_attribution"] = "missing_summary"
            return missing
        summary = json.loads(archive.read(summary_name).decode("utf-8"))
        case_failure = None
        model = None
        observed: list[str] = []
        oracle_failures: list[str] = []
        first_case: dict | None = None
        retry_history = None
        for name in names:
            if not name.endswith("/logs/summary.json"):
                continue
            case = json.loads(archive.read(name).decode("utf-8"))
            if first_case is None:
                first_case = case
            if case_failure is None:
                case_failure = _failure_from_payload(case)
            oracle = case.get("oracle") if isinstance(case.get("oracle"), dict) else {}
            oracle_failures.extend(str(item) for item in (oracle.get("failures") or []) if item)
            run = case.get("provider_run") if isinstance(case.get("provider_run"), dict) else {}
            observed_model = run.get("model_observed") or run.get("model_selector")
            if observed_model and model is None:
                model = str(observed_model)
            usage_model = ((case.get("token_usage") or {}).get("model") if isinstance(case.get("token_usage"), dict) else None)
            if usage_model and (not model or model == "unknown"):
                model = str(usage_model)
            case_observed = case.get("mcp_protocols_observed")
            if isinstance(case_observed, list):
                for item in case_observed:
                    if item and str(item) not in observed:
                        observed.append(str(item))
            payloads = []
            if isinstance(case.get("mcp_calls"), list):
                payloads.extend(item for item in case["mcp_calls"] if isinstance(item, dict))
            for extra in observed_mcp_protocols_from_payloads(payloads):
                if extra not in observed:
                    observed.append(extra)
            if retry_history is None and isinstance(case.get("retry_history"), list):
                retry_history = case.get("retry_history")
    status = str(summary.get("status") or "").lower()
    passed = status in {"passed", "pass", "ok"}
    attribution = None if passed else (
        case_failure
        or _failure_from_payload(summary)
        or (("oracle: " + ", ".join(oracle_failures)) if oracle_failures else None)
        or summary.get("failure_kind")
        or summary.get("status")
        or "acceptance_failed"
    )
    collapsed = collapse_observed_mcp_protocols(observed)
    case = first_case or {}
    capabilities = (
        case.get("acp_app_server_capabilities")
        or case.get("provider_capabilities")
        or (case.get("provider_run") or {}).get("capabilities")
        or summary.get("acp_app_server_capabilities")
    )
    return {
        "oracle_result": summary.get("gates") or summary.get("acceptance_levels") or summary.get("status"),
        "token_usage": summary.get("token_usage") or case.get("token_usage"),
        "final_status": "pass" if passed else "fail",
        "failure_attribution": attribution,
        "model": model,
        "mcp_protocol_version": collapsed["mcp_protocol_version"],
        "mcp_protocols_observed": collapsed["mcp_protocols_observed"],
        "mcp_protocol_note": collapsed["mcp_protocol_note"],
        "validation_commands": case.get("validation_commands") or case.get("validation_command") or summary.get("validation_commands"),
        "validation_results": case.get("validation_results") or summary.get("validation_results"),
        "scope_result": case.get("scope_result") or case.get("scope_requests") or summary.get("scope_result"),
        "git_evidence": case.get("git_evidence") or case.get("runtime_bin_integrity") or summary.get("git_evidence"),
        "human_gate_evidence": case.get("human_gate_evidence") or case.get("elicitations") or summary.get("human_gate_evidence"),
        "execution_profile": case.get("execution_profile") or summary.get("execution_profile"),
        "harness_feature_state": case.get("harness_feature_state") or summary.get("harness_feature_state"),
        "acp_app_server_capabilities": capabilities,
        "retry_history": retry_history if retry_history is not None else case.get("retry_history"),
        "summary": {"status": summary.get("status"), "dynosai_version": summary.get("dynosai_version")},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MATRIX_1.0 probe and one-shot live trials")
    parser.add_argument("--live", action="store_true", help="Run one new live trial per selected cell")
    parser.add_argument("--cells", help="Comma-separated provider.mode list")
    parser.add_argument("--workspace", default=None, help="Override workspace. Default: <TEMP>/dynosai-matrix-1.0/")
    parser.add_argument("--matrix", default=str(ROOT / "docs" / "validation" / "matrix-1.0.json"))
    parser.add_argument(
        "--expected-subject-sha256",
        default=None,
        help="Required for --live. Abort before launching a provider if certification_subject_sha256 differs.",
    )
    args = parser.parse_args(argv)

    matrix_path = Path(args.matrix)
    matrix = load_live_matrix(matrix_path if matrix_path.is_file() else None)
    env = probe_environment(ROOT)
    matrix["environment"] = env
    matrix["copied_from_historical"] = False
    if not args.live:
        save_live_matrix(matrix, matrix_path)
        print(json.dumps({"mode": "probe", "environment": env, "cells": {f"{p}.{m}": "not_run" for p, m in CELL_KEYS}}, indent=2, default=str))
        return 0

    expected = str(args.expected_subject_sha256 or "").strip()
    if not expected:
        print(
            "Certification live runs require --expected-subject-sha256.\nProvider execution was not started.",
            file=sys.stderr,
        )
        return 2

    cells_specified = args.cells is not None
    selected_cells = _select_cells(args.cells)
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else default_matrix_workspace(ROOT)
    for provider, mode in selected_cells:
        try:
            env = guard_live_certification(ROOT, expected_subject_sha256=expected)
        except CertificationAborted as exc:
            print(str(exc), file=sys.stderr)
            print(str(exc))
            return 2
        matrix["environment"] = {
            key: value for key, value in env.items()
            if key not in {"certification_dirty_paths", "unexpected_dirty_paths"}
        }
        cell = next(item for item in matrix["cells"] if item["provider"] == provider and item["mode"] == mode)
        attempt = len(cell.get("trials") or []) + 1
        trial = empty_trial(provider=provider, mode=mode, attempt=attempt, environment=env)
        trial["started_at"] = _now()
        trial["execution_profile"] = "unknown"
        trial["harness_feature_state"] = "unknown"
        trial["certification_dirty_paths"] = list(env.get("certification_dirty_paths") or [])
        trial["unexpected_dirty_paths"] = list(env.get("unexpected_dirty_paths") or [])
        try:
            live = _run_live_cell(provider, mode, workspace, attempt)
        except Exception as exc:
            trial["finished_at"] = _now()
            trial["final_status"] = "fail"
            trial["failure_attribution"] = f"{type(exc).__name__}: {exc}"
            append_trial(matrix, provider, mode, trial)
            save_live_matrix(matrix, matrix_path)
            print(json.dumps({"cell": f"{provider}.{mode}", "attempt": attempt, "status": "fail", "error": trial["failure_attribution"]}))
            continue
        summary = _summarize_bundle(live.get("output"))
        trial.update({
            "started_at": live.get("started_at"),
            "finished_at": live.get("finished_at"),
            "scenario": live.get("scenario"),
            "final_status": summary.get("final_status") or ("fail" if live.get("exit_code") else "run"),
            "failure_attribution": summary.get("failure_attribution"),
            "oracle_result": summary.get("oracle_result"),
            "token_usage": summary.get("token_usage"),
            "model": summary.get("model") or trial.get("model"),
            "estimated_cost": ((summary.get("token_usage") or {}).get("totals") or {}).get("estimated_list_price_usd"),
            "mcp_protocol_version": summary.get("mcp_protocol_version"),
            "mcp_protocols_observed": summary.get("mcp_protocols_observed") or [],
            "mcp_protocol_note": summary.get("mcp_protocol_note"),
            "validation_commands": summary.get("validation_commands"),
            "validation_results": summary.get("validation_results"),
            "scope_result": summary.get("scope_result"),
            "git_evidence": summary.get("git_evidence"),
            "human_gate_evidence": summary.get("human_gate_evidence"),
            "execution_profile": summary.get("execution_profile") or trial.get("execution_profile"),
            "harness_feature_state": summary.get("harness_feature_state") or trial.get("harness_feature_state"),
            "acp_app_server_capabilities": summary.get("acp_app_server_capabilities"),
            "retry_history": summary.get("retry_history") if summary.get("retry_history") is not None else [],
            "artifact_paths": [path for path in (live.get("output"), live.get("log_dir")) if path],
            "artifact_hashes": {},
        })
        if live.get("exit_code") not in (0, None) and trial["final_status"] == "pass":
            trial["final_status"] = "fail"
            trial["failure_attribution"] = trial.get("failure_attribution") or f"exit_code:{live.get('exit_code')}"
        from dynosai_flow.certification_matrix import hash_file
        trial["artifact_hashes"] = {path: hash_file(path) for path in trial["artifact_paths"] if Path(path).is_file()}
        append_trial(matrix, provider, mode, trial)
        save_live_matrix(matrix, matrix_path)
        print(json.dumps({"cell": f"{provider}.{mode}", "attempt": attempt, "status": trial["final_status"], "attribution": trial.get("failure_attribution")}, indent=2))
    return live_exit_code(matrix, selected_cells, cells_specified=cells_specified)


if __name__ == "__main__":
    raise SystemExit(main())
