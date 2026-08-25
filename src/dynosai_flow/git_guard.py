# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Guardrails that restrict unmanaged Git operations inside managed agent sessions."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from .policy import GitCommandPolicy


class GitGuard:
    """Agent-only Git wrapper with full-command validation.

    DynosAI itself calls the real Git executable. Child agent sessions receive the
    guarded PATH. The wrapper does not trust provider prompts or permission files.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.guard_dir = self.root / ".dynosai" / "runtime" / "git-guard"

    def install(self) -> dict[str, Any]:
        real_git = shutil.which("git")
        if not real_git:
            raise RuntimeError("git executable not found")
        self.guard_dir.mkdir(parents=True, exist_ok=True)
        meta = self.guard_dir / "guard.json"
        meta.write_text(json.dumps({"real_git": real_git, "policy": "exact-read-only-v2"}, indent=2), encoding="utf-8")
        posix = self.guard_dir / "git"
        policy_module = "dynosai_flow.policy"
        posix.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, subprocess, sys\n"
            f"from {policy_module} import GitCommandPolicy\n"
            f"META={str(meta)!r}\n"
            "cfg=json.load(open(META, encoding='utf-8'))\n"
            "args=sys.argv[1:]\n"
            "if os.environ.get('DYNOSAI_GIT_BYPASS') != '1':\n"
            "    ok, reason = GitCommandPolicy.allowed(args)\n"
            "    if not ok:\n"
            "        sys.stderr.write('DynosAI Git Guard: blocked: git %s (%s)\\n' % (' '.join(args), reason))\n"
            "        sys.exit(73)\n"
            "raise SystemExit(subprocess.call([cfg['real_git'], *args]))\n",
            encoding="utf-8",
        )
        try:
            posix.chmod(0o755)
        except OSError:
            pass
        cmd = self.guard_dir / "git.cmd"
        python_exe = os.environ.get("PYTHON", "python")
        cmd.write_text("@echo off\r\n" f'"{python_exe}" "{posix}" %*\r\n', encoding="utf-8")
        return {"directory": str(self.guard_dir), "real_git": real_git, "policy": "exact-read-only-v2"}

    def environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        self.install()
        env = dict(os.environ)
        env["PATH"] = str(self.guard_dir) + os.pathsep + env.get("PATH", "")
        # The wrapper imports DynosAI's policy module. Make that import robust both
        # from an installed wheel and from a source checkout used for development.
        package_parent = str(Path(__file__).resolve().parents[1])
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = package_parent + (os.pathsep + current_pythonpath if current_pythonpath else "")
        env["DYNOSAI_MANAGED_AGENT"] = "1"
        env.pop("DYNOSAI_GIT_BYPASS", None)
        if extra:
            env.update(extra)
        return env

    @staticmethod
    def check(args: list[str]) -> tuple[bool, str]:
        return GitCommandPolicy.allowed(args)
