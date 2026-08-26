#!/usr/bin/env python3
"""Verify that developer-facing and packaged Studio assets stay byte-identical."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("index.html", "styles.css", "app.js"):
    app = ROOT / "apps" / "studio" / name
    package = ROOT / "src" / "dynosai_flow" / "studio_assets" / name
    if app.read_bytes() != package.read_bytes():
        raise SystemExit(f"Studio asset drift: {name}")
print("DynosAI Studio asset sync: PASS")
