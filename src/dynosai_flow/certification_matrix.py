# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Versioned MATRIX_1.0 live-certification evidence.

Four provider-aware cells (Codex/Cursor × greenfield/brownfield) stay
``not_run`` until a real provider run writes evidence. Historical 0.13
Quality 100 results are not 1.0 proof and must not be copied forward.
Schema stays v6: this file is documentation/runtime evidence, not a new
authority table.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MATRIX_SCHEMA = "MATRIX_1.0"
CELL_STATUSES = ("not_run", "run", "pass", "fail")
CELL_KEYS = (
    ("codex", "greenfield"),
    ("codex", "brownfield"),
    ("cursor", "greenfield"),
    ("cursor", "brownfield"),
)
HISTORICAL_MATRIX_PATH = "docs/validation/final-matrix-0.13.0.json"
LIVE_MATRIX_PATH = "docs/validation/matrix-1.0.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_live_matrix() -> dict[str, Any]:
    """Honest 1.0 placeholder. Every cell is not_run; no copied scores."""
    cells = [
        {
            "provider": provider,
            "mode": mode,
            "status": "not_run",
            "quality_score": None,
            "oracle_passed": None,
            "evidence": None,
        }
        for provider, mode in CELL_KEYS
    ]
    return {
        "schema": MATRIX_SCHEMA,
        "schema_version": 1,
        "release_line": "1.0",
        "copied_from_historical": False,
        "all_passed": False,
        "historical_baseline": {
            "release": "0.13.0",
            "path": HISTORICAL_MATRIX_PATH,
            "note": "Historical core evidence. Not 1.0 live certification.",
        },
        "cells": cells,
    }


def cell_status_map(matrix: dict[str, Any]) -> dict[str, str]:
    return {f"{cell['provider']}.{cell['mode']}": str(cell["status"]) for cell in matrix.get("cells") or []}


def summarize_live_matrix(matrix: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = matrix or load_live_matrix()
    return {
        "schema": payload["schema"],
        "schema_version": payload["schema_version"],
        "copied_from_historical": bool(payload.get("copied_from_historical")),
        "all_passed": bool(payload.get("all_passed")),
        "cells": cell_status_map(payload),
        "historical_baseline": (payload.get("historical_baseline") or {}).get("path") or HISTORICAL_MATRIX_PATH,
    }


def validate_live_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    """Reject copied 0.13 scores and inconsistent global pass flags."""
    if matrix.get("schema") != MATRIX_SCHEMA:
        raise ValueError("live matrix schema must be MATRIX_1.0")
    if int(matrix.get("schema_version") or 0) != 1:
        raise ValueError("MATRIX_1.0 schema_version must be 1")
    if matrix.get("copied_from_historical"):
        raise ValueError("1.0 live matrix must not be copied from historical 0.13 evidence")
    cells = list(matrix.get("cells") or [])
    keys = {(str(cell.get("provider")), str(cell.get("mode"))) for cell in cells}
    if keys != set(CELL_KEYS):
        raise ValueError("MATRIX_1.0 requires Codex/Cursor × greenfield/brownfield cells")
    for cell in cells:
        status = str(cell.get("status") or "")
        if status not in CELL_STATUSES:
            raise ValueError(f"invalid MATRIX_1.0 cell status: {status}")
        if cell.get("evidence") == HISTORICAL_MATRIX_PATH:
            raise ValueError("cannot attach 0.13 historical evidence as a 1.0 live cell")
        if status == "pass" and not cell.get("evidence"):
            raise ValueError("a MATRIX_1.0 pass requires real provider evidence")
        if status != "pass" and cell.get("quality_score") == 100:
            raise ValueError("Quality 100 cannot appear on a 1.0 cell that has not passed")
    passed = all(str(cell.get("status")) == "pass" for cell in cells)
    if bool(matrix.get("all_passed")) != passed:
        raise ValueError("all_passed must match the four provider-aware cells")
    return matrix


def load_live_matrix(path: str | Path | None = None) -> dict[str, Any]:
    """Load checked-in MATRIX_1.0 evidence, or the in-code not_run placeholder."""
    target = Path(path) if path else _repo_root() / LIVE_MATRIX_PATH
    if target.is_file():
        payload = json.loads(target.read_text(encoding="utf-8"))
        return validate_live_matrix(payload)
    return validate_live_matrix(default_live_matrix())
