#!/usr/bin/env python3
"""Run the no-model-turn Codex App Server MCP preflight.

Does not start turn/start, does not increment MATRIX attempts, and must not
consume provider model tokens.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dynosai_flow.codex_mcp_preflight import portable_codex_mcp_preflight, run_codex_mcp_preflight  # noqa: E402
from dynosai_flow.runtime_paths import default_matrix_workspace  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex App Server MCP preflight (no model turn)")
    parser.add_argument("--project", help="Existing project directory. Default: certification workspace.")
    parser.add_argument("--logs", help="Evidence directory for json/jsonl/stderr files.")
    args = parser.parse_args(argv)
    workspace = default_matrix_workspace(ROOT)
    project = Path(args.project).expanduser() if args.project else workspace / "codex-mcp-preflight" / "project"
    logs = Path(args.logs).expanduser() if args.logs else workspace / "codex-mcp-preflight-logs"
    report = run_codex_mcp_preflight(project, logs=logs)
    print(json.dumps(portable_codex_mcp_preflight(report), indent=2, default=str))
    if report.get("status") != "pass":
        print(f"CODEX MCP PREFLIGHT FAIL: {report.get('classification')}", file=sys.stderr)
        return 1
    print("CODEX MCP PREFLIGHT PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
