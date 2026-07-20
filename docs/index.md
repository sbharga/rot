# Project guide

## Start here

- [README](../README.md): installation, the public API, and short end-to-end examples.
- [Recipes](examples.md): copyable projects for narration, rankings, and clip discovery.
- [Architecture](architecture.md): rendering boundaries and extension interfaces.
- [Contributing](../CONTRIBUTING.md): local setup and required validation before a change.

## Source map

| Area | Location | Responsibility |
| --- | --- | --- |
| Public API | `src/rot/__init__.py` | Stable imports exposed to application code. |
| Project definition | `src/rot/project.py`, `src/rot/models.py` | Fluent builder and validated public configuration. |
| Dialogue and captions | `src/rot/script.py`, `src/rot/captions.py` | `.rot` parsing, timing, ASS, and SRT generation. |
| Timeline and rendering | `src/rot/render.py`, `src/rot/ffmpeg.py` | Media preparation and safe FFmpeg graph compilation. |
| Media utilities | `src/rot/probe.py`, `src/rot/clips.py` | FFmpeg capability checks, probing, and clip discovery. |
| Optional integrations | `src/rot/integrations/` | Lazy integrations for TTS, alignment, OpenRouter, and YouTube. |
| Command line | `src/rot/cli.py` | `rot` command implementation. |
| Tests | `tests/` | Unit coverage plus synthetic FFmpeg integration tests. |

Keep user-facing examples, architecture decisions, and guides in `docs/`; keep generated output
outside the repository. The package ships `py.typed`, so public type information is available to
downstream type checkers.
