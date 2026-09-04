# DynosAI 1.0.0-rc.4

Public RC · Installation. This is not stable 1.0.

Package version: `1.0.0rc4` (PEP 440). Display: `1.0.0-rc.4`.
Development classifier remains `Development Status :: 4 - Beta`.
Database schema remains **v6**.
MCP unique surface remains **31**.

## What RC4 proves

- Existing `doctor` / `deep_doctor` checks are visible in Studio (Overview and Diagnostics) through `GET /api/doctor`. The checks are not rewritten.
- Wheel, source ZIP and `SHA256SUMS.txt` still come from `scripts/build_release.py`.
- Installation is documented from those real artifacts. `scripts/install_local_wheel.py` verifies SHA-256 and refuses remote/index URLs, including pypi.org.
- DynosAI is not published on PyPI. There is no curl-pipe installer.
- CI keeps the broad Python matrix on Linux and adds an installed-wheel smoke job on Ubuntu, Windows and macOS against Python 3.11.
- That smoke runs in a fresh venv, from a temporary directory outside the checkout, with `PYTHONPATH` cleared. It is not a checkout `src/` import labeled as an installed-package test.
- A representative 0.19.0 schema-v6 project state is opened by the 1.0 wheel without wiping schema v6 or existing work.

## What RC4 does not prove

- No live-provider 1.0 certification. `MATRIX_1.0` cells remain `not_run`.
- No published PyPI package.
- No desktop `.msi` / `.dmg` installer.
- No OS-level sandbox, Docker, VM or remote execution runtime.
- No additional certified clients.
- No autonomous predictive routing.
- No claim of production-ready 1.0.

## Next

RC5 (threat model, contribution/certification process and freeze) is delivered in `1.0.0-rc.5`.
