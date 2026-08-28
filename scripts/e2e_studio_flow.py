# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""End-to-end Studio flow harness with a real coding provider.

Emulates exactly what the Studio UI does, in process, against the real provider
binary installed on this machine:

1. Create a fresh project (same code path as the Studio "Nuevo proyecto" dialog).
2. Initialize DynosAI (same as the onboarding screen).
3. Enable auto-approve (same as project settings).
4. Start the Fibonacci tutorial change (same as "Nuevo cambio").
5. Poll execution, resolve any pending review the way the Approvals screen does,
   and press "Continue/Retry agent" (``/api/execution/start``) whenever the
   workflow stalls in an agent-owned state.
6. Verify the change reached ``done``: files exist on the merged main branch and
   the project test command passes.

Usage:

    python scripts/e2e_studio_flow.py --provider codex
    python scripts/e2e_studio_flow.py --provider cursor --keep
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from dynosai_flow.app_server import StudioAPI  # noqa: E402

DESCRIPTION = (
    "Crea una pequeña librería Python de Fibonacci. "
    "Añade una función fibonacci(n) que devuelva los primeros n números de Fibonacci. "
    "Para n = 0 devuelve una lista vacía. Rechaza valores negativos con ValueError. "
    "Usa una implementación iterativa, añade tests con pytest para 0, 1, 5 y entrada "
    "negativa, y añade un ejemplo corto de uso en el README."
)

AGENT_STATES = {"discovery", "plan_review", "ready", "implementing", "validating"}


def log(event: str, **data) -> None:
    print(f"E2E|{time.strftime('%H:%M:%S')}|{event}|{json.dumps(data, ensure_ascii=False, default=str)[:400]}", flush=True)


def fail(api: StudioAPI, work_id: str, reason: str) -> int:
    state = ""
    try:
        items = api.get("/api/work")[1].get("items") or []
        state = next((str(w.get("state") or "") for w in items if str(w.get("id")) == work_id), "")
    except Exception:
        pass
    tail = ""
    try:
        for item in (api.get("/api/execution", {"work_id": [work_id]})[1].get("items") or []):
            tail = str(item.get("stderr_tail") or item.get("stdout_tail") or "")[-1500:]
            if tail:
                break
    except Exception:
        pass
    log("FAIL", reason=reason, work_state=state, diagnostic_tail=tail)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="codex", choices=["codex", "cursor"])
    parser.add_argument("--parent", default=str(Path(tempfile.gettempdir()) / "dynosai-e2e"))
    parser.add_argument("--name", default=None, help="Project folder name (default: generated)")
    parser.add_argument("--description", default=DESCRIPTION)
    parser.add_argument("--budget-minutes", type=float, default=45.0)
    parser.add_argument("--keep", action="store_true", help="Keep the project folder (default keeps it too)")
    args = parser.parse_args()

    parent = Path(args.parent)
    parent.mkdir(parents=True, exist_ok=True)
    name = args.name or f"e2e fib {args.provider} {time.strftime('%H%M%S')}"

    api = StudioAPI()
    code, created = api.post("/api/projects/create", {"parent": str(parent), "name": name, "template": "python"})
    if code != 200:
        log("FAIL", step="projects/create", response=created)
        return 1
    root = Path(created["root"])
    log("project_created", root=str(root))

    code, init = api.post("/api/project/initialize", {
        "agent": args.provider, "allow_git_init": True, "language": "python",
        "test_command": "python -m pytest",
    })
    if code != 200:
        log("FAIL", step="project/initialize", response=init)
        return 1
    log("initialized", response=init)

    code, settings = api.post("/api/project-settings", {"auto_approve": True})
    if code != 200:
        log("FAIL", step="project-settings", response=settings)
        return 1
    log("auto_approve_enabled")

    code, work = api.post("/api/work/start", {"description": args.description, "provider": args.provider})
    if code != 200:
        log("FAIL", step="work/start", response=work)
        return 1
    work_id = str(work["id"])
    log("work_started", work_id=work_id, execution=work.get("execution", {}).get("status"))

    deadline = time.monotonic() + args.budget_minutes * 60
    last_state = ""
    stalls: dict[str, int] = {}
    while time.monotonic() < deadline:
        time.sleep(5)
        executions = api.get("/api/execution", {"work_id": [work_id]})[1].get("items") or []
        running = any(str(item.get("status")) == "running" for item in executions)
        items = api.get("/api/work")[1].get("items") or []
        current = next((w for w in items if str(w.get("id")) == work_id), {})
        state = str(current.get("state") or "")
        if state != last_state:
            log("state", state=state)
            last_state = state
        if state == "done":
            break
        # Emulate the Approvals screen: resolve anything pending as a human would.
        reviews = api.get("/api/reviews")[1].get("items") or []
        pending = [r for r in reviews if str(r.get("work_id") or "") == work_id]
        for review in pending:
            rid = str(review.get("id") or "")
            log("review_resolve", id=rid, gate=review.get("gate"), kind=review.get("kind"))
            try:
                api.post("/api/interaction/resolve", {"interaction_id": rid, "action": "accept", "content": {"decision": "approve", "source": "e2e_user"}})
            except Exception as exc:
                # Auto-approval may win the race; the UI shows the same behaviour
                # when a gate disappears before the user clicks.
                log("review_resolve_race", id=rid, error=str(exc))
        if running or pending:
            continue
        if state in AGENT_STATES or state in {"spec_review", "code_review", "ready_to_merge", "inbox"}:
            stalls[state] = stalls.get(state, 0) + 1
            if stalls[state] > 12:
                return fail(api, work_id, f"workflow stuck in {state} after repeated continues")
            code, resp = api.post("/api/execution/start", {"work_id": work_id})
            log("execution_start", state=state, code=code, status=resp.get("status"), error=resp.get("error"))
            if code == 409 and resp.get("error") == "human_gate":
                continue
            if code not in {200, 409}:
                return fail(api, work_id, f"execution/start returned {code}: {resp}")
    else:
        return fail(api, work_id, "budget exhausted")

    # --- final verification -------------------------------------------------
    problems: list[str] = []
    fib = root / "fibonacci.py"
    tests = root / "tests" / "test_fibonacci.py"
    if not fib.exists():
        problems.append("fibonacci.py missing on merged main branch")
    if not tests.exists():
        problems.append("tests/test_fibonacci.py missing on merged main branch")
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=root, text=True, capture_output=True).stdout.strip()
    log("final_branch", branch=branch)
    if not problems:
        run = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, text=True, capture_output=True, timeout=300)
        log("pytest", returncode=run.returncode, tail=(run.stdout + run.stderr)[-400:])
        if run.returncode != 0:
            problems.append("pytest failed on merged result")
        try:
            sys.path.insert(0, str(root))
            import importlib

            fibonacci = importlib.import_module("fibonacci")
            importlib.reload(fibonacci)
            assert fibonacci.fibonacci(0) == []
            assert fibonacci.fibonacci(5) == [0, 1, 1, 2, 3]
            try:
                fibonacci.fibonacci(-1)
            except ValueError:
                pass
            else:
                problems.append("fibonacci(-1) did not raise ValueError")
        except Exception as exc:
            problems.append(f"direct library check failed: {exc}")
        finally:
            sys.path.remove(str(root))
    status = "PASS" if not problems else "FAIL"
    log("RESULT", status=status, problems=problems, project=str(root))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
