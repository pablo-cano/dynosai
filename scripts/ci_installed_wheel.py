#!/usr/bin/env python3
"""Create a fresh venv, install the built wheel, and run installed-package smoke.

Invoked by CI. Delegates checks to scripts/smoke_installed_package.py from a
temporary directory outside the repository with PYTHONPATH cleared.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
SMOKE = ROOT / "scripts" / "smoke_installed_package.py"
INSTALLER = ROOT / "scripts" / "install_local_wheel.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def main() -> int:
    wheels = sorted(DIST.glob("dynosai-*.whl")) or sorted(DIST.glob("dynosai_flow-*.whl"))
    if not wheels:
        print("No dynosai / dynosai_flow wheel in dist/; build it first", file=sys.stderr)
        return 2
    wheel = wheels[-1]
    parent = Path(tempfile.mkdtemp(prefix="dynosai-wheel-ci-"))
    venv = parent / "venv"
    smoke_cwd = parent / "cwd"
    smoke_cwd.mkdir()
    sums = parent / "SHA256SUMS.txt"
    sums.write_text(f"{sha256(wheel)}  {wheel.name}\n", encoding="utf-8")

    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv_python(venv)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run(
        [sys.executable, str(INSTALLER), "--wheel", str(wheel), "--sums", str(sums), "--python", str(python)],
        check=True,
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = ""
    result = subprocess.run(
        [str(python), str(SMOKE)],
        cwd=smoke_cwd,
        env=env,
    )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
