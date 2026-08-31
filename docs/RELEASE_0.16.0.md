# DynosAI 0.16.0 — Governed Agent Teams

0.16.0 sits on the 0.15.0 verified-harness baseline and adds the minimum scheduling layer needed before any real multi-worker execution.

## What this release is

Multi-agent work is a **scheduling problem**, not a swarm. DynosAI still does not replace Codex, Cursor or future providers, and it still does not launch extra provider processes. Parallelism is allowed only when the approved plan proves that work can be isolated.

## Shipped

- Plan DAG → serial/parallel waves with file-disjoint leases.
- Scope ceilings, evidence and validation contracts per lease.
- Follow-on roles as contracts: tester = governed validation on the lease; reviewer = human code-review gate for high-risk work.
- Claim rules: only the current wave, never overlapping claimed siblings, never spawn-on-claim.
- Wave-scoped fan-in: overlapping verified scopes in the same wave block silent merge.
- Studio Work slots for the current wave, with copy that extra agents are not started.
- `dynosai_schedule` returns the team plan plus fan-in.

## Deliberately not shipped

- Host-launched N Codex/Cursor processes (a second worker still requires the host to open another governed session).
- Specialist reviewer/tester as extra spawned agents.
- Schema 7 / a new lease authority table.
- OS-level network sandboxing for child processes (0.18).
- Live-provider eval leaderboards (0.17).

## Authority unchanged

Schema remains v6. Git is source truth. `.dynosai/knowledge.db` is workflow truth. Cursor ACP and Codex app-server transports from 0.14.1 are unchanged in role. The 0.15 harness (handles, ExecutionRuntime, integrity, eval registry v0) is preserved.

## Validation

Release still requires repository checks, Studio asset sync, the stable pytest suite, and website check/typecheck/lint/build when web files change.
