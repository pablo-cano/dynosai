# Getting Started

This guide takes a new user from a clean clone to a governed DynosAI project.

## 1. Requirements

- Python 3.11 or newer.
- Git for governed repository operations.
- At least one supported coding-agent provider if you want provider-native execution. The current public provider surface is Codex or Cursor.
- Internet access may be needed the first time the local embedding model is downloaded. Runtime project state remains local.

## 2. Install from source

```bash
git clone <your-dynosai-repository-url>
cd dynosai
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
python -m pip install -e .
```

For development tooling:

```bash
python -m pip install -e ".[dev]"
```

Optional code-graph support:

```bash
python -m pip install -e ".[codegraph]"
```

Verify the installation:

```bash
dynosai --version
dynosai doctor
```


## 3. Launch Local Studio (optional, recommended for onboarding)

From the repository you want DynosAI to govern:

```bash
cd /path/to/your/project
dynosai studio
```

DynosAI opens `http://127.0.0.1:8765/` by default. The App Server binds only to loopback and the browser talks through `DynosAIApplication`; it does not open `knowledge.db` directly.

Use another port or run without opening a browser:

```bash
dynosai studio --port 9876 --no-browser
dynosai app-server --port 9876
```

Studio 0.15 keeps the 0.14.1 guided product. Start on **Home** and follow the primary action shown there. The normal Studio path is:

```text
Projects -> Home -> New change -> Work -> Approvals
```

Studio detects the repository stack and validation candidates before initialization. Discovered validation commands are only proposals until you explicitly approve them. If an uninitialized Git repository is dirty, Studio explains that the changes must be committed or stashed before adoption; it never discards user work automatically.

Appearance can be System, Light or Dark. Settings also contains an optional **Show technical details** switch for risk internals, diagnostics and the provider-neutral activity log.

## 4. Configure providers once

```bash
dynosai setup --provider all
```

Or configure one provider:

```bash
dynosai setup --provider codex
dynosai setup --provider cursor
```

Inspect the generated provider-neutral configuration:

```bash
dynosai agent-config show --provider codex
dynosai agent-config show --provider cursor
```

## 5. Initialize a project

In an existing Git repository:

```bash
cd /path/to/project
dynosai project detect .
dynosai project initialize . --agent codex
```

For an existing codebase without Git, Git initialization is intentionally explicit:

```bash
dynosai project initialize . --agent codex --init-git
```

DynosAI creates `.dynosai/` for local runtime state. The authoritative workflow database is `.dynosai/knowledge.db`; Git remains authoritative for source code.

## 6. Start work

The normal path is provider-native: use the DynosAI instructions/skills generated for your coding agent and describe the feature in natural language.

The provider-neutral CLI equivalent is:

```bash
dynosai start "Add a CSV export for completed invoices"
dynosai status
dynosai continue
```

A normal feature progresses through:

```text
Discovery -> Spec Review -> Plan Review -> Ready -> Implementation
          -> Code Review -> Validation -> Ready to Merge -> Done
```

Specification, plan, code, scope extension, and merge decisions are human-governed where required.

## 7. Inspect context and evidence

```bash
dynosai status
dynosai context
dynosai ask "Which requirements affect invoice export?"
dynosai board
dynosai scorecard
```

## 8. Resume after a provider restart

```bash
dynosai resume --provider codex
dynosai resume --provider cursor
```

DynosAI reconstructs the current contract from durable state instead of asking the chat history to recreate it.

## 9. Back up local DynosAI state

```bash
dynosai backup --reason "before refactor"
dynosai restore /path/to/snapshot.json.gz
```

Restores are validated before atomically replacing the live database.

## 10. Run the test suite

```bash
python -m compileall -q src/dynosai_flow
python scripts/check_repository.py
python -m pytest
```

## 11. Learn the behavior before production use

Read these next:

1. [User guide](docs/USER_GUIDE.md)
2. [Quality and validation](docs/QUALITY_AND_VALIDATION.md)
3. [Architecture](docs/ARCHITECTURE.md)
4. [Security](SECURITY.md)

DynosAI is a governance layer, not a replacement for project-specific engineering standards. Configure build, lint, type-check, security, integration, and performance validations that your repository actually requires.

## Project links

- Source repository: https://github.com/pablo-cano/dynosai
- Author: [Pablo Cano](https://www.linkedin.com/in/pablo-cano-galan/)
