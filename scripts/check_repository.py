#!/usr/bin/env python3
"""Run fast, dependency-free repository quality checks for DynosAI."""

from __future__ import annotations

import ast
import json
import re
import subprocess
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
    "CONTRIBUTING.md",
    "pyproject.toml",
    "docs/ARCHITECTURE.md",
    "docs/COMPATIBILITY.md",
    "docs/THREAT_MODEL.md",
    "docs/QUALITY_AND_VALIDATION.md",
    "docs/validation/matrix-1.0.json",
    "docs/STUDIO.md",
    "apps/studio/index.html",
    "apps/studio/app.js",
    "apps/studio/i18n.js",
    "apps/studio/styles.css",
    "apps/studio/theme-init.js",
    "apps/studio/icon.svg",
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
    web_package = json.loads((ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    if str(web_package.get("version")) != version:
        fail("apps/web package version does not match dynosai_flow.version")
    if '"dynosai_flow.studio_assets" = ["*.html", "*.css", "*.js", "*.svg"]' not in pyproject:
        fail("pyproject does not package Local Studio static assets")
    for asset in ("index.html", "app.js", "i18n.js", "styles.css", "theme-init.js", "icon.svg"):
        public_asset = ROOT / "apps" / "studio" / asset
        packaged_asset = SRC / "studio_assets" / asset
        if public_asset.read_bytes() != packaged_asset.read_bytes():
            fail(f"Local Studio source/package drift: {asset}")

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

    architecture_deny = {
        "engine.py": {"app_server", "cli"},
        "application.py": {"app_server"},
        "db.py": {"app_server", "cli", "mcp"},
        "execution_runtime.py": {"app_server", "cli", "mcp"},
        "validation_integrity.py": {"app_server", "cli"},
        "context_handles.py": {"app_server", "cli", "mcp"},
        "harness_contracts.py": {"app_server", "cli", "mcp", "engine"},
        "eval_registry.py": {"app_server", "cli", "mcp"},
        "eval_intelligence.py": {"app_server", "cli", "mcp", "engine"},
        "execution_profiles.py": {"app_server", "cli", "mcp", "engine"},
        "capability_manifests.py": {"app_server", "cli", "mcp", "engine"},
        "team_scheduler.py": {"app_server", "cli", "mcp", "engine"},
    }
    for filename, denied in architecture_deny.items():
        path = SRC / filename
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level and node.module:
                    imported.add(node.module.split(".")[0])
                elif node.module and node.module.startswith("dynosai_flow"):
                    parts = node.module.split(".")
                    if len(parts) > 1:
                        imported.add(parts[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("dynosai_flow."):
                        imported.add(alias.name.split(".")[1])
        blocked = imported & denied
        if blocked:
            fail(f"architecture import violation in {filename}: {', '.join(sorted(blocked))}")

    studio_js = (ROOT / "apps" / "studio" / "app.js").read_text(encoding="utf-8")
    if "knowledge.db" in studio_js or re.search(r"\bSELECT\b", studio_js):
        fail("Studio must not query knowledge.db or embed SQL")

    forbidden_prefixes = (
        "src/dynosai_flow.egg-info/",
        "src/dynosai.egg-info/",
        "dist/",
        "build/",
    )

    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    for tracked_file in result.stdout.splitlines():
        normalized = tracked_file.replace("\\", "/")
        if normalized.startswith(forbidden_prefixes):
            fail(f"generated artifact must not be committed: {normalized}")

    print(f"DynosAI repository checks: PASS ({len(source_files)} modules, version {version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
