#!/usr/bin/env python3
"""Verify that developer-facing and packaged Studio assets stay byte-identical."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("index.html", "styles.css", "app.js", "i18n.js", "theme-init.js", "icon.svg")
for name in ASSETS:
    app = ROOT / "apps" / "studio" / name
    package = ROOT / "src" / "dynosai_flow" / "studio_assets" / name
    if app.read_bytes() != package.read_bytes():
        raise SystemExit(f"Studio asset drift: {name}")
print("DynosAI Studio asset sync: PASS")
