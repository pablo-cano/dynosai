# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano

# Decision: PyPI distribution name for DynosAI 1.0

- Status: Accepted
- Date: 2026-09-04
- Release: 1.0.0-rc.6

## Context

The product name is DynosAI. The Python import package is `dynosai_flow`.
Until RC6 the PEP 621 distribution name in `pyproject.toml` was `dynosai-flow`,
which setuptools turns into wheels named `dynosai_flow-*.whl`. Nothing has been
published to PyPI or TestPyPI.

Stable 1.0 needs one public distribution name. Changing it after publication
is a migration. Changing it before publication is not.

Checked on 2026-09-04:

| Index | Name | HTTP |
|---|---|---|
| pypi.org | `dynosai` | 404 (not registered) |
| pypi.org | `dynosai-flow` | 404 (not registered) |
| test.pypi.org | `dynosai` | 404 (not registered) |

`dynosai` is therefore available to this project. There is no installed-base
of `dynosai-flow` to migrate.

## Decision

Use:

```text
PyPI distribution: dynosai
Python import:     dynosai_flow
CLI:               dynosai
legacy CLI:        dyno
MCP serverInfo:    dynosai-flow
```

The import package stays `dynosai_flow` so provider configs, tests and
`dynosai-mcp` entry points do not churn. MCP `serverInfo.name` stays
`dynosai-flow` because that wire identity is already in the 1.0 compatibility
surface.

This RC does **not** publish to PyPI. The supported zero-source install path
remains a verified local/GitHub Release wheel plus checksums:

```text
pipx install ./dist/dynosai-1.0.0rc6-py3-none-any.whl
uv tool install ./dist/dynosai-1.0.0rc6-py3-none-any.whl
python scripts/install_local_wheel.py --wheel dist/dynosai-1.0.0rc6-py3-none-any.whl
```

`pip install dynosai` from the public index is reserved until a later explicit
publication. GitHub Release wheels carry `SHA256SUMS.txt`. SLSA/GitHub
attestations may be added on the first GitHub Release that is actually cut;
they are not faked in this candidate.

## Consequences

- Wheel filenames change from `dynosai_flow-*` to `dynosai-*`.
- Import, CLI and MCP tool names do not change.
- A later rename back to `dynosai-flow` would be unnecessary churn.
