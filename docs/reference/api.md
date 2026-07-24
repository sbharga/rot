---
layout: default
title: Python API
parent: Reference
nav_order: 1
---

# Python API

Everything below is importable directly from `rot`. Paths accept `str` or `pathlib.Path` unless
stated otherwise. Times and durations are seconds. Invalid public configuration raises
`ConfigurationError` before encoding whenever possible.

## Conventions

```python
import rot

# Every supported public object is also available as a direct import.
from rot import Project, RenderSettings
```

- Python 3.12 or newer is required.
- Public path parameters accept `str` or `pathlib.Path`. Stored and returned paths are usually
  normalized to `Path`; output and probe paths are absolute.
- Timeline times use **output seconds** unless a parameter is explicitly described as a source
  trim. Source trims are applied before the clip's `speed`.
- Fluent `Project` composition methods mutate the project and return that same instance. Model
  constructors validate immediately; media existence and stream capabilities are checked when
  probing, transcribing, or rendering.
- `progress=True` displays Rich progress, `False` disables it, and a `ProgressCallback` receives
  structured events.
- Optional integrations remain importable without their dependencies. They raise
  `DependencyError` with the relevant `uv sync --extra ...` command when first used.
- The names and signatures below are the supported root-package API. Objects not exported from
  `rot` are implementation details.

## Project composition

<!-- rot-api:Project -->
### `Project(*, settings: RenderSettings | None = None)`

Mutable fluent builder for one output video. `settings` is a `RenderSettings`; omitting it uses
the short-form defaults. Its public mutable state includes `clips`, `speakers`, `script_data`,
`caption_theme`, `clip_caption_theme`, `caption_renderer`, `overlays`, `text_overlays`,
`global_effects`, `aligner`, `transcriber`, and `music`.

| Method | Parameters and behavior |
| --- | --- |
| `short_form() -> Project` | Class method constructing the standard vertical-video preset. It is currently equivalent to `Project(settings=RenderSettings())`. |
| `background(source, *, trim=None, duration=None, loop=True, fit="cover", fit_amount=0.5, fill="black", fill_blur=40, facecam=None, focus=None, position=None, transcribe=False, anchor="center", keep_audio=False, volume=1, speed=1, clip_id=None)` | Clear **only** the primary clip list, then add the first video or still. Existing speakers, script, overlays, effects, and music remain. A lone still may infer duration from dialogue; otherwise give it `duration`. |
| `add_clip(source, *, trim=None, duration=None, loop=True, fit="cover", fit_amount=0.5, fill="black", fill_blur=40, facecam=None, focus=None, position=None, transcribe=False, anchor="center", keep_audio=False, volume=1, speed=1, transition="cut", transition_duration=0.3, clip_id=None)` | Append media in playback order. Here `transition` is the **incoming** transition from the preceding clip; internally it is stored as that preceding `Clip`'s outgoing transition. |
| `transition(name, *, duration=0.3)` | Set the latest clip's **outgoing** transition to its future successor. Names: `cut`, `fade`, `crossfade`, `slide-left`, `slide-right`, `zoom`. Requires an existing clip and positive duration. |
| `overlay_image(source, *, at=None, duration=None, during=None, speaker=None, during_clip=None, position="center", width=None, opacity=1, animation="pop", z_index=0)` | Add a static image using exactly one timing selector. Absolute overlays default to two seconds. `width=None` means 560 pixels. Animations: `none`, `pop`, `fade`, `slide`, `bounce`. |
| `overlay_text(text, *, at=None, duration=None, during=None, speaker=None, during_clip=None, position="top", font="DejaVu Sans", font_size=76, color="#FFFFFF", outline_color="#000000", outline_width=6, shadow=2, bold=True, uppercase=False, margin_x=70, margin_y=160, z_index=0)` | Add styled non-caption text. An absolute overlay without `duration` remains until video end. |
| `add_speaker(name, *, voice=None, portrait=None, language="en", portrait_position="bottom-right", portrait_width=420, portrait_animation="pop")` | Register one uniquely named script speaker, optional voice provider, and static portrait. Duplicate names are rejected. |
| `script(source, *, parser=None)` | Replace `script_data` with text parsed by `RotScriptParser` or a custom `ScriptParser`. |
| `script_file(path, *, parser=None)` | Parse a UTF-8 file. If the parser has `parse_file`, that method is used; otherwise the file text is passed to `parse`. The built-in parser resolves relative line audio beside the script. |
| `captions(theme="pop", **overrides)` | Select `classic`, `pop`, `karaoke`, or `bounce`, or pass a `CaptionTheme`; keyword fields override it. |
| `effect(effect, **options)` | Apply a built-in name, `EffectSpec`, or custom `Effect` to the whole visual track. |
| `soundtrack(source, *, volume=0.15, trim=None, loop=True, fade_in=0, fade_out=0, ducking=False)` | Configure one music bed. A later call replaces it. `trim=(start, end)` selects the repeated segment; `loop=False` leaves silence after one play. Fades use output time. Ducking sidechain-compresses music beneath dialogue. |
| `with_aligner(aligner)` | Use a `WordAligner` for accurate caption timing. |
| `with_transcriber(transcriber)` | Replace the lazily created default `StableTSTranscriber` used by opted-in clips. |
| `clip_captions(theme="pop", **overrides)` | Configure the separate caption lane generated from clip speech. String presets retain the lane's default top placement unless `position` is overridden. |
| `transcribe_clips(*, progress=True) -> tuple[ClipTranscript, ...]` | Prepare and transcribe opted-in video clips without rendering. Results are in project order and use the same persistent transcript cache as rendering. |
| `with_caption_renderer(renderer)` | Replace the built-in ASS renderer. |
| `render(output, *, progress=True, overwrite=None, keep_workdir=False) -> RenderResult` | Validate and atomically encode an `.mp4`. `overwrite=None` uses `settings.overwrite`; it applies to both MP4 and optional SRT. `keep_workdir=True` preserves intermediate files and logs their path. |

Image positions and anchors are `center`, `top`, `bottom`, `left`, `right`, and their four corner
forms. `during` names an utterance ID, `speaker` repeats over that speaker's lines, and
`during_clip` accepts a stable clip ID or zero-based index.

Image overlays, text overlays, speaker portraits, and captions also accept `Placement` for exact
normalized positioning. Inline text supports safe nested `color`, `b`, `i`, `u`, `font`, and
`size` BBCode tags.

When `source` is an existing `Clip`, `background`/`add_clip` append that object instead of rebuilding
it. Media-shaping keyword arguments therefore come from the object; only `clip_id` can replace its
ID. On `add_clip`, a non-`cut` incoming transition still updates the preceding clip.

```python
from rot import Clip, Placement, Project

project = (
    Project.short_form()
    .background(Clip("intro.png", duration=1.5, fit="contain"))
    .add_clip(
        "gameplay.mp4",
        trim=(12, 30),
        loop=False,
        keep_audio=True,
        transition="crossfade",
        transition_duration=0.25,
        clip_id="gameplay",
    )
    .overlay_text(
        "[color=#FFE135]WATCH THIS[/color]",
        during_clip="gameplay",
        position=Placement(0.5, 0.06, anchor="top"),
    )
)
```

Non-cut transitions overlap the two clips, shortening the combined timeline. The requested
overlap is capped at half the duration of each adjacent clip. Clip-bound overlays switch ownership
at the overlap midpoint.

## Timeline and render models

<!-- rot-api:Clip -->
### `Clip(source, trim_start=0, trim_end=None, duration=None, loop=True, fit="cover", anchor="center", keep_audio=False, volume=1, speed=1, effects=[], transition="cut", transition_duration=0.3, id=None, fit_amount=0.5, fill="black", fill_blur=40, facecam=None, focus=None, position=None, transcribe=False)`

Primary-track media fields: `source`, `trim_start=0`, `trim_end=None`, `duration=None`,
`loop=True`, `fit="cover"`, `anchor="center"`, `keep_audio=False`, `volume=1`, `speed=1`,
`effects=[]`, `transition="cut"`, `transition_duration=0.3`, `id=None`, `fit_amount=0.5`,
`fill="black"`, `fill_blur=40`, `facecam=None`, `focus=None`, `position=None`, and
`transcribe=False`. Still images cannot be trimmed and need explicit duration in
multi-clip or dialogue-free projects; their playback speed must remain 1.

`focus=(x, y)` controls the exact normalized source focal point for `cover`/`custom` cropping.
`position=Placement(...)` controls normalized canvas placement for `contain`/`custom` foregrounds.
`transcribe=True` enables language detection; pass `ClipTranscription` for a language override.

`fit` has four modes:

| Mode | Behavior |
| --- | --- |
| `cover` | Preserve aspect ratio, fill the canvas, and crop overflow. |
| `contain` | Preserve the full frame and letterbox uncovered canvas. |
| `custom` | Interpolate from contain (`fit_amount=0`) to cover (`fit_amount=1`). |
| `stretch` | Fill the canvas without preserving aspect ratio. |

`fill="blur"` is valid only with `contain` or `custom`; `facecam` requires `custom`; `focus`
requires `cover` or `custom`; and `position` requires `contain` or `custom`. Trims must be ordered,
`duration`, `speed`, `fill_blur`, and transition duration must be positive, volume cannot be
negative, and IDs are stripped and must be non-empty. A `Clip` is mutable so advanced callers can
append per-clip `effects` or opt into transcription after construction.

<!-- rot-api:Placement -->
### `Placement(x, y, anchor="center")`

Normalized canvas point for a layered element. Coordinates range from 0 through 1; `anchor`
selects the point on the element attached to that coordinate. Both coordinates must be finite.
Anchors are the nine named positions listed above.

<!-- rot-api:NormalizedRect -->
### `NormalizedRect(x, y, width, height)`

Normalized source or destination rectangle. Dimensions must be positive and the rectangle must
remain inside its frame: `x + width <= 1` and `y + height <= 1`. Every value must be finite.

<!-- rot-api:Facecam -->
### `Facecam(crop, destination)`

Extract an embedded facecam from the same custom-fit video clip. `crop` and `destination` are
`NormalizedRect` values; the crop aspect-preservingly cover-fills the destination. It is rendered
from the same decoded video and does not introduce a second audio track.

<!-- rot-api:ClipTranscription -->
### `ClipTranscription(language=None)`

Per-clip speech-to-text options. `language=None` delegates language detection to the provider.
Non-null language values are stripped and cannot be empty.

<!-- rot-api:TranscriptSegment -->
### `TranscriptSegment(text, start, end, words=())`

One clip-local transcription segment with optional word-level `WordTiming` values. Text is
stripped and must remain non-empty; `start >= 0`, `end > start`, and all words must stay inside the
segment.

<!-- rot-api:Transcript -->
### `Transcript(segments=(), language=None)`

Structured provider output for one non-looped, trimmed, speed-adjusted clip pass. `text` joins all
segment text with spaces. Segments must be ordered and non-overlapping; an explicit language
cannot be blank.

<!-- rot-api:ClipTranscript -->
### `ClipTranscript(clip_index, clip_id, source, transcript)`

Associates a clip-local `Transcript` with its nonnegative project index, optional ID, resolved
source, and structured transcript. Its `text` property delegates to `transcript.text`.

<!-- rot-api:Overlay -->
### `Overlay(source, at=None, duration=None, during=None, speaker=None, during_clip=None, position="center", width=None, opacity=1, animation="pop", z_index=0)`

Static-image fields mirror `Project.overlay_image`: `source`, the mutually exclusive `at`,
`during`, `speaker`, and `during_clip` selectors, plus `duration`, `position`, `width`, `opacity`,
`animation`, and `z_index`. Exactly one selector is required. `duration` is legal only with `at`;
when omitted for an absolute overlay it resolves to two seconds. Indices and start times cannot be
negative, width must be positive, and opacity is inclusive from 0 to 1.

<!-- rot-api:TextOverlay -->
### `TextOverlay(text, at=None, duration=None, during=None, speaker=None, during_clip=None, position="top", font="DejaVu Sans", font_size=76, color="#FFFFFF", outline_color="#000000", outline_width=6, shadow=2, bold=True, uppercase=False, margin_x=70, margin_y=160, z_index=0)`

Immutable text configuration with the parameters shown on `Project.overlay_text`. Colors must use
`#RRGGBB`; margins and outline/shadow widths are nonnegative. Text accepts safe inline BBCode and
is stored as plain text plus parsed internal `styled_runs`. Exactly one timing selector is
required. `duration` is legal only with `at`; an omitted absolute duration means through the end of
the video. The font must be a non-empty Fontconfig family without commas or newlines.

Supported inline forms are `[color=#RGB]`, `[color=#RRGGBB]`, `[b]`, `[i]`, `[u]`,
`[font=Family]`, and `[size=82]`. Tags can nest but must close in stack order. Use `[[` and `]]`
for literal brackets. Styling is removed from stored plain text before TTS, alignment, and SRT
output.

<!-- rot-api:Soundtrack -->
### `Soundtrack`

Immutable music configuration: `source`, `volume=0.15`, `trim_start=0`, `trim_end=None`,
`loop=True`, `fade_in=0`, `fade_out=0`, and `ducking=False`. Music never changes project duration.
The source must contain audio. `trim_start` and fades cannot be negative, `trim_end` must be after
the start, and volume must be nonnegative. If non-looping music is shorter than the video, the
remainder is silent; fade-out is applied at the effective end of the bed.

<!-- rot-api:Speaker -->
### `Speaker(name, voice=None, portrait=None, language="en", portrait_position="bottom-right", portrait_width=420, portrait_animation="pop")`

Speaker fields: `name`, `voice=None`, `portrait=None`, `language="en"`,
`portrait_position="bottom-right"`, `portrait_width=420`, and `portrait_animation="pop"`.
Names must be non-empty and contain no whitespace. Portrait width must be positive; portrait
animations use the same five values as image overlays.

<!-- rot-api:Utterance -->
### `Utterance(speaker, text, id=None, audio=None, gap_after=0.15, start=None, end=None, words=())`

One dialogue line: `speaker`, plain `text`, parsed inline `styled_runs`, optional `id`, optional
prerecorded `audio`, `gap_after=0.15`, and renderer-resolved `start`, `end`, and `words`.
The constructor parses and strips inline style markup from `text`; `gap_after` cannot be negative.
Normally callers leave timings unset and let rendering resolve them.

<!-- rot-api:Script -->
### `Script(utterances=[])`

Mutable ordered utterances. `ids() -> set[str]` returns all non-null line IDs. The render validator
requires every referenced speaker to be registered and every line to have either prerecorded
audio or a speaker voice.

<!-- rot-api:WordTiming -->
### `WordTiming(text, start, end)`

One word and its absolute start/end times. Start must be nonnegative and end cannot precede start.
Provider-returned timings are clip- or utterance-local as described by the relevant protocol;
prepared timeline timings are absolute.

<!-- rot-api:SynthesizedAudio -->
### `SynthesizedAudio(path, duration=None)`

Audio created by a voice provider; `duration` is optional provider metadata. Providers may return a
different actual `path` than the requested destination, but that file must exist and contain audio
when the render prepares it.

<!-- rot-api:CaptionTheme -->
### `CaptionTheme(name="pop", font="DejaVu Sans", font_size=82, primary_color="#FFFFFF", highlight_color="#FFE135", outline_color="#000000", outline_width=7, shadow=2, position_y=1310, position=None, max_words=5, uppercase=False)`

Fields: `name="pop"`, `font="DejaVu Sans"`, `font_size=82`, `primary_color="#FFFFFF"`,
`highlight_color="#FFE135"`, `outline_color="#000000"`, `outline_width=7`, `shadow=2`,
`position_y=1310`, `position=None`, `max_words=5`, and `uppercase=False`. `position` accepts a
normalized `Placement` and overrides `position_y`. `font_size` and `max_words` must be positive.
`preset(name)` loads a built-in theme:

| Preset | Differences from the base defaults |
| --- | --- |
| `classic` | 72 px font, white highlight, 5 px outline |
| `pop` | Base defaults |
| `karaoke` | Cyan highlight and groups of up to seven words |
| `bounce` | Pink highlight, 88 px font, and groups of up to four words |

The built-in ASS renderer animates according to `name`; arbitrary names are accepted as static
theme names, while `preset()` accepts only the four values above.

<!-- rot-api:RenderSettings -->
### `RenderSettings(width=1080, height=1920, fps=30, video_bitrate="10M", min_video_bitrate="8M", max_video_bitrate="12M", buffer_size="20M", audio_bitrate="192k", audio_sample_rate=48000, audio_channels=2, video_encoder="libx264", preset="veryfast", pixel_format="yuv420p", overwrite=False, captions=True, caption_sidecar=False, normalize_audio=False)`

Encoding fields: `width=1080`, `height=1920`, `fps=30`, `video_bitrate="10M"`,
`min_video_bitrate="8M"`, `max_video_bitrate="12M"`, `buffer_size="20M"`,
`audio_bitrate="192k"`, `audio_sample_rate=48000`, `audio_channels=2`,
`video_encoder="libx264"`, `preset="veryfast"`, `pixel_format="yuv420p"`,
`overwrite=False`, `captions=True`, `caption_sidecar=False`, and `normalize_audio=False`.
Dimensions and FPS must be positive. The library deliberately fixes output audio to 48 kHz stereo;
other `audio_sample_rate` or `audio_channels` values are rejected. `captions` controls burned-in
dialogue and clip captions, but not `TextOverlay`. `caption_sidecar=True` writes a sibling `.srt`
when caption utterances exist. `normalize_audio=True` enables EBU-style loudness normalization.

<!-- rot-api:RenderResult -->
### `RenderResult(output, duration, warnings=(), command=(), transcripts=())`

Completed output path, duration, nonfatal warnings, executed FFmpeg argument vector, and clip
transcripts used by the render. The command is an argument tuple, not a shell string. A successful
result means the temporary output passed the configured output-contract probe and was atomically
moved into place.

<!-- rot-api:MediaInfo -->
### `MediaInfo(path, duration, width, height, has_video, has_audio, format_name="", video_codec=None, audio_codec=None, pixel_format=None, frame_rate=None, sample_rate=None, channels=None, color_primaries=None, color_transfer=None, color_space=None, bit_rate=None)`

Probe metadata: `path`, `duration`, `width`, `height`, `has_video`, `has_audio`, `format_name`,
`video_codec`, `audio_codec`, `pixel_format`, `frame_rate`, `sample_rate`, `channels`,
`color_primaries`, `color_transfer`, `color_space`, and `bit_rate`. Missing stream metadata is
represented by `None`; still-image duration is normally zero. `MediaInfo` is returned internally
by probing and is public primarily for diagnostics and integration boundaries.

<!-- rot-api:ProgressEvent -->
### `ProgressEvent(stage, completed, total=1, message="")`

Progress update. `fraction` returns `completed / total` clamped to 0 through 1, or 0 when
`total <= 0`. Stable render stages include `validate`, `speech`, `transcribe`, `compile`, `render`,
and `done`; integrations may emit their own stages.

<!-- rot-api:ProgressCallback -->
### `ProgressCallback`

Type alias for `Callable[[ProgressEvent], None]`. Use it with project rendering, transcription, or
publishing:

```python
from rot import ProgressEvent, Project

def report(event: ProgressEvent) -> None:
    print(event.stage, f"{event.fraction:.0%}", event.message)

Project.short_form().background("clip.mp4").render("out.mp4", progress=report)
```

<!-- rot-api:StageProgressCallback -->
### `StageProgressCallback`

Provider callback: `Callable[[stage: str, completed: float, total: float, message: str], None]`.
This lower-level shape is used by voice, alignment, and transcription protocols. The project
adapts it into `ProgressEvent` updates for callers.

## Extension protocols and effects

<!-- rot-api:VoiceProvider -->
### `VoiceProvider`

Implement `synthesize(text, output_path, *, language, progress=None) -> SynthesizedAudio`.
Write the requested file or return the actual generated path. Times in `SynthesizedAudio` are
provider metadata; rot probes the file before using it. The protocol is runtime-checkable.

<!-- rot-api:WordAligner -->
### `WordAligner`

Implement `align(audio_path, text, *, language, progress=None) -> tuple[WordTiming, ...]`.
Returned times are local to the supplied audio. Words should be ordered and use the text actually
shown in captions. The protocol is runtime-checkable.

<!-- rot-api:Transcriber -->
### `Transcriber`

Implement `transcribe(audio_path, *, language=None, progress=None) -> Transcript`. Returned segment
and word timings are local to the prepared clip audio. The input is a non-looped prepared pass
after trim and speed adjustment; the renderer repeats cues when the clip itself loops.

<!-- rot-api:ScriptParser -->
### `ScriptParser`

Implement `parse(source) -> Script`. A parser may additionally implement `parse_file(path)` for
path-aware behavior. `Project.script_file` detects `parse_file` structurally, so it is an optional
extension rather than part of the protocol.

<!-- rot-api:CaptionRenderer -->
### `CaptionRenderer`

Implement `render(path, utterances, theme, *, width, height) -> Path`. The renderer must produce
an ASS file compatible with libass and return its actual path. `utterances` already contain
absolute line and word timings.

<!-- rot-api:AssCaptionRenderer -->
### `AssCaptionRenderer`

Built-in renderer implementing
`render(path, utterances, theme, *, width, height) -> Path`. It escapes user text, emits
resolution-aware ASS styles, groups words by `max_words`, applies active-word highlighting, and
supports inline styles. Utterances without resolved line or word timing are skipped.

<!-- rot-api:Effect -->
### `Effect`

Provide a `name` property and `filters(*, duration, width, height) -> tuple[FilterNode, ...]`.
Effects are applied in insertion order. Values must be represented through `FilterNode`; custom
implementations must not return raw filter-graph strings.

<!-- rot-api:FilterNode -->
### `FilterNode(name, arguments=())`

Safe FFmpeg filter name plus ordered `(option, value)` pairs. Names and option keys may contain
only alphanumerics and underscores. String values containing `;`, `[`, `]`, or line breaks are
rejected so a custom effect cannot splice graph structure.

<!-- rot-api:EffectSpec -->
### `EffectSpec(name, options=())`

Serializable effect request. `create(name, **options)` sorts keyword options deterministically.
It does not validate that `name` is built in until the project normalizes the spec.

<!-- rot-api:BuiltinEffect -->
### `BuiltinEffect(name, options=())`

`create(name, **options)` validates `zoom`, `punch-zoom`, `pan`, `shake`, `blur`, `grayscale`, or
`saturation`. `filters(*, duration, width, height)` emits safe filter nodes.

| Effect | Recognized option and default |
| --- | --- |
| `zoom` | `amount=1.08` |
| `punch-zoom` | `amount=1.18` |
| `pan` | No options; pans horizontally across a 110% scale |
| `shake` | `strength=8` pixels |
| `blur` | `radius=8` |
| `grayscale` | No options |
| `saturation` | `amount=1.5` |

`BuiltinEffect.create()` sorts its options. Direct construction is possible, but unknown names
fail when `filters()` is compiled.

```python
from dataclasses import dataclass
from rot import FilterNode

@dataclass(frozen=True)
class Contrast:
    amount: float = 1.15

    @property
    def name(self) -> str:
        return "contrast"

    def filters(self, *, duration: float, width: int, height: int):
        return (FilterNode("eq", (("contrast", self.amount),)),)
```

## Scripts, speech, and alignment

<!-- rot-api:RotScriptParser -->
### `RotScriptParser`

`parse(source)` reads `@speaker [id=..., audio=..., gap=...]: text`. `parse_file(path)` also
resolves relative audio paths beside the UTF-8 script.

Blank lines and lines whose first non-space character is `#` are ignored. Speaker names may use
letters, digits, `_`, `.`, and `-`. Metadata can be comma- or whitespace-separated and uses shell
quoting, so paths containing spaces can be quoted. Supported keys are:

| Key | Meaning |
| --- | --- |
| `id` | Unique line selector used by `during=` |
| `audio` | Prerecorded line audio; relative paths are resolved only by `parse_file()` |
| `gap` | Nonnegative silence after the line; default `0.15` |

Unknown keys, malformed lines, duplicate IDs, invalid gaps, and an empty script raise
`ScriptError` with a one-based line number where possible.

```text
# Comments and blank lines are allowed.
@alex [id=hook, gap=0.25]: [b]Wait[/b] for it.
@sam [audio="recordings/final take.wav"]: I did not expect that.
```

<!-- rot-api:ChatterboxVoice -->
### `ChatterboxVoice`

`ChatterboxVoice(reference_audio=None, variant="turbo", device="auto", exaggeration=0.5,
cfg_weight=0.5, multilingual_version="v3")` supports `turbo`, `english`, and `multilingual`.
Turbo requires consented `reference_audio`; English and multilingual may use their model default.
`exaggeration` and `cfg_weight` apply to the English model, `language` is forwarded to the
multilingual model, and `multilingual_version` is `v2` or `v3`. Models are cached by variant,
device, and multilingual version. `synthesize(...)` preserves Chatterbox watermarking and wraps
generation failures in `VoiceError`.

`synthesize(text, output_path, *, language, progress=None) -> SynthesizedAudio` writes the
requested audio path and reports optional provider-stage progress.

Install with `uv sync --extra chatterbox` (`tts` is an alias). Never clone a person's voice
without that person's informed permission.

<!-- rot-api:KokoroVoice -->
### `KokoroVoice`

`KokoroVoice(voice="af_heart", speed=1, device="auto", lang_code=None,
repo_id="hexgrad/Kokoro-82M", split_pattern=r"\n+")` accepts a built-in voice name,
comma-separated blend, or local `.pt` pack. `speed` must be positive. Devices are `auto`, `cpu`,
`cuda`, and `mps`; explicit unavailable acceleration raises `ConfigurationError`, and MPS requires
`PYTORCH_ENABLE_MPS_FALLBACK=1`.

`lang_code` can force Kokoro's `a`, `b`, `e`, `f`, `h`, `i`, `j`, `p`, or `z` pipelines.
Otherwise the speaker language—and for generic English, the voice prefix—selects one.
`synthesize(...)` caches models/pipelines and writes 24 kHz mono PCM-16 WAV. Install with
`uv sync --extra kokoro`.
Its full provider signature is
`synthesize(text, output_path, *, language, progress=None) -> SynthesizedAudio`.

<!-- rot-api:StableTSAligner -->
### `StableTSAligner`

Parameters: `model="base"`, `device=None`, `backend="whisper"`, and `failure_threshold=0.25`.
`backend` is `whisper` or `faster-whisper`. `align(audio_path, text, *, language, progress=None)`
loads/caches the chosen Stable-TS model, requests original-split alignment, and returns local word
timings. A provider failure, null result, or result without words raises `AlignmentError`. Install
with `uv sync --extra align`.

<!-- rot-api:StableTSTranscriber -->
### `StableTSTranscriber`

Parameters: `model="base"`, `device=None`, and `backend="whisper"`. The `transcribe` method returns
Stable-TS segments with word timestamps and lazily shares model instances with the aligner.
`language=None` enables detection. Empty Stable-TS segments are skipped; invalid provider results
raise `TranscriptionError`. Install with `uv sync --extra transcribe` (the `align` extra installs
the same dependency).
`transcribe(audio_path, *, language=None, progress=None) -> Transcript` accepts the prepared local
audio path and an optional stage callback.

<!-- rot-api:OpenRouterParser -->
### `OpenRouterParser`

Parameters: required `model`, `speakers=()`, `api_key=None`, the default OpenRouter `endpoint`,
`timeout=60`, and `retries=2`. `parse(source)` uses strict structured output. The API key defaults
to `OPENROUTER_API_KEY` and is never included in representations or errors. An explicit
`speakers` tuple becomes a JSON-Schema enum, preventing the model from inventing speakers.
Requests use temperature zero, retry HTTP 429 and 5xx responses with exponential backoff, and
validate speaker names and unique optional IDs again locally. Install with
`uv sync --extra openrouter`.

## Clip discovery

<!-- rot-api:ClipDetectionSettings -->
### `ClipDetectionSettings(method="hybrid", clip_duration=30, clip_count=5, ...)`

Selection: `method="hybrid"`, `clip_duration=30`, `clip_count=5`, `max_overlap_ratio=0.2`, and
`max_per_source=None`. Extraction: `scene_threshold=0.3`, `analysis_interval=0.5`,
`analysis_width=320`, `motion_fps=15`. Audio normalization: `audio_floor_db=-50`,
`audio_ceiling_db=-12`, `audio_mean_weight=0.7`, `audio_peak_weight=0.3`. Scoring:
`scene_half_saturation=0.25`, `motion_reference=12`, `scene_weight=0.35`,
`motion_weight=0.2`, `audio_weight=0.45`. Boundaries: `boundary_penalty=0.15`,
`edge_probe=0.4`, `snap=True`, `snap_window=1`, `snap_silence_level=0.25`.

`method` may be `hybrid`, `scene`, `motion`, or `audio`. Single-signal methods zero the other
weights; the selected signal's configured weight still participates before normalization.
`signal_weights` returns the resolved dictionary. `cache_key_fields` returns only the four
settings that change decoded signals: threshold, interval, analysis width, and motion FPS.

Important validation bounds are: positive duration/count/interval/FPS/references; even
`analysis_width >= 64`; `0 < scene_threshold <= 1`; overlap and boundary penalty in `[0, 1)`;
snap silence in `[0, 1]`; `audio_floor_db < audio_ceiling_db`; and nonnegative blend weights with
at least one positive hybrid weight.

<!-- rot-api:ClipCandidate -->
### `ClipCandidate(source, start, end, score, scene_score, motion_score, audio_score)`

Fields: `source`, `start`, `end`, combined `score`, `scene_score`, `motion_score`, and
`audio_score`. `duration` computes the span. `as_clip(*, keep_audio=True)` creates a trim-aware
non-looping `Clip` with `trim_start=start` and `trim_end=end`.

<!-- rot-api:SkippedSource -->
### `SkippedSource(path, reason)`

Records a media path that could not be analyzed and its sanitized reason.

<!-- rot-api:ClipSearchResult -->
### `ClipSearchResult`

Fields: `candidates`, `sources=()`, `exports=()`, `skipped=()`, and `warnings=()`. `source` returns
the only source and raises for multi-source searches. `project_clips(*, keep_audio=True)` converts
all candidates, preserving ranked order.

<!-- rot-api:VideoClipFinder -->
### `VideoClipFinder(settings=None, *, cache=True)`

`cache` enables the default on-disk signal cache. `analyze(source, *, reporter=None)` returns
candidates; `analyze_many(sources, *, reporter=None)` ranks across files; `find(source,
output_dir, *, export=True, overwrite=False, progress=False)` performs the common workflow; and
`export(candidates, output_dir, *, overwrite=False)` encodes candidates.

The default cache lives below `$XDG_CACHE_HOME/rot/clip-signals`, or
`~/.cache/rot/clip-signals`. Cache keys include the resolved source path, size, modification time,
cache format version, and decoding-related settings. Cache corruption or an unwritable cache
degrades to a miss rather than failing analysis.

`analyze` requires a non-empty video; audio-only ranking additionally requires audio.
`analyze_many` skips per-file probe/analysis failures, records them as `SkippedSource`, ranks
surviving candidates globally, and raises only when no source can be analyzed. `max_per_source`
limits how many candidates enter global competition. `export` writes deterministic H.264/AAC MP4
names and refuses to replace files unless requested.

<!-- rot-api:FolderClipFinder -->
### `FolderClipFinder`

Inherits `VideoClipFinder`. `find(root, output_dir, *, export=True, overwrite=False,
progress=False, recursive=True, extensions=None)` ranks a local video library while reporting
unreadable sources.
Hidden files/directories are skipped. `extensions` is case-insensitive; an empty discovery result
raises `ClipAnalysisError`.

<!-- rot-api:YouTubeClipFinder -->
### `YouTubeClipFinder`

Inherits `VideoClipFinder`. `download(url, output, *, overwrite=False)` writes one permitted MP4.
`find(url, output_dir, *, export=True, overwrite_download=False, overwrite_exports=False,
progress=False)` downloads, analyzes, and optionally exports.

Install with `uv sync --extra youtube`. Downloads reject non-HTTP(S) and non-YouTube hosts, disable
playlists, prefer AVC MP4 plus M4A, and remux or transcode to MP4 when necessary. The common
workflow stores the download as `output_dir/source.mp4`. Only download and reuse material you are
authorized to use.

<!-- rot-api:TwitchClipFinder -->
### `TwitchClipFinder(settings=None, *, client_id, access_token, cache=True, timeout=60)`

Inherits `VideoClipFinder` and uses Twitch's official Clips Download API. The user token must
include `channel:manage:clips` or `editor:manage:clips`, and its user must be the broadcaster or an
authorized editor for the clip's channel. `download(clip, output, *, overwrite=False,
variant="landscape")` accepts a clip ID or standard Twitch clip URL. `find(clip, output_dir, *,
export=True, overwrite_download=False, overwrite_exports=False, progress=False,
variant="landscape")` downloads, analyzes, and optionally exports. Variants are `landscape` and
`portrait`; unavailable requested variants raise `DownloadError`.

Install with `uv sync --extra twitch`. Constructor credentials are stripped and must be non-empty;
`timeout` must be positive. Downloads require an `.mp4` destination, stream to a temporary sibling,
and atomically replace the destination only after a non-empty response. Signed media URLs and
tokens are deliberately excluded from user-facing transport errors.

<!-- rot-api:discover_videos -->
### `discover_videos(root, *, recursive=True, extensions=None, follow_symlinks=False)`

Return sorted, resolved, deduplicated videos beneath a directory. Hidden paths and, by default,
symbolic links are skipped. The default case-insensitive suffixes are `.mp4`, `.mov`, `.mkv`,
`.webm`, `.m4v`, `.avi`, `.flv`, `.wmv`, `.mpeg`, and `.mpg`. A missing/non-directory root raises
`ConfigurationError`.

## Publishing

Publishing always requires explicit consent and the optional `publish` dependency
(`uv sync --extra publish`). Constructors do not publish. `preflight` performs local validation
and may contact the platform to resolve the destination; only `publish` and `publish_all` create
remote media.

<!-- rot-api:TokenProvider -->
### `TokenProvider`

Implement `access_token() -> str` and `refresh_access_token() -> str | None`. Providers own any
credential persistence; rot never logs returned tokens. Publishers call `refresh_access_token`
once after an authorization failure and retry only when a replacement token is returned.

<!-- rot-api:StaticTokenProvider -->
### `StaticTokenProvider(token)`

In-memory, redacted non-empty token. Its dataclass representation hides the token.
`access_token()` returns it and `refresh_access_token()` returns `None`.

<!-- rot-api:YouTubeShort -->
### `YouTubeShort(title, privacy, made_for_kids, contains_synthetic_media, has_paid_product_placement, description="", tags=(), category_id="22")`

Required: `title`, `privacy`, `made_for_kids`, `contains_synthetic_media`, and
`has_paid_product_placement`. Optional: `description=""`, `tags=()`, `category_id="22"`.
Titles are stripped and limited to 1–100 characters. Privacy is `private`, `unlisted`, or `public`;
category IDs must contain only digits, and tags cannot be blank.

<!-- rot-api:InstagramReel -->
### `InstagramReel(caption="", share_to_feed=True)`

Instagram caption and feed-placement choice.
Captions may contain at most 2,200 Python characters.

<!-- rot-api:TikTokVideo -->
### `TikTokVideo(privacy, allow_comments, allow_duet, allow_stitch, brand_organic, branded_content, ai_generated, caption="")`

Required: `privacy`, `allow_comments`, `allow_duet`, `allow_stitch`, `brand_organic`,
`branded_content`, and `ai_generated`; optional `caption=""`.
Privacy must be `PUBLIC_TO_EVERYONE`, `MUTUAL_FOLLOW_FRIENDS`,
`FOLLOWER_OF_CREATOR`, or `SELF_ONLY`. Caption length is limited to 2,200 UTF-16 code units,
matching TikTok's accounting. Every interaction and disclosure choice is explicit so platform
defaults cannot silently change the post's policy.

<!-- rot-api:PublishPreflight -->
### `PublishPreflight(platform, account_name=None, warnings=(), details={})`

Resolved destination account, policy warnings, and platform-specific confirmation details.
`details` is an independent dictionary per instance and is excluded from equality/repr comparisons.

<!-- rot-api:PublishResult -->
### `PublishResult(platform, remote_id, status="published", post_id=None, url=None, account_name=None, warnings=())`

Fields: `platform`, `remote_id`, `status="published"`, `post_id=None`, `url=None`,
`account_name=None`, and `warnings=()`.

<!-- rot-api:PublishFailure -->
### `PublishFailure(platform, message, remote_id=None)`

Sanitized platform failure; `remote_id` preserves a resumable upload when available.

<!-- rot-api:PublishBatchResult -->
### `PublishBatchResult(results=(), failures=())`

`successful` is true when at least one publish succeeded and none failed.

<!-- rot-api:Publisher -->
### `Publisher`

Protocol with `platform`, `accepts(metadata)`, `preflight(video, metadata)`, and
`publish(video, metadata, *, consent, progress=True, wait_timeout=900, poll_interval=2)`.
`preflight` returns `PublishPreflight`; `publish` returns `PublishResult`. Built-in publishers
require a valid rendered MP4, positive wait timeout, nonnegative polling interval, matching
metadata, and `consent=True`.

<!-- rot-api:PublishJob -->
### `PublishJob(publisher, metadata)`

Validated pairing of a `Publisher` and compatible platform metadata.
Construction calls `publisher.accepts(metadata)` and rejects a mismatch immediately.

<!-- rot-api:YouTubePublisher -->
### `YouTubePublisher(token, *, chunk_size=8388608)`

`token` is a string or `TokenProvider`; chunks must be positive multiples of 256 KiB. Supports
`accepts`, `preflight`, and consent-gated `publish` with the common Publisher parameters.
It uses YouTube's resumable upload API, applies audience/synthetic-media/paid-promotion
declarations, and waits for terminal processing. The result retains the resumable video ID if
processing times out.

Methods are `accepts(metadata) -> bool`, `preflight(video, metadata) -> PublishPreflight`, and
`publish(video, metadata, *, consent, progress=True, wait_timeout=900, poll_interval=2)
-> PublishResult`.

<!-- rot-api:InstagramPublisher -->
### `InstagramPublisher(token, user_id, *, api_version="v25.0")`

Publishes to an Instagram professional account. Supports `accepts`, account-aware `preflight`,
and consent-gated `publish` with the common Publisher parameters.
`user_id` and API version are required non-empty strings. Upload is resumable; publishing creates
a Reel container, waits until it is ready, then publishes it.

Methods are `accepts(metadata) -> bool`, `preflight(video, metadata) -> PublishPreflight`, and
`publish(video, metadata, *, consent, progress=True, wait_timeout=900, poll_interval=2)
-> PublishResult`.

<!-- rot-api:TikTokPublisher -->
### `TikTokPublisher(token)`

Checks creator privacy and interaction settings during `preflight`, then performs chunked,
consent-gated `publish` with the common Publisher parameters.
Requested privacy must be among the creator's currently allowed options. Interaction settings and
commercial-content disclosures are checked before upload; processing timeout retains the publish
ID.

Methods are `accepts(metadata) -> bool`, `preflight(video, metadata) -> PublishPreflight`, and
`publish(video, metadata, *, consent, progress=True, wait_timeout=900, poll_interval=2)
-> PublishResult`.

<!-- rot-api:publish_all -->
### `publish_all(video, jobs, *, consent, progress=True, wait_timeout=900, poll_interval=2, on_preflight=None)`

Preflight all jobs, optionally ask `on_preflight(tuple[PublishPreflight, ...])` for confirmation,
then publish valid jobs in order while retaining partial successes.

At least one job is required. Preflight failures are collected rather than aborting other
destinations. Consent is sufficient when either `consent=True` or the callback returns exactly
`True`; without it, valid jobs are not published. Publish failures are sanitized into
`PublishFailure`, timeouts preserve `remote_id`, and successful earlier platforms remain in
`results`.

```python
from rot import (
    PublishJob,
    YouTubePublisher,
    YouTubeShort,
    publish_all,
)

job = PublishJob(
    YouTubePublisher("access-token"),
    YouTubeShort(
        title="The impossible save",
        privacy="private",
        made_for_kids=False,
        contains_synthetic_media=False,
        has_paid_product_placement=False,
    ),
)

def confirm(checks):
    for check in checks:
        print(check.platform, check.account_name, check.warnings)
    return True  # This return value is explicit consent.

result = publish_all(
    "short.mp4",
    [job],
    consent=False,
    on_preflight=confirm,
)
```

## Exceptions

Catch the narrowest exception that lets the application recover. All exceptions inherit
`RotError` and accept the normal exception message unless noted. User-facing messages are designed
to be actionable and to avoid tokens, API keys, and signed media URLs.

<!-- rot-api:RotError -->
### `RotError`

Base class for all expected rot failures. A broad CLI boundary may catch this to display a clean
error without hiding unrelated programming errors.

<!-- rot-api:ConfigurationError -->
### `ConfigurationError`

Invalid project, model, option, path policy, overwrite choice, consent state, or media
configuration. Most model constructors raise this before external work begins.

<!-- rot-api:ScriptError -->
### `ScriptError`

Deterministic `.rot` parsing or validation failure, normally including the one-based line number.

<!-- rot-api:DependencyError -->
### `DependencyError`

Missing executable, codec, FFmpeg filter, or optional Python dependency. Messages identify the
required executable or installation extra.

<!-- rot-api:ProbeError -->
### `ProbeError`

FFprobe could not inspect an asset or returned unusable metadata.

<!-- rot-api:RenderError -->
### `RenderError`

FFmpeg execution, missing temporary output, or post-render output-contract failure. Atomic
rendering leaves an existing destination untouched until a new output has succeeded.

<!-- rot-api:VoiceError -->
### `VoiceError`

Speech generation or generated-audio failure from a voice provider.

<!-- rot-api:AlignmentError -->
### `AlignmentError`

Known-transcript alignment failure, including a provider result with no usable word timings.

<!-- rot-api:TranscriptionError -->
### `TranscriptionError`

Clip speech-to-text, prepared-audio, or invalid transcription-provider result.

<!-- rot-api:ParserError -->
### `ParserError`

Remote/AI parser configuration, request, response, or structured-validation failure.

<!-- rot-api:DownloadError -->
### `DownloadError`

Remote media authorization, availability, transport, conversion, or overwrite failure.

<!-- rot-api:ClipAnalysisError -->
### `ClipAnalysisError`

Clip signal extraction, scoring, selection, multi-source result access, or export failure.

<!-- rot-api:PublishError -->
### `PublishError`

Platform request, upload, policy, or processing failure.

<!-- rot-api:PublishTimeoutError -->
### `PublishTimeoutError(message, *, platform, remote_id)`

Subclass of `PublishError` for a timed-out remote operation. Its `platform` and `remote_id`
attributes retain the existing resumable upload/container identifier so callers can reconcile
state instead of blindly creating a duplicate.
