# DynosAI 0.17.0 — Eval Intelligence

0.17.0 sits on the 0.16.0 governed-team baseline and turns local failures into bounded eval cases without handing quality or routing to the model.

## What this release is

Eval intelligence is a **learning-loop problem**, not a live leaderboard. DynosAI attributes failures, mines local traces, and can open an inbox improvement task. It still does not run live-provider evals and still does not let predictive routing spend model budget.

## Shipped

- Failure attribution across model / harness / provider / project / infrastructure / governance.
- Mining of validations, audit failures, eval records and model-control outcomes into bounded cases.
- Host-owned propose: inbox work only, no provider spawn.
- Offline regression evidence that does not auto-complete or auto-approve work.
- Studio Overview eval cases and `dynosai_stats` summary.
- Offline `team_overlap` and `mined_regression` fixtures.

## Deliberately not shipped

- Live-provider eval quality claims or leaderboards.
- Autonomous predictive-routing enablement.
- Silent creation of improvement work from MCP agent sessions.
- Schema 7 / a new eval authority table.

## Authority unchanged

Schema remains v6. Git is source truth. `.dynosai/knowledge.db` is workflow truth. Eval cases live under `.dynosai/runtime/`. Cursor ACP and Codex app-server transports are unchanged. 0.16 leases and the 0.15 harness are preserved.

## Validation

Release still requires repository checks, Studio asset sync, the stable pytest suite, and website check/typecheck/lint/build when web files change.
