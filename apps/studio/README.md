# DynosAI Local Studio

This directory mirrors the dependency-free Local Studio frontend shipped inside the Python package under `dynosai_flow.studio_assets`.

Run it through the governed local App Server:

```bash
dynosai studio --project /path/to/repository
```

The server binds to loopback only. Studio never opens `.dynosai/knowledge.db` directly: every read/write goes through `DynosAIApplication` and the local App Server API.

When changing these static sources, keep the packaged copies in `src/dynosai_flow/studio_assets/` synchronized. `scripts/check_studio_sync.py` enforces that invariant in CI/repository checks.
