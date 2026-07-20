---
layout: default
title: Architecture
nav_order: 5
---

# Architecture

`Project` is the user-facing builder. It owns a typed background track, speakers, dialogue,
overlays, effects, caption configuration, and export settings. `Renderer` validates these objects,
generates or loads speech, aligns captions, resolves timeline selectors, and produces
`PreparedMedia`.

`FFmpegCompiler` converts `PreparedMedia` into one typed FFmpeg filter graph. FFmpeg performs the
frame work and final encode; Python does not iterate through frames. Inputs and filters are passed
as a subprocess argument vector rather than through a shell. Custom effects return validated
`FilterNode` objects so extensions cannot accidentally inject shell commands.

The main extension interfaces are `VoiceProvider`, `WordAligner`, `ScriptParser`,
`CaptionRenderer`, and `Effect`. Integrations are imported safely but defer heavyweight or network
dependencies until invoked.

Render outputs are first written beside the destination under a unique temporary name and moved
atomically after FFmpeg succeeds. Speech cache keys include provider configuration, language, and
text. API keys and remote script bodies are not logged.

`VideoClipFinder` is a preprocessing boundary rather than part of rendering. A single FFmpeg pass
writes scene-change, motion, and windowed RMS-audio metadata to separate files — separate files
rather than one stderr stream, because interleaved output would corrupt the timestamp-to-value
pairing. Those signals become `SignalSeries` values whose prefix sums and sparse tables make each
candidate window O(1) to score, and they are cached on disk keyed by file identity plus the
settings that affect decoding, so re-ranking never re-decodes. Selected segments can then be
accurately encoded back to MP4 under deterministic, source-derived names.

`FolderClipFinder` ranks across a whole library, reducing each file to its own best windows before
they compete globally and collecting unreadable sources as `SkippedSource` rather than failing the
scan. `YouTubeClipFinder` adds an explicit yt-dlp download step through the optional `youtube`
extra. Downloaded videos never enter a `Project` implicitly; candidates can be exported or
converted to ordinary trim-aware `Clip` models.
