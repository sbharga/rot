---
layout: default
title: Command line
parent: Reference
nav_order: 1
---

# Command line

The `rot` command loads a trusted project file, inspects media, checks dependencies, parses a
draft with OpenRouter, and finds source highlights.

| Command | Purpose |
| --- | --- |
| `rot render FILE.py[:object] -o OUTPUT.mp4` | Render a `Project`; `-f` permits replacing an existing output. |
| `rot doctor` | Verify FFmpeg, FFprobe, libass, H.264, AAC, and optional integrations. |
| `rot probe ASSET [--json]` | Print duration, streams, codecs, dimensions, and color metadata. |
| `rot parse INPUT --model MODEL --speaker NAME` | Convert a free-form draft to a validated `.rot` script. |
| `rot clips TARGET [options]` | Rank and export clips from a YouTube URL, a video file, or a folder. |

Use `-v` for diagnostic logs, `-vv` for source locations in tracebacks, and `--json-logs` for
machine-readable logging. The renderer shows progress by default; pass `--no-progress` for
non-interactive environments.

Outputs are written atomically. Existing files remain protected unless you pass `--force`.

See [clip discovery](../guides/clip-discovery.md) for the `clips` options and
[OpenRouter parsing](../guides/captions-and-voices.md#turn-a-draft-into-a-script) for `parse`.
