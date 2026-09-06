# DynosAI 1.0.0-rc.9

Package version: `1.0.0rc9` (PEP 440). Display: `1.0.0-rc.9`.
Public status: **RC9 · certification pending**. Schema remains v6.

This candidate is **not** stable 1.0. `MATRIX_1.0` is **not** declared PASS.
This candidate is **not** live-certified.

## Why RC9 exists

RC8 verified provider-safe local runtime placement but revealed that
Codex App Server still did not start or expose the managed DynosAI MCP
server.

RC8 live `codex.greenfield` attempt 4 started Codex (`provider_exit=0`) with
workspace and `CODEX_HOME` outside OS temp, then recorded `mcp_calls=0` and no
`mcp-activity.jsonl`. That is not a Fibonacci/model failure.

RC8 `codex --version` only proved the CLI could start with the managed home.
It did not prove App Server used that home, loaded `config.toml`, saw
`mcp_servers.dynosai`, started DynosAI, or could call a DynosAI tool.

RC9 adds deterministic App Server MCP introspection and a no-model-turn
preflight so configuration, startup, tool discovery and direct MCP calls
must succeed before provider tokens can be consumed.

## Integrity work in this candidate

- Codex App Server preflight: `initialize`, `config/read`,
  `mcpServerStatus/list`, `mcpServer/startupStatus/updated`, and a read-only
  `mcpServer/tool/call` (`dynosai_get_next_action` with `execute=false`).
- Hard gate in `scripts/run_matrix_1_0.py --live --cells codex.*`: preflight
  FAIL returns exit 2, starts no `turn/start`, consumes 0 model tokens, and
  does not append a MATRIX trial / increment the live attempt counter.
- Opaque client `_meta` (progress tokens / vendor keys) no longer aborts a
  negotiated MCP `2025-11-25` / `2025-06-18` session. MCP 2026 still requires
  per-request `io.modelcontextprotocol/protocolVersion` and capabilities.
- Evidence files: `codex-mcp-preflight.json`,
  `codex-mcp-preflight-app-server.jsonl`, `codex-mcp-preflight-stderr.log`
  (secrets redacted; public/portable reports omit absolute paths).

## Observed Codex App Server behavior

`mcpServerStatus/list` without `threadId` lists configured servers with
`runtimeStatus=null`. DynosAI therefore starts a thread (never a turn) so
startup status materializes.

Codex listed 15 DynosAI tools on the acceptance profile during the no-token
preflight. The public contract remains 31 names. The preflight requires at
least one unambiguous DynosAI tool, not a count of 31.

`features.plugins`, `features.apps` and `features.recommended_plugins` stayed
false. Effective `config/read` still contained `[mcp_servers.dynosai]`. Those
flags were not the RC8 MCP absence.

## What this RC does not claim

- Cursor and Codex are **supported 1.0 target providers**. They are **not**
  live-certified for 1.0 while `MATRIX_1.0` is not PASS.
- Passing the no-token App Server MCP preflight is a prerequisite for a later
  live Codex cell. It is not that live cell.
- Roadmap 1.1–1.5 stay out of this candidate.
- Remaining on `1.0.0rc9` until MATRIX_1.0 is green is an honest successful outcome.

## Compatibility contract (unchanged)

- Schema v6
- 31 frozen public MCP names
- Git source authority, `knowledge.db` workflow authority, human gates
- Predictive Router remains shadow-only
- No fake OS sandbox
- MCP support for `2026-07-28`, `2025-11-25` and `2025-06-18`

## Candidate identity

Canonical candidate:

```text
dynosai_git_commit
+
certification_subject_sha256
```

`candidate_version` is a human label (`1.0.0-rc.9`). Release gates do not require
the same attempt number across cells.

RC8 live evidence remains historical: commit
`181624b8df6eab3224fbfc23c44ee27b4cacef0d`, subject
`b433bd5252f6c0343e2b246e04d43b72b528871ad30f01b3886c089041e60120`,
`codex.greenfield` attempt 4 FAIL.
