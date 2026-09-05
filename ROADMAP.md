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

- OS-level network interception for child processes (policy API exists; 0.18 still records decision-only);
- live-provider eval quality claims (v0 is offline/fixture measurement);
- a fully autonomous long-running loop with all budgets always enforced at the workflow layer (checkpoints, token budgets and stagnation signals already exist from 0.12–0.14; bounded replanning is not silent scope expansion).

0.18 ships named execution profiles and runtime-only vault materialization on top of this harness. It does not complete OS-level sandboxing or container runtimes.

No general multi-agent execution in 0.15.

## 0.16 — Governed Agent Teams

**Goal:** parallelize work only when the approved plan proves that work can be isolated safely.

Prerequisite: single-agent execution is measurable, resumable, contained and independently verifiable.

Delivered in 0.16.0:

- Plan DAG → serial/parallel waves with file-disjoint leases;
- scope ceilings, evidence and validation contracts per lease;
- implementer as the claimable worker role; reviewer = human `code_review` gate; tester = governed validation on the same lease;
- conflict-aware, wave-scoped fan-in;
- token/time budgets attached to each lease;
- Studio Work slots and `dynosai_schedule` fan-in without spawning extra providers.

Still incomplete inside 0.16 (do not advertise as done):

- host-launched N Codex/Cursor processes (a second worker exists only if the host opens another governed session);
- specialist reviewer/tester as extra spawned agents;
- a dedicated lease authority table (schema remains v6; leases are derived from `tasks.claimed_run` / runs).

DynosAI will not treat an unconstrained agent swarm as a product feature. Multi-agent is a scheduling problem before it is a prompting problem.

## 0.17 — Eval Intelligence

**Goal:** turn real failures into durable quality improvements.

Delivered in 0.17.0:

- failure attribution across model, harness, provider, project, infrastructure and governance;
- mining of local validations, audit, eval records and model-control traces into bounded regression cases;
- learning loop: attributed failure → eval case → inbox improvement work → offline regression evidence;
- Studio Overview cases and `dynosai_stats` summary;
- offline `team_overlap` / `mined_regression` fixtures.

Still incomplete inside 0.17 (do not advertise as done):

- live-provider eval quality claims or leaderboards;
- autonomous enablement of predictive routing;
- mining of production/acceptance zip bundles into the registry as a first-class importer.

Predictive routing remains shadow-only until this evidence shows autonomous decisions improve cost/quality without unsafe downshifts.

## 0.18 — Secure Autonomous Runtime

**Goal:** allow longer autonomous work while placing authoritative security policy outside the model/harness.

Delivered in 0.18.0:

- `Strict`, `Balanced` and `Autonomous` execution profiles (host-owned; human gates still required);
- runtime-only credential materialization from a project vault to the local process;
- policy evidence on Studio reviews, overview, stats and diagnostic bundles.

Still incomplete inside 0.18 (do not advertise as done):

- container/VM/remote runtime implementations (requesting them raises a policy error);
- OS-level network enforcement for child processes (local network policy remains decision-only).

## 0.19 — Ecosystem & Interoperability

**Goal:** keep DynosAI provider-neutral while making the governed core available in more clients and stacks.

**Baseline already shipped:** Cursor ACP is implemented in 0.14.1 Studio (`agent acp`, MCP injection, permission handling, process-tree shutdown). Codex uses app-server. Do not treat ACP as a greenfield invention.

Delivered in 0.19.0:

- host-owned capability manifests for Cursor ACP and Codex app-server;
- refusal of uncertified clients and project extension packs;
- Studio Overview inventory plus stats/diagnostic evidence;
- no new MCP tools.

Still incomplete inside 0.19 (do not advertise as done):

- broader ACP/application adapters for additional clients;
- certification for additional coding-agent runtimes after they meet the Codex/Cursor bar;
- Skills/validation pack ecosystem;
- loadable extension APIs for project-specific quality and policy integrations.

## 1.0 — Stable Agentic Development Control Plane

**Goal:** publish stable contracts suitable for broader production adoption.

DynosAI is a **local-first governed control plane for agentic software development**.
Coding agents are workers. DynosAI governs intention and contracts, scope, Git,
durable workflow state, permissions, leases, budgets, validation, human gates,
evidence, evals, provider certification, recovery, and cost/quality attribution.

It is not another coding agent, prompt framework, SDD-only framework, swarm
orchestrator, proprietary skill format, or IDE.

### RC1 — Contract freeze

Delivered in `1.0.0-rc.1`: frozen MCP names (31), optional harness switches,
schema v6, one documented release-gate policy.

### RC2 — Certification evidence

Delivered in `1.0.0-rc.2`: versioned `MATRIX_1.0` placeholders; two-session
file-disjoint leases. Historical 0.13 evidence stays historical.

### RC3 — Eval maturity

Delivered in `1.0.0-rc.3`: acceptance ZIP importer, governed-change cost
aggregates, stable authority prefix.

### RC4 — Installation

Delivered in `1.0.0-rc.4`: Studio doctor, verified local-wheel checksums,
installed-package CI, 0.19.0 schema-v6 upgrade preservation.

### RC5 — Freeze

Delivered in `1.0.0-rc.5`: threat model, contribution/certification process,
loopback honesty. External PRs remain closed.

### 1.0.0-rc.6 — Standards & Release Evidence

Delivered in `1.0.0-rc.6`: MCP `2026-07-28` compatibility with backwards
compatibility for `2025-11-25` and `2025-06-18`, unified release gate, live
`MATRIX_1.0` runner with explicit trials, eval corpus, distribution ADR.
Schema remains v6; 31 frozen MCP names; Predictive Router stays shadow-only.
MATRIX_1.0 was not PASS.

### 1.0.0-rc.7 — Certification Integrity & Protocol Correctness (this candidate)

Must-have before spending tokens on MATRIX attempt 3. No 1.1–1.5 features.

- release archive safety (gitignore-aware source manifest)
- MCP 2026 strict per-request metadata and capability replacement
- MCP lifecycle by era (2026 does not use legacy initialize/ping)
- MCP 2026 MRTR `input_required` for DynosAI human gates
- MATRIX workspace outside the repo; one provider execution per trial
- observed MCP protocol vs supported revisions
- source identity fingerprint for dirty candidates
- public MATRIX path privacy

### 1.0.0-rc.8 — Contingency only

No features are planned. RC8 exists only if RC7 live certification discovers
defects that require another candidate. Do not lower the gate to avoid RC8.

### 1.0.0 — Stable Governance Core

A promotion with **minimal delta** from the accepted RC. No features during
promotion. Stable requires:

- release gate green
- website gate green if the site changed
- installed-wheel / install smoke green
- provider Tier-A matrix green
- no dishonest or `not_run` stable claims
- current MCP compatibility decision complete
- threat model still honest
- clean upgrade path
- release quality evidence
- all public version/status surfaces coherent

## 1.1 — Enforced Secure Runtimes

Move important controls from logical decisions to real enforcement. Design a
stable runtime backend interface, for example:

```text
ExecutionRuntime
    LocalExecutionRuntime
    SandboxedLocalRuntime
    OpenShellRuntime
    ContainerRuntime
```

Investigate existing implementations before building a sandbox. Needs:
filesystem policy enforcement, network enforcement, credential brokering,
process-tree policy, timeout/resource ceilings, runtime capability manifest,
auditable policy decisions, no silent fallback from a strict runtime, and a
common conformance suite per backend.

Stay local-first. Do not make an external dependency mandatory for Core.

## 1.2 — Portable Agent Plugins + DynosAI Governance Extensions

Implement Agent Plugins compatibility (`plugin.json`, `skills/`, `mcp.json`,
`extensions.com.dynosai`). First iteration: local directory and Git URL /
immutable revision. No marketplace.

Capabilities: local discovery, manifest validation, path containment, skills,
MCP config, namespaced DynosAI extension, host-owned enable/disable, trust
metadata, hashes/versioning, project compatibility, eval fixtures,
validators/policies. A plugin must not add authority silently. Each
plugin/skill must be measurable enabled vs disabled for quality, tokens, cost,
latency and regressions.

## 1.3 — ACP Governance Gateway

Investigate DynosAI as an agent endpoint/governance layer consumed by ACP
clients:

```text
IDE / Client → ACP → DynosAI governance → underlying coding agent
```

Investigate the current ACP specification, registry, capability negotiation,
session lifecycle, blocking user interactions, permissions and
provider-native extensions. Do not certify a client merely because it
connects. Each new client needs a capability manifest, conformance,
greenfield, brownfield, refusal, human-gate and recovery tests.

## 1.4 — Long-Horizon Governed Operations

Do not build a generic swarm. Use provider-native workers/subagents where they
exist. DynosAI governs work, scope, lease, budget, context, evidence,
checkpoint, validation, human gate, fan-in and merge authority.

Add when safe: durable background queue, pause/resume, checkpoints, provider
quota pause state, budget/deadline ceilings, parked `needs_review` jobs,
scheduled maintenance, CI-triggered governed work. Background/autonomous must
never mean auto-merge.

## 1.5 — Eval/Data Flywheel & Adaptive Routing

```text
real failure → trace → bounded eval → reproduce → compare
→ recommendation → shadow → validated promotion
```

Expand failure attribution. Add eval versioning, environment fingerprints,
multiple trials, confidence intervals where useful, broken/ambiguous-case
review, provider/model/skill/runtime comparisons, cost per successful
governed change, latency, human-intervention rate, false-completion rate and
scope-violation rate.

Predictive Router: shadow → recommendation only → opt-in bounded automatic
routing. Never shadow → full autonomous authority without evidence. No silent
quality downshift to save cost.

## Continuous architecture health

From 1.0, avoid accumulating large monolithic controllers. Do not impose a
mass formatter/refactor. Create characterization tests around each hotspot
before extracting services. Approximate future order: `engine.py`,
`acceptance.py`, `mcp.py`, `token_usage.py`, `debug.py`, `cli.py`. Extract
real conceptual boundaries (protocol adapters, certification evidence,
execution runtime, eval services, release services, telemetry), not functions
whose only purpose is a lower LOC count.

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
