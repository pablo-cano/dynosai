# DynosAI 1.0.0-rc.6

Package version: `1.0.0rc6` (PEP 440). Display: `1.0.0-rc.6`.
Public status: **Public RC · Standards & Release Evidence**. Schema remains v6.

This candidate is the last planned release candidate before `1.0.0`. `1.0.0-rc.7`
exists only as contingency if live RC6 certification finds defects.

## Delivered

- MCP revisions `2026-07-28`, `2025-11-25` and `2025-06-18` on stdio. 2026 is
  stateless (`server/discover`, per-request `_meta`). Legacy initialize remains
  for Cursor/Codex. Unique MCP names stay 31.
- `scripts/release_gate.py` is the single executable release gate for CI,
  `scripts/build_release.py` and local preparation.
- MATRIX_1.0 trial schema and `scripts/run_matrix_1_0.py` (probe by default;
  `--live` is one explicit attempt per cell, no silent retry-to-pass).
- Attempt 1 live trials (2026-09-04, Windows) all failed honestly: Codex
  stdin UTF-8/cp1252, OrderFlow SQLite tempdir on Windows, and Cursor calling
  `dynosai_get_next_action` before `dynosai_project` (PermissionError). Those
  DynosAI defects were fixed; attempt-1 zips are preserved.
- Attempt 2 live trials (explicit, one per cell) also failed. Codex greenfield
  could not `dynosai_work start` because GitManager treated a nested fixture as
  the dirty parent DynosAI checkout. Codex brownfield created `DYN-0001` then
  exited with `oracle: work_not_done` and Codex patches outside the project.
  Cursor cells hit `RetriableError: unable to open database file` and recorded
  zero DYN work items. Git toplevel isolation is now in the candidate; MATRIX
  stays fail until a later explicit trial.
- Eval Registry catalog expanded to a Tier A / Tier B corpus with provenance
  and graders.
- PyPI name decision: distribution `dynosai`, import `dynosai_flow`, CLI
  `dynosai`/`dyno`. Not published.
- Install contract: verified local/GitHub Release wheel with checksums. A
  native desktop installer is not a 1.0 must-have.
- Agent Plugins ADR: future `plugin.json` + `extensions.com.dynosai`. No
  DynosAI Pack format. No plugin runtime in RC6.
- `docs/CODE_QUALITY.md` regenerated from the current tree.

## Not claimed

- production-ready stable `1.0.0`
- live MATRIX_1.0 PASS unless a real provider trial recorded it
- official HTTP MCP conformance (DynosAI is stdio)
- OS sandbox, Docker/VM/remote execution
- PyPI publication
- autonomous predictive routing
- plugin marketplace

Promotion to `1.0.0` stays reserved until the documented gates are actually
green. Remaining on `1.0.0rc6` is an honest successful outcome.
