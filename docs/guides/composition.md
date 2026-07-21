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

## Mix background music

`soundtrack` creates one music bed beginning at video time zero. `trim=(start, end)` selects the
source segment, `loop=True` repeats only that segment, and `loop=False` leaves silence after one
play. `fade_in` and `fade_out` are seconds on the audible bed. Set `ducking=True` to smoothly
sidechain-compress music under prerecorded or synthesized dialogue. Calling `soundtrack` again
replaces the earlier bed; music never lengthens or shortens the video.

Effects include zoom, punch zoom, pan, shake, blur, grayscale, and saturation. See the
[ranked countdown recipe](../examples.md#ranked-countdown) for a complete composition.
