# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""User-scoped Studio project registry and simple greenfield project creation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudioProjectRegistry:
    """Persist recent projects outside governed repositories.

    Removing an entry never removes project files. Creating a project is an
    explicit user action and creates only a small starter folder; DynosAI
    initialization remains a separate guided step.
    """

    RECENT_LIMIT = 10

    def __init__(self, state_path: str | Path | None = None):
        default = Path.home() / ".dynosai" / "studio" / "projects.json"
        if state_path is None and not os.environ.get("DYNOSAI_STUDIO_PROJECTS_FILE") and os.environ.get("PYTEST_CURRENT_TEST"):
            default = Path(tempfile.gettempdir()) / f"dynosai-studio-tests-{os.getpid()}" / "projects.json"
        self.state_path = Path(state_path or os.environ.get("DYNOSAI_STUDIO_PROJECTS_FILE") or default).expanduser().resolve()
        self.data_dir = self.state_path.parent

    @staticmethod
    def _key(path: str | Path) -> str:
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            resolved = Path(path).expanduser().absolute()
        return os.path.normcase(str(resolved))

    @staticmethod
    def _internal_test_artifact(project: Path) -> bool:
        """Recognize stale Studio entries created by DynosAI's own test suite.

        Older 0.14.x tests could accidentally use the user's default Studio
        registry.  Those projects live below the OS temp directory and use the
        deterministic ``dynosai-140-*`` / ``dynosai-141-*`` prefixes.  They are
        safe to forget from recents; project files are never deleted here.
        """
        try:
            temp_root = Path(tempfile.gettempdir()).resolve()
            resolved = project.expanduser().resolve()
            resolved.relative_to(temp_root)
        except (OSError, ValueError):
            return False
        return bool(re.match(r"^dynosai-(?:140|141|14)[-_]", resolved.name, re.IGNORECASE))

    def _sanitized_projects(self, data: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        cleaned: list[dict[str, Any]] = []
        seen: set[str] = set()
        changed = False
        source_items = sorted(data.get("projects", []), key=lambda item: str(item.get("last_opened") or ""), reverse=True)
        for item in source_items:
            raw = str(item.get("path") or "").strip()
            if not raw:
                changed = True
                continue
            project = Path(raw).expanduser()
            try:
                project = project.resolve()
            except OSError:
                changed = True
                continue
            key = self._key(project)
            if key in seen or not project.exists() or not project.is_dir() or self._internal_test_artifact(project):
                changed = True
                continue
            seen.add(key)
            cleaned.append({
                "path": str(project),
                "name": str(item.get("name") or project.name or str(project)),
                "last_opened": item.get("last_opened"),
                "exists": True,
            })
        cleaned.sort(key=lambda item: str(item.get("last_opened") or ""), reverse=True)
        if len(cleaned) > self.RECENT_LIMIT:
            cleaned = cleaned[: self.RECENT_LIMIT]
            changed = True
        if cleaned != data.get("projects", []):
            changed = True
        return cleaned, changed

    @staticmethod
    def resolve_project(path: str | Path) -> Path:
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("project path is required")
        project = Path(raw).expanduser().resolve()
        if not project.exists():
            raise ValueError(f"project folder does not exist: {project}")
        if not project.is_dir():
            raise ValueError(f"project path is not a directory: {project}")
        return project

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("projects"), list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"version": 1, "projects": []}

    def _write(self, data: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_path)

    def touch(self, path: str | Path, *, display_name: str | None = None) -> dict[str, Any]:
        project = self.resolve_project(path)
        data = self._read()
        entries, _ = self._sanitized_projects(data)
        key = self._key(project)
        entries = [item for item in entries if self._key(item.get("path") or "") != key]
        entry = {
            "path": str(project),
            "name": str(display_name or project.name or str(project)),
            "last_opened": _now(),
            "exists": True,
        }
        entries.insert(0, entry)
        data["projects"] = entries[: self.RECENT_LIMIT]
        self._write(data)
        return entry

    def list(self, *, current: str | Path | None = None) -> list[dict[str, Any]]:
        data = self._read()
        entries, changed = self._sanitized_projects(data)
        if changed:
            data["projects"] = entries
            self._write(data)

        current_path = str(Path(current).expanduser().resolve()) if current is not None else None
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        if current_path:
            current_obj = Path(current_path)
            if current_obj.exists() and current_obj.is_dir():
                result.append({
                    "path": current_path,
                    "name": current_obj.name or current_path,
                    "last_opened": _now(),
                    "exists": True,
                    "current": True,
                })
                seen.add(self._key(current_path))
        for item in entries:
            raw = str(item.get("path") or "")
            key = self._key(raw)
            if key in seen:
                continue
            result.append({**item, "exists": True, "current": False})
            seen.add(key)
        return result[: self.RECENT_LIMIT]

    def remove(self, path: str | Path) -> bool:
        key = self._key(path)
        data = self._read()
        before = len(data.get("projects", []))
        data["projects"] = [item for item in data.get("projects", []) if self._key(item.get("path") or "") != key]
        if len(data["projects"]) != before:
            self._write(data)
            return True
        return False

    def create_project(self, *, parent: str | Path, name: str, template: str = "empty") -> dict[str, Any]:
        raw_name = str(name or "").strip()
        if not raw_name:
            raise ValueError("project name is required")
        if raw_name in {".", ".."} or "/" in raw_name or "\\" in raw_name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,79}", raw_name):
            raise ValueError("project name contains unsupported characters")
        parent_path = Path(parent or "").expanduser().resolve()
        if not parent_path.exists() or not parent_path.is_dir():
            raise ValueError(f"parent folder does not exist: {parent_path}")
        normalized_template = str(template or "empty").lower()
        if normalized_template not in {"empty", "python"}:
            raise ValueError("template must be empty or python")
        project = parent_path / raw_name
        if project.exists():
            raise ValueError(f"project folder already exists: {project}")
        project.mkdir(parents=False)
        try:
            (project / "README.md").write_text(f"# {raw_name}\n\nCreated with DynosAI Studio.\n", encoding="utf-8")
            (project / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.pyc\n.venv/\n", encoding="utf-8")
            if normalized_template == "python":
                slug = re.sub(r"[^a-z0-9]+", "-", raw_name.lower()).strip("-") or "dynosai-project"
                (project / "pyproject.toml").write_text(
                    f'[project]\nname = "{slug}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\n\n'
                    '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
                    encoding="utf-8",
                )
            git_created = False
            if shutil.which("git"):
                subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True, text=True)
                subprocess.run(["git", "config", "user.email", "studio@dynosai.local"], cwd=project, check=True, capture_output=True, text=True)
                subprocess.run(["git", "config", "user.name", "DynosAI Studio"], cwd=project, check=True, capture_output=True, text=True)
                subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True, text=True)
                subprocess.run(["git", "commit", "-m", "chore: create project starter"], cwd=project, check=True, capture_output=True, text=True)
                git_created = True
            entry = self.touch(project)
            return {**entry, "template": normalized_template, "git_created": git_created, "created": True}
        except Exception:
            shutil.rmtree(project, ignore_errors=True)
            raise



def browse_directory(path: str | Path | None = None) -> dict[str, Any]:
    """Return a directory-only view for Studio's in-app folder browser.

    The browser intentionally exposes names and paths only. It never reads file
    contents and works before a project has been selected.
    """

    current = Path(path).expanduser() if path else Path.home()
    try:
        current = current.resolve()
    except OSError as exc:
        raise ValueError(f"folder is not available: {current}") from exc
    if not current.exists():
        raise ValueError(f"folder does not exist: {current}")
    if not current.is_dir():
        raise ValueError(f"path is not a folder: {current}")

    directories: list[dict[str, str]] = []
    try:
        children = list(current.iterdir())
    except OSError as exc:
        raise ValueError(f"folder cannot be opened: {current}") from exc
    for child in children:
        try:
            if not child.is_dir():
                continue
        except OSError:
            continue
        directories.append({"name": child.name or str(child), "path": str(child.resolve())})
    directories.sort(key=lambda item: (item["name"].startswith("."), item["name"].casefold()))

    roots: list[dict[str, str]] = []
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = Path(f"{letter}:\\")
            if candidate.exists():
                roots.append({"label": f"{letter}:", "path": str(candidate.resolve())})
    else:
        roots.append({"label": "/", "path": "/"})

    parent = None if current.parent == current else str(current.parent)
    return {
        "path": str(current),
        "parent": parent,
        "home": str(Path.home().resolve()),
        "roots": roots,
        "directories": directories,
        "writable": os.access(current, os.W_OK),
    }

def create_directory(parent: str | Path, name: str) -> dict[str, Any]:
    """Create one child directory for Studio's folder browser.

    The operation is deliberately narrow: ``name`` is a single directory
    component, separators and traversal tokens are rejected, and no project
    initialization or file generation is performed.
    """

    raw_name = str(name or "").strip()
    if not raw_name:
        raise ValueError("folder name is required")
    if raw_name in {".", ".."} or "/" in raw_name or "\\" in raw_name:
        raise ValueError("folder name must be a single directory name")
    if not re.fullmatch(r"[^<>:\"/\\|?*]{1,120}", raw_name):
        raise ValueError("folder name contains unsupported characters")
    parent_path = Path(parent or "").expanduser().resolve()
    if not parent_path.exists() or not parent_path.is_dir():
        raise ValueError(f"parent folder does not exist: {parent_path}")
    if not os.access(parent_path, os.W_OK):
        raise ValueError(f"folder is not writable: {parent_path}")
    target = parent_path / raw_name
    if target.exists():
        raise ValueError(f"folder already exists: {target}")
    try:
        target.mkdir(parents=False)
    except OSError as exc:
        raise ValueError(f"folder could not be created: {target}") from exc
    return {"created": True, "path": str(target.resolve()), "parent": str(parent_path)}
