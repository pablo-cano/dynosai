# Security Policy

## Supported version

The latest public line is `1.0.0-rc.4` (`1.0.0rc4`). It remains a beta-classified release candidate. Schema authority is v6. Production `1.0.0` is not claimed. The 1.0 live certification cells are `not_run` until real provider evidence exists. DynosAI is not published on PyPI.

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the maintainer before opening a public issue when disclosure could expose users, secrets, repository integrity, or managed-agent escape paths.

Maintainer: **Pablo Cano**.

If no private security channel has been configured on the hosting repository yet, open a minimal issue requesting a private contact path without publishing exploit details.

## Security boundaries

DynosAI is designed to reduce agent authority, not to create a perfect sandbox. Important boundaries include:

- governed path/scope policy, including path-escape and sensitive-file denial;
- network and dependency **decisions** that the model cannot self-approve (OS-level child-process network sandboxing is not claimed in 0.18);
- named Strict/Balanced/Autonomous execution profiles selected by the host, not by the model;
- optional runtime-only secret vault materialization (never into model context);
- credential redaction in MCP results and Studio review diffs;
- Git write authority in managed sessions;
- strict managed-runtime controls;
- explicit provider tool configuration;
- local workflow state and audit evidence;
- validation command allow/configuration boundaries.

Users remain responsible for operating-system permissions, secrets management, provider credentials, repository access, CI security, and the commands they approve as validation profiles.

## Secrets

Do not commit provider credentials, API keys, tokens, `.env` secrets, private acceptance bundles, or `.dynosai` runtime databases containing sensitive project metadata.
