# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Secret redaction and a fail-closed secret-broker boundary.

Raw secrets must not enter model prompts, evidence, logs, diagnostics, Studio
summaries or exported bundles. 0.18 may materialize a configured vault value
to the local runtime process; the model still never receives the raw secret.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .util import json_loads

_PEM = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----")
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PEM, "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bxai-[A-Za-z0-9]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b", re.I), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|password|token|authorization)\s*[:=]\s*([^\s,;]{8,})"), r"\1=[REDACTED]"),
)


def redact_text(value: str | None) -> str:
    """Replace known credential patterns. Unknown secrets cannot be guaranteed."""
    text = str(value or "")
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(value: Any) -> Any:
    """Redact strings inside JSON-like structures without changing shape."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def vault_path_for(root: str | Path) -> Path:
    """Project-local vault path. Path policy already treats `.dynosai/runtime/**` as sensitive."""
    return Path(root).expanduser().resolve() / ".dynosai" / "runtime" / "secret-vault.json"


class SecretBroker:
    """Reference-only secret access. Values are not returned for model context.

    Without a configured vault, runtime materialization refuses closed (0.15 behavior).
    With a vault and allow_runtime=True, values may be injected into the local process
    environment only. Evidence, logs, MCP and Studio must keep names/refs, never values.
    """

    def __init__(self, *, vault_path: str | Path | None = None, allow_runtime: bool = False):
        self.vault_path = Path(vault_path).expanduser().resolve() if vault_path else None
        self.allow_runtime = bool(allow_runtime)

    def reference(self, name: str) -> str:
        slug = str(name or "").strip()
        if not slug:
            raise ValueError("secret name is required")
        return f"secret:{slug}"

    def materialize_for_model(self, reference: str) -> None:
        raise PermissionError(
            f"{reference} must not be materialized into model context, evidence, logs or Studio"
        )

    def _load_vault(self) -> dict[str, str]:
        if self.vault_path is None or not self.vault_path.exists():
            return {}
        payload = json_loads(self.vault_path.read_text(encoding="utf-8"), {})
        secrets = payload.get("secrets") if isinstance(payload, dict) else {}
        if not isinstance(secrets, dict):
            return {}
        return {str(key): str(value) for key, value in secrets.items() if str(key).strip()}

    def materialize_for_runtime(self, reference: str, runtime_name: str) -> str:
        if str(runtime_name or "").strip().lower() != "local":
            raise PermissionError(f"{reference} cannot be materialized to runtime {runtime_name!r} in 0.18")
        if not self.allow_runtime:
            raise PermissionError(
                f"{reference} has no configured runtime secret broker; refuse rather than embed the raw secret"
            )
        key = str(reference or "").strip()
        if key.startswith("secret:"):
            key = key.split(":", 1)[1]
        vault = self._load_vault()
        if key not in vault:
            raise PermissionError(f"{reference} is not present in the configured local vault")
        return vault[key]
