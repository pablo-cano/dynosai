#!/usr/bin/env python3
"""Single executable source of truth for the DynosAI stable release gate.

CI, scripts/build_release.py and local release preparation must invoke this
file rather than duplicating compileall / repository / Studio / pytest steps.
Historical aggregate suites stay excluded unless DYNOSAI_HISTORICAL_TESTS=1.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def gate_steps(*, skip_tests: bool = False) -> list[tuple[str, list[str]]]:
    python = sys.executable
    steps = [
        ("compileall", [python, "-m", "compileall", "-q", "src/dynosai_flow"]),
        ("check_repository", [python, "scripts/check_repository.py"]),
        ("check_studio_sync", [python, "scripts/check_studio_sync.py"]),
    ]
    if not skip_tests:
        steps.append(("pytest", [python, "-m", "pytest"]))
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DynosAI stable release gate")
    parser.add_argument("--skip-tests", action="store_true", help="Skip pytest; still run compile and sync checks")
    args = parser.parse_args(argv)
    for name, command in gate_steps(skip_tests=args.skip_tests):
        print("+", " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=ROOT)
        if completed.returncode != 0:
            print(f"release gate failed at {name} (exit {completed.returncode})", file=sys.stderr)
            return completed.returncode
    print("DynosAI release gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
