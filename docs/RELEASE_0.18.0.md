# DynosAI 0.18.0 — Secure Autonomous Runtime

0.18.0 sits on the 0.17.0 eval-intelligence baseline and places named execution policy **outside** the model without claiming a container sandbox.

## What this release is

Longer local work needs a host-owned security profile. Strict, Balanced and Autonomous select network, dependency and secret materialization. They never skip human gates. Local network policy remains decision-only.

## Shipped

- Execution profiles: Strict (offline), Balanced (default-deny), Autonomous (allowlist — not unrestricted).
- Runtime-only secret vault under `.dynosai/runtime/secret-vault.json`. Values never enter model context, evidence, logs or Studio.
- Policy evidence on Studio code/merge review, project overview, `dynosai_stats` and diagnostic bundles.
- Host-owned Studio Project settings control. The coding agent cannot change the profile through MCP.

## Deliberately not shipped

- Docker, VM or remote `ExecutionRuntime` implementations.
- OS-level network interception for child-process sockets.
- Secret materialization into model prompts.
- Skipping specification, plan, code or merge gates because a profile is named Autonomous.

## Authority unchanged

Schema remains v6. Git is source truth. `.dynosai/knowledge.db` is workflow truth. The vault is a runtime file, not a new authority table. Cursor ACP and Codex app-server transports are unchanged. 0.16 leases, the 0.15 harness and 0.17 eval intelligence are preserved.

## Validation

Release still requires repository checks, Studio asset sync, the stable pytest suite, and website check/typecheck/lint/build when web files change.
