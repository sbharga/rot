from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rot import (
    ClipDetectionSettings,
    FolderClipFinder,
    Project,
    RenderSettings,
    VideoClipFinder,
)
from rot.clips import _MOTION_RE, _SCENE_RE, SignalSeries, _parse_signal_output
from rot.probe import doctor, executable, probe

pytestmark = pytest.mark.skipif(not doctor().healthy, reason="full FFmpeg toolchain unavailable")


def test_real_vertical_render_contract(tmp_path: Path) -> None:
    ffmpeg = executable("ffmpeg")
    background = tmp_path / "background.mp4"
    speech = tmp_path / "speech.wav"
    portrait = tmp_path / "portrait.png"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:r=30:d=1.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(background),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=magenta:s=120x160:d=0.1",
            "-frames:v",
            "1",
            str(portrait),
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.8",
            "-ac",
            "2",
            str(speech),
        ],
        check=True,
    )
    project = (
        Project(
            settings=RenderSettings(
                width=1080,
                height=1920,
                fps=30,
                preset="ultrafast",
            )
        )
        .background(background, loop=True)
        .add_speaker("narrator", portrait=portrait, portrait_width=180)
        .script(
            f"@narrator [audio={speech}]: A real render test\n"
            f"@narrator [audio={speech}]: The portrait follows the speaker"
        )
        .captions("pop")
    )
    result = project.render(tmp_path / "result.mp4", progress=False)
    info = probe(result.output)
    assert (info.width, info.height) == (1080, 1920)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.pixel_format == "yuv420p"
    assert info.frame_rate == pytest.approx(30, abs=0.01)
    assert info.sample_rate == 48_000
    assert info.channels == 2
    assert (info.color_primaries, info.color_transfer, info.color_space) == (
        "bt709",
        "bt709",
        "bt709",
    )


def test_real_still_overlay_and_ducked_soundtrack(tmp_path: Path) -> None:
    ffmpeg = executable("ffmpeg")
    background = tmp_path / "background.png"
    overlay = tmp_path / "overlay.png"
    speech = tmp_path / "speech.wav"
    music = tmp_path / "music.wav"
    for output, color, size in (
        (background, "navy", "320x240"),
        (overlay, "yellow@0.75", "100x100"),
    ):
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s={size}:d=0.1",
                "-frames:v",
                "1",
                str(output),
            ],
            check=True,
        )
    for output, frequency, duration in (
        (speech, 440, 1.0),
        (music, 220, 0.8),
    ):
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
                "-ac",
                "2",
                str(output),
            ],
            check=True,
        )

    project = (
        Project(settings=RenderSettings(preset="ultrafast"))
        .background(background, clip_id="still")
        .add_speaker("narrator")
        .script(f"@narrator [audio={speech}]: Still images and music work together")
        .overlay_image(overlay, during_clip="still", width=220, animation="fade")
        .soundtrack(
            music,
            volume=0.12,
            trim=(0.1, 0.5),
            fade_in=0.1,
            fade_out=0.1,
            ducking=True,
        )
    )
    result = project.render(tmp_path / "still-music.mp4", progress=False)
    info = probe(result.output)
    assert result.duration == pytest.approx(1.0, abs=0.03)
    assert (info.width, info.height, info.video_codec, info.audio_codec) == (
        1080,
        1920,
        "h264",
        "aac",
    )
    joined = " ".join(result.command)
    assert "aloop=loop=-1" in joined
    assert "sidechaincompress=" in joined


def test_real_hybrid_clip_discovery_and_export(tmp_path: Path) -> None:
    ffmpeg = executable("ffmpeg")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:sample_rate=48000:duration=2,volume=0.03",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=700:sample_rate=48000:duration=2,volume=0.8",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x180:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=400:sample_rate=48000:duration=2,volume=0.03",
            "-filter_complex",
            (
                "[0:v][2:v][4:v]concat=n=3:v=1:a=0[v];"
                "[1:a][3:a][5:a]concat=n=3:v=0:a=1[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
    )
    finder = VideoClipFinder(
        ClipDetectionSettings(
            method="hybrid",
            clip_duration=2,
            clip_count=2,
            scene_threshold=0.2,
            analysis_interval=0.25,
            max_overlap_ratio=0.1,
        )
    )
    candidates = finder.analyze(source)
    assert candidates[0].start <= 2.5 < candidates[0].end
    assert candidates[0].audio_score > candidates[1].audio_score
    # The middle segment is testsrc2, so the winning window carries real frame-to-frame motion.
    assert candidates[0].motion_score > 0
    assert candidates[0].source == source

    output = finder.export(candidates[:1], tmp_path / "clips")[0]
    output_info = probe(output)
    assert output_info.duration == pytest.approx(2, abs=0.08)
    assert output_info.video_codec == "h264"
    assert output_info.audio_codec == "aac"
    assert output_info.sample_rate == 48_000
    assert output_info.channels == 2


def _make_clip(path: Path, *, video: str, audio: str | None = None) -> Path:
    command = [executable("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
    command += ["-f", "lavfi", "-i", video]
    if audio is not None:
        command += ["-f", "lavfi", "-i", audio]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio is not None:
        command += ["-c:a", "aac"]
    command.append(str(path))
    subprocess.run(command, check=True)
    return path


def test_real_single_pass_matches_separate_passes(tmp_path: Path) -> None:
    """The merged filter graph must produce the same signals as independent passes.

    The merged graph is the riskiest part of extraction: split, several metadata sinks, and a
    mapped null output behave differently across FFmpeg builds, and a silently empty signal
    file would yield plausible-looking but meaningless rankings.
    """
    ffmpeg = executable("ffmpeg")
    source = _make_clip(
        tmp_path / "source.mp4",
        video="testsrc2=s=320x180:r=30:d=4",
        audio="sine=frequency=440:sample_rate=48000:duration=4",
    )
    finder = VideoClipFinder(ClipDetectionSettings(method="hybrid"), cache=False)
    info = probe(source)
    merged = finder._decode_signals(source, info)

    # Independent, single-signal passes of the kind this replaced.
    scene_file = tmp_path / "scene.txt"
    motion_file = tmp_path / "motion.txt"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vf",
            f"scale=320:-2,select=gt(scene\\,{finder.settings.scene_threshold}),"
            f"metadata=print:key=lavfi.scene_score:file={scene_file}",
            "-an", "-f", "null", "-",
        ],
        check=True,
    )
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-vf",
            f"scale=320:-2,fps={finder.settings.motion_fps},signalstats,"
            f"metadata=print:key=lavfi.signalstats.YDIF:file={motion_file}",
            "-an", "-f", "null", "-",
        ],
        check=True,
    )
    separate_scene = SignalSeries.from_events(
        _parse_signal_output(scene_file.read_text(), _SCENE_RE)
    )
    separate_motion = SignalSeries.from_events(
        (time, min(1.0, max(0.0, value / finder.settings.motion_reference)))
        for time, value in _parse_signal_output(motion_file.read_text(), _MOTION_RE)
    )

    assert len(merged.motion) > 0
    assert len(merged.audio) > 0
    assert merged.scene.times == separate_scene.times
    assert merged.motion.times == separate_motion.times
    for merged_value, separate_value in zip(
        merged.motion.values, separate_motion.values, strict=True
    ):
        assert merged_value == pytest.approx(separate_value, abs=1e-6)


def test_real_folder_discovery_ranks_across_two_sources(tmp_path: Path) -> None:
    library = tmp_path / "library"
    (library / "nested").mkdir(parents=True)
    busy = _make_clip(
        library / "busy.mp4",
        video="testsrc2=s=320x180:r=30:d=6",
        audio="sine=frequency=440:sample_rate=48000:duration=6,volume=0.9",
    )
    calm = _make_clip(
        library / "nested" / "calm.mp4",
        video="color=c=gray:s=320x180:r=30:d=6",
        audio="sine=frequency=200:sample_rate=48000:duration=6,volume=0.01",
    )
    # A file that looks like a video but is not, plus an unrelated extension.
    (library / "broken.mp4").write_text("not a video")
    (library / "notes.txt").write_text("ignored")

    finder = FolderClipFinder(
        ClipDetectionSettings(clip_duration=2, clip_count=3, max_overlap_ratio=0.1),
        cache=False,
    )
    result = finder.find(library, tmp_path / "exports")

    assert set(result.sources) == {busy, calm}
    assert result.candidates[0].source == busy
    # The unreadable file is reported, not fatal; the .txt is filtered out before probing.
    assert [item.path for item in result.skipped] == [library / "broken.mp4"]
    assert all(path.exists() for path in result.exports)


def test_real_export_filename_is_deterministic(tmp_path: Path) -> None:
    source = _make_clip(tmp_path / "source.mp4", video="testsrc2=s=320x180:r=30:d=4")
    finder = VideoClipFinder(
        ClipDetectionSettings(clip_duration=2, clip_count=1, motion_weight=0), cache=False
    )
    candidates = finder.analyze(source)
    first = finder.export(candidates[:1], tmp_path / "exports")
    second = finder.export(candidates[:1], tmp_path / "exports", overwrite=True)
    assert first == second


def test_real_join_transition_overlay_and_effect(tmp_path: Path) -> None:
    ffmpeg = executable("ffmpeg")
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    overlay = tmp_path / "overlay.png"
    for output, color in ((first, "red"), (second, "green")):
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x240:r=30:d=0.8",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(output),
            ],
            check=True,
        )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=yellow@0.9:s=120x120:d=0.1",
            "-frames:v",
            "1",
            str(overlay),
        ],
        check=True,
    )
    settings = RenderSettings(preset="ultrafast", captions=False)
    project = (
        Project(settings=settings)
        .background(
            first,
            duration=0.7,
            loop=False,
            clip_id="red",
            fit="custom",
            fit_amount=0.4,
            fill="blur",
            fill_blur=24,
        )
        .transition("crossfade", duration=0.2)
        .add_clip(
            second,
            duration=0.7,
            loop=False,
            clip_id="green",
            fit="custom",
            fit_amount=0.4,
            fill="blur",
            fill_blur=24,
        )
        .overlay_text("#2 — RED", during_clip="red", position="top", font_size=52)
        .overlay_text("#1 — GREEN", during_clip="green", position="top", font_size=52)
        .overlay_image(overlay, at=0.2, duration=0.5, width=240, opacity=0.8)
        .overlay_image(
            overlay,
            at=0.1,
            duration=0.4,
            width=120,
            position="top-left",
            animation="fade",
        )
        .overlay_image(
            overlay,
            at=0.4,
            duration=0.5,
            width=120,
            position="top-right",
            animation="bounce",
        )
        .overlay_image(
            overlay,
            at=0.6,
            duration=0.4,
            width=120,
            position="bottom-left",
            animation="slide",
        )
        .effect("saturation", amount=1.2)
    )
    result = project.render(tmp_path / "composed.mp4", progress=False)
    assert result.duration == pytest.approx(1.2)
    assert probe(result.output).duration == pytest.approx(1.2, abs=0.05)


@pytest.mark.parametrize(
    ("effect", "options"),
    [
        ("zoom", {"amount": 1.04}),
        ("punch-zoom", {"amount": 1.08}),
        ("pan", {}),
        ("shake", {"strength": 4}),
        ("blur", {"radius": 2}),
        ("grayscale", {}),
        ("saturation", {"amount": 1.2}),
    ],
)
def test_real_builtin_effects(tmp_path: Path, effect: str, options: dict[str, float | int]) -> None:
    ffmpeg = executable("ffmpeg")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x240:r=30:d=0.35",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    project = (
        Project(settings=RenderSettings(preset="ultrafast", captions=False))
        .background(source, duration=0.3, loop=False)
        .effect(effect, **options)
    )
    result = project.render(tmp_path / f"{effect}.mp4", progress=False)
    assert probe(result.output).duration == pytest.approx(0.3, abs=0.05)


@pytest.mark.parametrize("transition", ["fade", "slide-left", "slide-right", "zoom"])
def test_real_builtin_transitions(tmp_path: Path, transition: str) -> None:
    ffmpeg = executable("ffmpeg")
    sources: list[Path] = []
    for index, color in enumerate(("blue", "orange")):
        source = tmp_path / f"source-{index}.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=320x240:r=30:d=0.4",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ],
            check=True,
        )
        sources.append(source)
    project = (
        Project(settings=RenderSettings(preset="ultrafast", captions=False))
        .background(sources[0], duration=0.35, loop=False)
        .add_clip(
            sources[1],
            duration=0.35,
            loop=False,
            transition=transition,
            transition_duration=0.1,
        )
    )
    result = project.render(tmp_path / f"{transition}.mp4", progress=False)
    assert probe(result.output).duration == pytest.approx(0.6, abs=0.05)
