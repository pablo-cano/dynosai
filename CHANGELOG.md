# Changelog

All notable public changes to DynosAI are documented here. The project uses semantic-version-style releases and a Keep-a-Changelog-inspired structure.

## 1.0.0-rc.8 - 2026-09-05

### Added

- User-local persistent state root and certification/acceptance workspaces outside the OS temp directory.
- Codex managed-runtime safety abort and `codex --version` preflight (no model turn) before live trials.
- Candidate certification status keyed by `dynosai_git_commit` + `certification_subject_sha256`, with per-cell attempt counters.

### Changed

- Package version is `1.0.0rc8`. Display surfaces may show `1.0.0-rc.8`. Public status is **RC8 · certification pending**, not stable 1.0.
- `matrix["all_passed"]` follows the current candidate identity, not a synchronized attempt wave.

### Fixed

- Managed Codex `CODEX_HOME` no longer defaults under `%TEMP%`, which current Codex rejects for helper binaries.

## 1.0.0-rc.7 - 2026-09-04

### Added

- Shared releasable-file manifest for source ZIPs and certification fingerprints (`git ls-files --cached --others --exclude-standard` with a deny overlay).
- MCP 2026-07-28 per-request `_meta` validation (protocolVersion + clientCapabilities), era-specific initialize/ping rejection, and `input_required` MRTR for human gates.
- MATRIX runner certification semantics: temp-dir workspace, one provider execution per trial, observed MCP protocols, source identity (`git_commit`, `git_dirty`, `source_tree_sha256`, `source_file_count`).
- Certification subject identity (`certification_subject_sha256`) distinct from the full release-tree fingerprint, plus live guards (`--expected-subject-sha256`, matrix-only dirty allowed).
- Public MATRIX_1.0 artifact refs without machine-local absolute paths.

### Changed

- Package version is `1.0.0rc7`. Display surfaces may show `1.0.0-rc.7`. Public status is **Public RC · Certification Integrity & Protocol Correctness**, not stable 1.0.
- `server/discover` instructions are provider-neutral so `cacheScope=public` is honest.
- Cursor/Codex remain supported 1.0 target providers. They are not declared 1.0 live-certified while MATRIX_1.0 is not PASS.

### Fixed

- Source ZIP construction no longer walks the working tree with `rglob` (which ignored `.gitignore` and could pack `.env` / `.dynosai/`).
- 2026 requests no longer inherit negotiated protocol or elicitation capabilities from a previous request.

## 1.0.0-rc.6 - 2026-09-04

### Added

- MCP revision `2026-07-28` on stdio: `server/discover`, per-request protocol `_meta`, deterministic `tools/list` ordering, list cache metadata and `resultType` envelopes. Legacy `2025-11-25` / `2025-06-18` initialize behaviour is unchanged.
- `scripts/release_gate.py` as the single executable stable release gate.
- MATRIX_1.0 trial records and `scripts/run_matrix_1_0.py` (probe-only by default; live trials are explicit attempts).
- Eval Registry catalog expanded to a Tier A / Tier B release corpus with provenance and graders.
- ADRs for the PyPI name `dynosai`, Agent Plugins (`com.dynosai`) and MCP protocol eras.

### Changed

- Package version is `1.0.0rc6`. Display surfaces may show `1.0.0-rc.6`. Public status is **Public RC · Standards & Release Evidence**, not stable 1.0.
- Python distribution name is `dynosai`. The import package remains `dynosai_flow`. MCP `serverInfo.name` remains `dynosai-flow`.
- CI Python job runs `python scripts/release_gate.py` (includes Studio/package sync). Historical aggregate suites stay excluded.
- Roadmap separates RC6 must-haves, RC7 contingency, stable promotion, and 1.1–1.5.
- Codex and Cursor acceptance Popen streams use UTF-8 on Windows so Spanish prompts and JSON-RPC are not decoded as cp1252.
- OrderFlow brownfield fixture tests use in-memory SQLite so Windows seed cleanup does not hit WinError 32.
- `dynosai_get_next_action` returns a bootstrap contract (`dynosai_project` / `dynosai_work start`) when no governed session exists, instead of a dead-end PermissionError.
- GitManager treats only the project toplevel as a repo, so nested greenfield directories no longer inherit a dirty parent checkout.

### Security

- Schema remains v6. Unique MCP names remain 31. Loopback is still not a full security boundary. DynosAI is not published on PyPI. Predictive routing stays shadow-only.

### Validation

- Added `tests/test_260.py` for protocol eras, unified release gate, eval catalog, ADRs and honest MATRIX_1.0 rules.

## 1.0.0-rc.5 - 2026-09-04

### Added

- `docs/THREAT_MODEL.md` freezes enforced vs not-enforced 1.0 controls, including the honest local-browser/loopback surface.
- `CONTRIBUTING.md` documents maintainer development, the shared release gate and the certified-provider process. External pull requests remain closed on purpose.
- Studio Help states that loopback is not a full security boundary (English and Spanish).

### Changed

- Package version is `1.0.0rc5`. Display surfaces may show `1.0.0-rc.5`. Public status is **Public RC · Freeze**, not stable 1.0.

### Security

- Schema remains v6. Unique MCP names remain 31. Loopback, Host-header and JSON POST controls stay enforced and are not claimed as a complete local security boundary. DynosAI is not published on PyPI.

### Validation

- Added `tests/test_250.py` for threat-model honesty, contribution policy, enforced path/secret/Git/certified-client/timeout/loopback checks and the freeze contract.

## 1.0.0-rc.4 - 2026-09-03

### Added

- Studio exposes existing `doctor` / `deep_doctor` checks on Overview and Diagnostics via `GET /api/doctor`. The check logic is unchanged.
- `scripts/install_local_wheel.py` verifies `SHA256SUMS.txt` before installing a local wheel and refuses remote/index URLs including pypi.org.
- CI installed-wheel smoke on Ubuntu, Windows and macOS (Python 3.11) in a fresh venv outside the checkout with `PYTHONPATH` cleared.
- Upgrade-preservation smoke from representative 0.19.0 schema-v6 state to the 1.0 wheel without wiping work.

### Changed

- Package version is `1.0.0rc4`. Display surfaces may show `1.0.0-rc.4`. Public status is **Public RC · Installation**, not stable 1.0.

### Security

- Schema remains v6. Unique MCP names remain 31. DynosAI is not published on PyPI.

### Validation

- Added `tests/test_240.py` for Studio doctor, verified local-wheel install, CI matrix shape, checkout-src refusal and 0.19.0 upgrade preservation.

## 1.0.0-rc.3 - 2026-09-01

### Added

- Host-owned acceptance ZIP importer writes failed children into bounded Eval Registry cases. Studio can import a local ZIP. Improvement stays in inbox and does not spawn a provider.
- Completed-work aggregates of `governed_change_cost()` on stats, scorecard, overview, Studio and diagnostic bundles. MCP duration and result bytes are recorded when measured.
- Deterministic `PROMPT_PREFIX_1.0` authority prefix with a stable hash stored in runtime/audit. Phase context remains a suffix. The hash is not a cache-hit claim.

### Changed

- Package version is `1.0.0rc3`. Display surfaces may show `1.0.0-rc.3`. Public status is **Public RC · Eval maturity**, not stable 1.0.

### Security

- Schema remains v6. Unique MCP names remain 31. Unused advertised MCP tools are not scored as waste.

### Validation

- Added `tests/test_230.py` for ZIP import bounds, inbox-only improvement, governed-change scorecard surfaces, prefix hashing and the no-waste rule.

## 1.0.0-rc.2 - 2026-09-01

### Added

- Introduced the versioned `MATRIX_1.0` live-certification schema (`docs/validation/matrix-1.0.json`) with Codex/Cursor × greenfield/brownfield cells.
- Studio Overview shows those four cells. `dynosai_stats`, project overview and diagnostic bundles expose the same provider-aware placeholder. No MCP tool was added.
- `engine.claim_lease` records an optional host session identity so two governed sessions can claim file-disjoint leases without spawning providers.

### Changed

- Package version is `1.0.0rc2`. Display surfaces may show `1.0.0-rc.2`. Public status is **Public RC · Certification evidence**, not stable 1.0.

### Security

- Schema remains v6. Historical 0.13 Quality 100 evidence is not copied into 1.0 live cells. `all_passed` stays false until every cell is a real provider pass.

### Validation

- Added `tests/test_220.py` for MATRIX_1.0 placeholders, two-session disjoint claims, overlap serialization, fan-in conflicts and scheduler-off serial fallback.

## 1.0.0-rc.1 - 2026-09-01

### Added

- Documented the 1.0 compatibility contract in `docs/COMPATIBILITY.md`: frozen MCP names, public CLI, schema v6, Studio ↔ App Server scope, human gates, and additive-field policy.
- Exposed optional harness features as host-owned project settings: context handles, governed team scheduling, and eval intelligence. Precedence is environment override → project setting → default. Studio Overview and Project settings show EN/ES status chips.
- Added `tests/test_210.py` to freeze the unique MCP tool-name set (31) and the optional-harness degraded paths.

### Changed

- Package version is `1.0.0rc1` (PEP 440). Display surfaces may show `1.0.0-rc.1`. Public status is **Public RC · Contract freeze**, not stable 1.0.
- Release gate, CI and `scripts/build_release.py` now share one policy: `python -m pytest` is the stable suite. Historical aggregate tests remain in the repository and stay excluded unless `DYNOSAI_HISTORICAL_TESTS=1`.
- `dynosai_stats` reports `mcp_surface_frozen` and `mcp_tool_count`. No MCP tool was added, removed or renamed.

### Security

- Human gates, capability certification, path security and execution-profile enforcement are not kill-switches. There is no `DYNOSAI_DISABLE_*` family. Schema remains v6. Agents cannot mutate harness settings through MCP.
- Turning context-handle creation off keeps historical `dynosai_retrieve_handle` reads. Scheduler off uses serial fallback and a structured `feature_disabled` refusal. Eval intelligence off hides propose/mining while keeping historical cases readable.
- Website Next.js and `eslint-config-next` are 16.3.3 (Dependabot #14/#15).

### Validation

- `tests/conftest.py` labels and excludes the four historical aggregate suites from the default collection.
- RC1 does not prove a 1.0 live provider matrix, installer, OS sandbox, additional certified clients, or autonomous predictive routing.

## 0.19.0 - 2026-08-31

### Added

- Introduced host-owned provider capability manifests for the already-shipped Cursor ACP and Codex app-server transports.
- Added `capability_manifests.py`. Unknown clients and project extension packs raise instead of silently inheriting Codex/Cursor behavior.
- Studio Overview shows the certified provider inventory. `dynosai_stats` and diagnostic bundles include a capability summary. `GET /api/provider-capabilities` is host-owned; no MCP tool was added.

### Changed

- Provider setup refuses uncertified names through the same manifest contract.
- Predictive routing remains shadow-only. Human gates remain required.

### Security

- Additional adapters cannot self-certify. Schema remains v6.

### Validation

- Added `tests/test_200.py` for shipped manifests, refused clients/packs, and secret-free inventory evidence. Studio tests require certified-provider copy in EN/ES.

## 0.18.0 - 2026-08-31

### Added

- Introduced Strict, Balanced and Autonomous execution profiles as host-owned policy outside the model. Balanced is the project default. Autonomous uses an allowlist, not unrestricted network.
- Added `execution_profiles.py`. Docker, VM and remote runtimes raise a policy error rather than silently falling back.
- Optional local secret vault materializes named secrets to the local runtime only. `materialize_for_model` still refuses.
- Studio Project settings can select the profile. Code/merge reviews, overview, `dynosai_stats` and diagnostic bundles include policy evidence without secret values.

### Changed

- Engine-constructed `LocalExecutionRuntime` now follows the selected profile (governed dependencies, profile network). Direct `LocalExecutionRuntime()` construction remains compatibility/unrestricted so 0.15 tests stay valid.
- Predictive routing remains shadow-only. Human gates remain required in every profile.

### Security

- OS-level child-process network interception is **not** shipped. Evidence records `enforcement: decision_only` and `os_network_enforcement: false`.
- Schema remains v6. Vault files live under `.dynosai/runtime/`. Agents cannot change the execution profile through MCP.

### Validation

- Added `tests/test_190.py` for profiles, vault materialization, refused container runtimes, and secret-free evidence. Studio tests require execution-profile settings and EN/ES OS-enforcement copy.

## 0.17.0 - 2026-08-31

### Added

- Introduced eval intelligence on top of Eval Registry v0: failure attribution by layer (model, harness, provider, project, infrastructure, governance), mining of local validations/audit/eval/model-control traces, and bounded regression cases under `.dynosai/runtime/`.
- Added `eval_intelligence.py`. Improvement work is created in **inbox only**; DynosAI does not auto-start a provider. Regression evidence does not complete or approve the work.
- Studio Overview shows mined eval cases with an explicit note that predictive routing stays in shadow mode.
- `dynosai_stats` includes an eval-intelligence summary. Offline fixtures cover `team_overlap` and `mined_regression` without live providers.

### Changed

- Predictive routing remains shadow-only. Non-model failures remain ineligible for capability learning.

### Security

- Eval mining writes runtime files only. Schema remains v6. Agents cannot spawn improvement sessions through MCP.

### Validation

- Added `tests/test_180.py` for attribution, mining, inbox-only propose, and regression evidence. Studio tests require eval-case rendering and EN/ES shadow-mode copy.

## 0.16.0 - 2026-08-31

### Added

- Introduced governed agent-team scheduling: approved task DAGs become serial or parallel waves with file-disjoint leases, scope ceilings, token/time budgets, and follow-on reviewer/tester contracts.
- Added `team_scheduler.py` as a Core scheduling module. Leases are derived from existing `tasks.claimed_run` / runs; schema remains v6.
- Exposed team waves on `engine.schedule`, `engine.claim_lease` and `engine.fan_in_report`. Fan-in is wave-scoped so sequential overlap of the same file is not a false conflict.
- Studio Work cards show current-wave team slots (role, files, lease status) with an explicit note that DynosAI does not spawn extra provider processes.
- `dynosai_schedule` now returns the team plan plus a `fan_in` report. A second worker exists only if the host opens another governed session against an open lease.

### Changed

- Parallel batches remain the compatibility view of a team wave. Overlapping planned files are never leased in the same parallel wave.
- Reviewer is the existing human `code_review` gate, not a spawned agent. Tester is the governed validation contract on the same lease.
- Predictive routing remains shadow-only.

### Security

- Team scheduling does not expand agent authority, spawn extra Codex/Cursor processes, or bypass human gates.

### Validation

- Added `tests/test_170.py` for DAG waves, claim denial, fan-in conflicts, schema-v6 integration and reviewer-as-human-gate.
- Studio asset tests require team-slot rendering and EN/ES copy that DynosAI does not start extra agents.

## 0.15.0 - 2026-08-31

### Added

- Introduced the Verified Agent Harness: typed execution/recovery contracts, persistent context handles, `dynosai_retrieve_handle`, and pass-by-reference compaction of large diffs/search/validation outputs.
- Formalized `ExecutionRuntime` / `LocalExecutionRuntime` as the provider-neutral hands boundary (filesystem, processes, network decisions, dependency classification). Local remains the default; Docker/VMs are not required.
- Added minimum execution policy outside the model: path-root/escape/symlink denial, process timeouts, network profiles (`unrestricted` default, `allowlist`, `default_deny`, `offline`), dependency use-vs-install classification, credential redaction, and a refuse-closed secret broker.
- Added Validation Integrity reports (requirement → acceptance → evidence → validation) and exposed them on Studio code/merge reviews together with bounded diffs and validation results.
- Added Eval Registry v0 (nine representative offline scenarios) and a skill on/off context-size comparison. No live-provider quality claim is made from these fixtures.
- Added cost-per-successful-governed-change raw observability (tokens, tools, retries, human gates; catalog price only when configured).
- Added mechanical architecture-import checks in `scripts/check_repository.py`.

### Changed

- Reframed 0.15 in the public roadmap around a verified harness rather than a vague loop-engine slogan. Cursor ACP remains documented as a 0.14.1 capability, not future 0.19 greenfield work.
- Studio completion reviews can expand the governed chain (requirements, acceptance, tasks, files, diff, validation, integrity) without making technical internals the default UX.
- Predictive routing remains shadow-only.

### Security

- Secrets matching known credential patterns are redacted from MCP tool results before they reach prompts, evidence-shaped payloads, or Studio review diffs.
- The secret broker refuses to materialize values into model context.

### Validation

- Added `tests/test_160.py` and `tests/test_161.py` for handles, runtime policy, integrity, evals, cost, MCP retrieve, and Studio reviewability.
- Schema remains v6; handles and eval records are runtime files under `.dynosai/runtime/`, not workflow-authority tables.

## 0.14.1 - 2026-08-26

### Added

- Added a guided Studio setup model that explains project detection, Git readiness, DynosAI initialization and project-check approval in user-facing terms.
- Added a Review Center backed by pending `human_interactions`, including bounded specification, plan and quality/evidence context for human gates.
- Added `/api/setup` and `/api/reviews` to the loopback App Server.
- Added System/Light/Dark Studio themes with browser-local persistence and the same DynosAI SVG mark used by the public website.
- Added in-product Help, plain-language workflow timelines, project-check selection, support summary and user-friendly task creation guidance.
- Added in-app project management with recent projects, local path opening and a contextual folder browser, without restarting Studio.
- Added an English/Spanish localization layer with English as the default and browser-local language preference.
- Added a contextual greenfield Fibonacci walkthrough that guides the user through the normal Studio UI without creating projects, starting agents or approving workflow steps on the user's behalf.
- Added accessible shadcn/ui-style combobox controls for provider, workspace and language choices.
- Added a neutral project hub: `dynosai studio` no longer selects the launch directory unless `--project` is explicitly supplied.
- Added explicit neutral greenfield project creation from Home; technology-specific files are introduced by governed changes rather than by a starter selector.
- Refined Home semantics so returning to Home closes the active project context and returns to a neutral project hub while keeping the project in Recents.
- Replaced OS-dependent folder-picker calls with an in-Studio directory browser for both project creation locations and existing-project opening.
- Fixed sidebar horizontal overflow and stopped rendering long project paths inside the navigation rail.
- Replaced the dedicated Fibonacci tutorial page and automatic demo creation with a contextual, state-aware walkthrough that highlights the normal UI while the user performs every action.
- Replaced browser-native confirmation dialogs with Studio-native shadcn/ui-style dialogs and styled the remaining checkbox/toggle controls consistently.
- Added folder creation directly inside the Studio folder browser.
- Added project-scoped execution settings and model routing inspection/editing for Codex and Cursor across discovery, specification, planning, implementation, code review, validation and merge.
- Bounded Recent projects with an internal vertical scroll area and removed the manual Refresh button in favor of background synchronization.
- Reduced repeated page headings/copy and corrected execution-summary spacing in the New change flow.
- Simplified New change to the request itself; coding agent, workspace and per-phase model choices now live only in Project settings.
- Locked project workflow pages until DynosAI initialization completes, removed the duplicated Next step panel/button, and added blocking progress feedback for mutating actions.
- Made model routing follow the project coding agent automatically instead of exposing a second Codex/Cursor switch inside the routing panel.
- Made Studio-created changes start the configured Codex/Cursor agent asynchronously instead of remaining in Inbox/Queued, with observable running/interrupted states and a safe Continue agent retry.
- Made project path/location fields read-only and folder-browser controlled, and fixed the Fibonacci walkthrough so repeated approval cycles do not prematurely jump to the final result step.
- Fixed Studio provider execution to use provider-native headless transports instead of terminal-oriented CLI launches, so Codex/Cursor can run from the browser-managed workflow without an interactive console; Studio now owns human gates and exposes provider diagnostic tails when a turn fails.
- Fixed Windows Codex Studio execution with explicit UTF-8 JSON-RPC/MCP stdio, preventing Spanish/non-ASCII task text from being encoded through the Windows ANSI code page; headless launches also avoid creating project-local provider config as a launch side effect.
- Fixed Cursor Studio execution by replacing terminal/print-mode automation with Cursor ACP (`agent acp`), including session-scoped DynosAI MCP injection, explicit provider-permission handling and actionable authentication failures.
- Fixed headless CLI exit-code propagation so Studio receives the real provider failure instead of a successful wrapper process plus a large runtime JSON dump.
- Made Work diagnostics use a Studio-native persistent disclosure panel, so background refresh no longer collapses diagnostic details while the user is reading them.
- Expanded Approvals so specification and plan gates expose the actual requirements, acceptance criteria, tasks, files and risks before a user can approve them; standard gate guidance is localized by Studio.
- Preserved request-change/clarification drafts across background refresh and prevented focused approval editors from being rebuilt while the user is typing.
- Added a bounded live Agent activity stream to Work, fed by Cursor ACP/Codex provider progress plus governed workflow events.
- Added an approval history on the Approvals page and a project-level auto-approve switch that records `studio_auto` decisions without answering clarifications. Ordinary product-file scope requests can be auto-approved; DynosAI-owned spec/plan overlays never become a human scope gate.
- Explained Project checks as later evidence commands for a library or web app, not extra work on that screen.
- Fixed Cursor planning turns so the provider-native `cursor/create_plan` transport gate is acknowledged without replacing or bypassing DynosAI's authoritative plan and human approval gate.
- Fixed the contextual tutorial to advance automatically when a real approval appears and to keep following repeated approval/work cycles until the governed task is done.
- Fixed Windows `WinError 32` after Cursor ACP workflow transitions: Studio now closes ACP stdin, waits for process exit, reaps the provider process tree, and refreshes the managed runtime without failing when leftover `acp-sessions` files are still unlocking.
- Stopped Studio refresh from jumping the page, diagnostic log or Agent activity scrollbar back to the top, and show newest activity first with a timestamp.
- Survived Windows locale subprocess output (`UnicodeDecodeError` in `_readerthread`) by decoding captured Git, pytest, tasklist and provider stdio as UTF-8 with replacement.
- Decoded Git C-quoted pathnames (`core.quotepath`) so Spanish overlay paths such as `specs/...pequeña.../plan.md` are recognized as DynosAI-owned artifacts instead of failing implementation `register_result`.
- Stopped exported `specs/**` overlays (including slash-corrupted Git octal names like `/303/261/plan.md`) from remaining as required task files, so an otherwise complete implementation can register and leave Implementar.
- Fixed final merge on interactive branches so leftover verified files such as `tests/test_fibonacci.py` are scooped into the checkpoint instead of blocking squash as collapsed `?? tests/` dirt; DynosAI-owned overlays and caches are not merge blockers.
- Unstuck Work when Final review has already been auto-approved but merge did not finish: Continue now completes the host-owned merge instead of demanding a missing Approvals card.
- After a specification is auto-approved during a Codex/Cursor turn, Studio relaunches the agent to author the plan instead of treating the already-advanced `plan_review` state as a finished turn.
- Stopped classifying requests that mention `ValueError` as bugs; exception type names are not bug reports.

### Changed

- Replaced the ambiguous 0.14.0 `Advanced mode` with opt-in **Show technical details** in Settings.
- Reworked Studio navigation around global Home/Settings/Help plus a nested selected-project workflow for Overview, New change, Work, Approvals and Project checks.
- Moved raw risk, diagnostics and activity/event views out of the default non-technical experience.
- Simplified risk presentation to a user-facing change-sensitivity level while keeping the deterministic score available in technical details.
- Aligned Studio branding, colors and interaction hierarchy more closely with `dynosai.com`.
- Kept Git, schema v6 workflow state, validation approval and human-gate authority unchanged.
- Removed Claude from Studio and public CLI provider choices; the supported public execution surface remains Codex and Cursor.
- Separated global Studio navigation (Home, Settings, Help) from a nested selected-project workflow (Overview, New change, Work, Approvals, Project checks).
- Removed normal-user UI wording about the underlying local server/loopback runtime; those implementation details remain architectural rather than product-navigation concepts.

### Validation

- Added 0.14.1 regression coverage for setup guidance, Review Center API, shared branding/favicon, theme persistence, non-technical navigation, local asset serving and technical-mode opt-in behavior.
- Stable Python regression now contains 441 tests, including Windows locale-safe subprocess capture, Studio auto-approve history, activity-log ordering, managed spec/plan overlays, Git C-quoted Spanish overlay paths, leftover verified `tests/` files at merge, Continue-to-finish on Final review, auto-continue into plan authoring after a mid-turn spec approval, Cursor ACP process-tree/`acp-sessions` lifecycle coverage, neutral project-hub startup, project creation/switching, contextual Fibonacci walkthrough, localization, accessible combobox and current-provider-boundary coverage.
- Repository-quality, Studio-sync and website check/typecheck/lint/build gates remain required for release.

## 0.14.0 - 2026-08-26

### Added

- Added a loopback-only DynosAI App Server as a provider-neutral interface over `DynosAIApplication`.
- Added `dynosai studio` and the first Local Studio alpha for project onboarding, recent work, blocker visibility, validation discovery, risk and diagnostics.
- Added stack-aware project detection and suggested validation/test commands.
- Added read-only validation auto-discovery for Python, Node.js/TypeScript, Rust, .NET and Java with explicit approval before profiles become governed checks.
- Added deterministic Risk Assessment v1, separate from workflow Quality, using blast-radius and sensitive-surface signals.
- Added explainable blockers and a bounded project overview read model for graphical clients.
- Added public `/studio` and `/roadmap` website surfaces plus a substantially expanded product roadmap through 1.0.

### Changed

- Provider process launching and acceptance shims are now cross-platform on Windows: Python shebang fixtures/provider overrides are routed through the active interpreter, and Windows Git Guard tests invoke the generated command wrapper explicitly.
- TOML configuration tests now validate parsed values instead of raw backslash escaping, making Codex configuration assertions portable across POSIX and Windows paths.
- Project onboarding now uses detected language/test hints instead of defaulting every repository to Python/pytest when stronger project evidence is available.
- Website version checks now compare against the authoritative `pyproject.toml` version instead of hardcoding a release number.
- The 0.13.0 governed SDD core, schema v6 authority model, Git boundaries and predictive-router shadow policy remain intact.

### Validation

- Added the 0.14 regression suite covering Studio assets/API, project/stack detection, validation approval, risk assessment, blocker explanations and CLI contracts.
- Existing stable regression suites remain release gates; historical pre-stable aggregate suites remain available for manual diagnostics.

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
