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

### RC1 — Contract freeze

Delivered in `1.0.0-rc.1`:

- `docs/COMPATIBILITY.md` and a frozen unique MCP name set (31), regression-tested;
- optional harness switches reuse `DYNOSAI_HARNESS_*` (context handles, team scheduling, eval intelligence) with a safe degraded path;
- host-owned project settings for those switches; MCP agents cannot mutate them;
- one explicit release-gate policy shared by CI, `scripts/build_release.py` and local `pytest`;
- public status **Public RC · Contract freeze**. Schema remains v6.

This RC does **not** ship a production-ready 1.0, installer, live 1.0 certification matrix, OS-level sandbox, additional certified clients, or autonomous predictive routing.

### RC2 — Certification evidence

Delivered in `1.0.0-rc.2`:

- versioned `MATRIX_1.0` with Codex/Cursor × greenfield/brownfield cells, all `not_run`;
- historical 0.13 evidence preserved and not copied into 1.0 live cells;
- two host-opened governed session identities claiming file-disjoint leases without provider spawn;
- overlap serialization, fan-in conflict blocking, and scheduler-off serial fallback.

This RC still does **not** contain a real 1.0 live provider matrix, installer, OS sandbox, extra certified clients, or autonomous predictive routing.

### RC3 — Eval maturity (this release)

Delivered in `1.0.0-rc.3`:

- acceptance ZIP importer into bounded Eval Registry cases; inbox-only improvement; no provider spawn;
- promoted `governed_change_cost()` with completed-work aggregates on stats, scorecard, overview and Studio;
- stable authority prefix hash without dumping the MCP tool list or claiming a cache hit.

This RC still does **not** contain a real 1.0 live provider matrix, installer, OS sandbox, extra certified clients, or autonomous predictive routing.

### Remaining 1.0 exit criteria

- desktop/local installer path with upgrade policy;
- multi-platform installed-package validation;
- published provider/capability certification matrix from real Codex/Cursor greenfield and brownfield runs;
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
