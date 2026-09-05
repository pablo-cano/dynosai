# Code Quality Review

Metrics below were generated from the `1.0.0-rc.7` source tree. Do not treat
0.13/0.14 historical counts as current.

## Current scale

Generated from `src/dynosai_flow/*.py` and `tests/test_*.py` on 2026-09-04
after the RC7 certification-integrity work:

- 64 Python modules in `src/dynosai_flow`.
- 23,327 source lines.
- 71 test modules and 11,487 test lines.
- Stable suite: 563 passed (`python scripts/release_gate.py`).
- SQLite schema v6.
- Frozen unique MCP names: 31.
- Supported MCP revisions: `2026-07-28`, `2025-11-25`, `2025-06-18`.

Largest production modules by LOC (hotspots, not a hit list):

| Module | LOC |
|---|---|
| `engine.py` | 1,649 |
| `acceptance.py` | 1,478 |
| `mcp.py` | 1,281 |
| `token_usage.py` | 1,052 |
| `debug.py` | 1,031 |
| `cli.py` | 919 |

## Strengths

### Clear authority model

The architecture separates workflow authority (SQLite), code truth (Git), and
transient provider reasoning. This reduces reliance on unstructured chat state.

### Strong evidence boundaries

Scope, Git diff, validation, human gates, provider routing, and retry evidence
are persisted and testable. Success does not automatically rewrite historical
failure attribution. MATRIX_1.0 trials are append-only.

### Failure hygiene

The code explicitly distinguishes model failures from provider/tool/
orchestration/validation-precondition conditions.

### Protocol eras are explicit

MCP 2026-07-28 is shaped in `mcp_protocol_2026.py`. Cursor/Codex 2025
initialize behaviour stays in `mcp_protocol_legacy.py`. `mcp.py` remains the
stdio server and tool dispatcher.

## Maintainability hotspots

These modules accumulated stable orchestration behaviour. They are tested.
They are **not** being split in RC7 except for the MCP protocol-era and
human-gate transport extraction required by the current spec.

Future modularization, only after characterization tests exist for the
boundary being extracted:

1. `engine.py`
2. `acceptance.py`
3. `mcp.py`
4. `token_usage.py`
5. `debug.py`
6. `cli.py`

Extract real conceptual services (protocol adapters, certification evidence,
execution runtime, eval/release/telemetry). Do not extract functions solely to
reduce LOC. Do not impose a project-wide formatter rewrite.

## Deliberately deferred

- splitting the remaining orchestration hotspots
- converting compact legacy statements into a formatting rewrite
- activating Predictive Router authority
- a mandatory independent second-model reviewer
- plugin runtime / marketplace
- OS sandbox, Docker/VM/remote execution
