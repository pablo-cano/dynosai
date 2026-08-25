# Testing Strategy

DynosAI uses several test layers because a coding-agent governance system can appear correct at one layer while failing at another.

## 1. Deterministic unit and regression tests

These tests exercise workflow state, schemas, scope rules, Git evidence, MCP contracts, validation classification, token accounting, routing decisions, and provider adapters without paying for model calls.

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
