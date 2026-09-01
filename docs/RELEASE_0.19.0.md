# DynosAI 0.19.0 — Ecosystem & Interoperability

0.19.0 sits on the 0.18.0 execution-profile baseline and publishes **host-owned capability manifests** for the transports that already exist.

## What this release is

Interoperability is a contract problem, not a new agent wrapper. Cursor ACP and Codex app-server remain the certified Studio transports. Additional clients are refused instead of being silently treated as Codex or Cursor.

## Shipped

- Capability manifests for Cursor (`cursor-acp`) and Codex (`codex-app-server`), including transport, elicitation, MCP result shape and 0.13.0 certification.
- Adapter registry that refuses uncertified clients (VS Code, Windsurf, Claude Code, and similar) and project extension packs.
- Studio Overview inventory, `dynosai_stats` summary and diagnostic-bundle evidence.
- Host-owned `GET /api/provider-capabilities` (no new MCP tool).

## Deliberately not shipped

- Broader ACP/application adapters for additional IDEs or CLIs.
- Certification of coding-agent runtimes beyond Codex and Cursor.
- A skills/validation pack marketplace or loadable project extension API.
- Schema 7.

## Authority unchanged

Schema remains v6. Git is source truth. `.dynosai/knowledge.db` is workflow truth. Cursor ACP (0.14.1) and Codex app-server are unchanged. 0.15–0.18 harness, teams, eval intelligence and execution profiles are preserved.

## Validation

Release still requires repository checks, Studio asset sync, the stable pytest suite, and website check/typecheck/lint/build when web files change.
