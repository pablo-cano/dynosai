# Configuration Reference

DynosAI prefers explicit CLI/project configuration. Environment variables are primarily runtime, provider, diagnostic, or advanced controls.

Common variables present in the 0.15.0 codebase include:

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
DYNOSAI_TOKEN_TELEMETRY
DYNOSAI_TOKEN_USAGE_FILE
DYNOSAI_TOKEN_CALIBRATION_PATH
```

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
