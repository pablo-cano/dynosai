# DynosAI 1.0.0-rc.3

Public RC · Eval maturity. This is not stable 1.0.

Package version: `1.0.0rc3` (PEP 440). Display: `1.0.0-rc.3`.
Development classifier remains `Development Status :: 4 - Beta`.
Database schema remains **v6**.
MCP unique surface remains **31**.

## What RC3 proves

- A host-owned acceptance ZIP importer turns failed suite children into bounded Eval Registry cases. Improvement work stays in inbox. DynosAI does not spawn a provider.
- `governed_change_cost()` is the promoted efficiency metric. Completed-work aggregates appear on `dynosai stats`, `dynosai scorecard`, project overview, Studio and diagnostic bundles.
- Measured fields include success, validation state, fresh/cached/output/reasoning tokens, retries, human gates, elapsed time, MCP calls/failures/rejections/normalizations, and duration/result bytes when those values were actually recorded.
- Unused advertised MCP tools are not treated as waste. Tool-surface size is an experimental context-overhead diagnostic.
- A deterministic `PROMPT_PREFIX_1.0` authority prefix is hashed and stored under `.dynosai/runtime/` plus audit. The prefix does not dump the 31-tool list. Phase context stays in a suffix. The hash means cacheable structure, not a provider cache hit.

## What RC3 does not prove

- No live-provider 1.0 certification. `MATRIX_1.0` cells remain `not_run`.
- No installer or published PyPI package.
- No OS-level sandbox, Docker, VM or remote execution runtime.
- No additional certified clients.
- No autonomous predictive routing.
- No claim of production-ready 1.0.

## Next

RC4 (installation, doctor-in-Studio and installed-package CI) is delivered in `1.0.0-rc.4`.
