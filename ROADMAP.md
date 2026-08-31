# DynosAI Roadmap

DynosAI is evolving from a stable governed Spec-Driven Development core into a **local-first control plane for agentic software development**.

The roadmap follows one rule: new autonomy must preserve the existing authority boundaries. Git remains authoritative for source, `.dynosai/knowledge.db` remains authoritative for workflow state, and coding agents remain workers inside governed scope rather than owners of project truth.

## 0.14.x — Control Plane & Studio

**Goal:** make the existing governed core usable without requiring users to learn the full CLI first.

Delivered in 0.14.0 and 0.14.1 (stable baseline):

- local-only App Server on `DynosAIApplication`;
- guided Local Studio (project hub, reviews, checks, themes, EN/ES, Fibonacci walkthrough);
- Cursor ACP (`agent acp`) and Codex app-server headless transports;
- cross-platform provider process-tree shutdown, stdin EOF, Windows `acp-sessions` lifecycle;
- validation discovery, Risk Assessment v1, Review Center with spec/plan contract content.

0.14.x follow-ups that are **not** required to start 0.15 (installer/UX polish):

- provider setup/doctor UX inside Studio;
- installer/user-environment hardening across Windows, macOS and Linux;
- additional validation discovery profiles and monorepo-aware selection;
- characterization-driven decomposition of orchestration hotspots.

## 0.15 — Verified Agent Harness

**Goal:** make longer, more complex provider work possible while intent, authority, evidence, security and completion stay in deterministic systems outside the model.

DynosAI is not a replacement for Codex, Cursor or future coding agents. It is the governed harness around them.

Delivered in 0.15.0:

- persistent typed context handles and `dynosai_retrieve_handle` (reference → selective retrieval);
- typed harness contracts for execution/recovery/completion state;
- `ExecutionRuntime` / `LocalExecutionRuntime` (brain / hands / session split; local default, no Docker required);
- minimum execution policy: path roots/escape/symlink, process timeout, network profile API, dependency install vs use, secret redaction and secret-broker refuse-closed;
- Validation Integrity (requirement → acceptance → evidence → validation) exposed on code/merge review;
- Eval Registry v0 plus skill on/off context-size comparison;
- cost-per-successful-governed-change raw observability;
- mechanical architecture import checks;
- Studio completion reviews that can expand diff, validation and integrity without dumping internals by default.

Still incomplete inside 0.15 (do not advertise as done):

- OS-level network interception for child processes (policy API exists; sandbox enforcement is 0.18);
- configured secret vault materialization for runtimes;
- live-provider eval quality claims (v0 is offline/fixture measurement);
- a fully autonomous long-running loop with all budgets always enforced at the workflow layer (checkpoints, token budgets and stagnation signals already exist from 0.12–0.14; bounded replanning is not silent scope expansion).

No general multi-agent execution in 0.15.

## 0.16 — Governed Agent Teams

**Goal:** parallelize work only when the approved plan proves that work can be isolated safely.

Prerequisite: single-agent execution is measurable, resumable, contained and independently verifiable.

Planned capabilities:

- Plan DAG → execution-wave/parallel-team scheduling;
- isolated worktrees and task leases;
- scope ceilings per worker;
- implementer, reviewer and tester roles activated by task/risk needs;
- evidence and validation contracts per worker;
- conflict-aware fan-in and merge gates;
- token/time budgets per worker and per team.

DynosAI will not treat an unconstrained agent swarm as a product feature. Multi-agent is a scheduling problem before it is a prompting problem.

## 0.17 — Eval Intelligence

**Goal:** turn real failures into durable quality improvements.

0.15 moved only Eval Registry v0 and skill-size comparison here. Remaining:

- broader scenario families including live-provider and multi-agent cases;
- failure attribution across model, harness, provider, project and infrastructure;
- production/acceptance trace mining into bounded regression cases;
- learning loop from failure → eval → improvement task → regression evidence.

Predictive routing remains shadow-only until this evidence shows autonomous decisions improve cost/quality without unsafe downshifts.

## 0.18 — Secure Autonomous Runtime

**Goal:** allow longer autonomous work while placing authoritative security policy outside the model/harness.

0.15 introduced the `ExecutionRuntime` interface, local default, filesystem/process policy, network **decision** profiles, dependency classification and a refuse-closed secret broker. Remaining:

- container/VM/remote runtime implementations;
- OS-level network enforcement for child processes;
- credential brokering that can materialize secrets to a runtime without model context;
- `Strict`, `Balanced` and `Autonomous` execution profiles;
- policy evidence in review and certification bundles.

## 0.19 — Ecosystem & Interoperability

**Goal:** keep DynosAI provider-neutral while making the governed core available in more clients and stacks.

**Baseline already shipped:** Cursor ACP is implemented in 0.14.1 Studio (`agent acp`, MCP injection, permission handling, process-tree shutdown). Codex uses app-server. Do not treat ACP as a greenfield invention.

Remaining:

- broader ACP/application adapters for additional clients;
- provider capability manifests;
- certification for additional coding-agent runtimes after they meet the Codex/Cursor bar;
- Skills/validation pack ecosystem;
- extension APIs for project-specific quality and policy integrations.

## 1.0 — Stable Agentic Development Control Plane

**Goal:** publish stable contracts suitable for broader production adoption.

Exit criteria include:

- stable application/CLI/MCP contracts and documented compatibility guarantees;
- desktop/local installer path with upgrade policy;
- multi-platform CI and installed-package validation;
- published provider/capability certification matrix;
- mature Eval Registry and release-quality evidence;
- security/runtime policy model documented and regression-tested;
- contribution and extension model ready for external ecosystem growth.

## Product principles

Across every release:

1. **Specs are contracts, not prompt decoration.**
2. **Evidence determines completion.**
3. **Git and durable project state outrank chat history.**
4. **Human authority is explicit at material gates.**
5. **Context is a budget.** Prefer retrieval/references over repeated serialization.
6. **Multi-agent is a scheduling problem before it is a prompting problem.**
7. **Evals are part of product development, not only release testing.**
8. **Security policy must not depend on the model choosing to obey it.**
9. **A feature is not finished when only the Core supports it.** User-visible capabilities must update Studio, website, documentation and tests as applicable.
10. **Harness features are hypotheses.** Independently disable components that stop helping.
