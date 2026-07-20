"""Regression tests for transition-overlap arithmetic.

The overlap between two clips used to be computed independently in four places, and the
video xfade offset clamped against the *accumulated timeline* rather than the outgoing
clip's own duration. From the second transition onward that made the video track consume
more overlap than the audio cursor and the reported duration assumed, so audio drifted
against the picture. These tests pin every consumer to ``transition_overlap``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from rot import Clip, MediaInfo, Project, RenderSettings
from rot.ffmpeg import FFmpegCompiler, PreparedMedia
from rot.models import transition_overlap
from rot.probe import doctor, executable, probe
from rot.render import _clip_timeline_intervals

_XFADE_OFFSET = re.compile(r"xfade=transition=\w+:duration=[\d.]+:offset=([\d.]+)")

# The pathological case: a short clip between two long ones. `0.5 / 2` is below the
# requested 0.4s transition, so both of the middle clip's junctions must saturate at 0.25s.
DURATIONS = (10.0, 0.5, 10.0)
TRANSITION = 0.4


def _media(tmp_path: Path, durations: tuple[float, ...], transition: str) -> PreparedMedia:
    clips: list[tuple[Clip, MediaInfo, float]] = []
    for index, duration in enumerate(durations):
        path = tmp_path / f"clip{index}.mp4"
        last = index == len(durations) - 1
        clip = Clip(
            path,
            duration=duration,
            transition="cut" if last else transition,
            transition_duration=TRANSITION,
        )
        clips.append((clip, MediaInfo(path, duration, 1080, 1920, True, False), duration))
    total = sum(durations) - sum(
        transition_overlap(clip, clips[i][2], clips[i + 1][2])
        for i, (clip, _, _) in enumerate(clips[:-1])
    )
    return PreparedMedia(
        clips=clips, utterances=[], overlays=[], portraits=[], duration=total
    )


def test_overlap_is_zero_for_cuts(tmp_path: Path) -> None:
    clip = Clip(tmp_path / "a.mp4", duration=10, transition="cut")
    assert transition_overlap(clip, 10.0, 10.0) == 0.0


def test_overlap_saturates_at_half_the_shorter_clip(tmp_path: Path) -> None:
    clip = Clip(tmp_path / "a.mp4", duration=10, transition="fade", transition_duration=0.4)
    assert transition_overlap(clip, 10.0, 10.0) == pytest.approx(0.4)  # requested
    assert transition_overlap(clip, 0.5, 10.0) == pytest.approx(0.25)  # outgoing caps
    assert transition_overlap(clip, 10.0, 0.5) == pytest.approx(0.25)  # incoming caps


def test_transitions_never_consume_more_than_their_clip(tmp_path: Path) -> None:
    """The invariant the old xfade offset violated: overlap_in + overlap_out <= duration."""

    media = _media(tmp_path, DURATIONS, "fade")
    clips = media.clips
    overlaps = [
        transition_overlap(clip, clips[i][2], clips[i + 1][2])
        for i, (clip, _, _) in enumerate(clips[:-1])
    ]
    for position, (_, _, duration) in enumerate(clips):
        incoming = overlaps[position - 1] if position else 0.0
        outgoing = overlaps[position] if position < len(overlaps) else 0.0
        assert incoming + outgoing <= duration + 1e-9, (
            f"clip {position} ({duration}s) is consumed by "
            f"{incoming}s + {outgoing}s of transitions"
        )


def test_xfade_offsets_match_the_timeline_layout(monkeypatch, tmp_path: Path) -> None:
    """The xfade offset must equal where the incoming clip starts on the timeline.

    Before the fix the second offset was 9.85 (derived from ``timeline_duration / 2``)
    while every other consumer placed that clip at 10.0.
    """

    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    media = _media(tmp_path, DURATIONS, "fade")
    command = FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4")

    offsets = [float(value) for value in _XFADE_OFFSET.findall(" ".join(command))]
    assert offsets == pytest.approx([9.75, 10.0])

    # The same starts the label intervals derive, independently.
    starts = [start for start, _ in _clip_timeline_intervals(media.clips)]
    assert offsets[0] == pytest.approx(starts[1] - 0.25 / 2)
    assert offsets[1] == pytest.approx(starts[2] - 0.25 / 2)


def test_reported_duration_matches_the_visual_track(tmp_path: Path) -> None:
    media = _media(tmp_path, DURATIONS, "fade")
    assert media.duration == pytest.approx(20.0)
    assert _clip_timeline_intervals(media.clips)[-1][1] == pytest.approx(media.duration)


@pytest.mark.skipif(not doctor().healthy, reason="full FFmpeg toolchain unavailable")
def test_real_render_duration_matches_the_report(tmp_path: Path) -> None:
    """Smoke test: a real multi-transition render reports the duration it produced.

    Note this does NOT detect the offset desync on its own -- the compiler passes ``-t
    media.duration``, which clamps the container to the reported length even when the
    xfade offsets disagree with it. ``test_xfade_offsets_match_the_timeline_layout`` is
    what actually pins the bug; this test guards the surrounding end-to-end path.
    """

    ffmpeg = executable("ffmpeg")
    sources: list[Path] = []
    for index, duration in enumerate((2.0, 0.4, 2.0)):
        path = tmp_path / f"src{index}.mp4"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", f"testsrc2=s=320x240:r=30:d={duration}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
            ],
            check=True,
        )
        sources.append(path)

    project = Project.short_form()
    for index, path in enumerate(sources):
        project.add_clip(
            path,
            duration=(2.0, 0.4, 2.0)[index],
            loop=False,
            transition="cut" if index == 0 else "fade",
            transition_duration=0.4,
        )

    output = tmp_path / "out.mp4"
    result = project.render(output, progress=False, overwrite=True)

    # 2.0 + 0.4 + 2.0 minus two 0.2s overlaps (both saturated by the 0.4s middle clip).
    assert result.duration == pytest.approx(4.0)
    info = probe(output)
    assert info.duration == pytest.approx(result.duration, abs=1 / 30)
