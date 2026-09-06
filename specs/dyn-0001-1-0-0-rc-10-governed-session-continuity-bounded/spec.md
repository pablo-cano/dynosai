---
dynosai_id: SPEC-DYN-0001
version: 7
status: approved
source: knowledge.db
---

# 1.0.0-rc.10 â€” Governed Session Continuity: bounded multi-turn Codex certification in one

## Objetivo

Prepare DynosAI 1.0.0rc10 so a Codex certification trial can use a bounded number of continuation turns inside one App Server session/thread, without lowering Done/oracle gates, without rewriting RC9 evidence, and with Cursor as Preview non-blocking for 1.0.

## Alcance

- Bounded same-session Codex continuation in the existing Codex App Server acceptance driver after turn/completed: inspect authoritative DynosAI work state in the fixture workspace; if progressable, send a short continuation turn/start on the same threadId without thread/start, new CODEX_HOME, new App Server process, or new MATRIX attempt.
- Progress fingerprint after each turn; fail provider_no_progress when fingerprint is unchanged without a valid reason; fail continuation_budget_exhausted after max_provider_turns=3 counting the initial turn.
- Per-turn evidence plus session aggregates (tokens, cost, runtime, MCP calls) while MATRIX attempt remains one.
- Synthetic no-model tests for cases A-G of session continuity.
- Release-policy documentation: Core STABLE; Codex certification required for 1.0; Cursor Preview/non-blocking; preserve Cursor MATRIX cells and RC9 FAILs.
- Version bump to 1.0.0rc10 / 1.0.0-rc.10 and docs/RELEASE_1.0.0-rc.10.md.
- 1.0 eligibility semantics: core gates plus Codex greenfield and brownfield candidate PASS; Cursor cells retained but not required.
- Deterministic RC10 preparation gates only. No live model turns in this work.

## Fuera de alcance

- Live Codex or Cursor model certification runs.
- Creating RC11, tags, GitHub Release, or PyPI publish.
- Promoting version to 1.0.0.
- MCP protocol or JSON schema changes.
- Unrelated refactors or 1.1+ roadmap features.
- Implementing Cursor ACP/session continuity or retrying Cursor live cells.
- Deleting Cursor MATRIX cells or converting Cursor FAIL into PASS.
- Rewriting or deleting RC9 live trials (commit 1ea7ef2aa95190ff207153e85ff6fdb71ec94ba7, subject 335036ae659b7d8685d4f6560d4b34fe8ff057eef950d7af2592cd05f9d4a4c3).
- Lowering Done/oracle/scope/Git/human-gate/strict-runtime success criteria.
- Auto-approving human gates from the model.
- Restarting CLI/App Server to emulate continuity.
- Fourth continuation turn or automatic MATRIX retries.

## Requisitos

### REQ-001

A Codex certification trial is one provider session (one App Server process, one thread_id, one CODEX_HOME, one workspace, one candidate, one governed DYN work, one evidence stream) that may contain up to max_provider_turns=3 model turns.

### REQ-002

After turn/completed the harness must not terminate the App Server until it has consulted authoritative DynosAI work state in the fixture (not the model text). If state is done, terminate and run oracle. If a genuine terminal failure exists, terminate FAIL. If the work is still progressable, start turn/start on the same threadId with a short continuation prompt.

### REQ-003

Continuation must use official Codex App Server lifecycle: same initialize connection, same thread_id, subsequent turn/start only. No thread/start for continuation, no thread/fork, no new CODEX_HOME, no new App Server.

### REQ-004

Continuation prompts must be short and recover state from DynosAI (existing work_id and current state allowed). They must not resend the full initial prompt, business goal, specs, plan, tasks, provider config, or huge history.

### REQ-005

If three turns complete and work is not done, fail with continuation_budget_exhausted. After each turn capture progress_fingerprint from fixture DynosAI authority as: work state, current phase, current action, completed task count, validation state, observed gate state, and Git/evidence revision. If a turn ends with an unchanged fingerprint without a valid reason (for example a resolved human gate that enables the next legal action), fail with provider_no_progress. Neither is a MATRIX retry.

### REQ-006

Each trial must record provider_session_id, thread_id, turn_count, per-turn records (index, turn_id, start/end, status, token usage, DynosAI calls, work state before/after, progress made) and session totals for tokens, cost, runtime, and MCP calls. MATRIX attempt numbering stays one per cell execution.

### REQ-007

Human gates remain real (Spec, Plan, Code, Merge) with the existing acceptance-only autoresponder. A later turn may continue after a gate resolved in an earlier turn. Gates must not be removed or auto-approved by the model.

### REQ-008

Token accounting must accumulate exact session usage across turns without double-counting, recording turn usage and session usage including cached input, input, output, and reasoning.

### REQ-009

Synthetic no-model tests must cover: A two-turn implementing-then-done PASS; B three-turn with progress then done PASS; C budget exhausted FAIL; D no-progress FAIL; E gate in turn 1 then continue same work PASS; F second DYN work FAIL; G new thread/session FAIL.

### REQ-010

Final PASS criteria stay strict: exactly one governed DYN work, final state done, validation/scope/Git/human gates/oracle PASS, strict managed runtime verified, same candidate identity, same provider session, zero hidden retries.

### REQ-011

1.0 eligibility requires Core release gate, CI, installed wheel, website, Codex MCP preflight, and Codex greenfield+brownfield candidate PASS. Cursor greenfield/brownfield remain in MATRIX as Preview evidence and do not block 1.0.

### REQ-012

Document Core STABLE, Codex Certification pending (Certified only after future RC10 live PASS), Cursor Preview/not 1.0 certified in ROADMAP, README, website, compatibility/provider docs, release notes, MATRIX docs, plus docs/RELEASE_1.0.0-rc.10.md. Do not present Cursor as production-certified.

### REQ-013

Preserve RC9 identity and live FAILs. Version this candidate as 1.0.0rc10 / 1.0.0-rc.10. Do not run live providers during this preparation work.

## Criterios de aceptación

### AC-001 → REQ-001

Given a Codex auto acceptance driver, when a trial runs, then at most three turn/start calls occur on one thread_id in one App Server process, and a fourth turn is never issued.

### AC-002 → REQ-002

Given turn/completed while DynosAI authoritative state is implementing and progressable, when the driver handles it, then it does not terminate yet and issues another turn/start on the same threadId.

### AC-003 → REQ-002

Given turn/completed while DynosAI authoritative state is done, when the driver handles it, then it terminates the session and the existing oracle/acceptance path runs.

### AC-004 → REQ-003

Given a continuation, when inspecting the App Server trace, then there is exactly one successful thread/start for the trial and continuation uses turn/start with that threadId.

### AC-005 → REQ-004

Given a continuation turn, when inspecting its input text, then it is a short continue-from-authoritative-state prompt and does not include the full original certification prompt.

### AC-006 → REQ-005

Given three turns still implementing, then the trial fails with continuation_budget_exhausted and does not start another MATRIX attempt.

### AC-007 → REQ-005

Given a completed turn whose progress_fingerprint equals the previous fingerprint without a valid reason, then the trial fails with provider_no_progress and does not spend remaining turn budget blindly.

### AC-008 → REQ-006

Given a multi-turn trial, then the trial record exposes turn_count, per-turn evidence, and aggregated session tokens/cost/runtime/MCP calls under a single MATRIX attempt.

### AC-009 → REQ-007

Given a Spec/Plan elicitation resolved in turn 1, when turn 2 continues, then the same work_id is used and gates are still harness-autoresponded, not model-autoapproved or removed.

### AC-010 → REQ-008

Given two turns with exact provider usage, then session totals equal the sum of turn usages without duplicated counts.

### AC-011 → REQ-009

Given synthetic cases A-G, when the no-model suite runs, then A/B/E PASS and C/D/F/G FAIL with the specified attributions.

### AC-012 → REQ-010

Given a trial that reaches Done with one governed DYN work and passing oracle/gates/runtime, then it PASS; provider_exit=0 after a single incomplete turn is not sufficient.

### AC-013 → REQ-011

Given candidate certification status for a candidate identity, when evaluating 1.0 eligibility, then only Codex greenfield and Codex brownfield are required; Cursor cell FAILs do not make core 1.0 eligibility false while Cursor is Preview.

### AC-014 → REQ-012

Given RC10 docs and support matrix, then Codex is Certification pending, Cursor is Preview, RC9 Codex connectivity vs single-turn limitation is explained, and Cursor is not labeled production-certified.

### AC-015 → REQ-013

Given the RC9 MATRIX trials and identity, when RC10 preparation lands, then those historical trials remain and package/docs versions are 1.0.0rc10 / 1.0.0-rc.10.

## Reglas de negocio

- **RULE-001** one trial != one model turn; one trial == one provider session.
- **RULE-002** Continuation is not a retry and must not increment MATRIX attempt numbers.
- **RULE-003** DynosAI store is the authority for work state; model text is not.
- **RULE-004** Cursor Preview is non-blocking for 1.0; Codex greenfield and brownfield remain blocking.
- **RULE-005** Do not mark Codex Certified until a later human-approved RC10 live campaign PASSes both Codex cells.
- **RULE-006** RC9 live evidence is immutable history.
- **RULE-007** No live model turns during this preparation work.

## Incertidumbres

- Ninguna ambigüedad material pendiente.
