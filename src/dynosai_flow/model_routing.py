# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider/activity model profiles and deterministic route selection."""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime import user_home

PROVIDERS = ("cursor", "codex")
ACTIVITIES = (
    "discovery",
    "specification",
    "planning",
    "implementation",
    "code_review",
    "validation",
    "merge",
)

PROVIDER_ALIASES = {
    "cursor": "cursor",
    "codex": "codex",
    "openai": "codex",
}

ACTIVITY_ALIASES = {
    "discovery": "discovery",
    "discover": "discovery",
    "spec": "specification",
    "specification": "specification",
    "spec_review": "specification",
    "plan": "planning",
    "planning": "planning",
    "plan_review": "planning",
    "implementation": "implementation",
    "implement": "implementation",
    "code": "implementation",
    "code_review": "code_review",
    "review": "code_review",
    "validation": "validation",
    "validate": "validation",
    "merge": "merge",
}

# Explicit versioned-ish defaults make acceptance reproducible. "auto" remains
# a supported user choice for Cursor but is deliberately not the test baseline.
BUILTIN_DEFAULTS: dict[str, dict[str, str]] = {
    # Production defaults stay on the stronger validated models. Acceptance can
    # deliberately select the economical profile without changing user runtime
    # defaults or persisted provider-model configuration.
    "codex": {"model": "gpt-5.6-sol", "effort": "medium"},
    "cursor": {"model": "grok-4.6", "effort": "medium"},
}

ACCEPTANCE_MODEL_PROFILES: dict[str, dict[str, dict[str, str]]] = {
    "economy": {
        "codex": {"model": "gpt-5.6-luna", "effort": "medium"},
        "cursor": {"model": "gpt-5.6-luna", "effort": "medium"},
    },
    "baseline": {
        "codex": {"model": "gpt-5.6-sol", "effort": "medium"},
        "cursor": {"model": "grok-4.6", "effort": "medium"},
    },
}

RECOMMENDED_MODELS: dict[str, list[dict[str, Any]]] = {
    "codex": [
        {
            "model": "gpt-5.6-sol",
            "label": "GPT-5.6 Sol",
            "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
            "recommended_effort": "medium",
        },
        {
            "model": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna (economy)",
            "efforts": ["none", "low", "medium", "high", "xhigh", "max"],
            "recommended_effort": "medium",
        },
    ],
    "cursor": [
        {
            "model": "grok-4.6",
            "label": "Cursor Grok 4.6",
            "efforts": ["low", "medium", "high", "xhigh"],
            "recommended_effort": "medium",
        },
        {
            "model": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna (economy)",
            "efforts": ["low", "medium", "high", "xhigh"],
            "recommended_effort": "medium",
        },
        {
            "model": "auto",
            "label": "Cursor Auto",
            "efforts": [],
            "recommended_effort": None,
        },
    ],
}


STATE_ACTIVITY = {
    "inbox": "discovery",
    "discovery": "discovery",
    "spec_review": "specification",
    "plan_review": "planning",
    "ready": "implementation",
    "implementing": "implementation",
    "code_review": "code_review",
    "validating": "validation",
    "ready_to_merge": "merge",
    "done": "merge",
}


def normalize_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    try:
        return PROVIDER_ALIASES[value]
    except KeyError as exc:
        raise ValueError(f"Unsupported provider: {provider}") from exc


def normalize_activity(activity: str | None) -> str:
    value = str(activity or "discovery").strip().lower().replace("-", "_")
    try:
        return ACTIVITY_ALIASES[value]
    except KeyError as exc:
        raise ValueError(f"Unsupported activity: {activity}") from exc


def activity_for_state(state: str | None) -> str:
    return STATE_ACTIVITY.get(str(state or "").strip().lower(), "discovery")


@dataclass(slots=True)
class ModelRoute:
    provider: str
    activity: str
    model: str
    effort: str | None
    source: str
    scope: str
    provider_application: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def cursor_cli_selector(route: ModelRoute) -> str | None:
    """Compile a logical Cursor route into the CLI selector.

    Cursor documents ``--model`` but currently exposes effort as part of model
    variants in its product/CLI. DynosAI therefore compiles an effort-specific
    slug for deterministic automation and verifies the model reported by the
    provider stream. ``auto`` intentionally stays provider-managed.
    """
    model = str(route.model or "").strip()
    if not model:
        return None
    if model.lower() in {"auto", "cursor-auto", "default"}:
        return "auto"
    effort = str(route.effort or "").strip().lower()
    if not effort or effort in {"default", "none"}:
        return model
    normalized = model.lower().replace("_", "-")
    # Respect an already explicit provider slug instead of appending twice.
    if normalized.endswith("-" + effort) or normalized.endswith("-thinking-" + effort):
        return model
    # Cursor's first-party Grok effort variants are exposed as sibling model
    # IDs. Keep the persisted config provider-neutral (`grok-4.6` + effort)
    # and compile the current Cursor wire/CLI selector at the adapter edge.
    if normalized.startswith("grok-4.6"):
        return f"cursor-{normalized}-{effort}"
    return f"{model}-{effort}"


def _tokenize_model(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def model_observation(route: ModelRoute, observed: str | None) -> dict[str, Any]:
    """Compare a provider-reported model string with a logical route."""
    observed_text = str(observed or "").strip()
    model_token = _tokenize_model(route.model)
    observed_token = _tokenize_model(observed_text)
    auto = str(route.model).strip().lower() in {"auto", "cursor-auto", "default"}
    model_match = bool(observed_text) and (auto and "auto" in observed_text.lower() or (model_token and model_token in observed_token))
    effort = str(route.effort or "").strip().lower()
    effort_match: bool | None
    if not effort or effort in {"default", "none"} or auto:
        effort_match = None
    else:
        effort_match = effort in observed_text.lower().replace("_", "-")
    verified = bool(model_match and (effort_match is not False))
    # If a provider only reports the model family but not effort, effort cannot
    # be certified. This is intentionally explicit rather than pretending it ran.
    if effort_match is False and observed_text and model_match and effort not in observed_text.lower():
        verified = False
    return {
        "requested": route.to_dict(),
        "observed": observed_text or None,
        "model_match": model_match,
        "effort_match": effort_match,
        "verified": verified,
    }


class ProviderModelRouting:
    """Provider-neutral model routing configuration.

    Precedence:
      project activity > project default > machine activity > machine default >
      built-in provider default.

    The Core resolves routes. Provider adapters are responsible for applying the
    route at the boundary supported by that provider.
    """

    def __init__(self, project_root: str | Path | None = None, home: str | Path | None = None):
        self.project_root = Path(project_root).expanduser().resolve() if project_root else None
        self.home = Path(home).expanduser().resolve() if home else user_home().resolve()
        self.machine_path = self.home / ".dynosai" / "provider-models.toml"
        self.project_path = (self.project_root / ".dynosai" / "provider-models.toml") if self.project_root else None

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"version": 1, "providers": {}}

    @classmethod
    def _load(cls, path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return cls._empty()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Invalid model routing config: {path}")
        providers = data.get("providers") or {}
        if not isinstance(providers, dict):
            raise ValueError(f"Invalid [providers] in model routing config: {path}")
        return {"version": int(data.get("version") or 1), "providers": providers}

    @staticmethod
    def _route_from(data: dict[str, Any], provider: str, activity: str | None) -> dict[str, Any] | None:
        p = ((data.get("providers") or {}).get(provider) or {})
        if activity:
            route = ((p.get("activities") or {}).get(activity) or {})
            return dict(route) if route.get("model") else None
        route = p.get("default") or {}
        return dict(route) if route.get("model") else None

    @staticmethod
    def _application(provider: str) -> str:
        if provider == "codex":
            return "turn_override"
        if provider == "cursor":
            # --model is reliable at launch/resume boundaries. A current Cursor
            # headless process does not expose a host callback for atomically
            # swapping the parent model while an MCP call is suspended.
            return "session_or_resume_boundary"
        return "unsupported"

    def _candidate_chain(self, provider: str, activity: str | None, *, include_activity: bool) -> list[tuple[dict[str, Any] | None, str, str]]:
        machine = self._load(self.machine_path)
        project = self._load(self.project_path)
        checks: list[tuple[dict[str, Any] | None, str, str]] = []
        if include_activity and activity:
            checks += [
                (self._route_from(project, provider, activity), "project_activity", "project"),
            ]
        checks += [(self._route_from(project, provider, None), "project_default", "project")]
        if include_activity and activity:
            checks += [(self._route_from(machine, provider, activity), "machine_activity", "machine")]
        checks += [
            (self._route_from(machine, provider, None), "machine_default", "machine"),
            (BUILTIN_DEFAULTS[provider], "builtin_default", "builtin"),
        ]
        return checks

    def _resolve(self, provider: str, activity: str, *, include_activity: bool) -> ModelRoute:
        provider = normalize_provider(provider)
        activity = normalize_activity(activity)
        for route, source, scope in self._candidate_chain(provider, activity, include_activity=include_activity):
            if route and route.get("model"):
                effort = route.get("effort")
                return ModelRoute(
                    provider=provider,
                    activity=activity,
                    model=str(route["model"]),
                    effort=str(effort).lower().strip() if effort else None,
                    source=source,
                    scope=scope,
                    provider_application=self._application(provider),
                )
        raise RuntimeError(f"No model route for provider {provider}")

    def resolve(self, provider: str, activity: str | None = None) -> ModelRoute:
        return self._resolve(provider, normalize_activity(activity), include_activity=True)

    def resolve_default(self, provider: str) -> ModelRoute:
        return self._resolve(provider, "discovery", include_activity=False)

    def effective(self, provider: str) -> dict[str, Any]:
        provider = normalize_provider(provider)
        return {
            "provider": provider,
            "default": self.resolve_default(provider).to_dict(),
            "activities": {activity: self.resolve(provider, activity).to_dict() for activity in ACTIVITIES},
            "recommended_models": RECOMMENDED_MODELS[provider],
        }

    def show(self, provider: str | None = None) -> dict[str, Any]:
        providers = [normalize_provider(provider)] if provider else list(PROVIDERS)
        return {
            "machine_config": str(self.machine_path),
            "project_config": str(self.project_path) if self.project_path else None,
            "providers": {p: self.effective(p) for p in providers},
            "activities": list(ACTIVITIES),
            "activity_aliases": dict(ACTIVITY_ALIASES),
            "provider_aliases": dict(PROVIDER_ALIASES),
            "precedence": ["project_activity", "project_default", "machine_activity", "machine_default", "builtin_default"],
        }

    @staticmethod
    def _toml_quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    @classmethod
    def _write(cls, path: Path, data: dict[str, Any]) -> None:
        lines = ["version = 1", ""]
        providers = data.get("providers") or {}
        for provider in PROVIDERS:
            pdata = providers.get(provider) or {}
            default = pdata.get("default") or {}
            if default.get("model"):
                lines += [
                    f"[providers.{provider}.default]",
                    f"model = {cls._toml_quote(str(default['model']))}",
                ]
                if default.get("effort"):
                    lines.append(f"effort = {cls._toml_quote(str(default['effort']))}")
                lines.append("")
            activities = pdata.get("activities") or {}
            for activity in ACTIVITIES:
                route = activities.get(activity) or {}
                if not route.get("model"):
                    continue
                lines += [
                    f"[providers.{provider}.activities.{activity}]",
                    f"model = {cls._toml_quote(str(route['model']))}",
                ]
                if route.get("effort"):
                    lines.append(f"effort = {cls._toml_quote(str(route['effort']))}")
                lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def set(
        self,
        provider: str,
        model: str,
        *,
        effort: str | None = None,
        activity: str | None = None,
        scope: str = "machine",
    ) -> dict[str, Any]:
        provider = normalize_provider(provider)
        model = str(model).strip()
        scope = str(scope).lower().strip()
        activity = normalize_activity(activity) if activity is not None else None
        if not model:
            raise ValueError("model is required")
        if scope not in {"machine", "project"}:
            raise ValueError("scope must be machine or project")
        path = self.machine_path if scope == "machine" else self.project_path
        if path is None:
            raise ValueError("project scope requires a project root")
        data = self._load(path)
        providers = data.setdefault("providers", {})
        pdata = providers.setdefault(provider, {})
        target = pdata.setdefault("activities", {}).setdefault(activity, {}) if activity else pdata.setdefault("default", {})
        target.clear()
        target["model"] = model
        if effort:
            target["effort"] = str(effort).lower().strip()
        self._write(path, data)
        route = self.resolve(provider, activity) if activity else self.resolve_default(provider)
        return {"updated": True, "scope": scope, "path": str(path), "route": route.to_dict()}

    def reset(self, provider: str, *, activity: str | None = None, scope: str = "machine") -> dict[str, Any]:
        provider = normalize_provider(provider)
        scope = str(scope).lower().strip()
        activity = normalize_activity(activity) if activity is not None else None
        if scope not in {"machine", "project"}:
            raise ValueError("scope must be machine or project")
        path = self.machine_path if scope == "machine" else self.project_path
        if path is None:
            raise ValueError("project scope requires a project root")
        data = self._load(path)
        pdata = (data.get("providers") or {}).get(provider) or {}
        if activity:
            (pdata.get("activities") or {}).pop(activity, None)
        else:
            pdata.pop("default", None)
        if path.exists() or data.get("providers"):
            self._write(path, data)
        route = self.resolve(provider, activity) if activity else self.resolve_default(provider)
        return {"reset": True, "scope": scope, "path": str(path), "route": route.to_dict()}


def env_override(provider: str, base: ModelRoute) -> ModelRoute:
    """Acceptance/CI escape hatch without mutating persistent configuration."""
    provider = normalize_provider(provider)
    prefix = f"DYNOSAI_{provider.upper()}_"
    model = os.environ.get(prefix + "MODEL") or base.model
    effort = os.environ.get(prefix + "EFFORT") or base.effort
    if model == base.model and effort == base.effort:
        return base
    return ModelRoute(
        base.provider,
        base.activity,
        model,
        effort,
        "environment",
        "process",
        base.provider_application,
    )
