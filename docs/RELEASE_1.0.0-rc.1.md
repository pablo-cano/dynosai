# DynosAI 1.0.0-rc.1

Public RC · Contract freeze. This is not stable 1.0.

Package version: `1.0.0rc1` (PEP 440). Display: `1.0.0-rc.1`.
Development classifier remains `Development Status :: 4 - Beta`.
Database schema remains **v6**.

## What RC1 freezes

- Product authority: the coding agent writes code; DynosAI governs scope, workflow state, Git evidence, validation, durable memory, human authority and the audit trail.
- Unique MCP tool names (31), regression-tested in `tests/test_210.py`.
- Public CLI compatibility scope in `docs/COMPATIBILITY.md`. Debug/acceptance internals are not frozen.
- Studio ↔ App Server compatibility for bundled vanilla Studio. The App Server is not a general third-party public API.
- Optional harness features reuse `DYNOSAI_HARNESS_*` with environment → project → default precedence:
  - context handles (creation off keeps historical `dynosai_retrieve_handle`);
  - governed team scheduling (`dynosai_schedule` returns `feature_disabled`; serial work continues);
  - eval intelligence (propose/mining off; historical cases remain readable).
- Auto-approve remains opt-in and off by default.
- Human gates and execution-profile enforcement are not kill-switches.
- One release-gate policy: `python -m pytest` matches CI and `scripts/build_release.py`. Historical aggregate suites stay in the repository and stay excluded unless `DYNOSAI_HISTORICAL_TESTS=1`.

## What RC1 does not prove

- No new installer or published PyPI package.
- No 1.0 live certification matrix. Codex/Cursor × greenfield/brownfield cells remain `not_run` until real provider runs exist. Historical Quality 100 / 0.13 evidence is not copied forward as 1.0 proof.
- No OS-level network sandbox, Docker, VM or remote execution runtime.
- No additional certified clients beyond Cursor ACP and Codex app-server.
- No autonomous predictive routing.
- No claim of production-ready 1.0.

Remaining on a later `1.0.0rcN` is a successful honest outcome if those gates are incomplete.

## Git tags

Do not assume `v0.19.0` exists. The 0.19.0 source baseline is `9e4c69eaabb34b687e58d1b4eb9bc3091d42d58d`. See `docs/RELEASE_PROCESS.md` for annotated tag commands. This RC does not create or push tags.

## Next

RC2 is certification evidence and two host-opened governed sessions. Do not start it from this note alone; wait for an explicit instruction.
