# Provider Integration

DynosAI keeps provider-specific behavior at the integration boundary while preserving one Core workflow.

## Codex

Codex is supported by the managed MCP/runtime path and was part of the final 0.13.0 greenfield and brownfield acceptance matrix.

For compatible Codex tool results, DynosAI can use **structured-primary transport**: the authoritative object is returned in MCP `structuredContent` while `content.text` is kept compact. This reduces duplicate model-visible transport when the provider exposes structured content correctly.

## Cursor

Cursor is also supported by the managed MCP/runtime path and passed both final 0.13.0 beta scenarios.

The accepted Cursor CLI behavior did not reliably expose MCP `structuredContent` to the model. DynosAI therefore uses **full-text compatibility transport** for Cursor so validation errors and complete contracts remain visible to the model.

## Why transport differs

Provider compatibility is treated as an observed capability, not an assumption. DynosAI does not force a byte-saving optimization on a provider when doing so hides authoritative workflow information.

## Studio / Cursor ACP

Studio 0.14.1 already uses Cursor ACP (`agent acp`) for custom-client turns, including DynosAI MCP injection and process-tree shutdown. Codex uses app-server. 0.19 remaining work is additional clients/providers, not inventing ACP from scratch.

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
