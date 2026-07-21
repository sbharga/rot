---
layout: default
title: Recipes
nav_order: 4
---

# Recipes

These recipes are complete starting points for common vertical-video formats. Copy a recipe into a
trusted Python file, replace the placeholder asset paths, and render it with `rot`. Project files
are executed as Python, so only run files that you trust.

Before rendering, install the development environment or the extras required by your recipe and
check the local media toolchain:

```console
uv sync --group dev
uv run rot doctor
```

All rendered files are 1080×1920 MP4s by default. The examples deliberately use local placeholder
paths: supply media that you have the right to use and keep generated output outside the repository.

## Prerecorded narration

This is the smallest useful project. It loops gameplay behind a prerecorded narration track, then
burns animated captions. Save it as `video.py` beside an `assets/` directory and run
`uv run rot render video.py -o short.mp4`.

```python
from rot import Project

project = (
    Project.short_form()
    .background("assets/background.mp4", loop=True)
    .add_speaker("narrator")
    .script(
        "@narrator [id=hook, audio=assets/narration.wav]: "
        "This is a rot video in one tiny script."
    )
    .captions("pop")
)
```

`audio=` bypasses text-to-speech for that line. If the line needs an image reaction, give it a
stable `id` as shown and target it with `during="hook"`:

```python
project.overlay_image("assets/reaction.png", during="hook", animation="bounce")
```

Add a trimmed background-music loop with gentle boundaries and dialogue ducking:

```python
project.soundtrack(
    "assets/music.mp3",
    volume=0.12,
    trim=(6, 26),
    fade_in=0.4,
    fade_out=0.8,
    ducking=True,
)
```

For a still-only narration background, replace `background.mp4` with `background.png`; its
duration follows the narration automatically. A still in a multi-clip recipe needs an explicit
`duration`.

For synthesized speech instead, register the speaker with a voice provider and omit `audio=`. See
[captions and voices](guides/captions-and-voices.md) for Chatterbox and Kokoro setup.

## Streamer facecam layout

Crop a webcam embedded in horizontal gameplay and move it into the lower part of the vertical
canvas. The source is decoded only once, and normalized coordinates keep the layout reusable.

```python
from rot import Facecam, NormalizedRect, Placement, Project

project = (
    Project.short_form()
    .background(
        "assets/stream.mp4",
        fit="custom",
        fit_amount=0.35,
        anchor="top",
        keep_audio=True,
        facecam=Facecam(
            crop=NormalizedRect(0.02, 0.04, 0.24, 0.32),
            destination=NormalizedRect(0.1, 0.7, 0.8, 0.25),
        ),
    )
    .overlay_text(
        "[color=#FFE135][b]CLUTCH[/b][/color] [i]or throw?[/i]",
        at=0,
        position=Placement(0.5, 0.06, anchor="top"),
    )
)

project.render("streamer-short.mp4", overwrite=True)
```

To caption speech already present in the stream, opt the clip in and configure a local transcriber:

```python
from rot import StableTSTranscriber

project.clips[0].transcribe = True
project.with_transcriber(StableTSTranscriber(model="base"))
project.clip_captions("pop", position=Placement(0.5, 0.08, anchor="top"))
```

Each spoken word highlights at its Stable-TS word timestamp. Use
`project.transcribe_clips()` to inspect the structured transcript before rendering.

## Ranked countdown

Use one clip per rank and bind the label to the clip rather than calculating timestamps. `rot`
keeps the insertion order, and `during_clip` changes the displayed title at the midpoint of any
transition.

```python
from rot import Project, RenderSettings

ranked_clips = [
    (5, "The surprise entrance", "assets/source.mp4", 103.0, 120.0),
    (4, "The impossible save", "assets/source.mp4", 130.0, 155.0),
    (3, "The perfect comeback", "assets/source.mp4", 162.0, 190.0),
    (2, "The last-second escape", "assets/source.mp4", 200.0, 230.0),
    (1, "The moment nobody expected", "assets/source.mp4", 240.0, 270.0),
]

project = Project(settings=RenderSettings(captions=False, normalize_audio=True))

for index, (rank, title, source, start, end) in enumerate(ranked_clips):
    clip_id = f"rank-{rank}"
    options = dict(
        trim=(start, end),
        loop=False,
        keep_audio=True,
        clip_id=clip_id,
        fit="custom",
        fit_amount=0.4,
        fill="blur",
        fill_blur=40,
    )
    if index == 0:
        project.background(source, **options)
    else:
        project.add_clip(source, **options)

    project.overlay_text(
        f"#{rank} — {title}",
        during_clip=clip_id,
        position="top",
        font_size=50,
        outline_width=7,
        shadow=3,
        uppercase=True,
    )

project.render("top-five.mp4", overwrite=True)
```

`fit="custom"` preserves more horizontal footage than `cover`; `fit_amount=0.0` behaves like
`contain`, while `1.0` behaves like `cover`. The blurred fill prevents empty side areas without
stretching the sharp foreground. Add `.transition("crossfade", duration=0.25)` after
`.background(...)` to blend the first clip into the next one.

## Find source highlights from YouTube

Install the optional downloader first:

```console
uv sync --extra youtube
```

The command below downloads a permitted source, scores candidate windows, and exports five
30-second MP4 clips. `hybrid` combines scene changes, frame-to-frame motion, and audio energy, and
is the recommended starting point for edited, energetic footage.

```console
uv run rot clips "https://www.youtube.com/watch?v=VIDEO_ID" \
  --method hybrid --duration 30 --count 5 -o build/youtube-clips
```

For application code, the same workflow returns candidates and trim-aware `Clip` values:

```python
from rot import ClipDetectionSettings, Project, YouTubeClipFinder

finder = YouTubeClipFinder(
    ClipDetectionSettings(method="hybrid", clip_duration=25, clip_count=3)
)
result = finder.find("https://youtu.be/VIDEO_ID", "build/youtube-clips")

project = Project.short_form().background(result.project_clips()[0])

# Inspect why each window ranked where it did.
for candidate in result.candidates:
    print(
        f"{candidate.start:.1f}s-{candidate.end:.1f}s "
        f"score={candidate.score:.3f} scene={candidate.scene_score:.3f} "
        f"motion={candidate.motion_score:.3f} audio={candidate.audio_score:.3f}"
    )
```

Use `--download-only` to inspect suggested ranges before exporting. Availability restrictions and
authentication are handled by yt-dlp; only process material you have permission to download and
reuse.

## Choosing a recipe

| Goal | Start with | Key choice |
| --- | --- | --- |
| Dialogue over looping gameplay | Prerecorded narration | Use `audio=` for recorded lines or a voice provider for TTS. |
| Fast list or ranking | Ranked countdown | Give every clip a stable `clip_id` and use `during_clip`. |
| Find raw moments before editing | YouTube highlights | Choose `hybrid`, then review exported candidates before publishing. |

For selectors, caption themes, and the render contract, see the [guides](guides/index.md) and
[reference](reference/index.md). For responsibilities inside the package, see the [project guide](index.md).
