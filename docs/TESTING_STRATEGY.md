# Testing Strategy

DynosAI uses several test layers because a coding-agent governance system can appear correct at one layer while failing at another.

## 1. Deterministic unit and regression tests

These tests exercise workflow state, schemas, scope rules, Git evidence, MCP contracts, validation classification, token accounting, routing decisions, and provider adapters without paying for model calls.

## 1.0 RC1 contract freeze and release gate

`tests/test_210.py` freezes the unique MCP tool-name set, optional harness switches, schema v6, Studio EN/ES harness copy and App Server disabled-feature responses. Compatibility scope is documented in `docs/COMPATIBILITY.md`.

The supported **release gate** is the same command in CI, `scripts/build_release.py` and a local checkout:

```bash
python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python scripts/check_studio_sync.py
python -m pytest
```

`python -m pytest` is the stable Python regression suite. It does **not** mean every file under `tests/` is collected. Four historical aggregate suites remain in the repository for manual diagnostics and are excluded by `tests/conftest.py`:

- `tests/test_07.py`
- `tests/test_brownfield06.py`
- `tests/test_hardening.py`
- `tests/test_runtime05.py`

Re-enable them explicitly with `DYNOSAI_HISTORICAL_TESTS=1`. Do not claim “all repository tests pass” when those suites are intentionally excluded.

When website files change, also run `npm run check`, `npm run typecheck`, `npm run lint` and `npm run build` from `apps/web`.

## 1.0 RC2 live matrix and two-session leases

`tests/test_220.py` freezes `MATRIX_1.0`: four provider-aware cells stay `not_run`, historical 0.13 evidence remains separate, and two governed session identities can claim file-disjoint leases without spawning providers. Overlap serializes; scheduler-off stays serial. This is not a live-provider 1.0 certification.

## 1.0 RC3 eval maturity and measurable efficiency

`tests/test_230.py` covers acceptance ZIP import into bounded Eval Registry cases, inbox-only improvement, `governed_change_cost()` aggregates, the rule that unused advertised MCP tools are not waste, and a stable authority prefix that does not dump the 31-tool list or claim a cache hit.

## 2. Migration and recovery tests

Release-candidate hardening covers database migration, duplicate/pending gates, restart/resume, missing or corrupted checkpoints, corrupted telemetry/model-control state, portable snapshot integrity, and atomic restore behavior.

## 3. Provider-native simulated tests

The provider-native harness exercises the same MCP/application contracts expected by real providers using deterministic fixtures. This is where transport differences, elicitation behavior, execution waves, and acceptance packaging can be tested cheaply.

## 4. Real-provider acceptance

When a change can only be validated through actual provider behavior, DynosAI runs isolated acceptance scenarios against the provider CLI. Each scenario uses a fresh workspace and produces portable evidence.

The 0.13.0 beta matrix covered:

- Codex + greenfield Fibonacci;
- Cursor + greenfield Fibonacci;
- Codex + brownfield contract discounts;
- Cursor + brownfield contract discounts.

All four passed in the final promotion source without retries.

## 5. Independent Oracle checks

Acceptance does not rely only on the coding agent's own tests. Scenario-specific Oracle checks inspect expected final behavior and invariants independently of the provider's completion message.

## 6. Installed-package testing

Release candidates are tested after installing the built wheel into an isolated environment. This catches packaging/import/entry-point problems that source-tree tests can miss.

## 7. Offline historical replay

Predictive model-routing logic is evaluated by replaying historical acceptance evidence chronologically. This launches no provider/model processes and deliberately avoids fabricating counterfactual outcomes.

## What a PASS means

A release PASS is therefore a combination of deterministic regression, packaging checks, scenario Oracle evidence, governance quality, and (when required) real-provider behavior. It is not a claim that every programming language, framework, security tool, or repository shape has already been certified.


## 0.14 application/product regression

`tests/test_140.py` adds deterministic coverage for the new product layer without requiring real providers or network access:

- version/package contract;
- stack-aware project detection;
- validation candidate discovery and explicit approval;
- deterministic risk signals;
- explainable uninitialized blockers;
- bounded project overview;
- Local Studio/App Server API health;
- loopback-only server binding;
- packaged Studio assets and source/package synchronization;
- public `studio` and `app-server` CLI contracts.

The 0.13.0 real-provider matrix remains the baseline certification for the unchanged governed provider/Core behavior. A later 0.14.x release may recertify real providers when Studio begins mutating additional workflow surfaces or provider runtime contracts change.

### 0.14.1 guided-Studio regression

`tests/test_141.py` adds deterministic coverage for the UX/application changes without browser automation or external providers:

- user-facing setup status for clean and dirty Git repositories;
- setup readiness after initialization;
- Review Center pending-interaction reads;
- `/api/setup` and `/api/reviews`;
- shared public-web/Studio brand icon and favicon;
- System/Light/Dark theme contract and persistence bootstrap;
- non-technical primary navigation and absence of the 0.14.0 `Advanced mode`;
- setup/task/review/help content surfaces;
- loopback serving for new SVG/theme assets;
- opt-in technical navigation contract;
- source/package synchronization for all Studio assets.

`tests/test_151.py` through `tests/test_158.py` extend that coverage to Cursor ACP, approval-contract rendering, Windows UTF-8 provider transport, the Windows ACP process-tree/`acp-sessions` lifecycle, Studio auto-approve history, activity timestamps, locale-safe subprocess decoding, managed spec/plan overlays, Git C-quoted Spanish overlay paths, leftover verified `tests/` files at merge, Continue-to-finish on Final review, auto-continue into plan authoring after a mid-turn spec approval, auto-continue after scope approval, and Studio scroll preservation.

### 0.15.0 verified-harness regression

`tests/test_160.py` and `tests/test_161.py` cover context handles, ExecutionRuntime policy (path escape, secrets, timeout, dependency install denial), network profiles, secret redaction, Validation Integrity gaps, Eval Registry v0, skill on/off size comparison, cost-per-successful-governed-change raw metrics, MCP retrieve-handle, and Studio completion-review surfaces.

### 0.16.0 governed-team regression

`tests/test_170.py` covers file-disjoint parallel waves, serialization of overlapping files, dependency-blocked claims, follow-on roles as contracts (not spawns), claim denial, fan-in conflicts, schema-v6 engine integration, and reviewer as a human gate. `tests/test_161.py` also requires Studio team-slot rendering and EN/ES copy that extra agents are not started.

### 0.17.0 eval-intelligence regression

`tests/test_180.py` covers failure attribution (non-model layers are ineligible for predictive learning), mining a failed validation into a case, inbox-only propose without provider spawn, regression evidence that does not complete work, and the offline `team_overlap` fixture. Studio tests require eval-case rendering and shadow-mode copy.

### 0.18.0 execution-profile regression

`tests/test_190.py` covers Strict install denial, Balanced vault materialization to local runtime only, refused Docker/VM backends, secret-free review/bundle evidence, and schema v6. Studio tests require the execution-profile combobox and EN/ES copy that OS-level network enforcement is not shipped.

### 0.19.0 capability-manifest regression

`tests/test_200.py` covers Cursor ACP / Codex app-server manifests, refusal of uncertified clients and extension packs, and inventory evidence on overview/stats/diagnostics. Studio tests require certified-provider copy stating additional clients are not shipped.

