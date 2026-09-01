# DynosAI

<img src="assets/dynosai-mark.svg" alt="DynosAI logo" width="76" height="76">

**Governed software development for coding agents.**

DynosAI is a local-first orchestration and governance layer for AI coding agents. Cursor, Codex, or another compatible agent can still inspect and write code, but DynosAI owns the workflow, scope, Git evidence, validation, durable memory, human approval gates, and audit trail.

> The agent writes code. DynosAI governs what may change, what must be proven, and when the work is actually done.

**Current release:** `0.19.0` · **Public beta · Ecosystem & Interoperability**
**License:** MIT
**Author and maintainer:** [Pablo Cano](https://www.linkedin.com/in/pablo-cano-galan/)
**Website:** [https://www.dynosai.com/](https://www.dynosai.com/)
**Source repository:** [github.com/pablo-cano/dynosai](https://github.com/pablo-cano/dynosai)

## Why DynosAI exists

Coding agents are effective at producing code, but a chat transcript is a weak source of truth for long-lived engineering work. A model can forget context, overstate what it changed, skip validation, broaden scope, or lose state when a session restarts.

DynosAI makes the engineering process persistent and evidence-driven:

```text
Need
  -> Discovery
  -> Specification
  -> Human approval
  -> Plan
  -> Human approval
  -> Implementation
  -> Independent evidence checks
  -> Code review gate
  -> Validation
  -> Merge gate
  -> Done
```

The authoritative state lives in `.dynosai/knowledge.db` and Git, not in the provider chat.

## What DynosAI provides

- **Spec-driven workflow** — requirements, acceptance criteria, plans, tasks, decisions, and evidence are linked and persisted.
- **Human gates** — specification, plan, code review, scope extensions, and merge remain explicit decisions.
- **Governed scope** — planned files and actions are compared with the actual Git diff.
- **Independent result verification** — an agent cannot simply declare its own work verified; DynosAI checks repository state and recorded evidence.
- **Validation profiles** — unit, lint, type-check, build, integration, or project-specific commands can be executed as governed checks.
- **Validation auto-discovery** — common Python, Node.js/TypeScript, Rust, .NET and Java checks are proposed from repository configuration and require explicit approval before becoming governed profiles.
- **Guided Studio** — `dynosai studio` opens a project hub with no project selected by default, in-app create/open/recent project management with a contextual folder browser that can create directories, project-scoped workflow/model settings, a contextual Fibonacci walkthrough, themes, English/Spanish UI, and opt-in technical diagnostics. Code/merge approvals can expand requirements, diff, validation and integrity without making internals the default view.
- **Verified harness** — large artifacts can be stored as context handles and retrieved on demand; `ExecutionRuntime` is the local hands boundary; completion reviews include Validation Integrity.
- **Brownfield support** — existing repositories are indexed to infer an evidence-backed AS-IS baseline without pretending inferred behavior is business truth.
- **Governed teams** — approved task DAGs become serial or parallel waves with file-disjoint leases. DynosAI does not spawn extra agents; a second worker exists only if the host opens another governed session.
- **Eval intelligence** — local failures are attributed and mined into bounded eval cases. Improvement work stays in inbox until a human starts it. Live-provider leaderboards are not claimed.
- **Execution profiles** — Strict, Balanced and Autonomous set host-owned network, dependency and secret policy. Human gates stay required. Docker/VM runtimes and OS-level network interception are not shipped.
- **Certified providers** — Cursor ACP and Codex app-server are the shipped Studio transports. Additional clients are not assumed certified.
- **Local semantic memory** — local embeddings, symbols, tests, decisions, features, and evidence can be retrieved in bounded context.
- **Session recovery** — active work can be resumed from durable state after a provider restart.
- **Model control** — phase-aware budgets, complexity, failures, and routing evidence are tracked while expensive escalation remains conservative.
- **Predictive routing in shadow mode** — historical recommendations are replayed offline, but version 0.19.0 still does not let the predictor autonomously change model tiers.
- **Observability** — MCP calls, token usage, context strategies, validation results, route decisions, retries, and acceptance evidence are persisted.
- **Real-provider acceptance harness** — greenfield and brownfield scenarios can be executed against Codex and Cursor and packaged into one auditable bundle.

## What DynosAI verifies

DynosAI distinguishes process quality from semantic code quality.

It verifies deterministic facts such as:

- actual Git files and status versus the approved plan;
- path scope and create/modify/delete/test actions;
- requirement -> acceptance criterion -> task -> evidence traceability;
- validation command exit codes and captured output;
- required gates and unresolved scope requests;
- database/state integrity during backup and restore;
- provider/runtime isolation and model-route evidence;
- acceptance Oracle results in the test harness.

`Quality = 100` means the DynosAI workflow has no detected governance/traceability blockers or warnings. It **does not** mean that cyclomatic complexity, security, architecture, performance, or maintainability are universally perfect. See [Quality and validation](docs/QUALITY_AND_VALIDATION.md).

## Does DynosAI compile or build the generated code?

DynosAI runs the validation profiles configured for the project. A profile can execute `pytest`, `ruff`, `mypy`, `npm run build`, `tsc`, `dotnet build`, `cargo test`, integration tests, or any other approved command.

A fresh project still gets a baseline unit profile, and DynosAI can additionally **discover** common repository-native unit/lint/type-check/build commands. Discovery is read-only: proposed commands must be explicitly approved before they become governed validation profiles. If a `build` profile is approved and selected by the plan, DynosAI executes it and records the real exit status.

## Does DynosAI review the generated code?

There are two layers:

1. **Deterministic Core verification:** Git diff, scope, planned actions, requirements, evidence, validations, and state transitions are checked independently of the agent's claim.
2. **Semantic review:** the managed agent is instructed to inspect the diff against requirements and regressions, followed by a human code-review gate.

Version 0.19.0 still does **not** spawn a second independent LLM reviewer. Reviewer work is the human code-review gate; tester work is the governed validation contract on the same lease. Eval intelligence does not auto-start a provider. Autonomous is an execution profile, not a skipped-gate mode. Additional IDE clients are not certified.

## Quick start

Clone the public beta and install it locally:

```bash
git clone https://github.com/pablo-cano/dynosai.git
cd dynosai

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
python -m pip install -e .

dynosai --version
dynosai setup --provider all
```

Then initialize or detect a project:

```bash
cd /path/to/your/project
dynosai project detect .
dynosai project initialize . --agent codex
```

Provider-native agent configuration can then drive the normal DynosAI workflow. Or launch the first graphical control plane:

```bash
dynosai studio
```

`dynosai studio` opens on a neutral **Home** project hub. It does not select the current directory unless `--project` is explicitly supplied. Returning to Home closes the active project context while keeping it available in Recent projects. After choosing a project, **Overview → New change → Work / Approvals → Project checks → Project settings** live together under that project. Work includes a bounded live agent-activity trace, while Approvals shows the actual specification requirements/acceptance criteria or plan tasks/files/risks before a user decides. Project settings expose the Codex/Cursor default for new changes plus the effective model/effort route for every governed workflow phase. Risk internals and raw diagnostics stay behind an opt-in technical-details setting. See [Local Studio](docs/STUDIO.md) and [Getting started](GETTING_STARTED.md).

## Documentation

| Document | Purpose |
|---|---|
| [Getting started](GETTING_STARTED.md) | Install, configure, initialize, and run the first workflow |
| [User guide](docs/USER_GUIDE.md) | Day-to-day workflow and common operations |
| [Architecture](docs/ARCHITECTURE.md) | Components, authority boundaries, state, Git, MCP, App Server, and memory |
| [Local Studio](docs/STUDIO.md) | Guided graphical control plane, setup, task workflow, reviews, checks, themes, help, and technical diagnostics |
| [Quality and validation](docs/QUALITY_AND_VALIDATION.md) | Exactly what DynosAI checks and what Quality 100 means |
| [Model control](docs/MODEL_CONTROL.md) | Complexity, token budgets, routing, failures, and predictive shadow mode |
| [Brownfield workflows](docs/BROWNFIELD.md) | How existing repositories are indexed and governed |
| [Observability](docs/OBSERVABILITY.md) | Logs, telemetry, audit events, tokens, and acceptance evidence |
| [Testing strategy](docs/TESTING_STRATEGY.md) | Deterministic, recovery, provider, Oracle, wheel, and replay test layers |
| [Provider integration](docs/PROVIDERS.md) | Codex/Cursor compatibility and transport behavior |
| [CLI reference](docs/reference/CLI.md) | Public command overview |
| [Module reference](docs/reference/MODULES.md) | Responsibility of every source module |
| [Configuration](docs/reference/CONFIGURATION.md) | Runtime/environment configuration |
| [Evolution](docs/EVOLUTION.md) | What changed across the major development iterations |
| [Release process](docs/RELEASE_PROCESS.md) | Versioning, changelog, tests, acceptance, and promotion policy |
| [0.19.0 release notes](docs/RELEASE_0.19.0.md) | Ecosystem: capability manifests for Cursor ACP and Codex app-server |
| [0.18.0 release notes](docs/RELEASE_0.18.0.md) | Secure Autonomous Runtime: execution profiles, runtime-only vault, policy evidence |
| [0.17.0 release notes](docs/RELEASE_0.17.0.md) | Eval Intelligence: attribution, local-trace mining, inbox-only improvement loop |
| [0.16.0 release notes](docs/RELEASE_0.16.0.md) | Governed Agent Teams: DAG waves, leases, fan-in, no extra provider spawn |
| [0.15.0 release notes](docs/RELEASE_0.15.0.md) | Verified Agent Harness: handles, ExecutionRuntime, integrity, eval registry v0 |
| [0.14.1 release notes](docs/RELEASE_0.14.1.md) | Guided Studio UX refresh, compatibility, validation and known limitations |
| [0.14.0 release notes](docs/RELEASE_0.14.0.md) | First Control Plane & Studio Alpha release |
| [Code quality review](docs/CODE_QUALITY.md) | Maintainability review and known technical hotspots |
| [Security](SECURITY.md) | Security policy and reporting guidance |

## 0.13.0 beta baseline evidence

The beta baseline promotion did not introduce functional changes from the accepted `0.12.9 RC4`. The final real-provider matrix passed all four quadrants with no infrastructure retries:

| Provider | Scenario | Mode | Result | Quality | Oracle |
|---|---|---|---|---:|---:|
| Codex | Fibonacci | Greenfield | PASS | 100 | 8/8 |
| Cursor | Fibonacci | Greenfield | PASS | 100 | 8/8 |
| Codex | Contract discounts | Brownfield | PASS | 100 | 10/10 |
| Cursor | Contract discounts | Brownfield | PASS | 100 | 10/10 |

Across that matrix: 63 MCP calls, 0 MCP failures, 0 MCP rejections, 0 scope requests, 0 reconnects, and 0 retries. The machine-readable evidence is kept in [`docs/validation`](docs/validation/).

## Public beta status

DynosAI `0.19.0` keeps the 0.14.x Studio, 0.15 harness, 0.16 team-scheduling, 0.17 eval-intelligence and 0.18 execution-profile authority model (schema v6) and publishes capability manifests for the already-shipped Cursor ACP and Codex app-server transports. Additional clients are refused, not silently certified. Predictive routing stays shadow-only. The 0.13.0 core acceptance evidence remains the baseline for Codex/Cursor greenfield and brownfield behavior. The project is still pre-1.0 and should be adopted with normal engineering review, repository backups, and project-specific validation profiles.

External pull requests are **not currently accepted** while the public API, packaging, and contribution model settle. Bug reports and practical feedback are welcome through [GitHub Issues](https://github.com/pablo-cano/dynosai/issues). Opening contributions is planned for a later beta phase and is not ruled out in the near term.

## License

DynosAI is open source under the [MIT License](LICENSE).

Copyright (c) 2026 **Pablo Cano**.
