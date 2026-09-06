# Provider Integration

DynosAI keeps provider-specific behavior at the integration boundary while preserving one Core workflow.

Cursor ACP and Codex app-server are **supported 1.0 target providers**. Historical 0.13 matrix evidence is not 1.0 live certification.

## 1.0 support matrix

| Provider | Integration | Greenfield | Brownfield | 1.0 status |
|---|---|---|---|---|
| Codex | App Server + MCP | certification required | certification required | Certification pending |
| Cursor | CLI/ACP + MCP | evidence retained | evidence retained | Preview / not 1.0 certified |

1.0 eligibility requires Core gates plus Codex greenfield and Codex brownfield PASS of the exact candidate. Cursor cells stay in MATRIX_1.0 as Preview evidence. Do not present Cursor as production-certified. A future release may migrate Cursor acceptance toward ACP session continuity; RC10 does not implement that.

## Codex

Codex is supported by the managed MCP/runtime path and was part of the final 0.13.0 greenfield and brownfield acceptance matrix.

Live Codex MATRIX cells now run a no-model-turn App Server MCP preflight first:
`initialize` (managed `CODEX_HOME` must match), `config/read` (effective
`mcp_servers.dynosai`), `mcpServerStatus/list` plus startup notifications, then
a read-only `mcpServer/tool/call`. Failure is `preflight-blocked`: no
`turn/start`, 0 model tokens, and the live attempt counter is not incremented.

RC8 live evidence showed App Server can start while DynosAI MCP still fails.
The RC9 handshake fix is: opaque Codex `_meta` without
`io.modelcontextprotocol/protocolVersion` must not abort a negotiated 2025
session. MCP 2026 `_meta` validation stays strict for 2026 requests.

For compatible Codex tool results, DynosAI can use **structured-primary transport**: the authoritative object is returned in MCP `structuredContent` while `content.text` is kept compact. This reduces duplicate model-visible transport when the provider exposes structured content correctly.

## Cursor

Cursor remains a supported integration and is **Preview for 1.0**. It is not a
1.0 release blocker and is not production-certified. MATRIX cells are retained.

Known RC9 Cursor evidence:

- greenfield: provider/model stopped before governed work creation
- brownfield: internal unable to open database file / ACP reconnects

The accepted Cursor CLI behavior did not reliably expose MCP `structuredContent` to the model. DynosAI therefore uses **full-text compatibility transport** for Cursor so validation errors and complete contracts remain visible to the model.

## Why transport differs

Provider compatibility is treated as an observed capability, not an assumption. DynosAI does not force a byte-saving optimization on a provider when doing so hides authoritative workflow information.

## Studio / Cursor ACP

Studio 0.14.1 already uses Cursor ACP (`agent acp`) for custom-client turns, including DynosAI MCP injection and process-tree shutdown. Codex uses app-server. 0.19 publishes those transports as capability manifests and refuses uncertified clients instead of inventing ACP from scratch.

## Configuration

One-time setup:

```bash
dynosai setup --provider codex
dynosai setup --provider cursor
```

Inspect configuration:

```bash
dynosai agent-config show --provider codex
dynosai agent-config show --provider cursor
```

## Provider-neutral contract

Regardless of provider, the Core still governs:

- work state;
- specification and plan;
- human gates;
- scope;
- Git evidence;
- validations;
- task/result verification;
- persistent memory;
- model-control telemetry.

Provider adapters should not redefine those semantics.
