# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano

# Decision: MCP protocol eras (2026-07-28 + legacy)

- Status: Accepted
- Date: 2026-09-04
- Release: 1.0.0-rc.6
- Spec consulted: https://modelcontextprotocol.io/specification/2026-07-28

## Context

Certified Studio hosts (Cursor ACP, Codex app-server) currently speak MCP
`2025-11-25` with a stateful `initialize` handshake and `elicitation/create`.
MCP revision `2026-07-28` is stateless: `server/discover`, per-request `_meta`
protocol version, deterministic `tools/list` ordering, list cache metadata,
and `resultType` envelopes. The official conformance runner talks HTTP
(`--url`), not stdio.

DynosAI remains a stdio MCP server. Remote Streamable HTTP, Tasks and Apps
are out of scope for RC6.

## Decision

Keep one `MCPServer` and split only protocol-era shaping:

```text
mcp.py                  # tools, dispatch, elicitation, stdio serve
mcp_protocol.py         # versions, `_meta` keys, errors
mcp_protocol_legacy.py  # 2025-11-25 / 2025-06-18 initialize + list/call shape
mcp_protocol_2026.py    # discover, stateless list/call envelopes
```

Supported versions, in advertisement order:

```text
2026-07-28
2025-11-25
2025-06-18
```

Rules:

- Legacy clients keep `initialize` / `notifications/initialized`, current
  Cursor/Codex tool-call and elicitation behaviour, and `-32002` when no
  protocol has been negotiated.
- `2026-07-28` clients may omit `initialize`. `server/discover` and
  `tools/list` / `tools/call` with
  `params._meta["io.modelcontextprotocol/protocolVersion"]` are enough.
- Unsupported versions return JSON-RPC `-32022` with `data.supported`.
- Human gates for 2025 hosts stay `elicitation/create` (form mode). RC7 adds
  2026 Multi Round-Trip `input_required` for the same DynosAI DB authority.
  Client `inputResponses` / `requestState` are never the source of authority.
- Exactly 31 frozen public MCP names. No Tasks, Apps, resources, sampling or
  remote HTTP transport.
- Supported protocol revisions are recorded separately from the protocol a
  provider actually negotiated on the wire (MATRIX observed protocols).

Official `@modelcontextprotocol/conformance server --url` is HTTP-oriented.
RC6/RC7 conformance evidence is the in-repo characterization suite plus an honest
record if the official HTTP runner is not applicable to stdio.

## RC7 amendment (2026-09-04)

- Every 2026 request must carry `_meta` protocolVersion and clientCapabilities.
  A later request without those fields fails; session negotiation is not reused.
- `clientCapabilities: {}` replaces prior optional capabilities for that request.
- `initialize` / `ping` with 2026-07-28 are rejected (`-32601`). 2025 handshake
  remains frozen.
- `server/discover` instructions are provider-neutral (`cacheScope=public`).
