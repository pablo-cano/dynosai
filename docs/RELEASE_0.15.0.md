# DynosAI 0.15.0 — Verified Agent Harness

0.15.0 sits on the stable 0.14.1 Studio/control-plane baseline (444 CI Python tests green before this work) and adds the minimum harness needed before more autonomy.

## What this release is

DynosAI remains a **governed control layer around coding agents**, not a replacement for Codex, Cursor or future providers. 0.15 makes longer work cheaper to context-manage and harder to complete without evidence, without handing security or completion to the model.

## Shipped

- Persistent context handles and selective retrieval (`dynosai_retrieve_handle`).
- `ExecutionRuntime` with a local default implementation.
- Filesystem/process policy tests (escape, secrets, timeout, child-process ownership stays in `provider_process.py`).
- Network and dependency **policy APIs** (default compatibility: unrestricted network, installs allowed but classified).
- Secret redaction and a refuse-closed secret broker.
- Validation Integrity on code/merge Studio reviews, plus bounded diffs.
- Eval Registry v0 and skill on/off size comparison (offline fixtures).
- Cost-per-successful-governed-change raw metrics.
- Architecture import checks.

## Deliberately not shipped

- General multi-agent teams (0.16).
- OS-level network sandboxing for arbitrary child processes (0.18).
- A configured vault that injects secrets into runtimes.
- Live-provider eval leaderboards.
- Silent replanning that expands approved scope.

## Authority unchanged

Schema remains v6. Git is source truth. `.dynosai/knowledge.db` is workflow truth. Cursor ACP and Codex app-server transports from 0.14.1 are unchanged in role.

## Validation

Release still requires repository checks, Studio asset sync, the stable pytest suite, and website check/typecheck/lint/build when web files change.
