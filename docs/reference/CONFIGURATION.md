# Configuration Reference

DynosAI prefers explicit CLI/project configuration. Environment variables are primarily runtime, provider, diagnostic, or advanced controls. Optional harness features are normally toggled in Studio Project settings. Environment overrides are for CI, evals and diagnostics.

Precedence for optional harness features: environment → project setting → default. See `docs/COMPATIBILITY.md`.

Common variables present in the 1.0 codebase include:

```text
DYNOSAI_PROJECT
DYNOSAI_PROJECT_ROOT
DYNOSAI_USER_HOME
DYNOSAI_RUNTIME_HOME
DYNOSAI_MODEL_CACHE
DYNOSAI_AGENT_PROVIDER
DYNOSAI_CURSOR_BIN
DYNOSAI_CODEX_BIN
DYNOSAI_MODEL_CONTROL_POLICY
DYNOSAI_CONTEXT_CHECKPOINTS
DYNOSAI_COMPACT_RESPONSES
DYNOSAI_MAX_EXECUTION_WAVE_TASKS
DYNOSAI_MAX_EXECUTION_WAVE_FILES
DYNOSAI_MCP_RESULT_TEXT_MODE
DYNOSAI_MCP_TOOL_PROFILE
DYNOSAI_PHASE_TOOL_DISCLOSURE
DYNOSAI_STRICT_MANAGED_RUNTIME
DYNOSAI_EXECUTION_PROFILE
DYNOSAI_NETWORK_PROFILE
DYNOSAI_DEPENDENCY_PROFILE
DYNOSAI_TOKEN_TELEMETRY
DYNOSAI_TOKEN_USAGE_FILE
DYNOSAI_TOKEN_CALIBRATION_PATH
DYNOSAI_HARNESS_CONTEXT_HANDLES
DYNOSAI_CONTEXT_HANDLES
DYNOSAI_HARNESS_TEAM_SCHEDULER
DYNOSAI_HARNESS_EVAL_INTELLIGENCE
DYNOSAI_HARNESS_SKILLS
DYNOSAI_HISTORICAL_TESTS
```

There is no `DYNOSAI_DISABLE_*` family. Use positive `DYNOSAI_HARNESS_<FEATURE>=true|false` overrides. Do not use harness variables to disable human gates, execution profiles, capability certification or path security.

Acceptance/debug-specific variables also exist and should not normally be used to change production behavior:

```text
DYNOSAI_ACCEPTANCE_AUTORESPOND
DYNOSAI_ACCEPTANCE_LOG_HOME
DYNOSAI_ACCEPTANCE_PROCESS_TRACE
DYNOSAI_ACCEPTANCE_TRACE
DYNOSAI_ACCEPTANCE_USE_CONFIGURED_MODELS
DYNOSAI_DEBUG_E2E
```

Provider/runtime internal variables include session IDs/tokens, runtime broker flags, external-default policies, and optional Postgres MCP command configuration.

Treat undocumented low-level variables as internal implementation details unless a release note explicitly promotes them to a supported public contract.
