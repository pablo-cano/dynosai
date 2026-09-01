# DynosAI Local Studio

DynosAI Studio is the local graphical interface for the governed DynosAI workflow. It keeps the 0.14.1 guided product, 0.15 reviews, 0.16 team slots, 0.17 eval cases, 0.18 execution profiles and 0.19 certified-provider inventory. Overview also shows optional harness features. Navigation after a project is selected is Overview, New change, Work, Approvals, Project checks and Project settings.

## What Studio is for

Studio answers a small set of practical questions:

1. Which local project am I working on?
2. Is the project ready for DynosAI?
3. What small change do I want to make?
4. What is DynosAI doing now?
5. Does DynosAI need a decision from me?
6. Which project checks will prove the result?
7. Is the work ready to finish?

Studio is not an IDE and it is not a replacement coding-agent chat. Codex or Cursor still perform implementation work. Studio is the **local control plane** around that work.

## Open Studio

Start Studio without a project:

```bash
dynosai studio
```

Studio opens on **Home**, a neutral project hub. It does **not** assume that the directory where the command was launched is the project you want to manage.

If you explicitly want one project selected at startup, pass it:

```bash
dynosai --project /path/to/repository studio
```

After launch, Home is where you:

- create a new project;
- open an existing folder;
- return to a recent project.

Selecting another project does not require restarting Studio. Returning to **Home** closes the active project context and takes you back to the neutral hub; the project remains available in Recent projects. Recent-project metadata is user-scoped and removing an entry never deletes the project files.

## Navigation model

Studio separates global navigation from project navigation.

Global pages are always available:

```text
Home
Settings
Help
```

After a project is selected, a nested **Project** section appears:

```text
Project name
  Overview
  New change
  Work
  Approvals
  Project checks
  Project settings
```

Optional technical views remain nested under the project and appear only when **Show technical details** is enabled.

### Home: the project hub

Home contains no workflow state for a project. Its job is only to help the user choose what to work on.

**Create a project** asks only for a name and parent folder. Studio creates a minimal Git project with a README and `.gitignore`; technology-specific files are added later by the governed changes that actually need them. This keeps greenfield creation neutral instead of forcing users to choose a framework or language template up front.

Use **Browse…** beside Location to choose the parent folder in Studio. The dialog shows the exact destination where the new project will be created and includes **New folder** when you need to create a directory at the current location. Creating the project folder is not the same as initializing DynosAI. Initialization remains a separate explicit step inside Project → Overview.

**Open a project** reads an existing folder and selects it. **Browse…** opens a contextual folder browser inside Studio, so you can navigate directories without leaving the app. Opening does not execute tests, agents or project commands.

### Project → Overview

Overview is the only project page available before DynosAI initialization. It shows the setup state and the single action required to continue. New change, Work and Approvals remain disabled until initialization succeeds. Project checks stay visible for inspection, and Project settings remains available so the coding agent/workspace can be chosen before initialization; model-route editing becomes available after initialization. If initialization discovers checks that still need approval, Studio opens Project checks automatically; otherwise it stays on Overview.

When local traces contain failures, Overview also shows **eval cases**. Creating an improvement task from a case puts work in the inbox only; Studio does not start a coding agent from that button. Predictive routing stays in shadow mode.

Overview lists **certified providers**: Cursor ACP and Codex app-server. Additional clients are not shipped.

### Project → New change

Describe only the desired outcome in normal language and create the change. Agent, workspace and model choices are intentionally removed from this page; they come from Project settings so users do not have to reconfigure every request.

### Project → Work

Work maps internal state onto a plain-language timeline:

```text
Understand -> Specify -> Plan -> Implement -> Validate -> Finish
```

When a change is ready or implementing, Work also shows **team slots** for the current scheduling wave: role, planned files and lease status. Parallel slots appear only when planned files do not overlap. DynosAI does not start extra coding agents from this panel; another slot needs another governed session.

When a coding-agent turn is active, Work also shows a bounded **Agent activity** stream. Newest events stay at the top, each line starts with its date/time, and Studio keeps your scroll position while the page refreshes in the background. The stream contains provider progress and workflow events rather than the full raw provider transcript. Full diagnostics remain available only when an execution fails or technical details are enabled.

### Project → Approvals

Only decisions requiring the user appear here. The user can approve, request changes, answer a clarification or cancel when the workflow permits it.

An approval must be reviewable before it can be accepted. Specification approvals show the objective, every bounded requirement and the acceptance criteria. Plan approvals show the implementation approach, tasks, planned files and risks. Standard gate explanations are localized by Studio instead of displaying provider/core English strings in a Spanish UI. Request-change drafts remain open and keep their text while Studio refreshes in the background. The bottom of the page keeps an **approval history**, including a recorded mark when a decision was given automatically.

### Project → Project checks

Project checks are not extra work on that screen. They are the existing test, lint, type-check and build commands DynosAI may later run as evidence — for a Python library that is often `pytest`; for a small web app it can be the frontend test or lint command if one is detected. Nothing is executed while you are choosing them. DynosAI still does not silently invent checks; you approve the ones that belong to the project.

### Project → Project settings

Project settings contain configuration that belongs to the selected project rather than to Studio itself.

The first section controls how every **new change** starts in this project:

- **Codex or Cursor** as the coding agent;
- **Interactive branch** to work in the current Git branch, or **Isolated worktree** to create a separate Git working directory for the change;
- **Auto-approve workflow gates**, which records automatic specification/plan/code/finish approvals and ordinary product-file scope requests as `studio_auto`. Questions still wait for a person. Exported `spec.md`/`plan.md` overlays under `specs/` are owned by DynosAI and do not create a scope gate, including when Git lists those paths with C-quoted non-ASCII names;
- **Execution profile** (Strict, Balanced or Autonomous). This is host policy, not a model setting. Autonomous does not skip human gates. OS-level network enforcement is not shipped.

The New change page consumes these settings directly and does not repeat them.

The **Models by workflow step** section is backed by the same `ProviderModelRouting` configuration used by the CLI. It always follows the coding agent selected above; there is no independent provider switch inside the routing panel. Inspect the effective model, effort and configuration source for:

```text
Default
Discovery
Specification
Planning
Implementation
Code review
Validation
Merge
```

Changing a row writes a project-scoped override to `.dynosai/provider-models.toml`. **Use inherited setting** removes only that project override so machine or built-in defaults become effective again. Studio does not imply that different providers run each phase: the coding agent is selected for the change, while model/effort routing is resolved per phase for that provider.

## Contextual Fibonacci walkthrough

The tutorial is **not a page** and it does **not perform the workflow for the user**. Start it from Home or Help and a contextual walkthrough appears over the normal Studio UI.

The walkthrough highlights the exact area to use and tells the user what to enter or which button to press. Every operation remains an explicit user action.

The walkthrough teaches this small greenfield flow:

1. On Home, create a neutral project such as `fibonacci-demo` and choose its parent folder.
2. In Project → Overview, press **Initialize project**.
3. In Project → New change, enter the supplied Fibonacci request yourself. A Copy button is available, but Studio does not submit it automatically.
4. In Project → Work, follow the task until DynosAI needs a decision.
5. In Project → Approvals, inspect the full specification/plan contract and make the requested decisions. This step may repeat.
6. Return to Work to follow implementation and validation until the task is actually complete.

Project checks remain available as normal project configuration, but the tutorial does not require a pre-existing pytest profile for a greenfield repository.


The example request is intentionally small:

```text
Create a small Python Fibonacci library. Add a function fibonacci(n) that returns
the first n Fibonacci numbers. For n = 0 return an empty list. Reject negative
values with ValueError. Use an iterative implementation, add pytest tests for 0,
1, 5 and negative input, and add a short README usage example.
```

## Language support

English is the default Studio language. Spanish is also included in 0.14.1.

Change language in:

```text
Settings -> Language -> English / Español
```

The preference is stored in browser `localStorage` and does not change project state. UI translations live in a dedicated `i18n.js` dictionary keyed by stable translation identifiers, so adding a language does not require duplicating the Studio UI.

Generated project content, specifications or agent responses are not automatically machine-translated by the UI; Studio localizes its own interface and guidance.

## Appearance

Studio supports:

- System
- Light
- Dark

The preference is browser-local and uses the same DynosAI icon/favicon as the public website.

## Technical details are opt-in

Enable **Show technical details** in Settings to add:

- Risk & governance
- Diagnostics
- Activity log

This changes presentation only. It does not weaken Git controls, validation policy, provider permissions or human gates.

## Local security boundary

The App Server accepts loopback bindings only (`127.0.0.1` or `localhost`). CORS is not enabled and non-loopback Host values are rejected.

```text
Local Studio
    ↓ HTTP/JSON on loopback
DynosAI App Server
    ↓
DynosAIApplication
    ↓
Core / Git / durable state
```

Project switching changes which `DynosAIApplication` instance is active. It does not give the browser direct filesystem authority.

## Main Studio API surfaces

Read surfaces:

```text
GET /api/health
GET /api/projects
GET /api/project/detect
GET /api/setup
GET /api/overview
GET /api/work
GET /api/reviews
GET /api/validations
GET /api/model-routing
GET /api/risk
GET /api/blockers
GET /api/events
```

Write surfaces:

```text
POST /api/projects/open
POST /api/projects/create
POST /api/projects/close
POST /api/projects/remove
POST /api/filesystem/list
POST /api/filesystem/mkdir
POST /api/project/initialize
POST /api/work/start
POST /api/validations/approve
POST /api/model-routing/set
POST /api/model-routing/reset
POST /api/interaction/resolve
```

## Current limitations

- Studio still launches from the CLI rather than a desktop launcher/installer.
- Provider installation/authentication remains outside the guided Studio flow.
- The in-Studio folder browser lists directories and can create a direct child folder; manual local-path entry remains available for power users.
- Approvals provide bounded summaries rather than a full visual Git diff editor.
- Requirement -> task -> evidence -> code trace visualization remains planned for a later iteration.


### Action feedback

Mutating Studio actions such as project creation/opening, initialization, check approval, change creation, approval decisions, folder creation and model-route updates display a blocking progress overlay until the operation completes. Background synchronization never displays that overlay.

## Studio-managed agent execution

Creating a change from Studio now starts the selected Codex or Cursor agent in a background host process. The HTTP/UI thread remains responsive while the agent works through the existing MCP/governance contract. Work shows whether the agent is running, waiting for a human approval, unavailable, or stopped unexpectedly. If a provider exits before completing the current governed step, **Continue agent** retries that work item instead of creating a duplicate change. If Work is sitting in Final review with nothing left in Approvals, **Continue** completes the host-owned merge instead of launching another coding-agent turn or waiting for a gate that was already answered. After a specification is auto-approved in the middle of a provider turn, Studio starts a new planning turn instead of leaving Work stopped at Plan review without a plan.

Studio uses provider-native integration transports for these background turns. Codex uses its app-server protocol. Cursor uses **ACP (`agent acp`)**, the protocol Cursor documents for custom clients and integrations. Studio creates the ACP session, injects the DynosAI MCP server for that session, answers provider transport permission requests, and leaves DynosAI specification/plan/code/merge decisions in the Studio Approvals surface. The hidden headless CLI wrapper propagates the real provider exit code instead of printing a large managed-runtime payload. If a turn fails, Work exposes a bounded diagnostic tail, and its disclosure state remains open across background refreshes while the user is reading it.

Studio performs only deterministic host-owned transitions itself: it opens discovery when a change is created, reconciles submitted artifacts when an agent returns, runs validation after code approval, and materializes the next human gate. Specifications, plans, implementation and scope changes remain agent/governance responsibilities.

Project-location fields are read-only in 0.14.1. Use **Browse…** and the in-Studio directory browser to choose a parent directory or existing project; this avoids malformed paths and makes the selected filesystem location explicit before an operation runs.


### Provider transport on Windows

Studio runs providers without a terminal. Codex app-server and the DynosAI MCP stdio bridge use UTF-8 explicitly, so localized/non-ASCII task descriptions are safe on Windows regardless of the system ANSI code page. Provider diagnostics shown in Studio are normalized to plain text.

Cursor ACP turns are one-shot: after `session/prompt` completes, Studio sends `session/cancel`, closes stdin (the ACP shutdown signal), waits for the agent to exit, and then stops any remaining Windows process-tree descendants. The next workflow turn, including resume after a specification or plan approval, refreshes DynosAI-owned config in place. Leftover `acp-sessions` files that Windows has not yet unlocked are skipped instead of aborting the turn with `WinError 32`.
