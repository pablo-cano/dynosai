# DynosAI 0.14.0 Release Notes

DynosAI 0.14.0 is the first **product/control-plane release** built on top of the stable 0.13.0 governed SDD core.

It does not expand autonomous authority. Git remains source truth, SQLite remains workflow truth, human gates remain explicit, and Predictive Router remains shadow-only.

## Highlights

- Local-only App Server over `DynosAIApplication`.
- `dynosai studio` graphical control plane.
- Stack-aware onboarding hints.
- Read-only validation discovery plus explicit approval.
- Deterministic Risk Assessment v1.
- Explainable blockers for users who do not know the state machine.
- Updated public website and roadmap.

## Install from wheel

Create/activate a Python 3.11+ environment, then install the wheel:

```bash
python -m pip install dynosai_flow-0.14.0-py3-none-any.whl
```

The wheel declares its normal runtime dependencies (`fastembed`, `sqlite-vec`, `numpy`). If you are installing completely offline, install those dependencies from your own wheelhouse first.

Verify:

```bash
dynosai --version
# DynosAI 0.14.0
```

## Install from source

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
dynosai --version
```

## Start Studio

```bash
cd /path/to/project
dynosai studio
```

Default URL:

```text
http://127.0.0.1:8765/
```

Headless App Server:

```bash
dynosai app-server --port 8765
```

## Validation before publishing

Repository checks:

```bash
python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python scripts/check_studio_sync.py
```

Stable CI regression suite:

```bash
python -m pytest \
  --ignore=tests/test_07.py \
  --ignore=tests/test_brownfield06.py \
  --ignore=tests/test_hardening.py \
  --ignore=tests/test_runtime05.py
```

Website (Node.js 22):

```bash
cd apps/web
npm install --no-audit --no-fund
npm run check
npm run typecheck
npm run lint
npm run build
```

Wheel build:

```bash
python -m pip install -e ".[dev]"
python -m build
```

A no-network fallback for environments that already contain compatible setuptools/wheel is:

```bash
python -m pip wheel . --no-deps --no-build-isolation -w dist
```

## Migration and compatibility

- Database schema remains **v6**.
- Existing 0.13.0 projects can be opened by 0.14.0 without a product-specific schema migration.
- Existing CLI and MCP workflow contracts remain intact.
- Provider test shims and local acceptance probes are portable across Linux and Windows; Windows TOML paths are validated after parsing rather than as raw escaped text.
- The Local Studio is an alpha surface; its HTTP endpoints are not yet a 1.0 compatibility contract.

## Known limitations

- Studio 0.14.0 focuses on onboarding/overview/risk/validations/start-work. Rich spec/plan editors, diff review and requirement-to-code visualization are planned for 0.14.x.
- Local Studio is browser-based in this release; Tauri/desktop installers remain future roadmap work.
- Claude configuration exists, but the final real-provider baseline currently remains Codex + Cursor.
- Risk is deterministic and advisory, not a semantic security review.

## One-command release build

After installing development and website dependencies:

```bash
python scripts/build_release.py
```

This runs repository/Studio checks, the stable regression suite, website checks/build, wheel creation, source-ZIP creation and SHA-256 manifest generation. Use `--skip-web` or `--skip-tests` only for local diagnostics, not for release promotion.
