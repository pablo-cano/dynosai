# Contributing to DynosAI

External pull requests are **not currently accepted** while the public API,
packaging and contribution model settle. That policy is intentional. Adding
this file does **not** open the repository to unsolicited PRs.

Bug reports and practical feedback are welcome through
[GitHub Issues](https://github.com/pablo-cano/dynosai/issues). Do not attach
secrets, private acceptance bundles, or exploit details to a public issue;
see `SECURITY.md`.

## Development (maintainers and invited collaborators)

Requirements: Python 3.11+, Git, Node.js 22+ if you change `apps/web`.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

Release gate (same executable in CI, `scripts/build_release.py` and local work):

```bash
python scripts/release_gate.py
```

That script runs:

```bash
python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python scripts/check_studio_sync.py
python -m pytest
```

When Studio files change, keep `apps/studio/` and
`src/dynosai_flow/studio_assets/` byte-identical. When website files change,
also run `npm run check`, `npm run typecheck`, `npm run lint` and `npm run build`
from `apps/web`.

Schema authority is **schema v6**. Unique MCP names stay **31**. Do not add
`DYNOSAI_DISABLE_*` kill-switches for human gates, execution profiles,
certification or path security. Predictive routing stays shadow-only.

## Certified-provider process

Cursor ACP and Codex app-server are the supported 1.0 target Studio transports. 1.0 live certification is pending MATRIX_1.0.

A later client is **not** certified by shipping an adapter sketch or by
passing checkout fixtures. Certification requires:

1. a host-owned capability manifest naming the real transport;
2. refusal of uncertified adapters and project extension packs;
3. real-provider `MATRIX_1.0` evidence for that client on greenfield **and**
   brownfield (not copied 0.13 scores);
4. the same human gates, Git/knowledge.db authority and path policy as Cursor
   and Codex.

Until those cells are real `pass` evidence, `MATRIX_1.0` stays `not_run` (or
records an explicit fail with attribution) and `1.0.0` stays reserved.
Remaining on `1.0.0rc7` is an honest successful outcome.

## Issues

Use GitHub Issues for defects, documentation gaps and certification questions.
Maintainers may request a private channel for security-sensitive reports.
