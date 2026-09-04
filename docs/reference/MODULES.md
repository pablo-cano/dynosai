# Source Module Reference

Every production module has an English module-level docstring and an SPDX MIT header. The table below explains the responsibility boundary.

| Module | Responsibility |
|---|---|
| `acceptance.py` | Real-provider acceptance harness, matrix orchestration, safe retry, consolidated certification |
| `acceptance_logging.py` | Trace normalization and portable acceptance evidence |
| `acceptance_policy.py` | Acceptance-only environment/provider safety policy |
| `agent_config.py` | Provider-neutral agent configuration generation and validation |
| `agents.py` | Managed agent rules, skills, instructions, provider integration helpers |
| `application.py` | Provider-neutral application API and graphical-client read/write contracts |
| `app_server.py` | Loopback-only HTTP/JSON adapter and Local Studio static server |
| `artifacts.py` | Constitution, baseline, specification, plan and Markdown artifact management |
| `baselines.py` | Accepted reference efficiency baselines |
| `benchmark.py` | Offline acceptance comparison helpers |
| `cli.py` | `dynosai` command-line interface |
| `context_control.py` | Context pressure and context-strategy selection |
| `cost_telemetry.py` | Usage/cost telemetry and cost-per-successful-governed-change aggregation |
| `context_handles.py` | Persistent pass-by-reference handles for large artifacts |
| `eval_registry.py` | Versioned eval scenarios and offline harness comparisons |
| `eval_intelligence.py` | Failure attribution, local-trace mining, acceptance ZIP import and inbox-only improvement loop |
| `prompt_prefix.py` | Deterministic MCP authority prefix hash; cacheable structure, not a cache-hit claim |
| `execution_profiles.py` | Strict/Balanced/Autonomous host policy, local runtime bind and unsupported-runtime refusal |
| `capability_manifests.py` | Cursor ACP / Codex app-server contracts and refusal of uncertified clients/packs |
| `certification_matrix.py` | MATRIX_1.0 live Codex/Cursor × greenfield/brownfield cells; historical 0.13 evidence stays separate |
| `execution_runtime.py` | Provider-neutral ExecutionRuntime and local hands implementation |
| `harness_contracts.py` | Typed execution/recovery/handle contracts and optional harness feature flags |
| `secrets.py` | Credential redaction and secret broker (model refuse-closed; optional local runtime vault) |
| `validation_integrity.py` | Requirement → evidence → validation integrity reports |
| `db.py` | SQLite schema, migrations, persistence and audit storage |
| `debug.py` | Diagnostic and end-to-end debug orchestration |
| `debug_fixtures.py` | Deterministic greenfield/brownfield test fixtures |
| `engine.py` | Main Core facade and workflow coordination |
| `estimator_calibration.py` | Token/context estimator calibration |
| `events.py` | Event primitives |
| `git_guard.py` | Managed-session Git restrictions |
| `git_manager.py` | Governed Git read/write operations and evidence |
| `indexer.py` | Files/symbols/tests/code relationship indexing |
| `interactions.py` | Human gate and elicitation lifecycle |
| `managed_runtime.py` | Strict provider runtime isolation |
| `mcp.py` | MCP server, 31 frozen tool contracts, elicitation and stdio serve |
| `mcp_protocol.py` | Shared MCP revision constants, `_meta` keys and protocol errors |
| `mcp_protocol_legacy.py` | 2025-11-25 / 2025-06-18 initialize and list/call shaping |
| `mcp_protocol_2026.py` | 2026-07-28 discover, stateless list/call envelopes |
| `model_benchmark.py` | Offline provider/model benchmark ranking |
| `model_control.py` | Phase budgets, adaptive control decisions and transition evidence |
| `model_routing.py` | Provider/activity routing profiles |
| `policy.py` | Path/scope, validation-profile, network and dependency policies |
| `predictive_routing.py` | Historical predictive routing memory (shadow use) |
| `predictive_validation.py` | Offline replay and authority-gate analysis |
| `provider_debug.py` | Provider-native debug/certification adapters |
| `provider_process.py` | Provider subprocess spawn/shutdown and Windows process-tree cleanup |
| `providers.py` | Provider capability detection and command construction |
| `retrieval.py` | Hybrid bounded retrieval of text, symbols, tasks and evidence |
| `runtime.py` | Machine-local project/session broker |
| `runtime_audit.py` | Runtime/provider isolation audit |
| `semantic.py` | Local embeddings and semantic index |
| `state.py` | Portable snapshots and atomic restore |
| `team_scheduler.py` | Plan-DAG waves, file-disjoint leases, claim rules and wave-scoped fan-in |
| `studio_projects.py` | Local Studio project hub registry and directory browsing |
| `token_usage.py` | Token normalization, phase attribution and usage reporting |
| `util.py` | Shared low-level helpers |
| `validation.py` | Workflow quality scoring and validation evaluation |
| `validation_discovery.py` | Stack-aware, read-only discovery and explicit approval of validation profiles |
| `risk.py` | Deterministic advisory project/work risk assessment |
| `version.py` | Package version |
