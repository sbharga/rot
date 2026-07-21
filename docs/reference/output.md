---
layout: default
title: Output contract
parent: Reference
nav_order: 3
---

# Output contract

`Project.short_form()` is tuned for Instagram Reels, YouTube Shorts, and TikTok. By default it
creates a vertical MP4 with the following properties:

| Property | Default |
| --- | --- |
| Frame size | 1080×1920 |
| Video codec | H.264 (`libx264`) |
| Video rate | 10 Mbps target, 8 Mbps minimum, 12 Mbps ceiling |
| Frame rate | Constant 30 fps |
| Pixel format | yuv420p |
| Audio codec | AAC, 48 kHz stereo |
| Color | SDR Rec.709 |

Use `RenderSettings` when a project needs to adjust rendering behavior, such as disabling captions,
normalizing audio, or requesting an SRT caption sidecar. The final encode is always performed by
FFmpeg; `rot` orchestrates timelines and validated filter graphs rather than processing frames in
Python.
