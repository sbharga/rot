# rot

`rot` is a Python library for assembling high-energy vertical videos for Instagram Reels,
YouTube Shorts, and TikTok. It combines backgrounds, dialogue, local TTS, synced captions,
speaker portraits, overlays, transitions, and effects into one FFmpeg render.

The default preset produces a 1080×1920 MP4 with H.264 video, AAC 48 kHz stereo audio,
constant 30 fps, a 10 Mbps target/12 Mbps ceiling, yuv420p, and SDR Rec.709 metadata.
The encoder is also given an 8 Mbps minimum rate target.

## Project map

- [Documentation home](docs/index.md) is the GitHub Pages-friendly guide to installation, API workflows, and references.
- [Recipes](docs/examples.md) provides copyable projects for narration, rankings, and clip discovery.
- [Architecture](docs/architecture.md) explains the render pipeline and extension boundaries.
- [Contributing](CONTRIBUTING.md) lists the development workflow and validation gates.
- [Changelog](CHANGELOG.md) records user-facing changes.

## Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A system FFmpeg build containing FFprobe, libx264, AAC, and libass

On Debian or Ubuntu:

```console
sudo apt-get install ffmpeg
uv sync --group dev
uv run rot doctor
```

Install only the integrations you use:

```console
uv sync --extra chatterbox   # Chatterbox (`tts` remains an alias)
uv sync --extra kokoro       # Kokoro-82M
uv sync --extra align        # Stable-TS word alignment
uv sync --extra openrouter   # OpenRouter script parsing
uv sync --extra youtube      # YouTube downloading with yt-dlp
```

## Quick start

Create `script.rot`:

```text
@alex [id=hook]: You will not believe what happened next.
@sam: There is absolutely no way.
@alex [audio=recordings/final-line.wav]: Look at this.
```

Create `video.py`:

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
    .with_aligner(StableTSAligner("base"))
)
```

Render it:

```console
uv run rot render video.py -o short.mp4
```

Project files are trusted Python code. Use `video.py:another_project` to select an object other
than the default `project`.

## Composition

Backgrounds accept videos or still images and use `cover` fitting by default. Add multiple clips
with `add_clip`, then select `cut`, `fade`, `crossfade`, `slide-left`, `slide-right`, or `zoom`
between them. Video effects include zoom, punch zoom, pan, shake, blur, grayscale, and saturation.

For horizontal footage, `fit="custom"` provides a controllable middle ground between preserving
the complete frame and filling the vertical canvas. `fit_amount=0.0` is equivalent to `contain`,
while `fit_amount=1.0` is equivalent to `cover`:

```python
project.add_clip(
    "horizontal.mp4",
    fit="custom",
    fit_amount=0.4,
    fill="blur",
    fill_blur=40,
    anchor="center",
)
```

Intermediate values enlarge the clip without distortion, crop the overflow according to `anchor`,
and pad any remaining uncovered canvas area. The default `fill="black"` uses solid letterboxing;
`fill="blur"` places a blurred, full-canvas copy of the clip behind the sharp foreground.

```python
project = (
    Project.short_form()
    .background("one.mp4", trim=(2, 8), loop=False)
    .transition("crossfade", duration=0.25)
    .add_clip("two.mp4", trim=(4, 12), keep_audio=True, volume=0.25)
    .effect("saturation", amount=1.3)
    .soundtrack("music.mp3", volume=0.12)
)
```

Use `overlay_image(..., at=3, duration=2)`, `during="line-id"`, or `speaker="alex"` to
bind an overlay to time. A registered speaker portrait automatically follows that speaker's
utterances.

Non-caption text uses `overlay_text`. Assign clips stable IDs and bind a title to each complete
clip without calculating timestamps:

```python
project = (
    Project.short_form()
    .background("number-5.mp4", clip_id="rank-5", keep_audio=True, loop=False)
    .add_clip("number-4.mp4", clip_id="rank-4", keep_audio=True, loop=False)
    .overlay_text("#5 — Huge comeback", during_clip="rank-5", position="top")
    .overlay_text("#4 — Impossible save", during_clip="rank-4", position="top")
)
```

Clips render in the order they are added. `during_clip` accepts either a clip ID or a zero-based
clip index. Text can also use `at`/`duration`, `during="line-id"`, or `speaker="alex"`; an `at`
overlay without a duration remains visible through the end of the video. Text overlays render
even when `RenderSettings(captions=False)`. With a transition, the outgoing title changes to the
incoming title at the transition midpoint. See the [ranked countdown recipe](docs/examples.md#ranked-countdown)
for a complete example.

## Captions and voices

Caption presets are `classic`, `pop`, `karaoke`, and `bounce`. Pass a `CaptionTheme` for full
font, color, outline, safe-area, casing, and word-group control. The built-in renderer writes ASS
and burns it with libass; `RenderSettings(caption_sidecar=True)` also emits SRT.

Every line may point to prerecorded audio. Otherwise its speaker needs a `VoiceProvider` such as
`ChatterboxVoice`, `KokoroVoice`, or a custom provider implementing `synthesize`. `StableTSAligner` provides
known-transcript word alignment. Without an aligner, `rot` estimates word timings from the audio
duration and emits a warning.

Kokoro uses named voices instead of reference-audio cloning and runs well on CPU:

```python
from rot import KokoroVoice

project.add_speaker(
    "alex",
    voice=KokoroVoice("af_heart", speed=1.05),
    language="en-US",
)
```

`KokoroVoice` accepts [built-in names](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md),
comma-separated voice blends, and local `.pt` voice packs.
Set `device="cpu"`, `"cuda"`, or `"mps"` to override automatic selection. Kokoro produces 24 kHz
mono WAV internally; the final render pipeline converts it to the configured AAC 48 kHz stereo
output. Use `lang_code="a"`, `"b"`, `"e"`, `"f"`, `"h"`, `"i"`, `"j"`, `"p"`, or `"z"` to
override the language inferred from the speaker. Install the system `espeak-ng` package for out-of-dictionary English words and languages
that use Kokoro's eSpeak phonemizer. Japanese and Mandarin additionally require the corresponding
Misaki language extra, for example `uv add 'misaki[ja]>=0.9.4'` or
`uv add 'misaki[zh]>=0.9.4'`.

Only clone a voice with the represented person's informed permission. `rot` preserves
Chatterbox's generated-audio watermark and provides no watermark-removal feature.

## OpenRouter

`OpenRouterParser` converts free-form text to the same validated script model using strict JSON
Schema output. It never runs for normal `.rot` files and requires an explicit model.

```console
export OPENROUTER_API_KEY=...
uv run rot parse draft.txt --model provider/model --speaker alex --speaker sam -o script.rot
```

```python
from rot import OpenRouterParser

parser = OpenRouterParser(model="provider/model", speakers=("alex", "sam"))
project.script(free_form_text, parser=parser)
```

## YouTube clip discovery

Install the `youtube` extra, then download a video and export ranked MP4 segments:

```console
uv run rot clips "https://www.youtube.com/watch?v=VIDEO_ID" \
  --method hybrid --duration 30 --count 5 -o clips
```

`hybrid` is the recommended default. It combines visual scene-change strength with short-window
RMS audio energy, then rejects heavily overlapping results. Use `--method scene` for edited
montages or silent footage, and `--method audio` for podcasts, interviews, and reactions where
energetic speech matters more than cuts. `--download-only` keeps `source.mp4` and reports the
suggested time ranges without exporting them.

The same workflow is available as typed Python APIs:

```python
from rot import ClipDetectionSettings, Project, YouTubeClipFinder

finder = YouTubeClipFinder(
    ClipDetectionSettings(method="hybrid", clip_duration=25, clip_count=3)
)
result = finder.find(
    "https://youtu.be/VIDEO_ID",
    "build/youtube-clips",
)

# Candidates also become trim-aware rot Clip objects without another encode.
project = Project.short_form().background(result.project_clips()[0])
```

The exported clips preserve the source dimensions but are accurately cut and encoded as H.264,
AAC 48 kHz stereo MP4s. A later `Project` render applies rot's vertical 1080×1920 output contract.
Only download and reuse videos you have permission to process; YouTube availability, age gates,
regional restrictions, and authentication are handled by yt-dlp and can still prevent a download.

## Logging and progress

The library emits records through the `rot` logger without configuring root logging. `render`
accepts `progress=False` or a callback receiving `ProgressEvent`. The CLI displays stage and
FFmpeg encoding progress; `-v`, `-vv`, and `--json-logs` control diagnostics.

Outputs are written atomically and existing files are protected unless `overwrite=True` or
`--force` is supplied. Generated speech is cached under the platform user cache directory.

## Development

```console
uv sync --group dev
uv run ruff check .
uv run mypy src/rot
uv run pytest
uv build
uv run twine check dist/*
```

The optional Chatterbox, Kokoro, and Stable-TS model-download smoke tests are intentionally not part of
the ordinary test run.

## License

MIT
