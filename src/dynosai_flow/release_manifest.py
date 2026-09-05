# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Deterministic releasable-file manifest for source ZIPs and certification fingerprints.

Git is preferred when a `.git` directory exists: tracked files plus untracked,
non-ignored files (`git ls-files --cached --others --exclude-standard`). A
conservative deny overlay still drops secrets and runtime trees even if they
were tracked by mistake. Trees without Git use an explicit deny-list walk.

Release archive identity (`source_tree_sha256`) fingerprints every releasable
file, including MATRIX evidence. Certification subject identity excludes only
mutable certification outputs such as `docs/validation/matrix-1.0.json`.
"""

from __future__ import annotations

import hashlib
import subprocess
import zipfile
from pathlib import Path
from typing import Iterable

DENY_DIR_NAMES = {
    ".git",
    ".dynosai",
    ".pytest_cache",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "out",
    "dist",
    "build",
    "acceptance-logs",
    "__pycache__",
}
DENY_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}
# Mutable certification evidence. Still packed in the source ZIP; excluded only
# from certification_subject_sha256 so live MATRIX writes do not retarget the candidate.
CERTIFICATION_SUBJECT_EXCLUDES = frozenset({
    "docs/validation/matrix-1.0.json",
})


def _posix(rel: str | Path) -> str:
    text = str(rel).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def is_release_denied(relative: str | Path) -> bool:
    """Return True when a repo-relative path must never enter a release archive."""
    posix = _posix(relative)
    if not posix or posix == ".":
        return True
    parts = tuple(part for part in posix.split("/") if part)
    name = parts[-1] if parts else ""
    if any(part in DENY_DIR_NAMES for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    suffix = Path(name).suffix.lower()
    if suffix in DENY_SUFFIXES:
        return True
    return False


def _git_listed_files(root: Path) -> list[str] | None:
    git_dir = root / ".git"
    if not git_dir.exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    files: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        files.append(_posix(raw.decode("utf-8", errors="surrogateescape")))
    return files


def _walk_fallback_files(root: Path) -> list[str]:
    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = _posix(path.relative_to(root))
        if is_release_denied(rel):
            continue
        files.append(rel)
    return files


def list_release_files(root: str | Path) -> list[str]:
    """Sorted posix-relative paths that may enter a source archive or fingerprint."""
    target = Path(root).resolve()
    listed = _git_listed_files(target)
    if listed is None:
        listed = _walk_fallback_files(target)
    files = sorted({item for item in listed if item and not is_release_denied(item)})
    return files


def is_certification_subject_excluded(relative: str | Path) -> bool:
    """Return True for mutable certification evidence that is not the candidate."""
    return _posix(relative) in CERTIFICATION_SUBJECT_EXCLUDES


def list_certification_subject_files(root: str | Path) -> list[str]:
    """Releasable files that define the certified candidate, excluding MATRIX output."""
    return [item for item in list_release_files(root) if not is_certification_subject_excluded(item)]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint_files(root: Path, files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in files:
        path = root / Path(*rel.split("/"))
        payload_hash = file_sha256(path) if path.is_file() else hashlib.sha256().hexdigest()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def source_tree_identity(root: str | Path) -> dict[str, str | int]:
    """Fingerprint the full releasable tree, including MATRIX evidence."""
    target = Path(root).resolve()
    files = list_release_files(target)
    return {
        "source_file_count": len(files),
        "source_tree_sha256": _fingerprint_files(target, files),
    }


def certification_subject_identity(root: str | Path) -> dict[str, str | int]:
    """Fingerprint the candidate under certification.

    Same releasable universe as the source ZIP, minus mutable MATRIX output.
    Changing product/harness/config changes this hash. Appending a MATRIX trial
    does not.
    """
    target = Path(root).resolve()
    files = list_certification_subject_files(target)
    return {
        "certification_subject_file_count": len(files),
        "certification_subject_sha256": _fingerprint_files(target, files),
    }


def write_source_zip(
    root: str | Path,
    target: str | Path,
    *,
    prefix: str,
    files: Iterable[str] | None = None,
) -> list[str]:
    """Write a source ZIP from the shared release manifest. Returns archive member names."""
    source_root = Path(root).resolve()
    archive_path = Path(target)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    members = list(files) if files is not None else list_release_files(source_root)
    written: list[str] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in members:
            if is_release_denied(rel):
                continue
            path = source_root / Path(*rel.split("/"))
            if not path.is_file():
                continue
            arcname = _posix(Path(prefix) / rel)
            archive.write(path, arcname)
            written.append(arcname)
    return written
