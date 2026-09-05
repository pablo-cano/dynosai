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
| 0.16.0 | Governed Agent Teams | DAG waves, file-disjoint leases, claim/fan-in, reviewer/tester as contracts; no extra provider spawn; schema v6 |
| 0.17.0 | Eval Intelligence | Failure attribution, local-trace mining, inbox-only improvement loop, offline regression evidence; predictive routing still shadow |
| 0.18.0 | Secure Autonomous Runtime | Strict/Balanced/Autonomous profiles, runtime-only vault, policy evidence; no Docker/VM or OS network interception |
| 0.19.0 | Ecosystem & Interoperability | Capability manifests for Cursor ACP and Codex app-server; uncertified clients and packs refused |
| 1.0.0rc1 | Contract freeze | Frozen MCP names, optional harness switches, shared release-gate policy; not production-ready 1.0 |
| 1.0.0rc2 | Certification evidence | MATRIX_1.0 not_run cells; two-session file-disjoint leases; 0.13 evidence kept historical |
| 1.0.0rc3 | Eval maturity | Acceptance ZIP importer, governed_change_cost aggregates, PROMPT_PREFIX_1.0 hash without cache-hit claims |
| 1.0.0rc4 | Installation | Studio doctor, verified local wheel, cross-OS installed-package smoke, 0.19.0 schema-v6 upgrade preservation |
| 1.0.0rc5 | Freeze | Threat model enforced vs not enforced, contribution/certification process, loopback honesty; PRs remain closed |
| 1.0.0rc6 | Standards & release evidence | MCP 2026-07-28 + legacy, unified release gate, eval corpus, distribution ADR; MATRIX_1.0 stays honest |
| 1.0.0rc7 | Certification integrity | Release archive safety, MCP 2026 strict metadata/lifecycle/MRTR, MATRIX runner honesty; MATRIX_1.0 not PASS |

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
