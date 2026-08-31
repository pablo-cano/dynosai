# DynosAI Local Studio

`apps/studio` contains the source assets for the loopback-only Local Studio shipped by the Python package.

The Studio is deliberately dependency-free HTML/CSS/JavaScript. The source assets are mirrored byte-for-byte into `src/dynosai_flow/studio_assets/` so the wheel can serve them without Node.js at runtime.

Run:

```bash
python scripts/check_studio_sync.py
```

before release packaging.

## 0.14.1 UX model

Primary navigation is intentionally non-technical:

- Home
- New task
- In progress
- Reviews
- Project checks
- Settings
- Help

Risk internals, diagnostics and the provider-neutral event stream are hidden until the user enables **Show technical details** in Settings.

The public website and Studio share the same DynosAI brand SVG. Theme preference supports System, Light and Dark and is stored only in browser local storage.

## 0.15.0 completion reviews

Code and merge approvals can expand the resulting diff, validation results and Validation Integrity risks. Studio remains a loopback client of `DynosAIApplication`; it does not open `knowledge.db`.

## 0.16.0 team slots

Work cards show current-wave leases (role, files, status). DynosAI does not start extra agents from Studio; another slot needs another governed session.

## 0.17.0 eval cases

Overview can list mined eval cases. Creating an improvement task from a case stays in inbox and does not launch Codex or Cursor.
