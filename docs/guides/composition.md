---
layout: default
title: Composition
parent: Guides
nav_order: 1
---

# Composition

Backgrounds accept videos or still images and use `cover` fitting by default. Add clips in the
order they should play, then choose `cut`, `fade`, `crossfade`, `slide-left`, `slide-right`, or
`zoom` transitions.

```python
project = (
    Project.short_form()
    .background("one.mp4", trim=(2, 8), loop=False)
    .transition("crossfade", duration=0.25)
    .add_clip("two.mp4", trim=(4, 12), keep_audio=True, volume=0.25)
    .effect("saturation", amount=1.3)
    .soundtrack(
        "music.mp3",
        volume=0.12,
        trim=(10, 40),
        fade_in=0.4,
        fade_out=0.8,
        ducking=True,
    )
)
```

## Use still images

A single still background may infer its duration from dialogue. Set `duration` when there is no
dialogue and for every still in a multi-clip timeline. Stills support the same fitting, fill,
anchor, effect, and transition options as video, but cannot be trimmed or speed-adjusted.

```python
project = (
    Project.short_form()
    .background("hook.png", duration=1.2, fit="contain", fill="blur")
    .transition("zoom", duration=0.2)
    .add_clip("payoff.mp4", trim=(18, 27), loop=False)
)
```

## Fit horizontal footage

Use `fit="custom"` to preserve more of a horizontal source while still producing a vertical frame.
`fit_amount=0.0` is equivalent to `contain`; `fit_amount=1.0` is equivalent to `cover`.

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

Intermediate values enlarge the clip without distortion, crop overflow according to `anchor`, and
pad the remaining canvas. `fill="black"` makes solid letterboxing; `fill="blur"` puts a blurred,
full-canvas copy of the clip behind the sharp foreground.

### Extract an embedded facecam

Use `Facecam` when streamer footage already contains a webcam that should occupy part of the
vertical layout instead of relying on blur. Source and destination rectangles use normalized 0–1
coordinates, and the crop cover-fills its destination without distortion.

```python
from rot import Facecam, NormalizedRect

project.add_clip(
    "stream.mp4",
    fit="custom",
    fit_amount=0.35,
    anchor="top",
    facecam=Facecam(
        crop=NormalizedRect(0.02, 0.04, 0.24, 0.32),
        destination=NormalizedRect(0.1, 0.7, 0.8, 0.25),
    ),
)
```

Facecam extraction requires a video and `fit="custom"`. It uses the same trim, looping, and speed
as its clip. The default black fill replaces blur; `fill="blur"` keeps blur behind the composition.

### Choose the exact framing point

`focus=(x, y)` chooses the normalized source point retained when `cover` or `custom` fitting crops
overflow. `position=Placement(...)` independently places a `contain` or `custom` foreground on the
output canvas:

```python
from rot import Placement

project.background(
    "gameplay.mp4",
    fit="custom",
    fit_amount=0.35,
    focus=(0.72, 0.4),
    position=Placement(0.5, 0.08, anchor="top"),
)
```

Coordinates range from 0 through 1 and clamp to valid crop/placement bounds. When either option is
omitted, the corresponding crop or placement continues to use `anchor`.

## Time-bound overlays

Use `at` and `duration` for absolute timing, `during="line-id"` for dialogue, `speaker="alex"`
for a speaker’s utterances, and `during_clip` for a complete clip. An image with `at` but no
duration uses a two-second default; a text overlay without duration remains until video end.

```python
project = (
    Project.short_form()
    .background("number-5.mp4", clip_id="rank-5", keep_audio=True, loop=False)
    .add_clip("number-4.mp4", clip_id="rank-4", keep_audio=True, loop=False)
    .overlay_text("#5 — Huge comeback", during_clip="rank-5", position="top")
    .overlay_text("#4 — Impossible save", during_clip="rank-4", position="top")
)
```

`during_clip` accepts either a stable clip ID or a zero-based clip index. During a transition, the
outgoing title changes to the incoming title at the transition midpoint. Registered speaker
portraits automatically follow that speaker’s lines.

Static overlays accept PNG, JPEG, and any other static format decoded by the installed FFmpeg;
transparent formats preserve alpha. `width` controls scale while keeping the aspect ratio,
`opacity` ranges from 0 through 1, and animations are `none`, `pop`, `fade`, `slide`, or `bounce`.

For exact placement, pass `Placement(x, y, anchor=...)` instead of a named position. Coordinates
are normalized against the output canvas. Image overlays, speaker portraits, text overlays, and
caption themes all accept this placement model while existing named anchors retain their margins.

## Mix background music

`soundtrack` creates one music bed beginning at video time zero. `trim=(start, end)` selects the
source segment, `loop=True` repeats only that segment, and `loop=False` leaves silence after one
play. `fade_in` and `fade_out` are seconds on the audible bed. Set `ducking=True` to smoothly
sidechain-compress music under prerecorded or synthesized dialogue. Calling `soundtrack` again
replaces the earlier bed; music never lengthens or shortens the video.

Effects include zoom, punch zoom, pan, shake, blur, grayscale, and saturation. See the
[ranked countdown recipe](../examples.md#ranked-countdown) for a complete composition.
