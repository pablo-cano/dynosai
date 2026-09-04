#!/usr/bin/env python3
"""Install a local DynosAI wheel after verifying SHA-256 checksums.

This path is for RC artifacts produced by scripts/build_release.py. It refuses
remote URLs, including pypi.org, because DynosAI is not published on PyPI.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sums(path: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition(" ")
        mapping[name.strip().lstrip("*").strip()] = digest.strip().lower()
    return mapping


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and install a local DynosAI wheel")
    parser.add_argument("--wheel", required=True, help="Path to a local .whl file")
    parser.add_argument("--sums", help="SHA256SUMS.txt next to the wheel by default")
    parser.add_argument("--python", default=sys.executable, help="Python used for pip install")
    parser.add_argument("--verify-only", action="store_true", help="Verify checksums without installing")
    args = parser.parse_args()

    wheel_arg = str(args.wheel).strip()
    lowered = wheel_arg.lower()
    if lowered.startswith(("http://", "https://", "pypi://")) or "pypi.org" in lowered:
        print("Refusing remote/index URLs including pypi.org; pass a local wheel path", file=sys.stderr)
        return 2

    wheel = Path(wheel_arg).expanduser().resolve()
    if not wheel.is_file():
        print(f"Wheel not found: {wheel}", file=sys.stderr)
        return 2
    sums = Path(args.sums).expanduser().resolve() if args.sums else wheel.parent / "SHA256SUMS.txt"
    if not sums.is_file():
        print(f"Checksum file required: {sums}", file=sys.stderr)
        return 2

    expected = parse_sums(sums).get(wheel.name)
    actual = sha256(wheel)
    if not expected:
        print(f"No SHA256 entry for {wheel.name} in {sums}", file=sys.stderr)
        return 2
    if expected != actual:
        print(f"SHA256 mismatch for {wheel.name}: expected {expected}, got {actual}", file=sys.stderr)
        return 2
    print(f"Verified SHA256 {actual}  {wheel.name}")
    if args.verify_only:
        return 0

    import subprocess

    subprocess.run([args.python, "-m", "pip", "install", str(wheel)], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
