# DynosAI Local Studio

`apps/studio` contains the source assets for the loopback-only Local Studio shipped by the Python package.

The Studio is deliberately dependency-free HTML/CSS/JavaScript. The source assets are mirrored byte-for-byte into `src/dynosai_flow/studio_assets/` so the wheel can serve them without Node.js at runtime.

Run:

```bash
python scripts/check_studio_sync.py
```

before release packaging.

## Current UX model

Primary navigation is project-scoped after a repository is selected:

- Home (project hub when no project is selected)
- Overview
- New change
- Work
- Approvals
- Project checks
- Project settings
- Settings
- Help

Risk internals, diagnostics and the provider-neutral event stream are hidden until the user enables **Show technical details** in Settings.

Project settings include coding-agent/workspace defaults, execution profile, auto-approve (off by default), optional harness features and model routing. Optional harness features are optimizations, not security authority.

The public website and Studio share the same DynosAI brand SVG. Theme preference supports System, Light and Dark and is stored only in browser local storage.

## 0.15.0 completion reviews

Code and merge approvals can expand the resulting diff, validation results and Validation Integrity risks. Studio remains a loopback client of `DynosAIApplication`; it does not open `knowledge.db`.

## 0.16.0 team slots

Work cards show current-wave leases (role, files, status). DynosAI does not start extra agents from Studio; another slot needs another governed session.

## 0.17.0 eval cases

Overview can list mined eval cases. Creating an improvement task from a case stays in inbox and does not launch Codex or Cursor.

## 0.18.0 execution profiles

Project settings choose Strict, Balanced or Autonomous. Code/merge reviews show policy evidence. OS-level network enforcement is not shipped.

## 0.19.0 supported providers

Overview lists Cursor ACP and Codex app-server as supported 1.0 target providers. 1.0 live certification is pending MATRIX_1.0. Additional clients are not shipped.

## 1.0.0-rc.2 live matrix

Overview shows Codex/Cursor × greenfield/brownfield cells. They stay **Not run** until a real provider run writes evidence. Historical 0.13 scores are not shown as 1.0 passes.

## 1.0.0-rc.3 eval maturity

Overview can import a local acceptance ZIP into bounded eval cases and shows governed-change cost totals. Unused advertised MCP tools are not treated as waste.

## 1.0.0-rc.4 installation

Overview and Diagnostics show doctor / deep-doctor results from the existing engine checks. Installation is a verified local wheel, not PyPI.

## 1.0.0-rc.5 freeze

Help states that loopback Studio is not a full security boundary. External pull requests remain closed. The threat model lives in `docs/THREAT_MODEL.md`.

## 1.0.0-rc.8 integrity

Overview still shows MATRIX_1.0 from real trials (currently explicit fails, not PASS). MCP `serverInfo.name` remains `dynosai-flow`. Protocol **support** is `2026-07-28`, `2025-11-25` and `2025-06-18`. That is not a claim that Cursor/Codex negotiated 2026. No plugin marketplace.

## 1.0.0-rc.7 integrity

Overview still shows MATRIX_1.0 from real trials (currently explicit fails, not PASS). MCP `serverInfo.name` remains `dynosai-flow`. Protocol **support** is `2026-07-28`, `2025-11-25` and `2025-06-18`. That is not a claim that Cursor/Codex negotiated 2026. No plugin marketplace.

## 1.0.0-rc.6 standards

Overview still shows MATRIX_1.0 from real trials or honest `not_run`. MCP `serverInfo.name` remains `dynosai-flow`. Protocol support is `2026-07-28`, `2025-11-25` and `2025-06-18`. No plugin marketplace.

## 1.0.0-rc.1 optional harness

Overview shows compact On/Off chips for context optimization, governed team scheduling and eval intelligence. Project settings can change those host-owned switches. Execution profiles, human gates and certified-provider refusal stay required and are not presented as optional optimizations.
