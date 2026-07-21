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

## Project composition

<!-- rot-api:Project -->
### `Project(*, settings=None)`

Mutable fluent builder for one output video. `settings` is a `RenderSettings`; omitting it uses
the short-form preset. Every composition method returns the same project.

| Method | Parameters and behavior |
| --- | --- |
| `short_form()` | Construct the standard vertical-video preset. |
| `background(source, *, trim=None, duration=None, loop=True, fit="cover", fit_amount=0.5, fill="black", fill_blur=40, facecam=None, focus=None, position=None, transcribe=False, anchor="center", keep_audio=False, volume=1, speed=1, clip_id=None)` | Clear existing clips and add the first video or still. A lone still may infer duration from dialogue; otherwise give it `duration`. |
| `add_clip(source, *, trim=None, duration=None, loop=True, fit="cover", fit_amount=0.5, fill="black", fill_blur=40, facecam=None, focus=None, position=None, transcribe=False, anchor="center", keep_audio=False, volume=1, speed=1, transition="cut", transition_duration=0.3, clip_id=None)` | Append media in playback order. `transition` describes the incoming transition from the previous clip. |
| `transition(name, *, duration=0.3)` | Set the latest clip's outgoing transition. Names: `cut`, `fade`, `crossfade`, `slide-left`, `slide-right`, `zoom`. |
| `overlay_image(source, *, at=None, duration=None, during=None, speaker=None, during_clip=None, position="center", width=None, opacity=1, animation="pop", z_index=0)` | Add a static image using exactly one timing selector. Absolute overlays default to two seconds. `width=None` means 560 pixels. Animations: `none`, `pop`, `fade`, `slide`, `bounce`. |
| `overlay_text(text, *, at=None, duration=None, during=None, speaker=None, during_clip=None, position="top", font="DejaVu Sans", font_size=76, color="#FFFFFF", outline_color="#000000", outline_width=6, shadow=2, bold=True, uppercase=False, margin_x=70, margin_y=160, z_index=0)` | Add styled non-caption text. An absolute overlay without `duration` remains until video end. |
| `add_speaker(name, *, voice=None, portrait=None, language="en", portrait_position="bottom-right", portrait_width=420, portrait_animation="pop")` | Register one script speaker, optional voice provider, and static portrait. |
| `script(source, *, parser=None)` | Parse text with `RotScriptParser` or a custom `ScriptParser`. |
| `script_file(path, *, parser=None)` | Parse a UTF-8 file; the built-in parser resolves relative line audio beside the script. |
| `captions(theme="pop", **overrides)` | Select `classic`, `pop`, `karaoke`, or `bounce`, or pass a `CaptionTheme`; keyword fields override it. |
| `effect(effect, **options)` | Apply a built-in name, `EffectSpec`, or custom `Effect` to the whole visual track. |
| `soundtrack(source, *, volume=0.15, trim=None, loop=True, fade_in=0, fade_out=0, ducking=False)` | Configure one music bed. A later call replaces it. `trim=(start, end)` selects the repeated segment; `loop=False` leaves silence after one play. Fades use output time. Ducking sidechain-compresses music beneath dialogue. |
| `with_aligner(aligner)` | Use a `WordAligner` for accurate caption timing. |
| `with_transcriber(transcriber)` | Replace the default `StableTSTranscriber` used by opted-in clips. |
| `clip_captions(theme="pop", **overrides)` | Configure the separate caption lane generated from clip speech. |
| `transcribe_clips(*, progress=True)` | Return cached structured transcripts for opted-in clips without rendering. |
| `with_caption_renderer(renderer)` | Replace the built-in ASS renderer. |
| `render(output, *, progress=True, overwrite=None, keep_workdir=False)` | Atomically encode an MP4. `progress` is a bool or `ProgressCallback`; `overwrite=None` uses settings. Returns `RenderResult`. |

Image positions and anchors are `center`, `top`, `bottom`, `left`, `right`, and their four corner
forms. `during` names an utterance ID, `speaker` repeats over that speaker's lines, and
`during_clip` accepts a stable clip ID or zero-based index.

Image overlays, text overlays, speaker portraits, and captions also accept `Placement` for exact
normalized positioning. Inline text supports safe nested `color`, `b`, `i`, `u`, `font`, and
`size` BBCode tags.

## Timeline and render models

<!-- rot-api:Clip -->
### `Clip`

Primary-track media fields: `source`, `trim_start=0`, `trim_end=None`, `duration=None`,
`loop=True`, `fit="cover"`, `anchor="center"`, `keep_audio=False`, `volume=1`, `speed=1`,
`effects=[]`, `transition="cut"`, `transition_duration=0.3`, `id=None`, `fit_amount=0.5`,
`fill="black"`, `fill_blur=40`, `facecam=None`, `focus=None`, `position=None`, and
`transcribe=False`. Still images cannot be trimmed and need explicit duration in
multi-clip or dialogue-free projects; their playback speed must remain 1.

`focus=(x, y)` controls the exact normalized source focal point for `cover`/`custom` cropping.
`position=Placement(...)` controls normalized canvas placement for `contain`/`custom` foregrounds.
`transcribe=True` enables language detection; pass `ClipTranscription` for a language override.

<!-- rot-api:Placement -->
### `Placement(x, y, anchor="center")`

Normalized canvas point for a layered element. Coordinates range from 0 through 1; `anchor`
selects the point on the element attached to that coordinate.

<!-- rot-api:NormalizedRect -->
### `NormalizedRect(x, y, width, height)`

Normalized source or destination rectangle. Dimensions must be positive and the rectangle must
remain inside its frame.

<!-- rot-api:Facecam -->
### `Facecam(crop, destination)`

Extract an embedded facecam from the same custom-fit video clip. `crop` and `destination` are
`NormalizedRect` values; the crop aspect-preservingly cover-fills the destination.

<!-- rot-api:ClipTranscription -->
### `ClipTranscription(language=None)`

Per-clip speech-to-text options. `language=None` delegates language detection to the provider.

<!-- rot-api:TranscriptSegment -->
### `TranscriptSegment(text, start, end, words=())`

One clip-local transcription segment with optional word-level `WordTiming` values.

<!-- rot-api:Transcript -->
### `Transcript(segments=(), language=None)`

Structured provider output for one non-looped, trimmed, speed-adjusted clip pass. `text` joins all
segment text.

<!-- rot-api:ClipTranscript -->
### `ClipTranscript(clip_index, clip_id, source, transcript)`

Associates a clip-local `Transcript` with its project index, optional ID, and resolved source.

<!-- rot-api:Overlay -->
### `Overlay`

Static-image fields mirror `Project.overlay_image`: `source`, the mutually exclusive `at`,
`during`, `speaker`, and `during_clip` selectors, plus `duration`, `position`, `width`, `opacity`,
`animation`, and `z_index`.

<!-- rot-api:TextOverlay -->
### `TextOverlay`

Immutable text configuration with the parameters shown on `Project.overlay_text`. Colors must use
`#RRGGBB`; margins and outline/shadow widths are nonnegative. Text accepts safe inline BBCode and
is stored as plain text plus parsed `styled_runs`.

<!-- rot-api:Soundtrack -->
### `Soundtrack`

Immutable music configuration: `source`, `volume=0.15`, `trim_start=0`, `trim_end=None`,
`loop=True`, `fade_in=0`, `fade_out=0`, and `ducking=False`. Music never changes project duration.

<!-- rot-api:Speaker -->
### `Speaker`

Speaker fields: `name`, `voice=None`, `portrait=None`, `language="en"`,
`portrait_position="bottom-right"`, `portrait_width=420`, and `portrait_animation="pop"`.

<!-- rot-api:Utterance -->
### `Utterance`

One dialogue line: `speaker`, plain `text`, parsed inline `styled_runs`, optional `id`, optional
prerecorded `audio`, `gap_after=0.15`, and renderer-resolved `start`, `end`, and `words`.

<!-- rot-api:Script -->
### `Script(utterances=[])`

Ordered utterances. `ids()` returns all non-null line IDs.

<!-- rot-api:WordTiming -->
### `WordTiming(text, start, end)`

One word and its absolute start/end times. Start must be nonnegative and end cannot precede start.

<!-- rot-api:SynthesizedAudio -->
### `SynthesizedAudio(path, duration=None)`

Audio created by a voice provider; `duration` is optional provider metadata.

<!-- rot-api:CaptionTheme -->
### `CaptionTheme`

Fields: `name="pop"`, `font="DejaVu Sans"`, `font_size=82`, `primary_color="#FFFFFF"`,
`highlight_color="#FFE135"`, `outline_color="#000000"`, `outline_width=7`, `shadow=2`,
`position_y=1310`, `position=None`, `max_words=5`, and `uppercase=False`. `position` accepts a
normalized `Placement` and overrides `position_y`. `preset(name)` loads a built-in theme.

<!-- rot-api:RenderSettings -->
### `RenderSettings`

Encoding fields: `width=1080`, `height=1920`, `fps=30`, `video_bitrate="10M"`,
`min_video_bitrate="8M"`, `max_video_bitrate="12M"`, `buffer_size="20M"`,
`audio_bitrate="192k"`, `audio_sample_rate=48000`, `audio_channels=2`,
`video_encoder="libx264"`, `preset="veryfast"`, `pixel_format="yuv420p"`,
`overwrite=False`, `captions=True`, `caption_sidecar=False`, and `normalize_audio=False`.

<!-- rot-api:RenderResult -->
### `RenderResult(output, duration, warnings=(), command=(), transcripts=())`

Completed output path, duration, nonfatal warnings, executed FFmpeg argument vector, and clip
transcripts used by the render.

<!-- rot-api:MediaInfo -->
### `MediaInfo`

Probe metadata: `path`, `duration`, `width`, `height`, `has_video`, `has_audio`, `format_name`,
`video_codec`, `audio_codec`, `pixel_format`, `frame_rate`, `sample_rate`, `channels`,
`color_primaries`, `color_transfer`, `color_space`, and `bit_rate`.

<!-- rot-api:ProgressEvent -->
### `ProgressEvent(stage, completed, total=1, message="")`

Progress update. `fraction` returns a clamped 0-to-1 ratio.

<!-- rot-api:ProgressCallback -->
### `ProgressCallback`

Callable receiving one `ProgressEvent`: `Callable[[ProgressEvent], None]`.

<!-- rot-api:StageProgressCallback -->
### `StageProgressCallback`

Provider callback: `Callable[[stage: str, completed: float, total: float, message: str], None]`.

## Extension protocols and effects

<!-- rot-api:VoiceProvider -->
### `VoiceProvider`

Implement `synthesize(text, output_path, *, language, progress=None) -> SynthesizedAudio`.
Write the requested file or return the actual generated path.

<!-- rot-api:WordAligner -->
### `WordAligner`

Implement `align(audio_path, text, *, language, progress=None) -> tuple[WordTiming, ...]`.
Returned times are local to the supplied audio.

<!-- rot-api:Transcriber -->
### `Transcriber`

Implement `transcribe(audio_path, *, language=None, progress=None) -> Transcript`. Returned segment
and word timings are local to the prepared clip audio.

<!-- rot-api:ScriptParser -->
### `ScriptParser`

Implement `parse(source) -> Script`. A parser may additionally implement `parse_file(path)` for
path-aware behavior.

<!-- rot-api:CaptionRenderer -->
### `CaptionRenderer`

Implement `render(path, utterances, theme, *, width, height) -> Path`. The renderer must produce
an ASS file compatible with libass.

<!-- rot-api:AssCaptionRenderer -->
### `AssCaptionRenderer`

Built-in renderer implementing `render(path, utterances, theme, *, width, height) -> Path`.

<!-- rot-api:Effect -->
### `Effect`

Provide a `name` property and `filters(*, duration, width, height) -> tuple[FilterNode, ...]`.

<!-- rot-api:FilterNode -->
### `FilterNode(name, arguments=())`

Safe FFmpeg filter name plus `(option, value)` pairs. Unsafe graph separators are rejected.

<!-- rot-api:EffectSpec -->
### `EffectSpec(name, options=())`

Serializable effect request. `create(name, **options)` sorts keyword options deterministically.

<!-- rot-api:BuiltinEffect -->
### `BuiltinEffect(name, options=())`

`create(name, **options)` validates `zoom`, `punch-zoom`, `pan`, `shake`, `blur`, `grayscale`, or
`saturation`. `filters(*, duration, width, height)` emits safe filter nodes.

## Scripts, speech, and alignment

<!-- rot-api:RotScriptParser -->
### `RotScriptParser`

`parse(source)` reads `@speaker [id=..., audio=..., gap=...]: text`. `parse_file(path)` also
resolves relative audio paths beside the UTF-8 script.

<!-- rot-api:ChatterboxVoice -->
### `ChatterboxVoice`

Parameters: `reference_audio=None`, `variant="turbo"`, `device="auto"`, `exaggeration=0.5`,
`cfg_weight=0.5`, and `multilingual_version="v3"`. Turbo requires consented reference audio.
`synthesize(text, output_path, *, language, progress=None)` preserves Chatterbox watermarking.

<!-- rot-api:KokoroVoice -->
### `KokoroVoice`

Parameters: `voice="af_heart"`, `speed=1`, `device="auto"`, `lang_code=None`,
`repo_id="hexgrad/Kokoro-82M"`, and `split_pattern=r"\n+"`. `voice` accepts a built-in name,
comma-separated blend, or local `.pt` pack. `synthesize(...)` writes 24 kHz mono WAV.

<!-- rot-api:StableTSAligner -->
### `StableTSAligner`

Parameters: `model="base"`, `device=None`, `backend="whisper"`, and `failure_threshold=0.25`.
`align(audio_path, text, *, language, progress=None)` returns local word timings.

<!-- rot-api:StableTSTranscriber -->
### `StableTSTranscriber`

Parameters: `model="base"`, `device=None`, and `backend="whisper"`. The `transcribe` method returns
Stable-TS segments with word timestamps and lazily caches model instances.

<!-- rot-api:OpenRouterParser -->
### `OpenRouterParser`

Parameters: required `model`, `speakers=()`, `api_key=None`, the default OpenRouter `endpoint`,
`timeout=60`, and `retries=2`. `parse(source)` uses strict structured output. The API key defaults
to `OPENROUTER_API_KEY` and is never included in representations or errors.

## Clip discovery

<!-- rot-api:ClipDetectionSettings -->
### `ClipDetectionSettings`

Selection: `method="hybrid"`, `clip_duration=30`, `clip_count=5`, `max_overlap_ratio=0.2`, and
`max_per_source=None`. Extraction: `scene_threshold=0.3`, `analysis_interval=0.5`,
`analysis_width=320`, `motion_fps=15`. Audio normalization: `audio_floor_db=-50`,
`audio_ceiling_db=-12`, `audio_mean_weight=0.7`, `audio_peak_weight=0.3`. Scoring:
`scene_half_saturation=0.25`, `motion_reference=12`, `scene_weight=0.35`,
`motion_weight=0.2`, `audio_weight=0.45`. Boundaries: `boundary_penalty=0.15`,
`edge_probe=0.4`, `snap=True`, `snap_window=1`, `snap_silence_level=0.25`.

<!-- rot-api:ClipCandidate -->
### `ClipCandidate`

Fields: `source`, `start`, `end`, combined `score`, `scene_score`, `motion_score`, and
`audio_score`. `duration` computes the span. `as_clip(*, keep_audio=True)` creates a trim-aware
non-looping `Clip`.

<!-- rot-api:SkippedSource -->
### `SkippedSource(path, reason)`

Records a media path that could not be analyzed and its sanitized reason.

<!-- rot-api:ClipSearchResult -->
### `ClipSearchResult`

Fields: `candidates`, `sources=()`, `exports=()`, `skipped=()`, and `warnings=()`. `source` returns
the only source and raises for multi-source searches. `project_clips(*, keep_audio=True)` converts
all candidates.

<!-- rot-api:VideoClipFinder -->
### `VideoClipFinder(settings=None, *, cache=True)`

`cache` enables the default on-disk signal cache. `analyze(source, *, reporter=None)` returns
candidates; `analyze_many(sources, *, reporter=None)` ranks across files; `find(source,
output_dir, *, export=True, overwrite=False, progress=False)` performs the common workflow; and
`export(candidates, output_dir, *, overwrite=False)` encodes candidates.

<!-- rot-api:FolderClipFinder -->
### `FolderClipFinder`

Inherits `VideoClipFinder`. `find(root, output_dir, *, export=True, overwrite=False,
progress=False, recursive=True, extensions=None)` ranks a local video library while reporting
unreadable sources.

<!-- rot-api:YouTubeClipFinder -->
### `YouTubeClipFinder`

Inherits `VideoClipFinder`. `download(url, output, *, overwrite=False)` writes one permitted MP4.
`find(url, output_dir, *, export=True, overwrite_download=False, overwrite_exports=False,
progress=False)` downloads, analyzes, and optionally exports.

<!-- rot-api:TwitchClipFinder -->
### `TwitchClipFinder(settings=None, *, client_id, access_token, cache=True, timeout=60)`

Inherits `VideoClipFinder` and uses Twitch's official Clips Download API. The user token must
include `channel:manage:clips` or `editor:manage:clips`, and its user must be the broadcaster or an
authorized editor for the clip's channel. `download(clip, output, *, overwrite=False,
variant="landscape")` accepts a clip ID or standard Twitch clip URL. `find(clip, output_dir, *,
export=True, overwrite_download=False, overwrite_exports=False, progress=False,
variant="landscape")` downloads, analyzes, and optionally exports. Variants are `landscape` and
`portrait`; unavailable requested variants raise `DownloadError`.

<!-- rot-api:discover_videos -->
### `discover_videos(root, *, recursive=True, extensions=None, follow_symlinks=False)`

Return sorted, resolved, deduplicated videos beneath a directory. Hidden paths and, by default,
symbolic links are skipped.

## Publishing

Publishing always requires explicit consent and the optional `publish` dependency.

<!-- rot-api:TokenProvider -->
### `TokenProvider`

Implement `access_token() -> str` and `refresh_access_token() -> str | None`. Providers own any
credential persistence; rot never logs returned tokens.

<!-- rot-api:StaticTokenProvider -->
### `StaticTokenProvider(token)`

In-memory, redacted token. `access_token()` returns it and `refresh_access_token()` returns `None`.

<!-- rot-api:YouTubeShort -->
### `YouTubeShort`

Required: `title`, `privacy`, `made_for_kids`, `contains_synthetic_media`, and
`has_paid_product_placement`. Optional: `description=""`, `tags=()`, `category_id="22"`.

<!-- rot-api:InstagramReel -->
### `InstagramReel(caption="", share_to_feed=True)`

Instagram caption and feed-placement choice.

<!-- rot-api:TikTokVideo -->
### `TikTokVideo`

Required: `privacy`, `allow_comments`, `allow_duet`, `allow_stitch`, `brand_organic`,
`branded_content`, and `ai_generated`; optional `caption=""`.

<!-- rot-api:PublishPreflight -->
### `PublishPreflight(platform, account_name=None, warnings=(), details={})`

Resolved destination account, policy warnings, and platform-specific confirmation details.

<!-- rot-api:PublishResult -->
### `PublishResult`

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

<!-- rot-api:PublishJob -->
### `PublishJob(publisher, metadata)`

Validated pairing of a `Publisher` and compatible platform metadata.

<!-- rot-api:YouTubePublisher -->
### `YouTubePublisher(token, *, chunk_size=8388608)`

`token` is a string or `TokenProvider`; chunks must be positive multiples of 256 KiB. Supports
`accepts`, `preflight`, and consent-gated `publish` with the common Publisher parameters.

<!-- rot-api:InstagramPublisher -->
### `InstagramPublisher(token, user_id, *, api_version="v25.0")`

Publishes to an Instagram professional account. Supports `accepts`, account-aware `preflight`,
and consent-gated `publish` with the common Publisher parameters.

<!-- rot-api:TikTokPublisher -->
### `TikTokPublisher(token)`

Checks creator privacy and interaction settings during `preflight`, then performs chunked,
consent-gated `publish` with the common Publisher parameters.

<!-- rot-api:publish_all -->
### `publish_all(video, jobs, *, consent, progress=True, wait_timeout=900, poll_interval=2, on_preflight=None)`

Preflight all jobs, optionally ask `on_preflight(tuple[PublishPreflight, ...])` for confirmation,
then publish valid jobs in order while retaining partial successes.

## Exceptions

All exceptions inherit `RotError` and accept the normal exception message unless noted.

<!-- rot-api:RotError -->
- `RotError`: base library exception.
<!-- rot-api:ConfigurationError -->
- `ConfigurationError`: invalid project, model, option, or media configuration.
<!-- rot-api:ScriptError -->
- `ScriptError`: deterministic `.rot` parsing failure.
<!-- rot-api:DependencyError -->
- `DependencyError`: missing executable, codec, filter, or optional Python dependency.
<!-- rot-api:ProbeError -->
- `ProbeError`: missing or unreadable media metadata.
<!-- rot-api:RenderError -->
- `RenderError`: FFmpeg or output-contract failure.
<!-- rot-api:VoiceError -->
- `VoiceError`: speech generation failure.
<!-- rot-api:AlignmentError -->
- `AlignmentError`: known-transcript alignment failure.
<!-- rot-api:TranscriptionError -->
- `TranscriptionError`: clip speech-to-text or prepared-audio failure.
<!-- rot-api:ParserError -->
- `ParserError`: remote or custom script parsing failure.
<!-- rot-api:DownloadError -->
- `DownloadError`: remote media download failure.
<!-- rot-api:ClipAnalysisError -->
- `ClipAnalysisError`: clip scoring or export failure.
<!-- rot-api:PublishError -->
- `PublishError`: platform request or processing failure.
<!-- rot-api:PublishTimeoutError -->
- `PublishTimeoutError(message, *, platform, remote_id)`: timed-out remote operation that retains
  its platform and resumable remote identifier.
