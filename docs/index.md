---
layout: default
title: Home
nav_order: 1
description: Build energetic vertical video with Python, FFmpeg, captions, and voice.
---

# rot documentation

Build energetic, platform-ready vertical videos for Instagram Reels, YouTube Shorts, and TikTok.
`rot` combines backgrounds, dialogue, local TTS, synced captions, speaker portraits, overlays,
transitions, and effects into a deterministic FFmpeg render.

## Start here

- [Getting started](getting-started.md): install prerequisites and render a first video.
- [Guides](guides/index.md): compose clips, add captions and voices, discover moments, and publish.
- [Recipes](examples.md): copyable narration, ranking, and highlight-finding projects.
- [Reference](reference/index.md): CLI commands and the media output contract.

## Repository map

- [Getting started](getting-started.md): installation and the public workflow.
- [Recipes](examples.md): copyable projects for narration, rankings, and clip discovery.
- [Architecture](architecture.md): rendering boundaries and extension interfaces.
- [Development](development.md): local setup and validation before a change.

## Source map

| Area | Location | Responsibility |
| --- | --- | --- |
| Public API | `src/rot/__init__.py` | Stable imports exposed to application code. |
| Project definition | `src/rot/project.py`, `src/rot/models.py` | Fluent builder and validated public configuration. |
| Dialogue and captions | `src/rot/script.py`, `src/rot/captions.py` | `.rot` parsing, timing, ASS, and SRT generation. |
| Timeline and rendering | `src/rot/render.py`, `src/rot/ffmpeg.py` | Media preparation and safe FFmpeg graph compilation. |
| Media utilities | `src/rot/probe.py`, `src/rot/clips.py` | FFmpeg capability checks, probing, and clip discovery. |
| Optional integrations | `src/rot/integrations/` | Lazy integrations for TTS, alignment, OpenRouter, and YouTube. |
| Publishing | `src/rot/publish.py` | Official API preflight, resumable upload, polling, and batch results. |
| Command line | `src/rot/cli.py` | `rot` command implementation. |
| Tests | `tests/` | Unit coverage plus synthetic FFmpeg integration tests. |

Keep user-facing examples, architecture decisions, and guides in `docs/`; keep generated output
outside the repository. The package ships `py.typed`, so public type information is available to
downstream type checkers.
