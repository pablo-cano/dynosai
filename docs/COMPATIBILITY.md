# DynosAI 1.0 compatibility

This document freezes what “stable” means for the DynosAI 1.0 line. It is the
compatibility contract for `1.0.x`. RC1 establishes the contract; later RCs
add certification, eval, installer and threat-model evidence without silently
renaming this surface.

DynosAI’s product identity does not change:

> The coding agent writes code. DynosAI governs scope, workflow state, Git
> evidence, validation, durable memory, human authority and the audit trail.

DynosAI is not a replacement for Cursor or Codex, not an unconstrained
multi-agent swarm, and not an OS sandbox.

## Authority boundaries

| Authority | Owner |
|---|---|
| Source files | Git |
| Governed workflow state | `.dynosai/knowledge.db` |
| Human specification, plan, code-review, scope and merge gates | Human (Studio, CLI review, or MCP elicitation) |
| Execution profile, harness optimizations, auto-approve | Host / Studio, never the MCP agent |
| Certified provider transports | Cursor ACP and Codex app-server |
| Optional harness optimizations | Environment override → project setting → default |

Schema authority remains **v6**. Telemetry, feature flags and harness settings
use existing `meta`, project config, audit events or `.dynosai/runtime/`.

## Stable MCP name contract

MCP tool names are a public compatibility surface for 1.0.x.

- Unique dispatchable names are frozen at **31**.
- 29 names are advertised in `tools/list` (`TOOLS` in `mcp.py`).
- 2 legacy read names remain dispatchable but are not advertised: `dynosai_get_symbol`, `dynosai_get_file_slice`.
- No removals and no silent renames in 1.0.x.
- Additive response fields are allowed.
- Existing required arguments cannot silently become incompatible.
- Typed failures may become more precise without changing authority semantics.
- A disabled optional capability returns a structured `feature_disabled` refusal where refusal is appropriate.
- Phase-aware disclosure may advertise a subset; the unique name set does not grow or shrink.

The exact frozen set is regression-tested in `tests/test_210.py`. That test is
the second source of truth next to `TOOLS` + `LEGACY_TOOLS` in `mcp.py`. Do not
add a hidden frozen list inside application code.

`dynosai_stats` reports `mcp_surface_frozen: true` and `mcp_tool_count`. There
is no extra MCP tool for this.

## Stable public CLI contract

Only the documented user-facing commands are a 1.0 compatibility commitment.
Parser internals, suppressed help entries and acceptance/debug commands are
not.

### Public stable

```text
studio
app-server
setup
project detect | initialize
init
adopt
connect / disconnect
open
start
status
continue
resume
review
board
index
search
ask
context
sync
stats
scorecard
usage
doctor
agent-config init | show | compile | clean | doctor
backup
restore
rollback
schedule
```

`schedule` remains a public command. When the optional team-scheduler harness
feature is off, it returns a structured disabled/serial-fallback result rather
than disappearing.

### Advanced / experimental

```text
model
provider-model
model-control
model-benchmark
benchmark
env
diagnose
```

These exist for host diagnostics, routing inspection and research. Predictive
routing remains shadow-only in 1.0. Their payloads may evolve.

### Debug / acceptance / internal

```text
debug e2e
debug acceptance
debug acceptance-status
debug acceptance-logs
demo
submit-spec
submit-plan
agent-result
```

Suppressed help commands and the acceptance harness are not a third-party
compatibility API. They may change to keep certification honest.

## App Server compatibility scope

The loopback App Server exists so bundled Studio can talk to
`DynosAIApplication`.

For 1.0:

- Studio ↔ App Server compatibility is required.
- Existing routes used by bundled Studio must not break during the RC program.
- Additive JSON fields are allowed.
- The App Server is **not** declared a general third-party public API.
- External stability is limited to contracts that are explicitly documented
  (today: MCP tool names, the public CLI above, schema v6, Git vs knowledge.db).

Studio remains vanilla HTML/CSS/JavaScript. `apps/studio` and
`src/dynosai_flow/studio_assets` must stay byte-identical. `apps/web` remains
the marketing/documentation website.

## Additive-field policy

JSON objects returned by MCP tools, `dynosai_stats`, Studio App Server routes
and CLI JSON may gain fields. Clients must ignore unknown fields. Required
request arguments for frozen MCP tools cannot silently become incompatible.

## Human-gate guarantee

Specification, plan, code-review, scope and merge remain human-governed.

- `studio_auto` / auto-approve is opt-in and **off by default**.
- The Autonomous execution profile does **not** bypass human gates.
- MCP agents cannot modify host-owned execution profiles or harness policy.
- `implementer` is a claimable governed lease. `reviewer` is human code-review
  authority. `tester` is the governed validation contract. DynosAI does not
  spawn extra Cursor or Codex processes to simulate a multi-agent system.

## Certified-provider boundary

The shipped Studio transports are Cursor ACP and Codex app-server. Unknown
clients and extension packs are refused. Additional IDEs are not assumed
certified because they exist.

## Optional harness features

Harness optimizations are hypotheses. They reuse `harness_enabled()` and
`DYNOSAI_HARNESS_*`. There is no parallel `DYNOSAI_DISABLE_*` family.

Canonical precedence:

1. environment override (`DYNOSAI_HARNESS_<FEATURE>`, plus the existing
   `DYNOSAI_CONTEXT_HANDLES` alias);
2. host-owned project setting in knowledge.db `meta`;
3. current default (`enabled=true` for the optional features below).

Optional, independently measurable features in RC1:

| Feature | Default | When off |
|---|---|---|
| `context_handles` | on | New MCP outputs stay inline. Historical handles remain readable through `dynosai_retrieve_handle`. |
| `team_scheduler` | on | No parallel/fan-in planning. `dynosai_schedule` returns `feature_disabled`. Ordinary serial work continues. Already-owned leases remain finishable. |
| `eval_intelligence` | on | Mining/propose UI is unavailable. `/api/eval/propose` returns `feature_disabled`. Historical eval records remain readable. |

Not optional kill-switches:

- human gates
- capability certification / refusal
- path and secret authority
- execution-profile enforcement (`Strict` does not silently fall back to `Balanced`)

`skills`, `retrieval`, `compression`, `replanning`, `reviewer` and `planner`
remain in `HARNESS_COMPONENTS` for eval inventory. They are not product
kill-switches: `reviewer`/`planner` are governance roles, and the others have
no safe independent A/B path beyond existing eval fixtures.

Environment variables are for CI, deterministic evals and diagnostics. Studio
Project settings is the normal-user UX.

## 1.0.x expectations

Compatible 1.0.x releases may:

- add optional response fields;
- add documentation, tests and diagnostics;
- tighten typed refusals without changing who holds authority;
- keep schema v6 unless a later 1.0.x change is impossible without an additive
  migration, which must be tested and documented.

Compatible 1.0.x releases will not:

- remove or silently rename frozen MCP tools;
- make auto-approve default on;
- let MCP agents change execution profiles or harness policy;
- enable autonomous predictive routing;
- introduce Docker/VM/remote execution or OS-level network sandboxing under the
  same 1.0 contract without a documented compatibility break.

## What remains experimental

- Predictive model routing (shadow only; no autonomous spend).
- Live 1.0 certification matrix cells until they are filled by real provider
  runs (`MATRIX_1.0` exists; every cell is currently `not_run`).
- Eval intelligence mining (optional harness feature; not a live-provider
  leaderboard).
- Team parallelism beyond host-opened governed sessions.
- Desktop installer packages beyond the verified local wheel (RC4 ships wheel checksums and installed-package CI; DynosAI is not on PyPI).
- OS-level network interception, Docker, VM and remote runtimes (not shipped).
- Additional certified clients beyond Cursor ACP and Codex app-server.
- Debug/acceptance CLI internals.
- App Server payloads consumed only by bundled Studio.

RC1 does not prove a production-ready 1.0. It freezes the contract so later
RCs can add evidence without moving the public surface. RC2 adds MATRIX_1.0
placeholders and two-session lease proof; it still does not fill live cells.
RC5 documents the threat model in `docs/THREAT_MODEL.md`. Loopback, Host
checks and JSON POSTs are enforced and are not a full security boundary.
OS-level network sandbox, Docker, VM and remote execution remain not shipped.
