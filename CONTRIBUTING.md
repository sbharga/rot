# Contributing

Install the uv-managed development environment with `uv sync --group dev`. Before submitting a
change, run `uv run ruff check .`, `uv run mypy src/rot`, `uv run pytest`, and `uv build`.

FFmpeg integration tests skip automatically when FFmpeg is absent. New media behavior should add
a small synthetic integration fixture rather than committing binary videos to the repository.

Keep user-facing workflows as copyable recipes in `docs/examples.md`; update the README when a
feature changes the primary getting-started path.

Do not commit API keys, downloaded model weights, cloned voice references, or generated videos.
