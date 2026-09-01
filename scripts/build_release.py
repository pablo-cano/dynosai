#!/usr/bin/env python3
"""Build a reproducible DynosAI source ZIP, wheel, manifest, and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXCLUDED_DIRS = {".git", ".pytest_cache", "__pycache__", ".venv", "venv", "node_modules", ".next", "out", "dist", "build", "dynosai_flow.egg-info"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tsbuildinfo"}


def run(*args: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(args))
    subprocess.run(args, cwd=cwd, check=True)


def version() -> str:
    namespace: dict[str, str] = {}
    exec((ROOT / "src" / "dynosai_flow" / "version.py").read_text(encoding="utf-8"), namespace)
    return str(namespace["__version__"])


def source_zip(target: Path) -> None:
    prefix = f"dynosai-{version()}"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_dir() or any(part in EXCLUDED_DIRS for part in rel.parts) or path.suffix in EXCLUDED_SUFFIXES:
                continue
            archive.write(path, Path(prefix) / rel)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-web", action="store_true")
    args = ap.parse_args()

    run(sys.executable, "-m", "compileall", "-q", "src/dynosai_flow")
    run(sys.executable, "scripts/check_repository.py")
    run(sys.executable, "scripts/check_studio_sync.py")

    if not args.skip_tests:
        run(sys.executable, "-m", "pytest")

    if not args.skip_web:
        for command in (("npm", "run", "check"), ("npm", "run", "typecheck"), ("npm", "run", "lint"), ("npm", "run", "build")):
            run(*command, cwd=ROOT / "apps" / "web")

    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir(parents=True, exist_ok=True)
    try:
        run(sys.executable, "-m", "build", "--wheel", "--outdir", str(DIST))
    except subprocess.CalledProcessError:
        print("python-build unavailable/failed; falling back to pip wheel without build isolation")
        run(sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation", "-w", str(DIST))

    src = DIST / f"dynosai-{version()}-source.zip"
    source_zip(src)
    artifacts = sorted(path for path in DIST.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt")
    manifest = {
        "version": version(),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "stable_test_policy": {
            "command": "python -m pytest",
            "excluded_historical_suites": [
                "tests/test_07.py",
                "tests/test_brownfield06.py",
                "tests/test_hardening.py",
                "tests/test_runtime05.py",
            ],
            "historical_override": "DYNOSAI_HISTORICAL_TESTS=1",
        },
        "artifacts": [{"name": p.name, "size": p.stat().st_size, "sha256": sha256(p)} for p in artifacts],
    }
    manifest_path = DIST / f"dynosai-{version()}-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    artifacts.append(manifest_path)
    (DIST / "SHA256SUMS.txt").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in artifacts), encoding="utf-8")
    print(f"Release {version()} built in {DIST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
