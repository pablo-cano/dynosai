# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Secret redaction and a future secret-broker boundary.

Raw secrets must not enter model prompts, evidence, logs, diagnostics, Studio
summaries or exported bundles. The broker abstraction exists so a later runtime
can resolve credentials for processes without handing them to the model.
"""

from __future__ import annotations

import re
from typing import Any

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


class SecretBroker:
    """Reference-only secret access. Values are not returned for model context.

    0.15 does not ship a configured vault. Callers must fail closed rather than
    inlining credentials into prompts or evidence.
    """

    def reference(self, name: str) -> str:
        slug = str(name or "").strip()
        if not slug:
            raise ValueError("secret name is required")
        return f"secret:{slug}"

    def materialize_for_model(self, reference: str) -> None:
        raise PermissionError(
            f"{reference} must not be materialized into model context, evidence, logs or Studio"
        )

    def materialize_for_runtime(self, reference: str, runtime_name: str) -> str:
        del runtime_name
        raise PermissionError(
            f"{reference} has no configured runtime secret broker; refuse rather than embed the raw secret"
        )
