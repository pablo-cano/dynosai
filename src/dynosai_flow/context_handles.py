# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Persistent pass-by-reference context handles for large artifacts.

Large tool outputs, diffs, logs and reports are stored outside the model
prompt. Agents retrieve slices explicitly through ``dynosai_retrieve_handle``.
Workflow state remains in knowledge.db; handles are runtime artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .harness_contracts import harness_enabled, validate_artifact_type
from .secrets import redact_value
from .util import utc_now

SCHEMA_VERSION = 1
INLINE_LIMIT = 4096
PREVIEW_CHARS = 800
RETRIEVAL_METHOD = "dynosai_retrieve_handle"


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _preview(body: Any) -> str:
    if isinstance(body, str):
        raw = body
    else:
        raw = json.dumps(body, ensure_ascii=False, default=str)
    if len(raw) <= PREVIEW_CHARS:
        return raw
    return raw[:PREVIEW_CHARS] + "…"


def _size_of(body: Any) -> int:
    if isinstance(body, str):
        return len(body.encode("utf-8"))
    return len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"))


class ContextHandleStore:
    """Project-local handle index under ``.dynosai/runtime/context-handles``."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.base = self.root / ".dynosai" / "runtime" / "context-handles"

    def _path(self, handle_id: str) -> Path:
        if not handle_id.startswith("HDL-") or "/" in handle_id or "\\" in handle_id:
            raise ValueError(f"invalid context handle id: {handle_id}")
        return self.base / f"{handle_id}.json"

    def put(
        self,
        artifact_type: str,
        origin: str,
        body: Any,
        *,
        work_id: str | None = None,
        task_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_type = validate_artifact_type(artifact_type)
        safe_body = redact_value(body)
        raw = json.dumps(safe_body, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        handle_id = f"HDL-{digest[:20]}"
        now = utc_now()
        existing = self._load(handle_id)
        record = {
            "schema_version": SCHEMA_VERSION,
            "id": handle_id,
            "artifact_type": artifact_type,
            "origin": str(origin),
            "work_id": work_id,
            "task_id": task_id,
            "session_id": session_id,
            "size": _size_of(safe_body),
            "checksum": digest[:32],
            "preview": _preview(safe_body),
            "created_at": existing.get("created_at") if existing else now,
            "updated_at": now,
            "retrieval": RETRIEVAL_METHOD,
            "metadata": metadata or {},
            "body": safe_body,
        }
        _atomic_write(self._path(handle_id), json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n")
        return self.meta(handle_id)

    def _load(self, handle_id: str) -> dict[str, Any] | None:
        path = self._path(handle_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupted context handle {handle_id}") from exc

    def meta(self, handle_id: str) -> dict[str, Any]:
        record = self._load(handle_id)
        if not record:
            raise ValueError(f"unknown context handle: {handle_id}")
        return {key: record.get(key) for key in (
            "id", "artifact_type", "origin", "work_id", "task_id", "session_id",
            "size", "checksum", "preview", "created_at", "updated_at", "retrieval", "metadata",
        )}

    def retrieve(
        self,
        handle_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        section: str | None = None,
    ) -> dict[str, Any]:
        record = self._load(handle_id)
        if not record:
            raise ValueError(f"unknown context handle: {handle_id}")
        body = record.get("body")
        if section:
            if not isinstance(body, dict) or section not in body:
                raise ValueError(f"handle {handle_id} has no section {section}")
            body = body[section]
        if isinstance(body, str):
            start = max(0, int(offset or 0))
            end = start + int(limit) if limit is not None else len(body)
            sliced = body[start:end]
            return {
                **self.meta(handle_id),
                "offset": start,
                "limit": limit,
                "section": section,
                "truncated": end < len(body) or start > 0,
                "content": sliced,
                "content_length": len(sliced),
                "total_length": len(body),
            }
        return {
            **self.meta(handle_id),
            "section": section,
            "content": body,
            "truncated": False,
        }

    def wrap_tool_result(
        self,
        tool_name: str,
        result: Any,
        *,
        work_id: str | None = None,
        session_id: str | None = None,
        enabled: bool | None = None,
        inline_limit: int = INLINE_LIMIT,
    ) -> Any:
        """Persist a handle and, when enabled, replace large bodies with a preview."""
        if not isinstance(result, dict):
            return result
        if enabled is None:
            enabled = harness_enabled("context_handles", True)
        out = dict(result)
        metrics = {"mode": "inline", "bytes_omitted": 0, "handle_id": None, "enabled": enabled}

        def attach(artifact_type: str, body: Any, replace_key: str | None = None) -> None:
            nonlocal metrics
            handle = self.put(
                artifact_type, tool_name, body,
                work_id=work_id, session_id=session_id,
                metadata={"tool": tool_name},
            )
            full = _size_of(body)
            preview = _size_of(handle.get("preview") or "")
            omit = max(0, full - preview) if enabled and full > inline_limit else 0
            metrics = {
                "mode": "handle" if omit else "inline_with_handle",
                "bytes_omitted": omit,
                "bytes_full": full,
                "bytes_preview": preview,
                "handle_id": handle["id"],
                "enabled": enabled,
            }
            out["context_handle"] = handle
            out["context_handle_metrics"] = metrics
            if omit and replace_key is not None:
                out[replace_key] = handle["preview"]
                out[f"{replace_key}_omitted"] = True

        if tool_name == "dynosai_git_diff" and isinstance(out.get("diff"), str):
            attach("diff", out["diff"], "diff")
            return out
        if tool_name == "dynosai_run_validation":
            outputs = [str(item.get("output") or "") for item in (out.get("results") or []) if isinstance(item, dict)]
            blob = "\n".join(outputs)
            if blob:
                attach("validation_report", {"passed": out.get("passed"), "outputs": outputs, "results": out.get("results")}, None)
                if enabled and _size_of(blob) > inline_limit:
                    compact = []
                    for item in out.get("results") or []:
                        if not isinstance(item, dict):
                            compact.append(item)
                            continue
                        row = dict(item)
                        output = str(row.get("output") or "")
                        if len(output) > PREVIEW_CHARS:
                            row["output"] = output[:PREVIEW_CHARS] + "…"
                            row["output_omitted"] = True
                        compact.append(row)
                    out["results"] = compact
                    metrics["mode"] = "handle"
                    metrics["bytes_omitted"] = max(0, _size_of(blob) - PREVIEW_CHARS * max(1, len(outputs)))
                    out["context_handle_metrics"] = metrics
            return out
        if tool_name == "dynosai_ask":
            raw_size = _size_of(out)
            if raw_size > inline_limit:
                attach("search", out, None)
                if enabled:
                    preview_items = list(out.get("results") or [])[:3] if isinstance(out.get("results"), list) else []
                    return {
                        "context_handle": out.get("context_handle"),
                        "context_handle_metrics": out.get("context_handle_metrics"),
                        "count": out.get("count"),
                        "preview": preview_items,
                        "omitted": True,
                    }
            else:
                attach("search", out, None)
            return out
        return out
