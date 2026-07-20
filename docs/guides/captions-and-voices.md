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
