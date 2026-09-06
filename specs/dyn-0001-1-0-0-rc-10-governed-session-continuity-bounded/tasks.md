---
dynosai_work_id: DYN-0001
source: knowledge.db
---

# Tareas

- [ ] **DYN-0001-T01 — Codex same-session continuation**
  - Implement max_provider_turns=3, progress_fingerprint, short continuation prompt, and CodexAppServerDriver loop: after turn/completed consult fixture DynosAI state; continue with turn/start on the same threadId or terminate for done/terminal/no-progress/budget. Keep one App Server, one thread_id, one CODEX_HOME. Fail immediately on a second governed DYN work or a new thread/session. Accumulate tokens across turns without double-counting. Keep existing human-gate autoresponder.
- [ ] **DYN-0001-T02 — MATRIX turn evidence and 1.0 eligibility**
  - Record provider_session_id, thread_id, turn_count, per-turn evidence, and session aggregates under a single MATRIX attempt. Add 1.0 eligibility that requires Codex greenfield and brownfield candidate PASS only. Cursor cells stay in MATRIX as Preview evidence and must not be deleted or converted to PASS.
- [ ] **DYN-0001-T03 — Synthetic continuation tests A-G**
  - Add no-model tests: A two-turn implementing-then-done PASS; B three-turn with progress then done PASS; C budget exhausted FAIL; D no-progress FAIL; E gate in turn 1 then continue same work PASS; F second DYN work FAIL; G new thread/session FAIL. Prove one MATRIX attempt, one thread/start, and no hidden retries.
- [ ] **DYN-0001-T04 — RC10 version and provider-policy docs**
  - Bump 1.0.0rc10 / 1.0.0-rc.10. Add docs/RELEASE_1.0.0-rc.10.md. Update ROADMAP, README, PROVIDERS, COMPATIBILITY, MATRIX docs, and website so Core is STABLE, Codex is Certification pending, Cursor is Preview/not 1.0 certified. Preserve RC9 identity and live FAILs. Update version-pin tests required for release_gate. Do not run live providers.
