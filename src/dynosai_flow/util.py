# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Small shared serialization, time, filesystem, and identifier utilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slugify(value: str, max_length: int = 48) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9áéíóúñü]+", "-", value)
    value = value.strip("-") or "work"
    return value[:max_length].rstrip("-")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def run_command(
    args: list[str],
    cwd: Path,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def executable_command(executable: str | Path, *args: str) -> list[str]:
    """Build a cross-platform command for a provider executable or test shim.

    Real provider binaries are returned unchanged. On Windows, extensionless
    Python/shebang shims are not directly executable by ``CreateProcess`` even
    when they are marked executable, so development/acceptance shims are routed
    through the interpreter declared by their shebang. This keeps the production
    path identical for real ``.exe``/``.cmd`` providers while making provider
    overrides and acceptance fixtures portable.
    """
    target = str(executable)
    if os.name != "nt":
        return [target, *args]

    path = Path(target)
    if not path.is_file():
        return [target, *args]

    try:
        first_line = path.open("r", encoding="utf-8", errors="replace").readline().strip().lower()
    except OSError:
        first_line = ""

    if path.suffix.lower() == ".py" or (first_line.startswith("#!") and "python" in first_line):
        return [sys.executable, target, *args]

    if first_line.startswith("#!") and any(shell in first_line for shell in ("/sh", "/bash", "/zsh")):
        shell = shutil.which("bash") or shutil.which("sh")
        if shell:
            return [shell, target, *args]

    return [target, *args]


STOPWORDS = {
    "de", "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o",
    "que", "se", "en", "con", "por", "para", "del", "al", "a", "es", "debe",
    "the", "a", "an", "and", "or", "to", "of", "in", "for", "is", "must",
}


def tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-zA-Z0-9_áéíóúñü]{2,}", text.lower())
    result: set[str] = set()
    for part in parts:
        if part in STOPWORDS:
            continue
        # Tiny language-neutral stemming useful for local ranking.
        for suffix in ("mente", "ciones", "ción", "ando", "iendo", "ados", "adas", "es", "s"):
            if part.endswith(suffix) and len(part) - len(suffix) >= 4:
                part = part[: -len(suffix)]
                break
        result.add(part)
    return result


def overlap_score(query: str, text: str) -> float:
    left, right = tokens(query), tokens(text)
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    return intersection / max(1, len(left))


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def iter_source_files(root: Path) -> Iterable[Path]:
    ignored = {".git", ".dynosai", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}
    allowed = {".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".java", ".cs", ".go"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if any(part in ignored for part in path.parts):
            continue
        yield path
