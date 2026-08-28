# DynosAI 0.14.1 — Studio UX Refresh

Release date: 2026-08-26

0.14.1 keeps the 0.14.0 Control Plane/App Server architecture and redesigns Local Studio around guided, non-expert usage.

## Highlights

- public DynosAI favicon/logo reused inside Local Studio;
- System / Light / Dark appearance with browser-local persistence;
- neutral project hub with global Home, Settings and Help plus a nested selected-project workflow for Overview, New change, Work, Approvals and Project checks;
- opt-in technical navigation instead of the ambiguous 0.14.0 Advanced mode;
- guided project setup model with explicit next actions;
- actionable explanation when a dirty Git repository blocks first-time adoption;
- natural-language New Task wizard explaining what DynosAI will do next;
- simple six-stage workflow timeline;
- Review Center for pending human interactions;
- bounded specification/plan/evidence context in review cards;
- project-check selection and explicit approval flow;
- simplified diagnostics plus copyable support summary;
- provider-neutral `/api/setup` and `/api/reviews` read surfaces;
- responsive visual refresh aligned with `dynosai.com` colors and branding;
- in-app project switching/recent-project management without restarting Studio;
- English and Spanish UI with an extensible translation dictionary;
- contextual greenfield Fibonacci walkthrough that guides rather than performs user actions;
- accessible shadcn/ui-style comboboxes instead of browser-native select controls;
- Codex/Cursor-only public provider choices.
- Studio-native shadcn/ui-style confirmation dialogs, styled checkboxes/toggles, and no browser-native confirm/select surfaces in normal flows;
- folder creation from the in-Studio directory browser;
- bounded Recent projects scrolling and background state synchronization instead of a manual Refresh button;
- project-scoped defaults plus editable Codex/Cursor model/effort routing for every governed workflow phase;
- reduced repeated headings and tighter New change execution-summary layout;

## Authority and compatibility

0.14.1 does not change the schema v6 authority model or relax governance:

- Git remains authoritative for source code;
- `.dynosai/knowledge.db` remains authoritative for workflow state;
- Studio talks through `DynosAIApplication` rather than reading SQLite;
- validation discovery remains read-only until explicit approval;
- risk remains advisory;
- material human gates remain explicit;
- technical-mode preferences affect presentation only.

## Validation target

The 0.14.1 release adds `tests/test_141.py` through `tests/test_158.py` for setup guidance, approvals, auto-approve history, locale-safe subprocess capture, activity-log ordering, implementation overlay/scope handling, Git C-quoted Spanish overlay paths, leftover verified `tests/` files at merge, Continue-to-finish on Final review, auto-continue into plan authoring after a mid-turn spec approval, Studio scroll preservation, neutral project-hub startup, greenfield project creation, project switching, the contextual Fibonacci walkthrough, English/Spanish localization, styled accessible comboboxes, current-provider boundaries, branding/themes, packaged asset serving, technical-mode opt-in behavior, and the Windows Cursor ACP process-tree/`acp-sessions` lifecycle. Repository checks, Studio asset synchronization and the public website check/typecheck/lint/build pipeline remain required release gates.

## Known limitations

- launch still begins from `dynosai studio` rather than a native desktop launcher;
- provider setup/authentication remains outside the Studio guided flow;
- the in-Studio directory browser is intentionally directory-only, though it can create a direct child folder and manual path entry remains available;
- the Review Center shows bounded review summaries rather than a full diff editor;
- rich requirement-to-code trace visualization is not included yet.

## Final Studio navigation refinement

The final 0.14.1 UX starts on a neutral project hub unless `--project` is explicitly supplied. Project workflow pages are nested beneath the selected project, greenfield project creation is available from Home, and the Fibonacci tutorial is a contextual walkthrough rather than an automatic demo creator or dedicated page. Normal-user UI no longer exposes runtime/server terminology.

## Project Hub usability follow-up

The final 0.14.1 polish makes Home a true neutral boundary: using Home leaves the active project context and returns it to Recent projects. Browse actions now use an in-Studio directory browser instead of an OS-dependent picker, with separate guidance for choosing a project folder versus choosing a parent folder for a new project. The sidebar also clamps project identity content so long project names or paths cannot introduce horizontal scrolling.

## Final UI and project-configuration polish

The final 0.14.1 polish removes browser-native confirmation surfaces, bounds long Recent-project lists, adds directory creation to the folder browser, removes the redundant manual Refresh action, and reduces duplicate page copy. A new Project settings surface exposes the actual project-level provider-model routing contract: Codex/Cursor routes can be inspected and overridden independently for discovery, specification, planning, implementation, code review, validation and merge while preserving the existing precedence and authority model.

## Interaction-flow simplification

The final interaction pass removes repeated guidance from day-to-day work. Before initialization, New change, Work and Approvals are disabled; Overview remains the setup surface, while Project settings stays available so the intended coding agent and workspace can be chosen. Initialization now shows a blocking progress overlay and opens Project checks only when detected checks still need review. New change contains only the requested change plus examples and the submit action; provider/workspace/model configuration lives exclusively in Project settings. Model routing automatically follows the project coding agent instead of exposing a second provider selector.

## Final recent-project and tutorial reliability pass

The final Studio pass sanitizes the recent-project registry on read, removes invalid/duplicate/internal-test temporary entries, records only successful opens/creates, limits the list to 10 and prevents pytest from writing into the user's normal Studio registry. Greenfield creation no longer asks users to choose a technology template. The contextual Fibonacci walkthrough is reduced to six steps and no longer requires Project checks before the first change, which avoids blocking on repositories that legitimately have no checks yet.

## Execution-flow reliability polish

The final 0.14.1 Studio pass closes a usability gap where creating a change could leave it in `inbox`/`queued` with no visible coding-agent process. Studio now advances the host-owned initial transition to discovery, starts the configured Codex/Cursor agent asynchronously, exposes execution status in Work, and offers a retry when the provider stops before reaching the next human gate. Provider return is reconciled into the authoritative workflow so submitted specs/results can become visible approvals without a manual CLI `continue` command.

Folder/path inputs used for project creation and opening are now read-only and populated only by the in-Studio directory browser. The Fibonacci walkthrough also remains on its approval/continuation step across repeated specification, plan, code and merge decisions and only reaches its result step when the governed work is actually `done`.

## Studio execution reliability

Studio background work uses provider-native integration transports rather than terminal-oriented launches: Codex app-server and Cursor ACP. Human gates remain in Studio, and failed turns expose bounded provider diagnostics for troubleshooting.


## Windows Codex Studio transport

Studio uses an explicit UTF-8 JSON-RPC transport for Codex app-server and forces UTF-8 on the managed DynosAI MCP child. This prevents Windows ANSI code pages from corrupting non-ASCII prompts such as Spanish task descriptions. Headless Studio execution also avoids creating project-local provider configuration as a launch side effect; the isolated managed runtime remains authoritative.

## Cursor ACP and diagnostic stability

Cursor execution from Studio now uses Cursor ACP (`agent acp`) instead of treating CLI print mode as a custom-client protocol. Studio performs the ACP initialize/authenticate/session flow, injects DynosAI MCP into the session, handles provider transport permission requests, and returns all DynosAI human gates to the Studio Approvals UI. The hidden `dynosai open --headless` wrapper now propagates provider exit codes and no longer prints the full managed-runtime payload into Studio logs. Diagnostic details in Work also remain expanded across background refreshes.


## Cursor ACP process lifecycle on Windows

After a successful Cursor ACP turn, Studio closes the ACP stdin stream and waits for the agent to exit before the next `open --headless` rebuilds `.dynosai/runtime/managed-agents/cursor`. Windows `Popen.terminate()` only stops the direct child, so DynosAI also reaps descendant processes (job object when available, plus a process-tree snapshot). Managed-runtime refresh retries `WinError 32` sharing violations and then skips leftover `acp-sessions` files instead of failing the following specification/plan turn. `tests/test_153.py` covers locked session files, stale-skill pruning, stdin shutdown, and resume-after-approval runtime rebuild.

## Approval visibility and live execution follow-up

The final approval pass makes the reviewed contract visible before a human can approve it: specifications expose objective, requirements and acceptance criteria, while plans expose approach, tasks, files and risks. Standard gate guidance is localized by Studio and request-change drafts remain stable while background refresh continues. Work now includes a bounded live Agent activity panel fed by provider progress and workflow events, with newest entries first and timestamps, without jumping the page or diagnostic scroll on background refresh. Approvals keep a history of past decisions, including automatic ones. Project settings can auto-approve specification/plan/code/finish gates and ordinary product-file scope requests, recording `source=studio_auto`, while DynosAI-owned spec/plan overlays are ignored for scope and result verification even when Git lists them with C-quoted octal UTF-8 names. Captured Windows subprocess output is decoded as UTF-8 with replacement so Git, pytest and tasklist locale bytes do not crash the coding-agent turn. Cursor ACP provider-side `cursor/create_plan` is acknowledged only as a transport UX gate; DynosAI's own structured plan and explicit human approval remain authoritative. The Fibonacci walkthrough now synchronizes against real pending reviews during every background refresh and follows repeated approval cycles until the work reaches `done`.
