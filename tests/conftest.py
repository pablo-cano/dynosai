# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Stable-release pytest collection policy.

The supported release gate is ``python -m pytest``. Historical aggregate
suites remain in the repository for manual diagnostics and are excluded
unless ``DYNOSAI_HISTORICAL_TESTS`` is enabled.
"""

from __future__ import annotations

import os
from pathlib import Path

HISTORICAL_TEST_FILES = frozenset({
    "test_07.py",
    "test_brownfield06.py",
    "test_hardening.py",
    "test_runtime05.py",
})


def pytest_ignore_collect(collection_path, config):
    flag = (os.environ.get("DYNOSAI_HISTORICAL_TESTS") or "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return None
    name = getattr(collection_path, "name", None) or Path(str(collection_path)).name
    if name in HISTORICAL_TEST_FILES:
        return True
    return None
