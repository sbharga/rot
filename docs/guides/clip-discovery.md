---
layout: default
title: Clip discovery
parent: Guides
nav_order: 3
---

# Clip discovery

`rot clips TARGET` finds the strongest short-form windows in a source. `TARGET` can be a YouTube
URL, a local video file, or a folder of existing footage.

## A YouTube video

Install the optional YouTube integration, then download a permitted video and export ranked MP4
segments:

```console
uv sync --extra youtube
uv run rot clips "https://www.youtube.com/watch?v=VIDEO_ID" \
  --method hybrid --duration 30 --count 5 -o clips
```

## A local file

```console
uv run rot clips ./recording.mp4 --duration 20 --count 4 -o clips
```

## A folder of footage

Point `rot` at a library and it ranks windows across every video it finds, recursing by default
and reporting anything it could not read instead of aborting the scan:

```console
uv run rot clips ./gameplay-archive --duration 15 --count 8 -o clips
```

Use `--no-recursive` to stay in the top directory, and `--max-per-source` to stop one strong video
from taking every slot.

```python
from rot import ClipDetectionSettings, FolderClipFinder

finder = FolderClipFinder(
    ClipDetectionSettings(clip_duration=15, clip_count=8, max_per_source=2)
)
result = finder.find("./gameplay-archive", "build/clips")

for candidate in result.candidates:
    print(candidate.source.name, candidate.start, candidate.score)
for skipped in result.skipped:
    print("skipped", skipped.path, skipped.reason)
```

Analysis results are cached on disk, keyed by the file's path, modification time, size, and the
settings that affect decoding. Re-running a search with a different `--count`, `--duration`, or
blend weight re-ranks the cached signals without decoding the video again. Pass `--no-cache` to
force a fresh decode. Cache invalidation uses modification time and size rather than file
contents, so an edit that preserves both will not be noticed.

## Choosing a method

`hybrid` blends visual scene-change strength, frame-to-frame motion, and short-window RMS audio
energy, then rejects heavily overlapping candidates.

| Method | Use it for |
| --- | --- |
| `hybrid` | The default. Mixed content where any of the three signals may carry the moment. |
| `scene` | Edited montages and silent footage, where hard cuts are the signal. |
| `motion` | Gameplay and action footage that moves constantly without cutting. |
| `audio` | Podcasts, interviews, and reactions, where energetic speech matters more than cuts. |

Pass `--download-only` to keep `source.mp4` and inspect the suggested ranges without exporting; it
applies only to YouTube targets.

```python
from rot import ClipDetectionSettings, Project, YouTubeClipFinder

finder = YouTubeClipFinder(
    ClipDetectionSettings(method="hybrid", clip_duration=25, clip_count=3)
)
result = finder.find("https://youtu.be/VIDEO_ID", "build/youtube-clips")
project = Project.short_form().background(result.project_clips()[0])
```

All three signals are extracted in a single FFmpeg pass, with progress reported as it decodes.

## How ranking works

Each candidate window `[a, b)` of duration `D` gets one score per signal, and the signals are
blended. Every constant below is a `ClipDetectionSettings` field you can tune.

**Scene** — cut density passed through a saturating curve:

```
density     = (sum of cut strengths in the window) / D
scene_score = density / (density + scene_half_saturation)
```

Dividing by `D` makes the score independent of window length, so a 40-second window no longer
outranks a 20-second one purely by containing more cuts. The curve is strictly increasing and
approaches 1 without reaching it, so busy windows stay *ordered* rather than all clamping to the
same value. `scene_half_saturation` defaults to `0.25`, which scores 0.5 at one full-strength cut
every four seconds — short-form pacing typically runs between one cut every two seconds (0.67) and
every eight (0.33), placing the interesting range across the middle of the curve.

**Motion** — the mean of `signalstats` YDIF, normalized against `motion_reference` (default `12.0`)
and clamped to `[0, 1]`. A static talking head sits around YDIF 1–3, handheld and gameplay footage
around 8–15, and whip pans above 20. The mean is used rather than the peak because a hard cut
spikes YDIF, and taking the peak would make this signal largely redundant with the scene score.
YDIF is measured at a fixed `motion_fps` (default `15.0`) so the value means the same thing on a
24 fps and a 60 fps source.

**Audio** — RMS level normalized between `audio_floor_db` and `audio_ceiling_db`, then combined:

```
audio_score = audio_mean_weight * mean + audio_peak_weight * peak   (weights normalized)
```

Defaults are `0.70` and `0.30`. The mean term rewards sustained energy, so continuous speech beats
one shout in dead air; the peak term keeps a punchline or impact from being averaged away.

**Blending** — `scene_weight` (0.35), `motion_weight` (0.20), and `audio_weight` (0.45) are
renormalized over the signals actually present, so a video with no audio track falls back to
visual-only scoring with rescaled weights instead of silently scoring low. Motion carries the
smallest share because YDIF is the noisiest of the three — grain, compression artifacts, and camera
shake all inflate it — so it breaks ties and rescues action footage without driving the ranking.

**Boundary quality** — a window is scored down by up to `boundary_penalty` (default `0.15`) in
proportion to how loud the audio is at its two cut points, measured over `edge_probe` seconds. The
penalty is deliberately small: it breaks ties between comparable windows rather than promoting a
dull one. Separately, selected clips snap onto a nearby scene cut or audio trough within
`snap_window` seconds so they begin cleanly, preserving clip duration. Set `snap=False` (or
`--no-snap`) to keep the raw ranked ranges.

## Results

`ClipSearchResult.candidates` holds ranked `ClipCandidate` objects. Each one carries the `source`
it came from, its time range, the blended `score`, and the `scene_score`, `motion_score`, and
`audio_score` that produced it. `as_clip()` turns a candidate into a trim-aware `Clip` without
re-encoding, and `project_clips()` does the same for the whole result.

When fewer clips are found than requested — a short source, or a `max_overlap_ratio` too strict to
fit that many distinct windows — the shortfall is reported in `ClipSearchResult.warnings` and
through the `rot` logger rather than passing silently.

Exports are named after their source and time range, so re-running a search produces the same
filenames and will not clobber unrelated clips that happen to share a stem. Use
`--overwrite-exports` to replace them, `--overwrite-downloads` to re-fetch `source.mp4`, or `-f` for
both.

Exports preserve source dimensions but are accurately cut and encoded as H.264, AAC 48 kHz stereo
MP4s. A later `Project` render applies the vertical output contract. Only download and reuse media
you have permission to process. yt-dlp handles availability, age gates, regional restrictions, and
authentication, which can still prevent a download.
