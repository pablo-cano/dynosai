# Release Process

## Versioning

DynosAI uses semantic-version-style numbering while pre-1.0:

- patch release: compatible bug fix, documentation, diagnostics, or release hardening;
- minor release: new compatible capabilities or significant behavior changes;
- `1.0.0`: reserved for a deliberately declared long-term public API/compatibility commitment.

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

## Pull-request gate

Before merge:

```bash
python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python -m pytest
```

Changes that affect provider behavior, MCP contracts, state transitions, scope, validation, routing, recovery, or acceptance orchestration require focused regression tests.

## Stable promotion

A stable promotion should minimize code delta from the accepted release candidate. Provider acceptance claims must retain machine-readable evidence. Infrastructure retries must remain transparent and should never turn a functional failure into a pass.
