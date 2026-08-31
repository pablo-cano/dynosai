# Project Evolution

DynosAI has been developed through repeated local and real-provider acceptance loops. Each iteration has tried to preserve previously verified behavior while narrowing a concrete failure or efficiency problem.

## Major stages

| Versions | Main theme | How it was evaluated |
|---|---|---|
| 0.4.x-0.5.x | Persistent workflow foundation, schema/bootstrap, provider configuration | Local flow tests, schema upgrade tests, early Cursor runs |
| 0.6.x | Brownfield context, task-aware execution, compact provider contracts | Cursor brownfield E2E and migration scenarios |
| 0.7.x | Combined greenfield/brownfield certification, MCP contract hardening, portable reads | Local/provider E2E and authority/evidence tests |
| 0.8.x | Provider-native acceptance, machine doctor, strict Codex evidence, model routing | Real-provider acceptance bundles and runtime audits |
| 0.9.x | Unified agent configuration, managed runtime, telemetry and stable provider baseline | Cursor/Codex runtime tests and measured acceptance |
| 0.10.x | Execution optimization, token telemetry, observability, roadmap for model control | Acceptance cost/token comparisons |
| 0.11.x | Adaptive Model Control Plane and boundary hardening | Phase-accurate telemetry, routing invariants, real Codex acceptance |
| 0.12.0-0.12.2 | Predictive/context efficiency, checkpoint reuse and data-hygiene corrections | Historical replay plus repeated Fibonacci acceptance |
| 0.12.3-0.12.4 | Execution waves, deterministic auto-advance, transport de-duplication | Real Codex acceptance; implementation tokens reduced substantially |
| 0.12.5 | Offline Predictive Router validation | Replay with zero model calls; router intentionally kept shadow-only |
| 0.12.6 | RC hardening: migrations, restart/resume, corrupted state/telemetry, scopes/gates | 257 selected source/wheel tests plus fault injection |
| 0.12.7 | Cursor transport compatibility and consolidated 4-case matrix | Real Codex/Cursor Green/Brown matrix; exposed provider-specific transport behavior |
| 0.12.8 | Transparent infrastructure retry policy | Simulated retry certification and real full matrix |
| 0.12.9 | Recovered-rejection-aware retry hardening | 280 selected source/wheel tests and final 4/4 real-provider matrix |
| 0.13.0 | Stable promotion with no functional change from accepted RC4 | Final matrix certification, promotion invariants, offline predictive replay |
| 0.14.0 | Product/control-plane layer on stable governed core | Local App Server, Studio alpha, validation discovery, risk, explainable blockers, roadmap/web refresh |
| 0.14.1 | Guided Studio UX refresh | Setup guidance, themes/branding, task flow, Review Center, project checks, help, opt-in technical details |
| 0.15.0 | Verified Agent Harness | Context handles, ExecutionRuntime, Validation Integrity, Eval Registry v0, execution policy; 0.14.1 Studio baseline preserved |

## How changes are recorded now

Public development uses three complementary records:

1. **`CHANGELOG.md`** — user-visible release changes, grouped as Added / Changed / Fixed / Validation / Security when relevant.
2. **Git commits and pull requests** — implementation-level history. Conventional Commit prefixes are recommended (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
3. **`docs/validation/`** — selected machine-readable evidence for stable/release-candidate claims.

Historical raw release notes from the pre-public development cycle are retained under `docs/history/`, but new releases should avoid accumulating generated logs in the repository root.

## Release philosophy

A version is not promoted because a feature appears to work once. The project has repeatedly used the pattern:

```text
change
-> deterministic tests
-> installed-wheel tests
-> provider acceptance when required
-> inspect failures and attribution
-> fix only the demonstrated cause
-> re-run
-> freeze
-> stable promotion
```

This is the standard expected for future DynosAI releases.
