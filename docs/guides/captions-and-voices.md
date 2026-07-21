---
layout: default
title: Captions and voices
parent: Guides
nav_order: 2
---

# Captions and voices

Caption presets are `classic`, `pop`, `karaoke`, and `bounce`. Use a `CaptionTheme` for custom
font, color, outline, safe area, casing, and word grouping. `rot` writes ASS captions and burns
them with libass. Set `RenderSettings(caption_sidecar=True)` to also emit SRT.

Use safe BBCode to change color and typography inline in dialogue or `overlay_text` content:

```python
project.script(
    "@alex: This is [color=#FFE135][b]actually wild[/b][/color]."
)
project.overlay_text(
    "[size=96]FINAL[/size] [i]ROUND[/i]",
    during_clip="final",
)
```

Supported tags are `color`, `b`, `i`, `u`, `font`, and `size`; they can be nested, and doubled
brackets render literal brackets. Markup is validated and removed from speech, alignment, and SRT
text. The active caption word uses the theme highlight color temporarily, while retaining its
inline font, size, bold, italic, and underline formatting.

Set `CaptionTheme(position=Placement(x, y, anchor=...))` for normalized two-dimensional caption
placement. Without it, the existing `position_y` baseline remains unchanged.

The default `AssCaptionRenderer` can be replaced with any object implementing the `CaptionRenderer`
protocol (a `render(path, utterances, theme, *, width, height)` method) via
`project.with_caption_renderer(...)`, mirroring how `with_aligner(...)` swaps the word-timing
source.

## Transcribe clip audio

Clips can generate captions directly from their existing audio without becoming script dialogue:

```python
from rot import ClipTranscription, Placement, StableTSTranscriber

project = (
    Project.short_form()
    .background("stream.mp4", keep_audio=True, transcribe=True)
    .add_clip(
        "interview.mp4",
        keep_audio=True,
        transcribe=ClipTranscription(language="en"),
    )
    .with_transcriber(StableTSTranscriber(model="base"))
    .clip_captions("pop", position=Placement(0.5, 0.08, anchor="top"))
)
```

Install the built-in provider with `uv sync --extra transcribe`, or supply any object implementing
`Transcriber`. `transcribe=True` auto-detects language; `ClipTranscription` sets it per clip. The
trimmed and speed-adjusted audio is transcribed and cached once, then word timings repeat with
looped clips and switch at transition midpoints. The active spoken word uses the theme highlight
color. Clip captions default to a separate top lane so scripted narration remains readable.

Call `project.transcribe_clips()` before rendering for structured clip-local results. A render also
returns them through `RenderResult.transcripts`; SRT sidecars merge clip and narration cues by
time. Transcription does not enable `keep_audio` or change video duration. Clips with no detected
speech emit a warning, while clips without an audio stream fail early.

Each dialogue line can use prerecorded audio with `audio=...`. Otherwise the speaker needs a
`VoiceProvider`: `ChatterboxVoice`, `KokoroVoice`, or a custom provider implementing `synthesize`.
`StableTSAligner` provides known-transcript word alignment. Without an aligner, `rot` estimates
word timings from audio duration and emits a warning.

## Use Kokoro named voices

Kokoro uses named voices rather than reference-audio cloning and runs well on CPU:

```python
from rot import KokoroVoice

project.add_speaker(
    "alex",
    voice=KokoroVoice("af_heart", speed=1.05),
    language="en-US",
)
```

`KokoroVoice` accepts built-in names, comma-separated voice blends, and local `.pt` voice packs.
Set `device="cpu"`, `"cuda"`, or `"mps"` to override automatic selection. Use `lang_code` to
override inferred language. Install system `espeak-ng` for out-of-dictionary English and languages
that use Kokoro’s eSpeak phonemizer. Japanese and Mandarin also require the corresponding Misaki
language extra.

Only clone a voice with the represented person’s informed permission. `rot` preserves
Chatterbox’s generated-audio watermark and provides no watermark-removal feature.

## Turn a draft into a script

`OpenRouterParser` converts free-form text into the validated script model with strict JSON Schema
output. It is only invoked when explicitly requested and requires a model.

```console
export OPENROUTER_API_KEY=...
uv run rot parse draft.txt --model provider/model --speaker alex --speaker sam -o script.rot
```

```python
from rot import OpenRouterParser

parser = OpenRouterParser(model="provider/model", speakers=("alex", "sam"))
project.script(free_form_text, parser=parser)
```

Keep API keys out of source files and logs.

`parser=` accepts any object implementing the `ScriptParser` protocol (a `parse(source) -> Script`
method), so a custom parser for another script format can be passed the same way.
