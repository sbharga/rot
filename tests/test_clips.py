from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from rot import (
    ClipAnalysisError,
    ClipCandidate,
    ClipDetectionSettings,
    ConfigurationError,
    MediaInfo,
    VideoClipFinder,
    YouTubeClipFinder,
)
from rot.clips import _parse_signal_output


def test_clip_detection_settings_validate_ranges() -> None:
    with pytest.raises(ConfigurationError, match="method"):
        ClipDetectionSettings(method="magic")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="audio_floor_db"):
        ClipDetectionSettings(audio_floor_db=-10, audio_ceiling_db=-20)
    with pytest.raises(ConfigurationError, match="max_overlap_ratio"):
        ClipDetectionSettings(max_overlap_ratio=1)


def test_signal_metadata_parser_pairs_timestamps_and_values() -> None:
    output = """
[Parsed_metadata] frame:0 pts:100 pts_time:1.25
[Parsed_metadata] lavfi.scene_score=0.42
[Parsed_metadata] frame:1 pts:200 pts_time:2.5
[Parsed_metadata] lavfi.scene_score=0.75
"""
    import re

    assert _parse_signal_output(output, re.compile(r"scene_score=([-+0-9.eE]+)")) == (
        (1.25, 0.42),
        (2.5, 0.75),
    )


def test_hybrid_analysis_ranks_energy_and_scene_activity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(
        "rot.clips.probe",
        lambda path: MediaInfo(path, 40, 1920, 1080, True, True, sample_rate=48_000),
    )
    finder = VideoClipFinder(
        ClipDetectionSettings(clip_duration=10, clip_count=2, analysis_interval=1)
    )
    monkeypatch.setattr(
        finder,
        "_scene_events",
        lambda path: ((12.0, 0.9), (14.0, 0.8), (32.0, 0.4)),
    )
    monkeypatch.setattr(
        finder,
        "_audio_samples",
        lambda path, rate: tuple(
            (float(second), -14.0 if 10 <= second < 20 else -45.0) for second in range(40)
        ),
    )
    candidates = finder.analyze(source)
    assert len(candidates) == 2
    assert candidates[0].start <= 12 < candidates[0].end
    assert candidates[0].score > candidates[1].score
    assert all(candidate.duration == pytest.approx(10) for candidate in candidates)
    assert candidates[0].as_clip(source).keep_audio


def test_audio_analysis_rejects_video_without_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "silent.mp4"
    source.touch()
    monkeypatch.setattr(
        "rot.clips.probe",
        lambda path: MediaInfo(path, 20, 1920, 1080, True, False),
    )
    with pytest.raises(ClipAnalysisError, match="requires an audio stream"):
        VideoClipFinder(ClipDetectionSettings(method="audio")).analyze(source)


def test_youtube_downloader_uses_mp4_template_and_rejects_other_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _Downloader:
        def __init__(self, options: dict[str, object]) -> None:
            captured.update(options)

        def __enter__(self) -> _Downloader:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, *, download: bool) -> dict[str, object]:
            captured["url"] = url
            captured["download"] = download
            template = str(captured["outtmpl"])
            Path(template.replace("%(ext)s", "mp4")).write_bytes(b"video")
            return {}

    module = ModuleType("yt_dlp")
    module.YoutubeDL = _Downloader  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    output = YouTubeClipFinder().download("https://youtu.be/abc123", tmp_path / "video.mp4")
    assert output.read_bytes() == b"video"
    assert captured["noplaylist"] is True
    assert captured["merge_output_format"] == "mp4"
    with pytest.raises(ConfigurationError, match="YouTube"):
        YouTubeClipFinder().download("https://example.com/video", tmp_path / "other.mp4")


def test_export_builds_accurate_h264_mp4_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        Path(command[-1]).write_bytes(b"clip")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("rot.clips.executable", lambda name: name)
    monkeypatch.setattr("rot.clips.subprocess.run", run)
    source = tmp_path / "source.mp4"
    source.touch()
    candidate = ClipCandidate(2, 7, 0.8, 0.7, 0.9)
    outputs = VideoClipFinder().export(source, [candidate], tmp_path / "exports")
    assert outputs[0].read_bytes() == b"clip"
    joined = " ".join(commands[0])
    assert "-ss 2" in joined
    assert "-t 5" in joined
    assert "-c:v libx264" in joined
    assert "-c:a aac" in joined
