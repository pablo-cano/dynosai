# User Guide

## Mental model

DynosAI coordinates three different sources of authority:

- **SQLite (`.dynosai/knowledge.db`)** — workflow, requirements, tasks, decisions, evidence, scopes, validations, audit history.
- **Git** — source-code truth and change history.
- **Provider session** — temporary reasoning and code-generation context; useful, but not authoritative.

The goal is to make an agent session behave like a controlled engineering workflow rather than an unstructured chat.

## Typical feature lifecycle

### Discovery

The agent inspects the repository and retrieves relevant project knowledge. DynosAI bounds context and records decisions instead of injecting the entire repository indiscriminately.

### Specification

The feature is represented as requirements, acceptance criteria, rules, unknowns, scope, and out-of-scope statements. DynosAI checks traceability and blocks materially incomplete specifications.

### Plan

The plan maps the approved specification to files, actions, tasks, dependencies, validation profiles, risks, and migration strategy where applicable.

### Implementation

The agent receives an authoritative task/scope contract. DynosAI compares later claims with the actual Git diff and planned file actions.

Short dependent task chains may be exposed as an **execution wave**, allowing one bounded edit/validation/register cycle while preserving dependency order.

### Code review

DynosAI first requires independently verified evidence. The managed agent is asked to review behavior against requirements and regressions; a human code-review gate remains explicit.

### Validation

Selected project validation profiles are executed. DynosAI stores real exit codes and output and classifies failures so infrastructure/precondition problems are not automatically treated as model-capability failures.

### Merge

Unresolved scopes, gates, validation blockers, and state inconsistencies prevent completion. Git merge/commit authority remains governed by DynosAI Core in managed sessions.

## Common commands

```bash
dynosai status                 # current work and phase
dynosai continue               # compute the next provider-neutral action
dynosai review                 # inspect pending human decisions
dynosai context                # bounded context for current work
dynosai ask "..."              # retrieve persisted project knowledge
dynosai stats                  # project/work statistics
dynosai scorecard              # governance quality score
dynosai usage                  # token telemetry
dynosai doctor --deep          # environment/runtime diagnostics
dynosai backup                 # portable state snapshot
```

## Scope changes

If an agent needs a file that was not in the approved plan, DynosAI treats that as a scope-extension request instead of silently widening access. The request can be approved, changed, or cancelled through the governance interaction.

## Recovering a session

If the provider process disappears or a new chat starts, use `dynosai resume --provider <provider>`. DynosAI reconstructs active tasks, scope, decisions, evidence, and the next action from durable state.

## Greenfield versus brownfield

- **Greenfield:** the specification is created from the requested behavior and new repository context.
- **Brownfield:** DynosAI first indexes existing code, symbols, tests, and relationships to build an evidence-backed inferred baseline. Inferred behavior is marked as inferred, not as an approved business requirement.

See [Brownfield workflows](BROWNFIELD.md) for details.
