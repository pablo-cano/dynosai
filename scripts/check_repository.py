#!/usr/bin/env python3
"""Run fast, dependency-free repository quality checks for DynosAI."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "dynosai_flow"
REQUIRED = [
    "README.md",
    "GETTING_STARTED.md",
    "CHANGELOG.md",
    "LICENSE",
    "AUTHORS.md",
    "ROADMAP.md",
    "SECURITY.md",
    "pyproject.toml",
    "docs/ARCHITECTURE.md",
    "docs/QUALITY_AND_VALIDATION.md",
    "docs/reference/MODULES.md",
]


def fail(message: str) -> None:
    print(f"ERROR: {message}")
    raise SystemExit(1)


def main() -> int:
    for relative in REQUIRED:
        if not (ROOT / relative).exists():
            fail(f"missing required public file: {relative}")

    version_text = (SRC / "version.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)', version_text)
    if not match:
        fail("could not read package version")
    version = match.group(1)

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if f'version = "{version}"' not in pyproject:
        fail("pyproject version does not match dynosai_flow.version")

    source_files = sorted(SRC.glob("*.py"))
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        if not text.startswith("# SPDX-License-Identifier: MIT"):
            fail(f"missing SPDX header: {path.relative_to(ROOT)}")
        if not any("Copyright (c) 2026 Pablo Cano" in line for line in text.splitlines()[:4]):
            fail(f"missing author copyright: {path.relative_to(ROOT)}")
        tree = ast.parse(text, filename=str(path))
        if not ast.get_docstring(tree):
            fail(f"missing module docstring: {path.relative_to(ROOT)}")

    forbidden = [
        ROOT / "src" / "dynosai_flow.egg-info",
        ROOT / "dist",
        ROOT / "build",
    ]
    for path in forbidden:
        if path.exists():
            fail(f"generated artifact must not be committed: {path.relative_to(ROOT)}")

    print(f"DynosAI repository checks: PASS ({len(source_files)} modules, version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
