# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Strict managed-runtime isolation and provider process policy enforcement."""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

from .agent_config import AgentConfiguration


@dataclass(slots=True)
class ManagedRuntime:
    provider: str
    root: str
    env: dict[str, str]
    files: list[str]
    external_defaults_policy: str = "ignore"
    auth_bridge: str | None = None
    tool_profile: str = "design"
    provider_extensions_policy: str = "deny"
    strict_managed_runtime: bool = True
    skill_catalog: list[str] = field(default_factory=list)
    active_skills: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def tool_profile_for_activity(activity: str) -> str:
    a = (activity or "discovery").lower()
    if a in {"discovery", "specification", "planning"}:
        return "design"
    if a in {"implementation", "code_review", "validation", "merge"}:
        return "delivery"
    return "design"


class ManagedProviderRuntime:
    """Build an isolated DynosAI-owned provider runtime.

    Provider configuration, rules, skills and MCP definitions are generated from
    DynosAI's effective config. User/global provider defaults are not imported.
    Authentication may be bridged read-only (symlink) so isolation does not force
    a second login.
    """

    def __init__(self, project: str | Path):
        self.project = Path(project).expanduser().resolve()
        self.base = self.project / ".dynosai" / "runtime" / "managed-agents"

    def _skill_catalog(self, provider: str) -> list[dict[str, Any]]:
        """Materialize only DynosAI-owned skills that may become relevant later.

        Provider-native skill discovery is progressive, so exposing the small set of
        DynosAI skill descriptors/bodies in the isolated runtime avoids stale
        discovery-only snapshots without importing any user/provider skill catalog.
        Activation remains phase-aware through the effective ``agent_config``.
        """
        resolver = AgentConfiguration(self.project)
        catalog: dict[str, dict[str, Any]] = {}
        for activity in ("discovery", "specification", "planning", "implementation", "code_review", "validation", "merge"):
            try:
                effective = resolver.resolve(provider=provider, activity=activity)
            except Exception:
                continue
            for skill in effective.get("skills") or []:
                sid = str(skill.get("id") or "").strip()
                if sid and sid not in catalog:
                    catalog[sid] = dict(skill)
        return list(catalog.values())

    @staticmethod
    def _skill_md(skill: dict[str, Any]) -> str:
        return AgentConfiguration._skill_md(skill)

    @staticmethod
    def _instructions(effective: dict[str, Any], provider: str) -> str:
        lines = [
            "# DynosAI managed runtime",
            "",
            f"Provider: {provider}",
            "External defaults policy: ignore",
            "Use only DynosAI-provided rules, skills, tools and workflow instructions for this managed session.",
            "",
        ]
        for rule in effective.get("rules") or []:
            lines.extend([f"## {rule['title']}", str(rule.get("text") or ""), ""])
        if effective.get("skills"):
            lines.extend(["## Skills", "Load only the relevant DynosAI skill body when needed.", ""])
            for skill in effective["skills"]:
                lines.append(f"- {skill['id']}: {skill['description']}")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _mcp_server(provider: str, tool_profile: str, extra_env: dict[str, str] | None = None) -> dict[str, Any]:
        server = {
            "command": str(Path(sys.executable).absolute()),
            "args": ["-m", "dynosai_flow.mcp"],
            "env": {
                "DYNOSAI_AGENT_PROVIDER": provider,
                "DYNOSAI_RUNTIME_BROKER": "1",
                "DYNOSAI_MANAGED_AGENT": "1",
                "DYNOSAI_EXTERNAL_DEFAULTS_POLICY": "ignore",
                "DYNOSAI_MCP_TOOL_PROFILE": tool_profile,
                "DYNOSAI_PROVIDER_EXTENSIONS_POLICY": "deny",
                "DYNOSAI_STRICT_MANAGED_RUNTIME": "1",
                "DYNOSAI_AUTO_ADVANCE": "1",
                "DYNOSAI_COMPACT_RESPONSES": "1",
                "DYNOSAI_TOKEN_TELEMETRY": "1",
                "DYNOSAI_MODEL_CONTROL_POLICY": "adaptive",
                "DYNOSAI_PHASE_TOOL_DISCLOSURE": "adaptive",
                "DYNOSAI_EVIDENCE_CACHE": "1",
                "DYNOSAI_CONTEXT_CHECKPOINTS": "1",
            },
        }
        if extra_env:
            server["env"].update({str(k): str(v) for k, v in extra_env.items() if v is not None})
        return server

    @staticmethod
    def authoritative_prompt(effective: dict[str, Any], user_prompt: str) -> str:
        """Deliver DynosAI policy independently of provider-native defaults.

        Provider-native skill catalogues are treated as non-authoritative. The
        active DynosAI skill bodies are included in this compact capsule; later
        phases receive a fresh ``agent_config`` through DynosAI MCP results.
        """
        lines = [
            "# DynosAI strict managed-session authority",
            "Use only DynosAI-selected rules, skills, MCP tools, hooks and workflow policy.",
            "Ignore provider/user plugin, skill, rule, MCP and project-instruction defaults.",
            "The agent_config returned by DynosAI MCP is authoritative after every workflow transition.",
            "Honor model_control, token_budget, context_strategy, context_checkpoint and tool_surface guidance returned by DynosAI; reuse checkpoint sections/evidence refs before rereading accepted context; do not self-switch provider models inside a suspended turn.",
            "",
            "## Active rules",
        ]
        for rule in effective.get("rules") or []:
            lines.extend([f"### {rule.get('title') or rule.get('id')}", str(rule.get("text") or ""), ""])
        skills = effective.get("skills") or []
        if skills:
            lines.append("## Active DynosAI skills")
            for skill in skills:
                lines.extend([
                    f"### {skill.get('id')}",
                    str(skill.get("description") or ""),
                    str(skill.get("body") or ""),
                    "",
                ])
        lines.extend(["## Task", user_prompt])
        return "\n".join(lines).rstrip() + "\n"

    def prepare(
        self,
        provider: str,
        *,
        activity: str = "discovery",
        effective: dict[str, Any] | None = None,
        tool_profile: str | None = None,
        auth_home: str | Path | None = None,
        clean: bool = True,
        extra_env: dict[str, str] | None = None,
    ) -> ManagedRuntime:
        provider = provider.lower()
        if provider not in {"cursor", "codex", "claude"}:
            raise ValueError(f"Unsupported provider: {provider}")
        effective = effective or AgentConfiguration(self.project).resolve(provider=provider, activity=activity)
        tool_profile = tool_profile or tool_profile_for_activity(activity)
        root = self.base / provider
        if clean and root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        files: list[str] = []
        env = {
            "DYNOSAI_EXTERNAL_DEFAULTS_POLICY": "ignore",
            "DYNOSAI_MCP_TOOL_PROFILE": tool_profile,
            "DYNOSAI_MANAGED_AGENT": "1",
            "DYNOSAI_AGENT_PROVIDER": provider,
            "DYNOSAI_PROVIDER_EXTENSIONS_POLICY": "deny",
            "DYNOSAI_STRICT_MANAGED_RUNTIME": "1",
            "DYNOSAI_AUTO_ADVANCE": "1",
            "DYNOSAI_COMPACT_RESPONSES": "1",
            "DYNOSAI_TOKEN_TELEMETRY": "1",
            "DYNOSAI_MODEL_CONTROL_POLICY": "adaptive",
            "DYNOSAI_PHASE_TOOL_DISCLOSURE": "adaptive",
            "DYNOSAI_EVIDENCE_CACHE": "1",
            "DYNOSAI_CONTEXT_CHECKPOINTS": "1",
        }
        if extra_env:
            env.update({str(k): str(v) for k, v in extra_env.items() if v is not None})
        auth_bridge = None
        instruction_text = self._instructions(effective, provider)
        server = self._mcp_server(provider, tool_profile, extra_env=extra_env)
        skill_catalog = self._skill_catalog(provider)

        if provider == "cursor":
            config_dir = root / "config"
            (config_dir / "rules").mkdir(parents=True, exist_ok=True)
            (config_dir / "skills").mkdir(parents=True, exist_ok=True)
            rules = config_dir / "rules" / "dynosai.mdc"
            rules.write_text(instruction_text, encoding="utf-8")
            files.append(str(rules))
            for skill in skill_catalog:
                p = config_dir / "skills" / skill["id"] / "SKILL.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(self._skill_md(skill), encoding="utf-8")
                files.append(str(p))
            mcp = config_dir / "mcp.json"
            mcp.write_text(json.dumps({"mcpServers": {"dynosai": server}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            files.append(str(mcp))
            env["CURSOR_CONFIG_DIR"] = str(config_dir)

        elif provider == "codex":
            home = root / "home"
            home.mkdir(parents=True, exist_ok=True)
            skills_dir = home / "skills"
            for skill in skill_catalog:
                p = skills_dir / skill["id"] / "SKILL.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(self._skill_md(skill), encoding="utf-8")
                files.append(str(p))
            instructions = home / "DYNOSAI.md"
            instructions.write_text(instruction_text, encoding="utf-8")
            files.append(str(instructions))
            # Isolated config: no user MCPs/config/rules are imported. Disable
            # project instruction ingestion; DynosAI passes its instructions in
            # the turn/prompt and owns the MCP registry below.
            cfg = home / "config.toml"
            cfg.write_text(
                "# DynosAI strict isolated managed runtime\n"
                "features.plugins = false\n"
                "features.apps = false\n"
                "features.recommended_plugins = false\n"
                "project_doc_max_bytes = 0\n\n"
                "[skills]\n"
                "include_instructions = false\n\n"
                "[skills.bundled]\n"
                "enabled = false\n\n"
                "[mcp_servers.dynosai]\n"
                f"command = {json.dumps(server['command'])}\n"
                "args = [\"-m\", \"dynosai_flow.mcp\"]\n"
                "env = { " + ", ".join(f"{k} = {json.dumps(str(v))}" for k, v in sorted(server["env"].items())) + " }\n"
                "enabled = true\n",
                encoding="utf-8",
            )
            files.append(str(cfg))
            env["CODEX_HOME"] = str(home)
            # Bridge authentication only; never copy provider settings.
            source_home = Path(auth_home).expanduser().resolve() if auth_home else Path.home()
            auth = source_home / ".codex" / "auth.json"
            if auth.exists():
                target = home / "auth.json"
                try:
                    target.symlink_to(auth)
                    auth_bridge = str(auth)
                    files.append(str(target))
                except OSError:
                    # If symlinks are unavailable, leave auth untouched rather than
                    # copying secrets into a generated runtime.
                    pass

        else:  # Claude is materialized for future use but not part of current acceptance.
            home = root / "home"
            home.mkdir(parents=True, exist_ok=True)
            instructions = home / "CLAUDE.md"
            instructions.write_text(instruction_text, encoding="utf-8")
            files.append(str(instructions))
            mcp = home / ".mcp.json"
            mcp.write_text(json.dumps({"mcpServers": {"dynosai": server}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            files.append(str(mcp))
            for skill in skill_catalog:
                p = home / ".claude" / "skills" / skill["id"] / "SKILL.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(self._skill_md(skill), encoding="utf-8")
                files.append(str(p))
            env["CLAUDE_CONFIG_DIR"] = str(home)

        manifest = root / "runtime.json"
        manifest.write_text(json.dumps({
            "provider": provider,
            "activity": activity,
            "tool_profile": tool_profile,
            "external_defaults_policy": "ignore",
            "provider_extensions_policy": "deny",
            "strict_managed_runtime": True,
            "instruction_authority": "dynosai",
            "instruction_delivery": ["provider_native", "prompt_capsule", "mcp_dynamic"],
            "execution_optimization": {
                "auto_advance_deterministic_transitions": True,
                "compact_mcp_responses": True,
                "batch_decisions": True,
                "batch_reads": True,
                "progressive_tool_surface": True,
                "provider_native_skill_catalog": True,
                "phase_tool_disclosure": "adaptive_safe_superset_then_phase_subset",
                "evidence_read_cache": True,
                "context_checkpoints": True,
                "targeted_checkpoint_sections": True,
                "checkpoint_first_context_reuse": True,
                "predictive_model_routing": True,
            },
            "model_control": {
                "policy": "adaptive",
                "phase_aware": True,
                "adaptive_escalation": True,
                "token_budgets": True,
                "validation_driven_retry": True,
                "safe_boundary_only": True,
                "predictive_outcome_memory": True,
                "context_pressure_excluded_from_capability_routing": True
            },
            "token_telemetry": {
                "enabled": True,
                "process_scope": True,
                "persisted": True,
                "cursor": "estimated_live_exact_final",
                "codex": "exact_live",
            },
            "skill_catalog": [skill.get("id") for skill in skill_catalog],
            "active_skills": [skill.get("id") for skill in (effective.get("skills") or [])],
            "effective": effective,
            "files": files,
            "auth_bridge": auth_bridge,
        }, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        files.append(str(manifest))
        return ManagedRuntime(
            provider, str(root), env, files, "ignore", auth_bridge, tool_profile, "deny", True,
            [str(skill.get("id")) for skill in skill_catalog if skill.get("id")],
            [str(skill.get("id")) for skill in (effective.get("skills") or []) if skill.get("id")],
        )
