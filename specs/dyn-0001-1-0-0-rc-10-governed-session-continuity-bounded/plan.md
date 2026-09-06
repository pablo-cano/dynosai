---
dynosai_id: PLAN-DYN-0001
version: 2
status: approved
source: knowledge.db
---

# Plan técnico — 1.0.0-rc.10 â€” Governed Session Continuity: bounded multi-turn Codex certification in one

## Referencias

- Trabajo: DYN-0001
- Spec: SPEC-DYN-0001 v7
- Baseline: BASELINE-001 v1.1.0

## Enfoque

Add bounded same-thread Codex continuation inside the existing Codex App Server acceptance driver. After turn/completed, inspect fixture DynosAI authority, then either terminate or issue a short turn/start on the same threadId. Split 1.0 eligibility so only Codex cells block release. Cursor stays Preview in MATRIX and docs. Bump to 1.0.0rc10 without live model runs.

## Delta arquitectónico

- CodexAppServerDriver.run no longer treats the first turn/completed as end of the provider session.
- A small session-continuity helper owns max_provider_turns=3, progress_fingerprint, continuation prompt, and FAIL reasons continuation_budget_exhausted / provider_no_progress / extra DYN work / new thread.
- Official Codex lifecycle: one initialize, one thread/start, subsequent turn/start on the same threadId. No new App Server or CODEX_HOME.
- MATRIX trial records one attempt with turn_count and session aggregates. 1.0 eligibility requires Codex greenfield+brownfield only; Cursor cells remain Preview evidence.
- Documentation and version identity become 1.0.0rc10 / 1.0.0-rc.10. Codex is Certification pending until a later live campaign.

## Archivos previstos

- `src/dynosai_flow/session_continuity.py` — create: Fingerprint, turn budget, continuation prompt, and continuation decision helpers used by the Codex driver and no-model tests.
- `src/dynosai_flow/acceptance.py` — modify: Keep the App Server process after turn/completed; inspect DynosAI authority; continue on the same threadId; record per-turn and session evidence.
- `src/dynosai_flow/certification_matrix.py` — modify: Persist turn evidence under one MATRIX attempt and distinguish core 1.0 eligibility (Codex required, Cursor preview) from all_cells_present.
- `src/dynosai_flow/version.py` — modify: Bump PEP440 and display versions to 1.0.0rc10 / 1.0.0-rc.10.
- `pyproject.toml` — modify: Package version 1.0.0rc10.
- `docs/RELEASE_1.0.0-rc.10.md` — create: RC10 release notes: trial != turn, Cursor Preview, RC9 evidence preserved.
- `ROADMAP.md` — modify: Document Core STABLE, Codex certification pending, Cursor Preview.
- `README.md` — modify: Provider support matrix; do not present Cursor as production-certified.
- `docs/PROVIDERS.md` — modify: Codex required for 1.0; Cursor Preview; future ACP continuity not implemented now.
- `docs/COMPATIBILITY.md` — modify: Compatibility/support matrix for Codex vs Cursor 1.0 status.
- `docs/validation/README.md` — modify: MATRIX 1.0 eligibility: Codex cells required, Cursor retained as preview evidence.
- `apps/web/app/page.tsx` — modify: Website must not claim Cursor is 1.0 certified.
- `apps/web/app/roadmap/page.tsx` — modify: Website roadmap reflects RC10 provider policy.
- `apps/web/app/validation/page.tsx` — modify: Website validation matrix distinguishes required Codex vs preview Cursor.
- `tests/test_291.py` — create: Synthetic no-model cases A-G for same-session continuation.

## Riesgos

- Inspecting DynosAI state from inside the still-running App Server must not open a second provider session or a second DYN work item.
- Existing Codex fake-server tests must keep passing when the driver waits for DynosAI state after the first turn/completed.
- Version-pin tests across the suite must be updated with the RC10 identity or release_gate will fail.
- Cursor live FAILs must remain visible; eligibility changes must not rewrite RC9 trials.

## Validación

- profile `unit`

## Constitution Check

- Trazabilidad requisito → tarea: PASS
- Evidencia por tarea: PASS
- Git gobernado por DynosAI: PASS
- Contexto mínimo: PASS
