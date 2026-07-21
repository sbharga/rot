---
layout: default
title: Getting started
nav_order: 2
description: Install rot, verify FFmpeg, and render a first vertical video.
---

# Getting started

`rot` targets Python 3.12+ and uses FFmpeg for all media work. Its default render is a 1080×1920
MP4: H.264 video, AAC 48 kHz stereo audio, constant 30 fps, yuv420p, and SDR/Rec.709 metadata.

## Install prerequisites

Install [uv](https://docs.astral.sh/uv/) and an FFmpeg build that contains FFprobe, libx264, AAC,
and libass. On Debian or Ubuntu:

```console
sudo apt-get install ffmpeg
uv sync --group dev
uv run rot doctor
```

`rot doctor` reports exactly which required FFmpeg capabilities are missing.

## Make your first video

Create `script.rot`:

```text
@alex [id=hook]: You will not believe what happened next.
@sam: There is absolutely no way.
@alex [audio=recordings/final-line.wav]: Look at this.
```

Then create a trusted Python project file, `video.py`:

```python
from rot import ChatterboxVoice, Project, StableTSAligner

project = (
    Project.short_form()
    .background("assets/gameplay.mp4", trim=(12, 42), loop=True)
    .add_speaker(
        "alex",
        voice=ChatterboxVoice("assets/alex-reference.wav"),
        portrait="assets/alex.png",
    )
    .add_speaker(
        "sam",
        voice=ChatterboxVoice("assets/sam-reference.wav"),
        portrait="assets/sam.png",
        portrait_position="bottom-left",
    )
    .script_file("script.rot")
    .captions("pop")
    .overlay_image("assets/reaction.png", during="hook", animation="bounce")
    .soundtrack("assets/music.mp3", volume=0.12, fade_out=0.8, ducking=True)
    .with_aligner(StableTSAligner("base"))
)
```

Render it:

```console
uv run rot render video.py -o short.mp4
```

Project files are executed as Python code. Only render files you trust. Pass
`video.py:another_project` when the exported project is not named `project`.

## Add only the integrations you need

```console
uv sync --extra chatterbox   # Voice cloning; `tts` is also an alias
uv sync --extra kokoro       # Kokoro-82M named voices
uv sync --extra align        # Stable-TS word alignment
uv sync --extra openrouter   # OpenRouter script parsing
uv sync --extra youtube      # YouTube downloading with yt-dlp
```

Continue with [composition](guides/composition.md), [captions and voices](guides/captions-and-voices.md),
the complete [Python API](reference/api.md), or a copyable [recipe](examples.md).
