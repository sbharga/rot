---
layout: default
title: Clip discovery
parent: Guides
nav_order: 3
---

# Clip discovery

Install the optional YouTube integration, then download a permitted video and export ranked MP4
segments:

```console
uv sync --extra youtube
uv run rot clips "https://www.youtube.com/watch?v=VIDEO_ID" \
  --method hybrid --duration 30 --count 5 -o clips
```

`hybrid` combines visual scene-change strength with short-window RMS audio energy and rejects
heavily overlapping candidates. Use `scene` for edited montages or silent footage and `audio` for
podcasts, interviews, and reactions where energetic speech matters more than cuts.

Pass `--download-only` to keep `source.mp4` and inspect the suggested ranges without exporting.

```python
from rot import ClipDetectionSettings, Project, YouTubeClipFinder

finder = YouTubeClipFinder(
    ClipDetectionSettings(method="hybrid", clip_duration=25, clip_count=3)
)
result = finder.find("https://youtu.be/VIDEO_ID", "build/youtube-clips")
project = Project.short_form().background(result.project_clips()[0])
```

Exports preserve source dimensions but are accurately cut and encoded as H.264, AAC 48 kHz stereo
MP4s. A later `Project` render applies the vertical output contract. Only download and reuse media
you have permission to process. yt-dlp handles availability, age gates, regional restrictions, and
authentication, which can still prevent a download.
