# Brownfield Workflows

DynosAI supports existing repositories without pretending the current code is a complete product specification.

## Inferred AS-IS baseline

For brownfield work the indexer can inspect:

- files and languages;
- symbols and signatures;
- code relationships/call graph where available;
- tests and implementation links;
- SQL migrations and persisted schema context;
- existing DynosAI feature/evidence history.

The resulting baseline is explicitly **inferred** and evidence-backed. It can describe what the repository appears to do, but it is not silently promoted to approved business intent.

## Planning a brownfield change

The plan must identify the expected delta from the baseline: files, task order, validation, migration rules, compatibility constraints, and risks. Scope is then checked against the real Git diff during implementation.

## Data migrations

Migration-sensitive plans can record preservation strategy, semantics for existing rows, rollback/recovery intent, and verification. DynosAI's own schema migrations and state restore are separately integrity-checked by the Core.

## Stable acceptance

The final 0.13.0 promotion evidence includes the `orderflow-contract-discounts` brownfield scenario on both Codex and Cursor. Both completed with Quality 100 and 10/10 Oracle checks in the consolidated acceptance matrix.
