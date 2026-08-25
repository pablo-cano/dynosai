# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Safety policy for acceptance-only environment controls and provider execution."""

from __future__ import annotations

from typing import Any

from .version import __version__


def automated_elicitation_content(request: dict[str, Any] | None = None, *, message: str = "") -> dict[str, Any]:
    """Return a deterministic response for *acceptance-test-only* elicitations.

    This policy is intentionally conservative and exists only for the debug
    acceptance harness. Production provider sessions never call it unless the
    explicit DYNOSAI_ACCEPTANCE_AUTORESPOND environment flag is set.
    """
    request = request or {}
    schema = request.get("requestedSchema") or request.get("requested_schema") or {}
    props = schema.get("properties") or {}
    required = list(schema.get("required") or [])
    content: dict[str, Any] = {}

    def choose_enum(name: str, options: list[Any]) -> Any:
        lowered = {str(x).lower(): x for x in options}
        preferences = {
            "decision": ["approve", "initialize_git", "whole_repository", "continue", "yes", "accept"],
            "answer": ["yes", "approve", "continue", "accept"],
        }.get(name, ["approve", "yes", "continue", "accept"])
        # Project bootstrap semantics are materially different from ordinary gates.
        text = (message or request.get("message") or "").lower()
        if "monorepo" in text and "whole_repository" in lowered:
            return lowered["whole_repository"]
        if "git" in text and "initialize_git" in lowered:
            return lowered["initialize_git"]
        # Acceptance fixtures are expected to have complete plan scope. Never
        # silently bless unexpected scope growth merely to keep an unattended run
        # green; resolve it negatively and let the acceptance gates report drift.
        if "agent requests" in text and "scope" not in text:
            if "request_changes" in lowered:
                return lowered["request_changes"]
            if "reject" in lowered:
                return lowered["reject"]
            if "decline" in lowered:
                return lowered["decline"]
        for item in preferences:
            if item in lowered:
                return lowered[item]
        for candidate in options:
            if str(candidate).lower() not in {"cancel", "decline", "reject", "no"}:
                return candidate
        return options[0] if options else "approve"

    for name, spec in props.items():
        spec = spec or {}
        enum = spec.get("enum")
        if isinstance(enum, list) and enum:
            content[name] = choose_enum(name, enum)
            continue
        typ = spec.get("type")
        if name == "decision":
            content[name] = "approve"
        elif name == "comments":
            content[name] = f"DynosAI {__version__} automated acceptance harness"
        elif name == "answer":
            content[name] = "Yes — deterministic acceptance response"
        elif name in required:
            if typ == "boolean": content[name] = True
            elif typ == "integer": content[name] = 1
            elif typ == "number": content[name] = 1
            elif typ == "array": content[name] = []
            elif typ == "object": content[name] = {}
            else: content[name] = "approved"

    if "decision" not in content and ("decision" in required or not props):
        content["decision"] = "approve"
    return content
