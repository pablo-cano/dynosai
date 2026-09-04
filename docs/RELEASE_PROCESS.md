# Release Process

## Versioning

Pre-1.0 used semantic-style numbering. From 1.0 release candidates, Python/package versions follow PEP 440 (`1.0.0rc6`). Display surfaces may show `1.0.0-rc.6`.

`1.0.0` remains reserved until the documented certification and installed-package gates are actually green. Remaining on `1.0.0rcN` is an honest successful outcome.

See `docs/COMPATIBILITY.md` for the frozen 1.0 contract.

## Changelog practice

`CHANGELOG.md` follows a Keep-a-Changelog-style structure. Add user-visible changes under `Unreleased`, then move them into the new version at release time.

Recommended headings:

- Added
- Changed
- Fixed
- Security
- Validation
- Deprecated / Removed when applicable

Avoid changelog entries such as "misc fixes". Describe behavior and why it matters.

## Commit practice

Conventional Commit prefixes are recommended:

```text
feat: add bounded execution wave support
fix: preserve Cursor validation details in MCP text transport
docs: document quality score semantics
refactor: isolate token attribution helpers without behavior change
test: add corrupted snapshot recovery coverage
chore: prepare 0.13.1 release metadata
```

## Pull-request / release gate

The supported release gate is one executable shared by CI, `scripts/build_release.py` and local release preparation:

```bash
python scripts/release_gate.py
```

That is the only source of truth. It runs:

```bash
python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python scripts/check_studio_sync.py
python -m pytest
```

`python -m pytest` collects the stable regression suite. Historical aggregate suites `tests/test_07.py`, `tests/test_brownfield06.py`, `tests/test_hardening.py` and `tests/test_runtime05.py` stay in the repository and are excluded unless `DYNOSAI_HISTORICAL_TESTS=1`.

Changes that affect provider behavior, MCP contracts, state transitions, scope, validation, routing, recovery, or acceptance orchestration require focused regression tests.

When `apps/web` or website metadata change:

```bash
cd apps/web
npm run check
npm run typecheck
npm run lint
npm run build
```

Do not report a green release if a required command did not actually run.

## Git tags

Public annotated tags are created only for promoted releases. Inspect `git tag --list` before assuming a version exists on the remote.

The 0.19.0 source baseline is commit `9e4c69eaabb34b687e58d1b4eb9bc3091d42d58d` (`feat: release DynosAI 0.19.0 ecosystem interoperability`). A historical tag was not present on that commit at RC1 freeze time. If one is needed later, use an annotated tag on **that** commit, not on whatever happens to be `HEAD`:

```bash
git tag -a v0.19.0 9e4c69eaabb34b687e58d1b4eb9bc3091d42d58d -m "DynosAI 0.19.0"
```

Future 1.0 candidates may use:

```bash
git tag -a v1.0.0-rc.6 -m "DynosAI 1.0.0-rc.6"
```

Do not push tags or GitHub releases from an RC implementation task unless explicitly instructed.

## Stable promotion

A stable promotion should minimize code delta from the accepted release candidate. Provider acceptance claims must retain machine-readable evidence. Infrastructure retries must remain transparent and should never turn a functional failure into a pass.
