# Changelog

All notable public changes to DynosAI are documented here. The project uses semantic-version-style releases and a Keep-a-Changelog-inspired structure.

## 0.13.0 - 2026-08-25

### Changed

- Promoted the validated `0.12.9 RC4` behavior to the first internally stabilized public-beta baseline with no functional architecture change.
- Kept database schema v6 and existing public MCP contracts unchanged.
- Kept Predictive Router runtime authority disabled (shadow mode) despite passing quantitative offline authority gates.

### Validation

- Final real-provider matrix passed 4/4: Codex/Cursor × Greenfield/Brownfield.
- Every child finished `done` with Quality 100 and complete Oracle checks.
- Matrix recorded 0 MCP failures, 0 MCP rejections, 0 scope requests, 0 reconnects, and 0 retries.

## 0.12.9

### Fixed

- Acceptance retry logic learned to distinguish recovered historical MCP rejections from unresolved rejections, without retrying functional failures.

### Validation

- Final RC4 source/wheel selected regression reached 280/280 PASS before beta baseline promotion.

## 0.12.8

### Added

- Transparent one-time retry for side-effect-free provider/worker idle-timeout failures.
- Preserved failed attempts and actual usage/cost including retries in acceptance bundles.

## 0.12.7

### Fixed

- Added provider-specific MCP result transport: Codex keeps structured-primary transport; Cursor uses full-text compatibility because its CLI does not reliably expose structured content to the model.

### Added

- Consolidated Codex/Cursor Green/Brown acceptance into one matrix bundle.

## 0.12.6

### Validation

- Release-candidate hardening for schema migration, restart/resume, corrupted state/checkpoints/telemetry, validation-learning hygiene, scope lifecycle, and human-gate polling.

## 0.12.5

### Added

- Offline chronological Predictive Router replay and authority gates with zero provider/model calls.
- Explicit exclusion of legacy/orchestration-contaminated outcomes from learning.

## 0.12.4

### Changed

- Reduced duplicate MCP text transport for compatible providers.
- Collapsed deterministic `READY -> IMPLEMENTING` after an approved plan when execution was already requested.
- Completed real certification of a two-task execution wave.

## 0.12.3

### Added

- Execution waves for bounded dependent task chains.
- Final-wave deterministic auto-advance to code review.

## 0.12.2

### Changed

- Reduced MCP payload/replayed context and added structural efficiency telemetry while keeping workflow semantics stable.

## 0.12.1

### Fixed

- Corrected scope lifecycle and predictive-learning hygiene after the 0.12.0 real acceptance exposed orchestration failures that could be misclassified as model failures.

## 0.12.0

### Added

- Predictive routing memory in shadow mode, context checkpoint reuse, targeted checkpoint loading, calibrated estimator updates, and phase-aware context strategies.

## 0.11.x

### Added / Changed

- Introduced and hardened the adaptive Model Control Plane: phase-aware budgets, complexity, safe routing boundaries, exact transition accounting, context-pressure separation, and initial-route verification.

## 0.10.x

### Added / Changed

- Improved execution/token efficiency, telemetry, observability, and model-control planning.

## 0.9.x

### Added / Changed

- Unified agent configuration, managed provider runtime, token telemetry, provider evidence, and acceptance baselines.

## 0.8.x

### Added / Changed

- Automated real-provider acceptance, machine diagnostics, strict provider evidence, and provider/activity model routing.

## 0.7.x

### Added / Changed

- Combined greenfield/brownfield certification, MCP contract hardening, portable read surface, and migration evidence improvements.

## 0.6.x

### Added / Changed

- Brownfield context, task-aware execution, compact MCP contracts, and Cursor brownfield hardening.

## 0.5.x and earlier

### Added / Changed

- Established the persistent SQLite-backed SDD workflow, provider configuration/bootstrap, schema migration, Git governance, and early local/provider E2E foundations.

For the curated pre-public evolution record, see [`docs/EVOLUTION.md`](docs/EVOLUTION.md).
