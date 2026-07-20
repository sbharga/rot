# Repository guidance

## Project goal

`rot` exists to make short-form “brain rot” content for Instagram Reels, YouTube Shorts, and
TikTok. Features and APIs should make energetic vertical videos—rankings, gameplay-backed
dialogue, rapid captions, speaker reactions, overlays, transitions, and similar formats—simple to
assemble while preserving professional, platform-ready output.

The default output contract is a 1080×1920 MP4 with H.264 video, AAC 48 kHz stereo audio, constant
30 fps, roughly 8–12 Mbps video bitrate, yuv420p, and SDR/Rec.709 metadata.

## Project structure

- `src/rot/`: typed public API, timeline preparation, integrations, and FFmpeg compilation.
- `tests/`: unit tests plus small synthetic FFmpeg integration tests.
- `docs/`: architecture notes and copyable user-facing recipes.

This is a Python 3.12+ uv project using the `uv_build` backend. FFmpeg performs media processing;
Python should orchestrate timelines and filters rather than process frames individually.

## Development commands

```console
uv sync --group dev
uv run ruff check .
uv run mypy src/rot
uv run pytest
uv build
uv run twine check dist/*
```

Use `uv run rot doctor` to check the local FFmpeg, FFprobe, libx264, AAC, and libass capabilities.
Heavy integrations are optional extras and must remain importable without installing their runtime
dependencies.

## Implementation conventions

- Keep the fluent `Project` API concise, intuitive, and backward-compatible where practical.
- Represent public configuration with typed models and fail early with actionable errors.
- Preserve user-selected clip order and resolve timeline-bound elements without requiring callers
  to calculate timestamps manually.
- Build FFmpeg commands as argument vectors and validated filter graphs; never interpolate user
  input into shell commands.
- Keep rendering deterministic, atomic, and compatible with progress reporting and logging.
- Cache expensive generated speech and model instances where safe.
- Add focused unit tests for timeline/filter logic and a small real FFmpeg test for new media
  behavior. Generate fixtures during tests instead of committing binary media.
- Update the README and an example when adding meaningful user-facing syntax.

Do not commit API keys, downloaded model weights, cloned voice references, copyrighted source
clips, or generated videos. Voice cloning must require the represented person’s informed
permission, and generated-audio watermarking must not be removed.
