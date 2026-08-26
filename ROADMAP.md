# DynosAI Roadmap

DynosAI is evolving from a stable governed Spec-Driven Development core into a **local-first control plane for agentic software development**.

The roadmap follows one rule: new autonomy must preserve the existing authority boundaries. Git remains authoritative for source, `.dynosai/knowledge.db` remains authoritative for workflow state, and coding agents remain workers inside governed scope rather than owners of project truth.

## 0.14.x — Control Plane & Studio

**Goal:** make the existing governed core usable without requiring users to learn the full CLI first.

Delivered in 0.14.0:

- local-only App Server built on `DynosAIApplication`;
- `dynosai studio` graphical control plane served on loopback;
- stack-aware project detection and onboarding hints;
- validation auto-discovery with explicit approval before persistence;
- deterministic Risk Assessment v1;
- user-facing blocker explanations;
- provider-neutral project overview/event APIs for graphical clients;
- public website pages for Local Studio and the product roadmap.

0.14.x follow-up priorities:

- richer specification/plan/review surfaces in Studio;
- requirement → task → evidence → code trace visualization;
- provider setup/doctor UX inside Studio;
- installer/user-environment hardening across Windows, macOS and Linux;
- additional validation discovery profiles and monorepo-aware selection;
- characterization-driven decomposition of orchestration hotspots.

## 0.15 — Harness & Loop Engine v2

**Goal:** improve reliability and efficiency for long-running work without buying correctness through larger prompts.

Planned research/delivery:

- persistent context handles and pass-by-reference artifacts;
- bounded previews for large evidence/tool results;
- long-running work loop with checkpoints, stagnation detection and bounded replanning;
- Skills contract with progressive disclosure;
- Skill Evals comparing behavior with/without a skill;
- risk-aware review/governance profiles;
- improved context/cache/cost accounting;
- `ExecutionRuntime` interface introduced while retaining the local runtime as default.

## 0.16 — Governed Agent Teams

**Goal:** parallelize work only when the approved plan proves that work can be isolated safely.

Planned capabilities:

- Plan DAG → execution-wave/parallel-team scheduling;
- isolated worktrees and task leases;
- scope ceilings per worker;
- implementer, reviewer and tester roles activated by task/risk needs;
- evidence and validation contracts per worker;
- conflict-aware fan-in and merge gates;
- token/time budgets per worker and per team.

DynosAI will not treat an unconstrained agent swarm as a product feature. Multi-agent authority remains plan-derived and auditable.

## 0.17 — Eval Intelligence

**Goal:** turn real failures into durable quality improvements.

Planned capabilities:

- versioned Eval Registry;
- greenfield, brownfield, migration, bug-fix, refactor, security, recovery and multi-agent scenario families;
- failure attribution across model, harness, provider, project and infrastructure;
- provider/model/harness comparison with quality, tokens, cost, time and human intervention metrics;
- production/acceptance trace mining into bounded regression cases;
- learning loop from failure → eval → improvement task → regression evidence.

Predictive routing remains shadow-only until this evidence shows autonomous decisions improve cost/quality without unsafe downshifts or missed capability escalations.

## 0.18 — Secure Autonomous Runtime

**Goal:** allow longer autonomous work while placing authoritative security policy outside the model/harness.

Planned capabilities:

- pluggable execution runtime;
- filesystem/process/network policy boundaries;
- credential/secret brokering without placing secrets in model context;
- controlled dependency installation and external MCP access;
- `Strict`, `Balanced` and `Autonomous` execution profiles;
- policy evidence included in review and certification bundles.

## 0.19 — Ecosystem & Interoperability

**Goal:** keep DynosAI provider-neutral while making the governed core available in more clients and stacks.

Planned capabilities:

- ACP adapter at the application boundary;
- provider capability manifests;
- broader provider certification, including Claude and additional coding-agent runtimes;
- Skills/validation pack ecosystem;
- extension APIs for project-specific quality and policy integrations;
- stronger static-analysis, security and coverage integrations.

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
