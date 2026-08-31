# Architecture

## Design principles

1. **Local-first authority.** Workflow state is local and persistent.
2. **Git is code truth.** Agent statements never replace repository evidence.
3. **Evidence before completion.** Work is not complete because a model says it is complete.
4. **Human governance at consequential boundaries.** Specs, plans, scope, code, and merge can require explicit approval.
5. **Provider-neutral Core.** Cursor/Codex integrations and graphical clients adapt to one workflow model instead of defining separate products.
6. **Context is a budget, not a reason to buy a larger model.** Compact/retrieve/reuse before escalation.
7. **Failure attribution matters.** Tool, provider, validation precondition, governance, and model failures are not interchangeable.

## Component view

```text
Coding Agent / CLI / Local Studio
      |
      | MCP / App Server / application adapters
      v
+-------------------------------+
|         DynosAI Core          |
|                               |
| Workflow engine               |
| Artifact manager              |
| Scope / path policy           |
| Git manager + guard           |
| Validation + quality          |
| Retrieval + semantic index    |
| Model control                 |
| Human interactions            |
| Runtime / provider adapters   |
| ExecutionRuntime (local)      |
| Context handles               |
| Team scheduler (leases)       |
| Eval intelligence             |
| Audit / token telemetry       |
+-------------------------------+
      |                  |
      v                  v
 SQLite knowledge.db     Git repository
```

## Authority boundaries

### SQLite

`Database` is authoritative for work items, artifacts, tasks, runs, gates, scopes, validations, evidence, audit records, migrations, and persisted feature knowledge.

### Git

`GitManager` and `GitGuard` observe and govern the source tree. DynosAI verifies actual file status and diff evidence rather than trusting an agent summary.

### Application API and Local App Server

`application.py` is the provider-neutral application boundary shared by CLI/MCP and graphical clients. `app_server.py` exposes a small loopback-only HTTP/JSON adapter for Local Studio. The browser never reads `.dynosai/knowledge.db` directly.

The App Server is intentionally not a remote administration plane: it accepts only loopback bindings and does not enable cross-origin access.

### MCP

`mcp.py` exposes a deliberately bounded tool surface. Provider-specific transport behavior is handled without changing Core semantics. Codex can use structured-primary results; Cursor currently uses a full-text compatibility representation because its CLI stream does not expose structured content to the model reliably.

### Semantic memory

`semantic.py`, `indexer.py`, and `retrieval.py` provide local embeddings, symbol indexing, test links, feature evidence, and bounded retrieval. Derived indexes can be rebuilt; authoritative workflow state is not delegated to the embedding store.

## Workflow state machine

The main order is:

```text
inbox
-> discovery
-> spec_review
-> plan_review
-> ready
-> implementing
-> code_review
-> validating
-> ready_to_merge
-> done
```

Human gates and scope requests are persisted interactions, not transient chat messages.

## Execution waves

An execution wave is a conservative bounded chain of dependent tasks that may be edited and validated in one model cycle. Dependency order remains authoritative. Independent ready tasks are not silently grouped.

## Governed team scheduling

`team_scheduler.py` turns the approved task DAG into serial or parallel **team waves**. A parallel wave is allowed only when planned file sets are disjoint. Each lease carries a scope ceiling, evidence/validation contracts, and token/time budgets. DynosAI does not spawn extra provider processes: a second worker exists only if the host opens another governed session against an open lease. Reviewer is the human code-review gate; tester is the validation contract on the same lease. Fan-in is wave-scoped so sequential reuse of a path is not treated as a silent merge conflict.

Leases are derived from existing `tasks.claimed_run` / runs. Schema remains v6.

## Eval intelligence

`eval_intelligence.py` attributes failures to a layer before they become eval cases. It mines local validations, audit events, eval records and model-control traces into bounded runtime files. Improvement work is created in inbox only. Predictive routing remains shadow. Live-provider quality claims are out of scope.

## State recovery

`StateManager` creates portable authoritative snapshots and SQLite backups. Restore happens into a temporary database, applies migrations, verifies SQLite integrity/foreign keys, and only then atomically replaces the live database.

## Model-control boundary

Model control evaluates phase, complexity, live phase token pressure, validated failures, scope risk, tool-loop signals, and historical outcomes. Context pressure triggers context-management actions before any model-capability transition. Predictive routing remains advisory in 0.18.0.

## ExecutionRuntime

`execution_runtime.py` is the hands boundary: filesystem, processes, network **decisions**, and dependency classification. The local runtime is the default. Provider transports (Cursor ACP, Codex app-server) stay in CLI/MCP adapters. Durable session/workflow state stays in knowledge.db.

## Execution profiles

`execution_profiles.py` binds Strict, Balanced or Autonomous policy onto the local runtime. Human gates stay required. Docker/VM/remote backends are refused. OS-level child-process network interception is not shipped; evidence records `enforcement: decision_only`. An optional vault may materialize secrets to the local process only.

## Context handles

Large diffs, search results and validation output can be stored as typed handles under `.dynosai/runtime/context-handles/` and retrieved with `dynosai_retrieve_handle`. This does not replace knowledge.db.

## Validation Integrity

`validation_integrity.py` connects requirements, acceptance criteria, tasks, evidence and validations. Passing tests are not automatically sufficient proof, especially when the only tests were authored in the same implementation. Reports are shown on Studio code/merge review; they do not silently rewrite `register_result` in 0.15.

For code ownership by module, see [Module reference](reference/MODULES.md).


## Project intelligence boundary

0.14.0 adds two deterministic advisory services outside the workflow state machine:

- `validation_discovery.py` reads project configuration and proposes safe validation profile commands. Discovery never executes or approves a command; explicit approval persists the profile through the application layer.
- `risk.py` scores review risk from repository/plan surfaces such as authentication, migrations, dependency manifests, CI, blast radius and unresolved scope. Risk is advisory and never grants authority or bypasses a gate.
