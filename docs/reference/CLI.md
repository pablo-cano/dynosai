# CLI Reference

Run `dynosai <command> --help` for the authoritative argument list. This document groups the public commands by purpose.

## Local Studio and App Server

```text
studio
app-server
```

`studio` serves the graphical control plane on loopback and opens the browser by default. `app-server` exposes the same local API without opening a browser.

## Setup and project lifecycle

```text
setup
project detect | initialize
init
adopt
connect / disconnect
open
```

## Work lifecycle

```text
start
status
continue
resume
review
board
```

## Knowledge and context

```text
index
search
ask
context
sync
```

## Quality, telemetry and control

```text
stats
scorecard
usage
model
provider-model
model-control
model-benchmark
benchmark
```

## Provider configuration

```text
agent-config init | show | compile | clean | doctor
```

## Recovery and administration

```text
doctor
env
diagnose
backup
restore
rollback
schedule
```

## Diagnostics and acceptance

```text
debug e2e
debug acceptance
debug acceptance-status
debug acceptance-logs
```

Some low-level commands exist for managed MCP/provider operation and are intentionally suppressed from the normal top-level help because end users should not manually bypass the workflow.

The 1.0 compatibility commitment covers only the public stable commands documented in `docs/COMPATIBILITY.md`. Advanced/experimental commands and `debug` / acceptance internals are not a long-term third-party API.
