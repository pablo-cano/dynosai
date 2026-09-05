# DynosAI 1.0.0-rc.8

Package version: `1.0.0rc8` (PEP 440). Display: `1.0.0-rc.8`.
Public status: **RC8 · certification pending**. Schema remains v6.

This candidate is **not** stable 1.0. `MATRIX_1.0` is **not** declared PASS.

## Why RC8 exists

RC7 live certification discovered that managed Codex runtime could be generated
below the OS temporary directory. Current Codex refuses helper-binary creation
from temporary `CODEX_HOME` locations.

RC8 moves provider acceptance and certification workspaces to platform-local
persistent state and changes MATRIX candidate grouping from synchronized
attempt numbers to immutable candidate identity.

RC7 `codex.greenfield` attempt 3 remains historical FAIL evidence. It is a
provider runtime / environment layout incompatibility, not a Fibonacci, model,
or MCP protocol failure. RC8 does not copy that trial as its own proof.

## Integrity work in this candidate

- User-local state root (`%LOCALAPPDATA%\DynosAI`, macOS Application Support, XDG state)
- MATRIX default workspace: `<user-local>/certification/matrix-1.0` (not repo, not OS temp)
- Acceptance default workspace: `<user-local>/acceptance/<run-id>`
- Pre-provider abort when `CODEX_HOME` would sit under the OS temp directory
- `codex --version` managed-runtime preflight (no model turn)
- `candidate_certification_status` groups cells by `dynosai_git_commit` + `certification_subject_sha256`
- Attempt counters stay per-cell and may be asymmetric (`3/2/2/2` after RC7; expected RC8 complete `4/3/3/3`)
- `matrix["all_passed"]` means the current candidate has a latest matching PASS in all four cells

## What this RC does not claim

- Cursor and Codex are **supported 1.0 target providers**. They are **not** live-certified for 1.0 while `MATRIX_1.0` is not PASS.
- `codex --version` does not prove DynosAI MCP works.
- Roadmap 1.1–1.5 stay out of this candidate.
- Remaining on `1.0.0rc8` until MATRIX_1.0 is green is an honest successful outcome.

## Compatibility contract (unchanged)

- Schema v6
- 31 frozen public MCP names
- Git source authority, `knowledge.db` workflow authority, human gates
- Predictive Router remains shadow-only
- No fake OS sandbox

## Candidate identity

Canonical candidate:

```text
dynosai_git_commit
+
certification_subject_sha256
```

`candidate_version` is a human label (`1.0.0-rc.8`). Release gates do not require
the same attempt number across cells.
