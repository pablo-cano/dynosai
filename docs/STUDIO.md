# Local Studio and App Server

DynosAI 0.14.0 introduces the first graphical product surface around the governed Core.

## Launch

From a repository:

```bash
dynosai --project /path/to/repository studio
```

or after changing into it:

```bash
cd /path/to/repository
dynosai studio
```

The default URL is:

```text
http://127.0.0.1:8765/
```

Use a different local port if needed:

```bash
dynosai studio --port 9876
```

Run the API without opening the browser:

```bash
dynosai app-server --port 8765
```

## Security boundary

The App Server intentionally accepts only loopback bindings (`127.0.0.1` or `localhost`). It is not a remote administration server.

The browser does **not** read `.dynosai/knowledge.db` directly. The flow is:

```text
Local Studio
    ↓ HTTP/JSON on loopback
DynosAI App Server
    ↓
DynosAIApplication
    ↓
Core / Git / durable state
```

POST endpoints require `application/json`, cross-origin CORS is not enabled, and the server rejects non-loopback Host values. These controls reduce accidental exposure, but users should still treat Studio as a local developer tool rather than an internet-facing service.

## 0.14 surfaces

The first Studio alpha exposes:

- project classification and stack detection;
- initialized/uninitialized state;
- recent governed work;
- start-work form for Codex, Cursor or Claude;
- deterministic risk score and signals;
- blocker explanations;
- discovered validation profiles and explicit approval;
- provider-neutral application events in Advanced mode;
- local server diagnostics/raw bounded overview.

Specification/plan editing, rich diff review and requirement-to-code trace visualization are roadmap items for the 0.14.x line.

## Validation discovery

Discovery is read-only. DynosAI may infer candidates from repository configuration such as:

- Python: `pytest`, Ruff, mypy;
- Node.js/TypeScript: package scripts, `tsconfig.json`;
- Rust: Cargo;
- .NET: solution/project files;
- Java: Maven/Gradle.

Discovered commands are not silently trusted. They only become approved `validation_profiles` after an explicit approval through the application API/Studio.

If an existing profile with the same name has a different command, auto-discovery does not overwrite it unless an explicit overwrite is requested.

## Risk Assessment v1

Risk is advisory and separate from the Quality score.

The deterministic v1 scorer considers signals such as:

- security/authentication paths;
- SQL/migrations;
- dependency/build manifests;
- CI configuration;
- planned blast radius;
- pending scope extensions;
- pending human gates.

Risk never bypasses workflow gates. Later releases may use it to request additional specialist review while keeping policy deterministic and auditable.
