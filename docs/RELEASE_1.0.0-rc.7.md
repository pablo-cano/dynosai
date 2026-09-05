# DynosAI 1.0.0-rc.7

Package version: `1.0.0rc7` (PEP 440). Display: `1.0.0-rc.7`.
Public status: **Public RC · Certification Integrity & Protocol Correctness**. Schema remains v6.

This candidate is **not** stable 1.0. `MATRIX_1.0` is **not** declared PASS.

## Why RC7 exists

RC6 live certification recorded eight explicit trials (two per cell). All four cells remain `fail`. RC7 does not add 1.1–1.5 features. It prepares a deterministically identifiable candidate before MATRIX attempt 3:

- release archive must not leak ignored secrets or `.dynosai/` runtime files
- MCP `2026-07-28` must not inherit protocol or capabilities across requests
- 2026 lifecycle must not reuse the 2025 initialize/ping handshake
- human gates on 2026 use Multi Round-Trip `input_required`, not a trusted client `requestState`
- MATRIX default workspace must sit outside the repository
- one MATRIX trial is one provider execution
- `mcp_protocol_version` records what a provider actually spoke, not `CURRENT_PROTOCOL`
- public MATRIX JSON must not publish machine-local absolute paths

## Integrity work in this candidate

- Shared releasable-file manifest (`git ls-files --cached --others --exclude-standard` plus a deny overlay) for the source ZIP and `source_tree_sha256`
- MCP 2026 required `_meta.io.modelcontextprotocol/protocolVersion` and `clientCapabilities` on every request
- 2026 `tools/call` human gates return `resultType=input_required`; legacy `elicitation/create` is unchanged for 2025 hosts
- `server/discover` instructions are provider-neutral with `cacheScope=public`
- stdio newline JSON-RPC smoke for 2026 and 2025
- MATRIX runner: temp workspace, certification-mode `max_infrastructure_attempts=1`, observed MCP protocols, source identity fields

## What this RC does not claim

- Cursor and Codex are **supported 1.0 target providers**. They are **not** live-certified for 1.0 while `MATRIX_1.0` is not PASS.
- DynosAI **supports** MCP revisions `2026-07-28`, `2025-11-25`, and `2025-06-18`. That is protocol compatibility, not a claim that a given host negotiated 2026.
- Official `@modelcontextprotocol/conformance server --url` is HTTP-oriented and is **not** recorded as PASS for stdio.
- Roadmap 1.1 Secure Runtimes, 1.2 Agent Plugins, 1.3 ACP Governance Gateway, 1.4 Long-Horizon Governed Operations, and 1.5 Eval/Data Flywheel stay out of this candidate.
- Remaining on `1.0.0rc7` until MATRIX_1.0 is green is an honest successful outcome. Live defects found after this candidate become RC8; the gate is not lowered.

## Compatibility contract (unchanged)

- Schema v6
- 31 frozen public MCP names
- Git source authority, `knowledge.db` workflow authority, human gates
- Predictive Router remains shadow-only
- No fake OS sandbox
