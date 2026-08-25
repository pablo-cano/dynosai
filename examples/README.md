# Examples

The public examples intentionally use provider-neutral commands and avoid embedding private provider credentials or repository-specific paths.

## Inspect a project

```bash
dynosai project detect .
dynosai doctor
```

## Initialize for Codex

```bash
dynosai project initialize . --agent codex
dynosai agent-config show --provider codex
```

## Start and inspect work

```bash
dynosai start "Add CSV export for completed invoices"
dynosai status
dynosai context
dynosai scorecard
```

## Offline model-control replay

```bash
dynosai --json model-control validate-history \
  acceptance-1.zip acceptance-2.zip \
  --output predictive-report.json
```

This last command launches no model/provider processes.
