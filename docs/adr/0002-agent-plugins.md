# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano

# Decision: Future Agent Plugins compatibility (no DynosAI Pack)

- Status: Accepted
- Date: 2026-09-04
- Release: 1.0.0-rc.6
- Spec consulted: Agent Plugins Specification 1.0.0 (https://agent-plugins.org/specification)

## Context

DynosAI must not invent a proprietary `DynosAI Pack` format. Post-1.0
interoperability for skills, MCP configs, validators and governance extras
should sit on the portable Agent Plugins standard that Cursor, Codex and other
hosts already treat as the packaging floor.

RC6 does **not** implement plugin discovery, a marketplace, or a plugin
runtime. This ADR freezes the *shape* so 1.2 can implement it without
redesigning a competing format.

The 1.0.0 spec is a closed manifest:

- required `plugin.json`
- optional `skills/` (Agent Skills / `SKILL.md`)
- optional `mcp.json` (`$schema` + `mcpServers` only)
- client-specific data only under reverse-domain `extensions` or a directory
  named for that namespace
- unknown top-level fields must be reported and ignored; they are not a
  DynosAI extension point

## Decision

Future DynosAI plugin support will consume Agent Plugins packages:

```text
plugin.json
skills/
mcp.json
com.dynosai/          # optional extension directory
```

`plugin.json` `extensions` namespace: **`com.dynosai`** (product domain
`dynosai.com`).

DynosAI-specific fields belong only in:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "example.plugin",
  "extensions": {
    "com.dynosai": {
      "validators": [],
      "policies": [],
      "evals": [],
      "risk": {},
      "required_capabilities": [],
      "governance": {},
      "evidence": {}
    }
  }
}
```

and/or files under `com.dynosai/`. They MUST NOT be added as arbitrary root
fields of `plugin.json` or `mcp.json`.

Host-owned rules for the later 1.2 implementation:

- local directory and Git URL / immutable revision first; no marketplace in 1.2
- path containment stays inside the plugin root (spec §4.1)
- enable/disable is host-owned; a plugin cannot add authority silently
- hashes, version and project compatibility are recorded as evidence
- each plugin/skill can be compared enabled vs disabled for quality, tokens,
  cost, latency and regressions
- uncertified extension packs remain refused on the 1.0 capability boundary

## Non-goals for RC6 / 1.0.0

- plugin runtime
- registry or marketplace
- loading `extensions.com.dynosai` in Core
- treating a skill folder as a certified provider

Re-consult the then-current Agent Plugins specification before implementing
1.2. If the portable field set changes, DynosAI follows the spec rather than
preserving this example JSON blindly.
