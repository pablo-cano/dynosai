# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider-neutral generation and validation of coding-agent configuration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .model_routing import ProviderModelRouting, activity_for_state

SCHEMA_VERSION = 1
PROVIDERS = ("cursor", "codex", "claude")
MODES = ("minimal", "recommended", "advanced")

# Provider-neutral capabilities. Prefer native capabilities first and only require
# an MCP implementation when the host cannot satisfy a capability natively.
NATIVE_CAPABILITIES: dict[str, set[str]] = {
    "cursor": {"repository.read", "repository.write", "repository.diff", "shell.execute", "tests.execute", "web.search"},
    "codex": {"repository.read", "repository.write", "repository.diff", "shell.execute", "tests.execute", "web.search"},
    "claude": {"repository.read", "repository.write", "repository.diff", "shell.execute", "tests.execute", "web.search"},
}

RULES: dict[str, dict[str, Any]] = {
    "core": {"title": "DynosAI Core", "text": "Follow the DynosAI workflow, inspect before editing, preserve project conventions, keep changes scoped, and report evidence rather than claiming unverified success."},
    "testing": {"title": "Testing", "text": "Run the smallest relevant validation after changes and the project acceptance gate before completion. Never report tests as passing unless their result was observed."},
    "security": {"title": "Security", "text": "Do not expose secrets, credentials or private keys. Prefer environment-variable references and least-privilege tooling. Do not broaden permissions without an explicit requirement."},
    "context": {"title": "Context Budget", "text": "Use progressive disclosure: load only the instructions, skills and references needed for the current activity. Prefer summaries and targeted retrieval over indiscriminate repository reads."},
    "sdd": {"title": "Spec Driven Development", "text": "Keep implementation traceable to specification, plan, tasks and evidence. Treat approved scope and acceptance criteria as constraints."},
}

SKILLS: dict[str, dict[str, Any]] = {
    "code-review": {
        "description": "Review a change for correctness, regressions, scope, maintainability and missing validation.",
        "requires": ["repository.read", "repository.diff"],
        "provides": ["code.review"],
        "activities": ["code_review", "validation", "merge"],
        "body": "Inspect the relevant diff first. Trace changed behavior to requirements and tests. Prioritize correctness and regression risks. Return concrete findings with file references and a short validation recommendation.",
    },
    "testing": {
        "description": "Select and execute focused tests and validation gates without wasting context or runtime.",
        "requires": ["repository.read", "tests.execute"],
        "provides": ["tests.validate"],
        "activities": ["implementation", "code_review", "validation", "merge"],
        "body": "Identify the narrowest tests covering the change, run them, then run the project gate when appropriate. Record commands, exit status and failures. Never infer a PASS from incomplete output.",
    },
    "debugging": {
        "description": "Diagnose defects using evidence, minimal reproduction and hypothesis-driven investigation.",
        "requires": ["repository.read", "shell.execute"],
        "provides": ["debug.root_cause"],
        "activities": ["discovery", "specification", "implementation", "validation"],
        "body": "Reproduce before changing code when possible. Form a small hypothesis, gather one discriminating piece of evidence, then iterate. Avoid broad speculative edits. Validate the root-cause fix with a regression test.",
    },
    "database-migrations": {
        "description": "Design and review safe database schema/data migrations with rollback and compatibility in mind.",
        "requires": ["repository.read", "database.schema.read"],
        "provides": ["database.migrate"],
        "activities": ["planning", "implementation", "validation"],
        "body": "Inspect current schema and migration conventions. Prefer backward-compatible staged changes. Define migration, validation and rollback/forward-fix strategy. Never execute destructive production changes implicitly.",
    },
    "api-development": {
        "description": "Implement and review HTTP/API behavior with contracts, validation and tests.",
        "requires": ["repository.read", "repository.write", "tests.execute"],
        "provides": ["api.implement"],
        "activities": ["planning", "implementation", "code_review"],
        "body": "Start from the API contract and existing project conventions. Keep transport, domain logic and persistence boundaries clear. Validate inputs and errors explicitly and add contract-focused tests.",
    },
    "frontend-development": {
        "description": "Implement web UI changes while preserving design-system, accessibility and responsive behavior.",
        "requires": ["repository.read", "repository.write", "tests.execute"],
        "provides": ["frontend.implement"],
        "activities": ["planning", "implementation", "code_review"],
        "body": "Reuse the project design system and existing components before creating new primitives. Preserve keyboard/accessibility behavior and responsive layouts. Validate the relevant UI/unit/type/lint gates.",
    },
    "data-ai": {
        "description": "Implement data/ML/AI changes with reproducibility, evaluation and resource-awareness.",
        "requires": ["repository.read", "repository.write", "tests.execute"],
        "provides": ["data_ai.implement"],
        "activities": ["planning", "implementation", "code_review", "validation"],
        "body": "Separate data assumptions, model/runtime configuration and evaluation. Keep experiments reproducible, avoid silent data leakage, and record measurable validation instead of subjective quality claims.",
    },
}

TOOLS: dict[str, dict[str, Any]] = {
    "dynosai": {
        "kind": "mcp", "provides": ["dynosai.workflow", "dynosai.context", "dynosai.git", "dynosai.validation"],
        "command": sys.executable, "args": ["-m", "dynosai_flow.mcp"], "env": {"DYNOSAI_RUNTIME_BROKER": "1"}, "always": True,
    },
    "postgres": {
        "kind": "mcp", "provides": ["database.schema.read", "database.query"],
        "command_env": "DYNOSAI_POSTGRES_MCP_COMMAND", "args_env": "DYNOSAI_POSTGRES_MCP_ARGS", "activation": "when_configured",
    },
}

AGENTS: dict[str, dict[str, Any]] = {
    "reviewer": {"description": "Independent reviewer focused on correctness, regression risk and acceptance evidence.", "skills": ["code-review"], "activities": ["code_review", "validation"]},
    "tester": {"description": "Validation specialist that selects focused tests and reports observable evidence.", "skills": ["testing", "debugging"], "activities": ["validation"]},
    "researcher": {"description": "Read-first investigator for discovery and unfamiliar code paths; avoids implementation until evidence is sufficient.", "skills": ["debugging"], "activities": ["discovery", "specification"]},
}

HOOKS: dict[str, dict[str, Any]] = {
    "test-before-complete": {"event": "before_complete", "action": "validate", "enforced_by": "dynosai_core", "description": "Require the relevant validation gate before completion."},
    "scope-after-edit": {"event": "after_edit", "action": "scope_check", "enforced_by": "dynosai_core", "description": "Check edited paths against approved scope."},
}

ARCHETYPES: dict[str, dict[str, list[str]]] = {
    "generic": {"rules": ["core", "context", "sdd"], "skills": ["code-review", "testing", "debugging"], "agents": ["reviewer", "tester"], "hooks": ["test-before-complete", "scope-after-edit"]},
    "python-api": {"rules": ["core", "context", "sdd", "testing", "security"], "skills": ["api-development", "code-review", "testing", "debugging"], "agents": ["reviewer", "tester", "researcher"], "hooks": ["test-before-complete", "scope-after-edit"]},
    "web-frontend": {"rules": ["core", "context", "sdd", "testing", "security"], "skills": ["frontend-development", "code-review", "testing", "debugging"], "agents": ["reviewer", "tester"], "hooks": ["test-before-complete", "scope-after-edit"]},
    "fullstack": {"rules": ["core", "context", "sdd", "testing", "security"], "skills": ["frontend-development", "api-development", "code-review", "testing", "debugging"], "agents": ["reviewer", "tester", "researcher"], "hooks": ["test-before-complete", "scope-after-edit"]},
    "data-ai": {"rules": ["core", "context", "sdd", "testing", "security"], "skills": ["data-ai", "code-review", "testing", "debugging"], "agents": ["reviewer", "tester", "researcher"], "hooks": ["test-before-complete", "scope-after-edit"]},
}


@dataclass(slots=True)
class ProjectFingerprint:
    archetype: str
    signals: list[str]
    confidence: float
    database: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectAnalyzer:
    def detect(self, root: Path) -> ProjectFingerprint:
        root = root.resolve()
        signals: list[str] = []
        has_py = (root / "pyproject.toml").exists() or (root / "requirements.txt").exists()
        has_node = (root / "package.json").exists()
        api = False
        frontend = False
        data_ai = False
        database: str | None = None
        text = ""
        for rel in ("pyproject.toml", "requirements.txt", "package.json"):
            p = root / rel
            if p.exists():
                try: text += "\n" + p.read_text(encoding="utf-8", errors="ignore").lower()
                except Exception: pass
        if has_py: signals.append("python")
        if has_node: signals.append("node")
        if any(x in text for x in ("fastapi", "django", "flask", "litestar", "starlette")):
            api = True; signals.append("api-framework")
        if any(x in text for x in ("next", "react", "vue", "svelte", "angular", "vite")):
            frontend = True; signals.append("frontend-framework")
        if any(x in text for x in ("torch", "tensorflow", "scikit-learn", "pandas", "numpy", "transformers", "fastembed")):
            data_ai = True; signals.append("data-ai-stack")
        if any(x in text for x in ("postgres", "psycopg", "asyncpg", "pgvector")):
            database = "postgres"; signals.append("postgres")
        if api and frontend: archetype = "fullstack"
        elif frontend: archetype = "web-frontend"
        elif api: archetype = "python-api"
        elif data_ai: archetype = "data-ai"
        elif has_py and any((root / x).exists() for x in ("api", "app", "server")): archetype = "python-api"
        else: archetype = "generic"
        confidence = 0.95 if archetype != "generic" and len(signals) >= 2 else 0.75 if archetype != "generic" else 0.5
        return ProjectFingerprint(archetype, signals, confidence, database)


def _default_profile() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "recommended",
        "project": {"autodetect": True, "archetype": "auto"},
        "providers": {"cursor": True, "codex": True, "claude": True},
        "context": {"strategy": "progressive", "budget": "balanced"},
        "permissions": {"mode": "safe"},
        "components": {"rules": [], "skills": [], "tools": [], "agents": [], "hooks": []},
        "overrides": {},
    }


def _merge_unique(*values: list[str]) -> list[str]:
    out: list[str] = []
    for seq in values:
        for item in seq:
            if item not in out: out.append(item)
    return out


class AgentConfiguration:
    """Provider-neutral control plane for agent instructions, skills, tools, agents and hooks."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.dir = self.root / ".dynosai"
        self.profile_path = self.dir / "agent-profile.json"
        self.generated_dir = self.dir / "generated"

    def init(self, *, mode: str = "recommended", force: bool = False) -> dict[str, Any]:
        if mode not in MODES: raise ValueError(f"mode must be one of: {', '.join(MODES)}")
        if self.profile_path.exists() and not force:
            return {"created": False, "path": str(self.profile_path), "profile": self.load()}
        profile = _default_profile(); profile["mode"] = mode
        self.dir.mkdir(parents=True, exist_ok=True)
        self.profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"created": True, "path": str(self.profile_path), "profile": profile}

    def load(self) -> dict[str, Any]:
        if not self.profile_path.exists(): return _default_profile()
        data = json.loads(self.profile_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict): raise ValueError("agent-profile.json must contain an object")
        if int(data.get("schema_version") or 0) != SCHEMA_VERSION: raise ValueError(f"Unsupported agent profile schema: {data.get('schema_version')}")
        mode = str(data.get("mode") or "recommended")
        if mode not in MODES: raise ValueError(f"Unsupported mode: {mode}")
        return data

    def _component_ids(self, profile: dict[str, Any], fingerprint: ProjectFingerprint) -> dict[str, list[str]]:
        mode = profile.get("mode", "recommended")
        if mode == "minimal":
            base = {"rules": ["core", "context"], "skills": [], "agents": [], "hooks": [], "tools": ["dynosai"]}
        else:
            archetype = profile.get("project", {}).get("archetype", "auto")
            if archetype == "auto": archetype = fingerprint.archetype
            if archetype not in ARCHETYPES: archetype = "generic"
            selected = ARCHETYPES[archetype]
            base = {k: list(selected.get(k, [])) for k in ("rules", "skills", "agents", "hooks")}
            base["tools"] = ["dynosai"]
            if fingerprint.database == "postgres" and mode in {"recommended", "advanced"}: base["tools"].append("postgres")
        explicit = profile.get("components") or {}
        if mode == "advanced":
            for key in base:
                base[key] = _merge_unique(base[key], list(explicit.get(key) or []))
        else:
            # Recommended permits additive overrides without forcing users to understand every default.
            for key in base:
                base[key] = _merge_unique(base[key], list(explicit.get(key) or []))
        return base

    @staticmethod
    def _tool_runtime(tool_id: str) -> dict[str, Any]:
        item = dict(TOOLS[tool_id])
        if "command_env" in item:
            command = os.environ.get(str(item["command_env"]), "").strip()
            if not command:
                item["available"] = False; return item
            item["command"] = command
            arg_text = os.environ.get(str(item.get("args_env") or ""), "").strip()
            item["args"] = arg_text.split() if arg_text else []
        item["available"] = bool(item.get("command"))
        return item

    @staticmethod
    def _apply_overrides(ids: dict[str, list[str]], profile: dict[str, Any], provider: str, activity: str) -> dict[str, list[str]]:
        out = {k: list(v) for k, v in ids.items()}
        layers = []
        overrides = profile.get("overrides") or {}
        if isinstance(overrides.get("all"), dict): layers.append(overrides["all"])
        providers = overrides.get("providers") or {}
        if isinstance(providers, dict) and isinstance(providers.get(provider), dict): layers.append(providers[provider])
        activities = overrides.get("activities") or {}
        if isinstance(activities, dict) and isinstance(activities.get(activity), dict): layers.append(activities[activity])
        for layer in layers:
            for key in out:
                add = list(layer.get(f"add_{key}") or [])
                remove = set(layer.get(f"remove_{key}") or [])
                out[key] = [x for x in _merge_unique(out[key], add) if x not in remove]
        return out

    @staticmethod
    def _context_plan(rules: list[dict[str, Any]], skills: list[dict[str, Any]]) -> dict[str, Any]:
        # Rough planning estimate only; provider tokenizers differ. The important
        # property is that skill bodies are on-demand rather than always injected.
        always_chars = sum(len(r.get("title", "")) + len(r.get("text", "")) for r in rules)
        metadata_chars = sum(len(s.get("id", "")) + len(s.get("description", "")) for s in skills)
        body_chars = sum(len(s.get("body", "")) for s in skills)
        return {
            "strategy": "progressive",
            "always_loaded_estimated_tokens": max(1, (always_chars + metadata_chars) // 4),
            "on_demand_skill_body_estimated_tokens": body_chars // 4,
            "on_demand_skills": [s["id"] for s in skills],
        }

    def resolve(self, *, provider: str, activity: str = "discovery", state: str | None = None) -> dict[str, Any]:
        provider = provider.lower()
        if provider not in PROVIDERS: raise ValueError(f"Unsupported provider: {provider}")
        if state is not None: activity = activity_for_state(state)
        profile = self.load(); fingerprint = ProjectAnalyzer().detect(self.root)
        if not (profile.get("providers") or {}).get(provider, True): raise ValueError(f"Provider disabled by agent profile: {provider}")
        ids = self._apply_overrides(self._component_ids(profile, fingerprint), profile, provider, activity)

        active_agents = [a for a in ids["agents"] if activity in AGENTS.get(a, {}).get("activities", [])]
        active_skills = [skill for skill in ids["skills"] if activity in SKILLS.get(skill, {}).get("activities", [])]
        for agent in active_agents:
            active_skills = _merge_unique(active_skills, [skill for skill in AGENTS[agent].get("skills", []) if activity in SKILLS.get(skill, {}).get("activities", [])])

        required: list[str] = []
        for skill in active_skills:
            if skill in SKILLS: required = _merge_unique(required, SKILLS[skill].get("requires", []))
        native = set(NATIVE_CAPABILITIES.get(provider, set()))
        tools: list[dict[str, Any]] = []
        supplied = set(native)
        for tool_id in ids["tools"]:
            if tool_id not in TOOLS: continue
            rt = self._tool_runtime(tool_id)
            if tool_id == "dynosai" or rt.get("available"):
                tools.append({"id": tool_id, **rt}); supplied.update(rt.get("provides", []))
        missing = sorted(set(required) - supplied)
        # Capabilities that have an optional tool mapping remain explicit missing capabilities
        # until the tool is configured; this prevents fake/implicit success.
        unresolved_tools = []
        unavailable_tools = []
        for tool_id in ids["tools"]:
            if tool_id in TOOLS:
                rt = self._tool_runtime(tool_id)
                if not rt.get("available"):
                    unavailable_tools.append(tool_id)
                    if set(rt.get("provides", [])) & set(missing): unresolved_tools.append(tool_id)

        model_route = None
        if provider in {"cursor", "codex"}:
            try: model_route = ProviderModelRouting(self.root).resolve(provider, activity).to_dict()
            except Exception: model_route = None
        return {
            "schema_version": SCHEMA_VERSION,
            "provider": provider,
            "activity": activity,
            "mode": profile.get("mode", "recommended"),
            "project": fingerprint.to_dict(),
            "context": profile.get("context") or {"strategy": "progressive", "budget": "balanced"},
            "permissions": profile.get("permissions") or {"mode": "safe"},
            "model_route": model_route,
            "rules": [{"id": x, **RULES[x]} for x in ids["rules"] if x in RULES],
            "skills": [{"id": x, **SKILLS[x]} for x in active_skills if x in SKILLS],
            "tools": tools,
            "agents": [{"id": x, **AGENTS[x]} for x in active_agents if x in AGENTS],
            "hooks": [{"id": x, **HOOKS[x]} for x in ids["hooks"] if x in HOOKS],
            "capabilities": {"required": sorted(set(required)), "native": sorted(native), "resolved": sorted(set(required) & supplied), "missing": missing},
            "context_plan": self._context_plan(
                [{"id": x, **RULES[x]} for x in ids["rules"] if x in RULES],
                [{"id": x, **SKILLS[x]} for x in active_skills if x in SKILLS],
            ),
            "unavailable_recommended_tools": unavailable_tools,
            "unresolved_tools": unresolved_tools,
        }

    @staticmethod
    def _skill_md(skill: dict[str, Any]) -> str:
        return f"---\nname: {skill['id']}\ndescription: {skill['description']}\n---\n\n# {skill['id']}\n\n{skill['body'].strip()}\n"

    @staticmethod
    def _managed_block(title: str, body: str) -> str:
        return f"<!-- DYNOSAI:{title}:BEGIN -->\n{body.rstrip()}\n<!-- DYNOSAI:{title}:END -->"

    @classmethod
    def _replace_managed_block(cls, path: Path, title: str, body: str) -> None:
        block = cls._managed_block(title, body)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        pattern = re.compile(rf"<!-- DYNOSAI:{re.escape(title)}:BEGIN -->.*?<!-- DYNOSAI:{re.escape(title)}:END -->", re.S)
        if pattern.search(current): current = pattern.sub(block, current)
        else: current = (current.rstrip() + "\n\n" + block + "\n").lstrip("\n")
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(current, encoding="utf-8")

    @staticmethod
    def _write_json_merge(path: Path, key: str, values: dict[str, Any]) -> None:
        data: dict[str, Any] = {}
        if path.exists():
            try: data = json.loads(path.read_text(encoding="utf-8"))
            except Exception: data = {}
        section = data.setdefault(key, {})
        for k, v in values.items(): section[k] = v
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _compile_common(self, effective: dict[str, Any], skill_dir: Path, instruction_path: Path, provider: str) -> list[str]:
        files: list[str] = []
        instruction = ["# DynosAI managed agent configuration", "", f"Provider: {provider}", f"Project archetype: {effective['project']['archetype']}", f"Context strategy: {effective['context'].get('strategy', 'progressive')}", ""]
        for rule in effective["rules"]:
            instruction += [f"## {rule['title']}", rule["text"], ""]
        if effective["skills"]:
            instruction += ["## Skills", "Load skills progressively and only when relevant to the current activity.", ""]
            instruction += [f"- {s['id']}: {s['description']}" for s in effective["skills"]]
        self._replace_managed_block(instruction_path, "AGENT-CONFIG", "\n".join(instruction))
        files.append(str(instruction_path))
        for skill in effective["skills"]:
            p = skill_dir / skill["id"] / "SKILL.md"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(self._skill_md(skill), encoding="utf-8"); files.append(str(p))
        return files

    def _compile_cursor(self, effective: dict[str, Any], *, materialize_tools: bool = True) -> list[str]:
        files = self._compile_common(effective, self.root / ".cursor" / "skills", self.root / ".cursor" / "rules" / "dynosai.mdc", "cursor")
        servers = {}
        for t in (effective["tools"] if materialize_tools else []):
            if t.get("kind") == "mcp":
                env = dict(t.get("env") or {}); env["DYNOSAI_AGENT_PROVIDER"] = "cursor" if t["id"] == "dynosai" else env.get("DYNOSAI_AGENT_PROVIDER", "cursor")
                servers[t["id"]] = {"command": t["command"], "args": t.get("args", []), "env": env}
        p = self.root / ".cursor" / "mcp.json"; self._write_json_merge(p, "mcpServers", servers); files.append(str(p))
        for agent in effective["agents"]:
            p = self.root / ".cursor" / "agents" / f"{agent['id']}.md"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(f"# {agent['id']}\n\n{agent['description']}\n\nSkills: {', '.join(agent['skills'])}\n", encoding="utf-8"); files.append(str(p))
        hp = self.generated_dir / "provider-hooks" / "cursor.json"; hp.parent.mkdir(parents=True, exist_ok=True); hp.write_text(json.dumps({"provider":"cursor","enforced_by":"dynosai","hooks":effective["hooks"]}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); files.append(str(hp))
        return files

    def _compile_claude(self, effective: dict[str, Any], *, materialize_tools: bool = True) -> list[str]:
        files = self._compile_common(effective, self.root / ".claude" / "skills", self.root / "CLAUDE.md", "claude")
        servers = {}
        for t in (effective["tools"] if materialize_tools else []):
            if t.get("kind") == "mcp":
                env = dict(t.get("env") or {}); env["DYNOSAI_AGENT_PROVIDER"] = "claude" if t["id"] == "dynosai" else env.get("DYNOSAI_AGENT_PROVIDER", "claude")
                servers[t["id"]] = {"command": t["command"], "args": t.get("args", []), "env": env}
        p = self.root / ".mcp.json"; self._write_json_merge(p, "mcpServers", servers); files.append(str(p))
        for agent in effective["agents"]:
            p = self.root / ".claude" / "agents" / f"{agent['id']}.md"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(f"---\nname: {agent['id']}\ndescription: {agent['description']}\n---\n\nUse skills: {', '.join(agent['skills'])}\n", encoding="utf-8"); files.append(str(p))
        hp = self.generated_dir / "provider-hooks" / "claude.json"; hp.parent.mkdir(parents=True, exist_ok=True); hp.write_text(json.dumps({"provider":"claude","enforced_by":"dynosai","hooks":effective["hooks"]}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); files.append(str(hp))
        return files

    def _compile_codex(self, effective: dict[str, Any], *, materialize_tools: bool = True) -> list[str]:
        files = self._compile_common(effective, self.root / ".agents" / "skills", self.root / "AGENTS.md", "codex")
        p = self.root / ".codex" / "config.toml"; p.parent.mkdir(parents=True, exist_ok=True)
        existing = p.read_text(encoding="utf-8") if p.exists() else ""
        # Only manage DynosAI-owned MCP sections; preserve arbitrary user config.
        for t in (effective["tools"] if materialize_tools else []):
            if t.get("kind") != "mcp": continue
            # The legacy AgentConfigurator already owns the DynosAI Codex MCP
            # block. Reuse it rather than emitting a duplicate TOML table.
            if t["id"] == "dynosai" and re.search(r"^\s*\[mcp_servers\.dynosai\]\s*$", existing, re.M):
                continue
            marker = f"MCP-{t['id'].upper()}"
            env = dict(t.get("env") or {}); env["DYNOSAI_AGENT_PROVIDER"] = "codex" if t["id"] == "dynosai" else env.get("DYNOSAI_AGENT_PROVIDER", "codex")
            env_toml = ", ".join(f"{k} = {json.dumps(str(v))}" for k, v in env.items())
            body = "\n".join([f"[mcp_servers.{t['id']}]", f"command = {json.dumps(str(t['command']))}", f"args = {json.dumps([str(x) for x in t.get('args', [])])}", f"env = {{ {env_toml} }}", "enabled = true"])
            begin = f"# DYNOSAI:{marker}:BEGIN"
            end = f"# DYNOSAI:{marker}:END"
            block = f"{begin}\n{body}\n{end}"
            pattern = re.compile(rf"# DYNOSAI:{re.escape(marker)}:BEGIN.*?# DYNOSAI:{re.escape(marker)}:END", re.S)
            if pattern.search(existing): existing = pattern.sub(block, existing)
            else: existing = existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
        p.write_text(existing, encoding="utf-8"); files.append(str(p))
        # Provider-neutral subagent definitions are materialized for hosts/adapters;
        # native provider registration can evolve independently of the semantic model.
        for agent in effective["agents"]:
            ap = self.root / ".codex" / "agents" / f"{agent['id']}.md"; ap.parent.mkdir(parents=True, exist_ok=True); ap.write_text(f"# {agent['id']}\n\n{agent['description']}\n\nSkills: {', '.join(agent['skills'])}\n", encoding="utf-8"); files.append(str(ap))
        hp = self.generated_dir / "provider-hooks" / "codex.json"; hp.parent.mkdir(parents=True, exist_ok=True); hp.write_text(json.dumps({"provider":"codex","enforced_by":"dynosai","hooks":effective["hooks"]}, ensure_ascii=False, indent=2)+"\n", encoding="utf-8"); files.append(str(hp))
        return files

    def compile(self, *, providers: list[str] | None = None, activity: str = "discovery", state: str | None = None, dry_run: bool = False, materialize_tools: bool = True) -> dict[str, Any]:
        providers = providers or [p for p, enabled in (self.load().get("providers") or {}).items() if enabled]
        providers = [p.lower() for p in providers]
        for p in providers:
            if p not in PROVIDERS: raise ValueError(f"Unsupported provider: {p}")
        effective = {p: self.resolve(provider=p, activity=activity, state=state) for p in providers}
        preview = {p: {"rules": [x["id"] for x in e["rules"]], "skills": [x["id"] for x in e["skills"]], "tools": [x["id"] for x in e["tools"]], "agents": [x["id"] for x in e["agents"]], "hooks": [x["id"] for x in e["hooks"]], "missing_capabilities": e["capabilities"]["missing"]} for p, e in effective.items()}
        if dry_run: return {"dry_run": True, "providers": preview, "effective": effective}
        written: dict[str, list[str]] = {}
        for provider, e in effective.items():
            if provider == "cursor": written[provider] = self._compile_cursor(e, materialize_tools=materialize_tools)
            elif provider == "codex": written[provider] = self._compile_codex(e, materialize_tools=materialize_tools)
            else: written[provider] = self._compile_claude(e, materialize_tools=materialize_tools)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        effective_path = self.generated_dir / "effective-config.json"
        effective_path.write_text(json.dumps(effective, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lock = {"schema_version": SCHEMA_VERSION, "providers": providers, "files": {}, "effective_sha256": hashlib.sha256(effective_path.read_bytes()).hexdigest()}
        for provider, files in written.items():
            lock["files"][provider] = [{"path": str(Path(f).resolve().relative_to(self.root)).replace("\\", "/"), "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()} for f in files if Path(f).exists()]
        lock_path = self.generated_dir / "lock.json"; lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"dry_run": False, "providers": preview, "written": written, "effective_config": str(effective_path), "lock": str(lock_path)}

    @staticmethod
    def _remove_block(path: Path, title: str) -> bool:
        if not path.exists(): return False
        current = path.read_text(encoding="utf-8")
        pattern = re.compile(rf"\n?<!-- DYNOSAI:{re.escape(title)}:BEGIN -->.*?<!-- DYNOSAI:{re.escape(title)}:END -->\n?", re.S)
        updated = pattern.sub("\n", current).strip()
        if updated:
            path.write_text(updated + "\n", encoding="utf-8")
        else:
            path.unlink()
        return current != (updated + "\n" if updated else "")

    @staticmethod
    def _remove_json_tools(path: Path, tool_ids: set[str]) -> None:
        if not path.exists(): return
        try: data = json.loads(path.read_text(encoding="utf-8"))
        except Exception: return
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            for tool_id in tool_ids: servers.pop(tool_id, None)
            if not servers: data.pop("mcpServers", None)
        if data: path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else: path.unlink()

    def clean(self, *, providers: list[str] | None = None) -> dict[str, Any]:
        providers = providers or list(PROVIDERS)
        providers = [p.lower() for p in providers]
        removed: list[str] = []
        built_skills = set(SKILLS)
        built_agents = set(AGENTS)
        tool_ids = set(TOOLS)
        for provider in providers:
            if provider not in PROVIDERS: raise ValueError(f"Unsupported provider: {provider}")
            if provider == "cursor":
                self._remove_block(self.root / ".cursor" / "rules" / "dynosai.mdc", "AGENT-CONFIG")
                for sid in built_skills:
                    d = self.root / ".cursor" / "skills" / sid
                    if d.exists(): shutil.rmtree(d); removed.append(str(d))
                for aid in built_agents:
                    ap = self.root / ".cursor" / "agents" / f"{aid}.md"
                    if ap.exists(): ap.unlink(); removed.append(str(ap))
                self._remove_json_tools(self.root / ".cursor" / "mcp.json", tool_ids)
            elif provider == "claude":
                self._remove_block(self.root / "CLAUDE.md", "AGENT-CONFIG")
                for sid in built_skills:
                    d = self.root / ".claude" / "skills" / sid
                    if d.exists(): shutil.rmtree(d); removed.append(str(d))
                for aid in built_agents:
                    ap = self.root / ".claude" / "agents" / f"{aid}.md"
                    if ap.exists(): ap.unlink(); removed.append(str(ap))
                self._remove_json_tools(self.root / ".mcp.json", tool_ids)
            else:
                self._remove_block(self.root / "AGENTS.md", "AGENT-CONFIG")
                for sid in built_skills:
                    d = self.root / ".agents" / "skills" / sid
                    if d.exists(): shutil.rmtree(d); removed.append(str(d))
                for aid in built_agents:
                    ap = self.root / ".codex" / "agents" / f"{aid}.md"
                    if ap.exists(): ap.unlink(); removed.append(str(ap))
                cp = self.root / ".codex" / "config.toml"
                if cp.exists():
                    text = cp.read_text(encoding="utf-8")
                    for tid in tool_ids:
                        marker = f"MCP-{tid.upper()}"
                        text = re.sub(rf"\n?# DYNOSAI:{re.escape(marker)}:BEGIN.*?# DYNOSAI:{re.escape(marker)}:END\n?", "\n", text, flags=re.S)
                    cp.write_text(text.strip() + "\n" if text.strip() else "", encoding="utf-8")
            hp = self.generated_dir / "provider-hooks" / f"{provider}.json"
            if hp.exists(): hp.unlink(); removed.append(str(hp))
        return {"cleaned": providers, "removed": removed}

    def doctor(self) -> dict[str, Any]:
        fingerprint = ProjectAnalyzer().detect(self.root)
        profile = self.load()
        results = {}
        ready = True
        for provider in PROVIDERS:
            if not (profile.get("providers") or {}).get(provider, True): continue
            e = self.resolve(provider=provider)
            ok = not e["capabilities"]["missing"]
            results[provider] = {"ready": ok, "missing_capabilities": e["capabilities"]["missing"], "unresolved_tools": e["unresolved_tools"]}
            ready = ready and ok
        return {"ready": ready, "profile_path": str(self.profile_path), "profile_exists": self.profile_path.exists(), "mode": profile.get("mode"), "project": fingerprint.to_dict(), "providers": results}
