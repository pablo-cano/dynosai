# Quality and Validation

This document defines exactly what DynosAI means by verification, validation, and quality.

## 1. Independent result verification

An agent cannot make a task authoritative by saying "done". DynosAI compares the registered result with repository and workflow evidence, including:

- actual Git paths and file status;
- approved file scope;
- planned create/modify/delete/test roles;
- completed tasks and dependencies;
- requirement and acceptance-criterion links;
- validation/evidence records;
- unresolved governance interactions.

Code approval requires a verified run; the model's own completion statement is not sufficient.

## 2. Validation profiles

DynosAI executes approved commands as subprocesses with captured output, timeout handling, and real exit codes. Profiles can represent:

- `unit` — unit tests;
- `lint` — static style/lint checks;
- `typecheck` — type checking;
- `build` — compile/build/package checks;
- `integration` — integration or system tests;
- project-specific validation commands.

Passing tests are not automatically the completion standard. Validation Integrity connects requirements, acceptance criteria, evidence and validation results. Self-authored tests created in the same implementation must not be the only proof for high-risk changes. The current default initialization creates a unit-test profile from the configured `test_command`. In 0.15.0, DynosAI also discovers common repository-native candidates (pytest/Ruff/mypy, npm scripts/TypeScript, Cargo, .NET, Maven/Gradle). **Discovery is read-only**: candidates require explicit user approval before they become governed validation profiles.

## 3. Does DynosAI check compilation?

**When a build/compile validation profile is configured and selected: yes.** DynosAI runs that command and records its actual status.

**Universally for every project without configuration: no.** A Python, TypeScript, .NET, Rust, or Java project has different build semantics, so DynosAI does not pretend one command is correct for every repository.

## 4. What Quality 100 means

The `QualityEngine` scores the structure and governance of the work. It looks for issues such as:

- specifications without requirements or acceptance criteria;
- weak or unlinked acceptance criteria;
- unresolved unknowns/placeholders;
- plans without baseline, validation, or constitution checks;
- migration work without migration governance;
- generic tasks or tasks without evidence;
- circular dependencies;
- unresolved scope requests.

A blocker has a larger score impact than a warning. A score of `100` means no quality issues were detected by these DynosAI governance rules.

It does **not** claim perfect:

- architecture;
- security;
- performance;
- cyclomatic complexity;
- maintainability;
- test coverage;
- accessibility;
- domain correctness beyond encoded requirements and validations.

Those dimensions should be enforced with project validations and, where appropriate, specialist tools or human review.


## 5. Risk Assessment v1

Risk is intentionally separate from Quality. Quality reports governance/traceability findings for a work item; Risk is an advisory signal used to communicate likely review intensity.

The deterministic 0.14 scorer can raise risk for security/authentication surfaces, schema/migration changes, dependency/build manifests, CI changes, large file blast radius, pending scope extensions and pending human decisions. A High risk result does not mean the change is wrong, and a Low risk result does not mean it is safe. Risk never bypasses the normal workflow.

## 6. Semantic code review

Managed-agent guidance asks the coding agent to inspect the diff, trace behavior to requirements, look for regressions, and check maintainability/validation gaps. A human code-review gate remains explicit.

0.19.0 still does not automatically launch a second independent reviewer model for every change. Reviewer work is the human code-review gate; tester work is the governed validation contract on the same lease. Eval intelligence can open an inbox improvement task from a mined failure or an imported acceptance ZIP; it does not auto-start a provider. Execution profile Autonomous does not skip those gates. `1.0.0-rc.7` keeps those boundaries. `MATRIX_1.0` live cells record real trials (currently explicit fails) or stay honestly `not_run`; they do not copy the historical 0.13 matrix.

## 7. Failure classification

DynosAI avoids learning "the model failed" from every non-zero outcome. It distinguishes, among others:

- syntax/test/spec/scope failures;
- provider/tool failures;
- validation preconditions/environment failures;
- human-gate/governance outcomes;
- orchestrator/telemetry failures.

Only evidence that is explicitly eligible should influence predictive model-capability learning. 0.17 attributes each failure to a layer first; governance, provider, harness and infrastructure outcomes stay out of predictive learning.

## 8. Stable acceptance evidence

The 0.13.0 promotion source (`0.12.9 RC4`) passed Codex and Cursor in both greenfield and brownfield scenarios with full Oracle checks, Quality 100, zero MCP failures/rejections/scopes, and zero infrastructure retries. See [`validation/final-matrix-0.13.0.json`](validation/final-matrix-0.13.0.json).
