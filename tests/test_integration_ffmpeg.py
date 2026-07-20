from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rot import ClipDetectionSettings, Project, RenderSettings, VideoClipFinder
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
            "color=c=blue:s=320x180:r=30:d=2",
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

    output = finder.export(source, candidates[:1], tmp_path / "clips")[0]
    output_info = probe(output)
    assert output_info.duration == pytest.approx(2, abs=0.08)
    assert output_info.video_codec == "h264"
    assert output_info.audio_codec == "aac"
    assert output_info.sample_rate == 48_000
    assert output_info.channels == 2


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
