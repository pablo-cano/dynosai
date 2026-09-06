# DynosAI 1.0.0-rc.10

Package version: `1.0.0rc10` (PEP 440). Display: `1.0.0-rc.10`.
Public status: **RC10 · Governed Session Continuity**. Schema remains v6.

This candidate is **not** stable 1.0. Codex is **Certification pending**.
Cursor is **Preview / not 1.0 certified**. No tag, GitHub Release, or PyPI
publish is part of this candidate.

## Why RC10 exists

RC9 proved provider connectivity but also demonstrated that requiring the
entire governed lifecycle to finish in a single provider turn does not match
the session-oriented lifecycle exposed by supported coding agents.

RC9 Codex live cells reached managed runtime PASS, DynosAI MCP active, zero
MCP failures, DYN work created, Spec Review and Plan Review accepted, then
stopped at `implementing` when the model turn completed. The oracle was
`work_not_done`. That is not an MCP startup failure.

RC10 certifies bounded multi-turn continuation inside one immutable provider
session. Cursor remains Preview for the 1.0 release and is not a release
blocker.

## Product policy for 1.0

| Layer | 1.0 status |
|---|---|
| Core | STABLE (workflow, scope, Git, validation, human gates) |
| Codex | Certification target / required for 1.0 |
| Cursor | Preview / non-blocking |

1.0 eligibility for this candidate requires Core release gates, CI, installed
wheel, website, Codex MCP preflight, and **Codex greenfield + Codex brownfield**
PASS of the exact candidate identity. Cursor MATRIX cells are retained as
Preview evidence. Their FAIL must not be rewritten as PASS.

Do not mark Codex Certified until a later human-approved RC10 live campaign
passes both Codex cells.

## Trial is not a turn

A certification trial is one provider session:

```text
Certification Trial
    └── one Provider Session
           ├── Turn 1
           ├── Turn 2 continuation
           └── Turn 3 continuation
```

Constraints that remain strict:

- one App Server process / session
- one `thread_id`
- one `CODEX_HOME`
- one workspace
- one candidate identity
- one governed DYN work item
- one continuous evidence stream
- at most `max_provider_turns = 3` (initial turn included)
- no hidden MATRIX retries
- no fourth turn
- no new `thread/start` for continuation

After `turn/completed` the harness consults fixture DynosAI authority, not the
model text. If the work is `done`, the session ends and the oracle runs. If the
work is still progressable, a short continuation `turn/start` is issued on the
same thread. If three turns complete without `done`, the trial fails with
`continuation_budget_exhausted`. If a turn makes no authoritative progress, it
fails with `provider_no_progress`.

Final PASS still requires work `done`, validation/scope/Git/human gates/oracle
PASS, strict managed runtime, and exactly one governed DYN work item.

## RC9 evidence preserved

RC9 identity is immutable history:

```text
commit:
1ea7ef2aa95190ff207153e85ff6fdb71ec94ba7

subject:
335036ae659b7d8685d4f6560d4b34fe8ff057eef950d7af2592cd05f9d4a4c3
```

RC9 live cells remain FAIL on the candidate that produced them. They are not
deleted, rewritten, or converted into PASS.

## Cursor Preview

Cursor remains integrated. Its MATRIX cells stay visible.

Known RC9 Cursor evidence:

- greenfield: provider/model stopped before governed work creation
- brownfield: internal unable to open database file / ACP reconnects

A future release may migrate Cursor acceptance from one-shot print execution
toward ACP session continuity. That is not implemented in RC10.

Do not present Cursor as production-certified.

## What this candidate does not do

- live Codex or Cursor model certification runs during preparation
- RC11, tags, GitHub Release, or PyPI
- promoting the package version to `1.0.0`
- MCP protocol or schema changes
- lowering Done/oracle/scope/Git/human-gate/runtime success criteria
