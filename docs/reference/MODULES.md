# Source Module Reference

Every production module has an English module-level docstring and an SPDX MIT header. The table below explains the responsibility boundary.

| Module | Responsibility |
|---|---|
| `acceptance.py` | Real-provider acceptance harness, matrix orchestration, safe retry, consolidated certification |
| `acceptance_logging.py` | Trace normalization and portable acceptance evidence |
| `acceptance_policy.py` | Acceptance-only environment/provider safety policy |
| `agent_config.py` | Provider-neutral agent configuration generation and validation |
| `agents.py` | Managed agent rules, skills, instructions, provider integration helpers |
| `application.py` | Provider-neutral application adapters |
| `artifacts.py` | Constitution, baseline, specification, plan and Markdown artifact management |
| `baselines.py` | Accepted reference efficiency baselines |
| `benchmark.py` | Offline acceptance comparison helpers |
| `cli.py` | `dynosai` command-line interface |
| `context_control.py` | Context pressure and context-strategy selection |
| `cost_telemetry.py` | Usage/cost telemetry aggregation |
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
| `mcp.py` | MCP server, tool contracts and provider transport adapters |
| `model_benchmark.py` | Offline provider/model benchmark ranking |
| `model_control.py` | Phase budgets, adaptive control decisions and transition evidence |
| `model_routing.py` | Provider/activity routing profiles |
| `policy.py` | Path/scope and validation-profile policies |
| `predictive_routing.py` | Historical predictive routing memory (shadow use) |
| `predictive_validation.py` | Offline replay and authority-gate analysis |
| `provider_debug.py` | Provider-native debug/certification adapters |
| `providers.py` | Provider capability detection and command construction |
| `retrieval.py` | Hybrid bounded retrieval of text, symbols, tasks and evidence |
| `runtime.py` | Machine-local project/session broker |
| `runtime_audit.py` | Runtime/provider isolation audit |
| `semantic.py` | Local embeddings and semantic index |
| `state.py` | Portable snapshots and atomic restore |
| `token_usage.py` | Token normalization, phase attribution and usage reporting |
| `util.py` | Shared low-level helpers |
| `validation.py` | Workflow quality scoring and validation evaluation |
| `version.py` | Package version |
