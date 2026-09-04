# DynosAI 1.0.0-rc.5

Public RC · Freeze. This is not stable 1.0.

Package version: `1.0.0rc5` (PEP 440). Display: `1.0.0-rc.5`.
Development classifier remains `Development Status :: 4 - Beta`.
Database schema remains **v6**.
MCP unique surface remains **31**.

## What RC5 proves

- The 1.0 threat model is documented in `docs/THREAT_MODEL.md` and linked from `SECURITY.md`. Enforced controls are listed separately from controls that are not shipped.
- Loopback, Host-header checks and JSON POSTs are regression-tested. They are **not** a full security boundary against other local processes or browsers.
- `CONTRIBUTING.md` documents maintainer development, the release gate and the certified-provider process. External pull requests remain **not currently accepted**. Publishing this file does not open the repository to unsolicited PRs.
- Studio Help states the local-browser honesty in English and Spanish.
- Enforced path roots, symlink escape, secret/context refusal, certified-client refusal, Git policy, human gates (including Autonomous), host-owned execution profiles, process timeout and unsupported Docker/VM runtimes stay in regression coverage. Those checks are not rewritten.

## What RC5 does not prove

- No live-provider 1.0 certification. `MATRIX_1.0` cells remain `not_run`.
- No published PyPI package.
- No desktop `.msi` / `.dmg` installer.
- No OS-level sandbox, Docker, VM or remote execution runtime.
- No additional certified clients.
- No autonomous predictive routing.
- No claim of production-ready 1.0.
- No opening of external pull requests.

## Next

Promotion to `1.0.0` stays reserved until live Codex/Cursor greenfield and brownfield cells are real `pass` evidence and the remaining 1.0 exit criteria are actually green. Remaining on `1.0.0rc5` is an honest successful outcome. Do not start that promotion from this note alone; wait for an explicit instruction.
