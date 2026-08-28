# Contributing

This repository is private during the 0.1.0 validation cycle.

Before submitting a change:

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build
```

Protocol changes need a sanitized fixture, an exact semantic schema, capability
gating, a no-retry test, and a HITL test entry. Raw command escape hatches are
not accepted.
