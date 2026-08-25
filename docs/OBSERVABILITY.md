# Observability

DynosAI records enough information to explain how a managed coding workflow progressed and why control decisions were made.

## Audit records

The local database contains workflow and audit events for significant state changes, scope decisions, validations, state snapshots/restores, and routing/control decisions.

## Token and context telemetry

Provider telemetry is normalized into process and phase views. Where exact provider counters are available, they are preferred; estimators are explicitly marked and calibrated separately.

Tracked signals include:

- input, cached input, generated/reasoning tokens;
- phase attribution and live phase deltas;
- MCP calls and MCP time;
- response/checkpoint compaction;
- execution waves and expected round trips saved;
- model-control decisions and transitions;
- acceptance retries and actual spend including retries.

## Acceptance bundles

`dynosai debug acceptance` can package normalized logs, provider protocol evidence, workspaces, summaries, Oracle results, runtime audits, and consolidated matrix metrics into a portable ZIP.

The acceptance harness deliberately preserves failed infrastructure attempts when a safe retry is used. Certification totals and actual paid totals are kept separately so retries cannot hide cost or evidence.

## Diagnostics

Useful commands include:

```bash
dynosai doctor --deep
dynosai env
dynosai diagnose
dynosai usage
dynosai debug acceptance-status
dynosai debug acceptance-logs
```
