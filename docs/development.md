---
layout: default
title: Development
nav_order: 7
---

# Development

Set up the project and run its complete local validation before submitting a change:

```console
uv sync --group dev
uv run ruff check .
uv run mypy src/rot
uv run pytest
uv build
uv run twine check dist/*
```

Use `uv run rot doctor` to verify the media stack. Unit tests cover timeline and filter logic;
small integration tests generate synthetic media during the test run instead of storing binary
fixtures in the repository.

See the repository’s `CONTRIBUTING.md` for contribution policy and `CHANGELOG.md` for user-facing
changes.
