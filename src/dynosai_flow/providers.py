# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Provider command construction, capability detection, and provider-specific runtime behavior."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .db import Database
from .runtime import user_home
from .util import json_dumps, utc_now


@dataclass(slots=True)
class ProviderCapabilities:
    provider: str
    tools: bool = True
    elicitation_form: bool = False
    elicitation_url: bool = False
    roots: bool = False
    prompts: bool = False
    protocol: str = ""
    client_name: str = ""
    client_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderCapabilityService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def from_initialize(provider: str, params: dict[str, Any]) -> ProviderCapabilities:
        caps = params.get("capabilities") or {}
        elic_present = "elicitation" in caps and isinstance(caps.get("elicitation"), dict)
        elic = caps.get("elicitation") if elic_present else {}
        info = params.get("clientInfo") or {}
        return ProviderCapabilities(
            provider=provider,
            tools=True,
            # MCP 2025-06 clients may advertise elicitation as an empty object;
            # absence of the key means no capability at all.
            elicitation_form=bool(elic_present and ("form" in elic or elic == {})),
            elicitation_url=bool(elic_present and "url" in elic),
            roots=bool(caps.get("roots") is not None),
            prompts=bool(caps.get("prompts") is not None),
            protocol=str(params.get("protocolVersion") or ""),
            client_name=str(info.get("name") or ""),
            client_version=str(info.get("version") or ""),
        )

    def record(self, capabilities: ProviderCapabilities) -> None:
        self.db.execute(
            "INSERT INTO provider_capabilities(provider,protocol,client_name,client_version,capabilities_json,checked_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(provider) DO UPDATE SET protocol=excluded.protocol,client_name=excluded.client_name,client_version=excluded.client_version,capabilities_json=excluded.capabilities_json,checked_at=excluded.checked_at",
            (capabilities.provider, capabilities.protocol, capabilities.client_name, capabilities.client_version, json_dumps(capabilities.to_dict()), utc_now()),
        )
        self.db.audit("ProviderCapabilitiesRecorded", capabilities.provider, capabilities.to_dict())


class MachineProviderSetup:
    """Machine-level provider bootstrap. No project DB is required."""

    def __init__(self, home: Path | None = None):
        self.home = (home or user_home()).resolve()

    def configure_cursor(self) -> dict[str, Any]:
        path = self.home / ".cursor" / "mcp.json"
        data: dict[str, Any] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Invalid Cursor config: {path}")
        data.setdefault("mcpServers", {})["dynosai"] = {
            "command": str(Path(sys.executable).absolute()), "args": ["-m", "dynosai_flow.mcp"],
            "env": {"DYNOSAI_AGENT_PROVIDER": "cursor", "DYNOSAI_RUNTIME_BROKER": "1"},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"provider": "cursor", "configured": True, "path": str(path), "installed": shutil.which("cursor-agent") is not None}

    def configure_claude(self) -> dict[str, Any]:
        """Configure DynosAI as a user-scoped Claude Code MCP server.

        Claude Code also supports project-local `.mcp.json`; AgentConfiguration
        compiles that form. Machine setup keeps the same provider-neutral MCP
        registration available before a project is initialized.
        """
        path = self.home / ".claude.json"
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data.setdefault("mcpServers", {})["dynosai"] = {
            "command": str(Path(sys.executable).absolute()),
            "args": ["-m", "dynosai_flow.mcp"],
            "env": {"DYNOSAI_AGENT_PROVIDER": "claude", "DYNOSAI_RUNTIME_BROKER": "1"},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"provider": "claude", "configured": True, "path": str(path), "installed": shutil.which("claude") is not None}

    def configure_codex(self) -> dict[str, Any]:
        path = self.home / ".codex" / "config.toml"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = current.splitlines(); out: list[str] = []; skipping = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[mcp_servers.dynosai") and stripped.endswith("]"):
                skipping = True; continue
            if skipping and stripped.startswith("[") and stripped.endswith("]"):
                skipping = False
            if not skipping:
                out.append(line)
        while out and not out[-1].strip(): out.pop()
        if out: out.append("")
        out.extend([
            "# DYNOSAI managed MCP server",
            "[mcp_servers.dynosai]",
            f'command = {json.dumps(str(Path(sys.executable).absolute()))}',
            'args = ["-m", "dynosai_flow.mcp"]',
            'env = { DYNOSAI_AGENT_PROVIDER = "codex", DYNOSAI_RUNTIME_BROKER = "1" }',
            "enabled = true",
            "",
        ])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(out), encoding="utf-8")
        return {"provider": "codex", "configured": True, "path": str(path), "installed": shutil.which("codex") is not None}

    def install_model(self) -> dict[str, Any]:
        from .semantic import FastEmbedProvider, DEFAULT_MODEL, DEFAULT_DIMENSIONS, default_cache_dir
        provider=FastEmbedProvider(DEFAULT_MODEL,default_cache_dir(),DEFAULT_DIMENSIONS)
        vector=provider.embed(["DynosAI global model setup probe"])[0]
        marker=default_cache_dir()/".dynosai-model.json"
        marker.parent.mkdir(parents=True,exist_ok=True)
        marker.write_text(json.dumps({"model":DEFAULT_MODEL,"dimensions":int(vector.shape[0]),"installed_at":utc_now()},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return {"installed":True,"model":DEFAULT_MODEL,"dimensions":int(vector.shape[0]),"cache":str(default_cache_dir()),"marker":str(marker)}


    @staticmethod
    def _run(command: list[str], timeout: int = 10) -> dict[str, Any]:
        try:
            r=subprocess.run(command,text=True,capture_output=True,timeout=timeout)
            return {"ok":r.returncode==0,"exit_code":r.returncode,"output":((r.stdout or "")+(r.stderr or "")).strip()[:2000]}
        except Exception as exc:
            return {"ok":False,"error":str(exc),"output":""}

    def doctor(self, *, deep: bool = False) -> dict[str, Any]:
        """Machine-level health check that deliberately requires no project DB."""
        import importlib.util
        import tomllib
        from .semantic import default_cache_dir

        cursor=shutil.which("cursor-agent")
        codex=shutil.which("codex")
        cursor_cfg=self.home/".cursor"/"mcp.json"
        codex_cfg=self.home/".codex"/"config.toml"
        expected_python=str(Path(sys.executable).absolute())

        cursor_server={}
        cursor_cfg_error=""
        try:
            data=json.loads(cursor_cfg.read_text(encoding="utf-8"))
            cursor_server=((data.get("mcpServers") or {}).get("dynosai") or {})
        except Exception as exc:
            cursor_cfg_error=str(exc)

        codex_server={}
        codex_cfg_error=""
        try:
            data=tomllib.loads(codex_cfg.read_text(encoding="utf-8"))
            codex_server=(((data.get("mcp_servers") or {}).get("dynosai")) or {})
        except Exception as exc:
            codex_cfg_error=str(exc)

        marker=default_cache_dir()/".dynosai-model.json"
        checks={
            "dynosai_importable": importlib.util.find_spec("dynosai_flow") is not None,
            "python_executable": Path(sys.executable).exists(),
            "model_installed": marker.exists(),
            "cursor_installed": bool(cursor),
            "cursor_mcp_configured": bool(cursor_server.get("command")),
            "cursor_mcp_uses_installed_python": cursor_server.get("command")==expected_python and cursor_server.get("args")==["-m","dynosai_flow.mcp"],
            "codex_installed": bool(codex),
            "codex_mcp_configured": bool(codex_server.get("command")),
            "codex_mcp_uses_installed_python": codex_server.get("command")==expected_python and codex_server.get("args")==["-m","dynosai_flow.mcp"],
        }
        from .model_routing import ProviderModelRouting
        routing=ProviderModelRouting(home=self.home)
        details={
            "scope":"machine",
            "project_initialized":False,
            "python":expected_python,
            "model_marker":str(marker),
            "provider_model_routing":{"cursor":routing.effective("cursor"),"codex":routing.effective("codex")},
            "cursor":{"executable":cursor,"config":str(cursor_cfg),"server":cursor_server,"config_error":cursor_cfg_error},
            "codex":{"executable":codex,"config":str(codex_cfg),"server":codex_server,"config_error":codex_cfg_error},
        }

        if deep:
            cursor_version=self._run([cursor,"--version"] if cursor else ["false"])
            cursor_status=self._run([cursor,"status"] if cursor else ["false"])
            cursor_mcp=self._run([cursor,"mcp","list"] if cursor else ["false"])
            cursor_tools=self._run([cursor,"mcp","list-tools","dynosai"] if cursor else ["false"],15)
            codex_version=self._run([codex,"--version"] if codex else ["false"])
            codex_login=self._run([codex,"login","status"] if codex else ["false"])
            codex_mcp=self._run([codex,"mcp","list"] if codex else ["false"],15)
            checks.update({
                "cursor_version_probe":bool(cursor_version.get("ok")),
                "cursor_authenticated":bool(cursor_status.get("ok") and "logged in" in cursor_status.get("output","").lower()),
                "cursor_mcp_visible":bool(cursor_mcp.get("ok") and "dynosai" in cursor_mcp.get("output","").lower()),
                "cursor_tools_visible":bool(cursor_tools.get("ok") and (
                    cursor_tools.get("output","").lower().count("dynosai_")>=20
                    or __import__("re").search(r"Tools\s+for\s+dynosai\s*\((\d+)\)",cursor_tools.get("output",""),__import__("re").IGNORECASE)
                    and int(__import__("re").search(r"Tools\s+for\s+dynosai\s*\((\d+)\)",cursor_tools.get("output",""),__import__("re").IGNORECASE).group(1))>=20
                )),
                "codex_version_probe":bool(codex_version.get("ok")),
                "codex_authenticated":bool(codex_login.get("ok") and "logged in" in codex_login.get("output","").lower()),
                "codex_mcp_visible":bool(codex_mcp.get("ok") and "dynosai" in codex_mcp.get("output","").lower()),
            })
            details["live"]={
                "cursor":{"version":cursor_version,"status":cursor_status,"mcp":cursor_mcp,"tools":cursor_tools},
                "codex":{"version":codex_version,"login":codex_login,"mcp":codex_mcp},
            }

        ready=all(checks.values())
        return {
            "scope":"machine",
            "project_initialized":False,
            "ready":ready,
            "full_ready":ready,
            "checks":checks,
            "details":details,
            "note":"Machine-level doctor: no DynosAI project database is required. Run inside a DynosAI project to add project/Git/index/worktree checks.",
        }

    def setup(self, providers: list[str], *, install_model: bool = True) -> dict[str, Any]:
        results=[]
        for provider in providers:
            if provider == "cursor": results.append(self.configure_cursor())
            elif provider == "codex": results.append(self.configure_codex())
            elif provider == "claude": results.append(self.configure_claude())
            else: raise ValueError(f"Unsupported provider: {provider}")
        model = self.install_model() if install_model else {"installed":False,"skipped":True}
        return {"providers": results, "model": model, "ready": all(x.get("configured") for x in results) and (bool(model.get("installed")) or bool(model.get("skipped")))}
