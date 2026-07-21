from __future__ import annotations

import io
import random
import re
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
    DependencyError,
    DownloadError,
    MediaInfo,
    ProbeError,
    TwitchClipFinder,
    VideoClipFinder,
    YouTubeClipFinder,
    discover_videos,
)
from rot.clips import (
    ClipSearchResult,
    SignalCache,
    SignalSeries,
    SourceSignals,
    _escape_filter_value,
    _export_name,
    _overlap_ratio,
    _parse_signal_output,
    _twitch_clip_id,
)


def _candidate(source: Path, start: float, end: float) -> ClipCandidate:
    return ClipCandidate(
        source=source, start=start, end=end, score=0.0, scene_score=0.0, motion_score=0.0,
        audio_score=0.0,
    )


def test_clip_detection_settings_validate_ranges() -> None:
    with pytest.raises(ConfigurationError, match="method"):
        ClipDetectionSettings(method="magic")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="audio_floor_db"):
        ClipDetectionSettings(audio_floor_db=-10, audio_ceiling_db=-20)
    with pytest.raises(ConfigurationError, match="max_overlap_ratio"):
        ClipDetectionSettings(max_overlap_ratio=1)
    with pytest.raises(ConfigurationError, match="analysis_width"):
        ClipDetectionSettings(analysis_width=321)
    with pytest.raises(ConfigurationError, match="analysis_width"):
        ClipDetectionSettings(analysis_width=32)
    with pytest.raises(ConfigurationError, match="signal weights"):
        ClipDetectionSettings(motion_weight=-1)
    with pytest.raises(ConfigurationError, match="hybrid"):
        ClipDetectionSettings(scene_weight=0, motion_weight=0, audio_weight=0)
    with pytest.raises(ConfigurationError, match="scene_half_saturation"):
        ClipDetectionSettings(scene_half_saturation=0)
    with pytest.raises(ConfigurationError, match="boundary_penalty"):
        ClipDetectionSettings(boundary_penalty=1)
    with pytest.raises(ConfigurationError, match="max_per_source"):
        ClipDetectionSettings(max_per_source=0)


def test_signal_weights_resolve_from_method() -> None:
    settings = ClipDetectionSettings(method="scene")
    assert settings.signal_weights == {"scene": 0.35, "motion": 0.0, "audio": 0.0}
    assert ClipDetectionSettings(method="motion").signal_weights["motion"] == 0.20
    assert ClipDetectionSettings(method="audio").signal_weights["audio"] == 0.45
    hybrid = ClipDetectionSettings().signal_weights
    assert hybrid == {"scene": 0.35, "motion": 0.20, "audio": 0.45}


def test_signal_series_window_aggregates_match_naive_scan() -> None:
    rng = random.Random(1234)
    events = [(index * 0.5, rng.uniform(0, 1)) for index in range(500)]
    series = SignalSeries.from_events(events)

    windows = [(0.0, 0.0), (0.0, 250.0), (-5.0, 3.0), (240.0, 400.0), (1.0, 1.5), (400.0, 500.0)]
    windows += [
        tuple(sorted((rng.uniform(-5, 260), rng.uniform(-5, 260))))  # type: ignore[misc]
        for _ in range(50)
    ]
    for start, end in windows:
        inside = [value for time, value in events if start <= time < end]
        assert series.count_range(start, end) == len(inside)
        assert series.sum_range(start, end) == pytest.approx(sum(inside), abs=1e-9)
        expected_mean = sum(inside) / len(inside) if inside else 0.0
        assert series.mean_range(start, end) == pytest.approx(expected_mean, abs=1e-9)
        assert series.max_range(start, end) == pytest.approx(max(inside) if inside else 0.0)


def test_signal_series_collapses_duplicate_and_unsorted_timestamps() -> None:
    series = SignalSeries.from_events(
        [(3.0, 0.3), (1.0, 0.1), (3.0, 0.9), (2.0, float("-inf")), (4.0, float("nan"))]
    )
    assert series.times == (1.0, 3.0)
    # The later value for t=3.0 wins; non-finite samples are dropped entirely.
    assert series.values == (0.1, 0.9)
    assert len(SignalSeries.from_events([])) == 0
    assert SignalSeries.from_events([]).max_range(0, 10) == 0.0


def test_signal_series_local_minima_finds_troughs() -> None:
    series = SignalSeries.from_events(
        [(0.0, 0.9), (1.0, 0.1), (2.0, 0.8), (3.0, 0.05), (4.0, 0.7)]
    )
    assert series.local_minima(below=0.25) == (1.0, 3.0)
    assert series.local_minima(below=0.06) == (3.0,)


def test_scene_score_is_duration_normalized_and_does_not_saturate() -> None:
    finder = VideoClipFinder(ClipDetectionSettings(method="scene"))
    source = Path("source.mp4")

    def scene_at(times: list[float]) -> SourceSignals:
        return SourceSignals(scene=SignalSeries.from_events([(time, 1.0) for time in times]))

    four = finder._score_window(source, 0, 30, scene_at([1, 8, 15, 22]))
    eight = finder._score_window(source, 0, 30, scene_at([1, 4, 8, 11, 15, 18, 22, 26]))
    # The old formula clamped both of these to exactly 1.0.
    assert eight.scene_score > four.scene_score
    assert eight.scene_score < 1.0

    # Equal cut density over different window lengths must score equally.
    short = finder._score_window(source, 0, 15, scene_at([1, 5, 9, 13]))
    long = finder._score_window(source, 0, 30, scene_at([1, 5, 9, 13, 17, 21, 25, 29]))
    assert short.scene_score == pytest.approx(long.scene_score, abs=1e-6)


def test_motion_score_averages_normalized_ydif() -> None:
    finder = VideoClipFinder(ClipDetectionSettings(method="motion", motion_reference=10.0))
    source = Path("source.mp4")
    # _score_window consumes an already-normalized motion series.
    signals = SourceSignals(
        motion=SignalSeries.from_events([(0.0, 0.2), (1.0, 0.4), (2.0, 1.0), (3.0, 1.0)])
    )
    assert finder._score_window(source, 0, 4, signals).motion_score == pytest.approx(0.65)


def test_blend_renormalizes_weights_when_a_signal_is_missing() -> None:
    source = Path("source.mp4")
    scene = SignalSeries.from_events([(1.0, 1.0), (5.0, 1.0)])
    signals = SourceSignals(scene=scene)

    hybrid = VideoClipFinder(ClipDetectionSettings(method="hybrid"))
    scene_only = VideoClipFinder(ClipDetectionSettings(method="scene"))
    # With no motion or audio series present, hybrid must fall back to pure scene scoring
    # rather than silently scaling the score down by the absent weights.
    assert hybrid._score_window(source, 0, 10, signals).score == pytest.approx(
        scene_only._score_window(source, 0, 10, signals).score
    )


def test_boundary_penalty_prefers_quiet_edges() -> None:
    source = Path("source.mp4")
    settings = ClipDetectionSettings(method="audio", boundary_penalty=0.15, edge_probe=0.4)
    finder = VideoClipFinder(settings)

    # Identical interior energy; the second window begins and ends mid-shout.
    quiet_edges = SignalSeries.from_events(
        [(0.0, 0.0), (1.0, 0.8), (2.0, 0.8), (3.0, 0.8), (4.0, 0.0)]
    )
    loud_edges = SignalSeries.from_events(
        [(0.0, 0.8), (1.0, 0.8), (2.0, 0.8), (3.0, 0.8), (4.0, 0.8)]
    )
    quiet = finder._score_window(source, 0, 4, SourceSignals(audio=quiet_edges))
    loud = finder._score_window(source, 0, 4, SourceSignals(audio=loud_edges))
    assert quiet.score > loud.score * (1 - settings.boundary_penalty) or quiet.score > loud.score
    # The penalty can never exceed boundary_penalty of the unpenalized score.
    assert loud.score >= loud.audio_score * (1 - settings.boundary_penalty) - 1e-9


def test_snapping_shifts_start_to_nearest_boundary_and_preserves_duration() -> None:
    finder = VideoClipFinder(ClipDetectionSettings(snap_window=1.0))
    source = Path("source.mp4")
    signals = SourceSignals(scene=SignalSeries.from_events([(9.4, 1.0)]))

    snapped = finder._snap_to_boundaries((_candidate(source, 10.0, 20.0),), signals, 60.0)
    assert snapped[0].start == pytest.approx(9.4)
    assert snapped[0].duration == pytest.approx(10.0)

    # A boundary outside snap_window leaves the candidate alone.
    far = SourceSignals(scene=SignalSeries.from_events([(5.0, 1.0)]))
    assert finder._snap_to_boundaries((_candidate(source, 10.0, 20.0),), far, 60.0)[0].start == 10.0

    # Snapping that would run past the end of the source is skipped.
    late = SourceSignals(scene=SignalSeries.from_events([(50.6, 1.0)]))
    kept = finder._snap_to_boundaries((_candidate(source, 50.0, 60.0),), late, 60.0)
    assert kept[0].start == 50.0


def test_signal_metadata_parser_pairs_timestamps_and_values() -> None:
    output = """
[Parsed_metadata] frame:0 pts:100 pts_time:1.25
[Parsed_metadata] lavfi.scene_score=0.42
[Parsed_metadata] frame:1 pts:200 pts_time:2.5
[Parsed_metadata] lavfi.scene_score=0.75
"""
    assert _parse_signal_output(output, re.compile(r"scene_score=([-+0-9.eE]+)")) == (
        (1.25, 0.42),
        (2.5, 0.75),
    )


def test_signal_metadata_parser_keeps_multiple_values_per_timestamp() -> None:
    # One frame printing several matching keys must yield a pair for each, not just the first.
    output = """
[Parsed_metadata] frame:0 pts:100 pts_time:1.0
[Parsed_metadata] lavfi.scene_score=0.10
[Parsed_metadata] lavfi.scene_score=0.20
[Parsed_metadata] lavfi.scene_score=0.30
[Parsed_metadata] frame:1 pts:200 pts_time:2.0
[Parsed_metadata] lavfi.scene_score=0.40
"""
    assert _parse_signal_output(output, re.compile(r"scene_score=([-+0-9.eE]+)")) == (
        (1.0, 0.10),
        (1.0, 0.20),
        (1.0, 0.30),
        (2.0, 0.40),
    )


def test_overlap_ratio_handles_zero_length_and_cross_source() -> None:
    first = Path("a.mp4")
    second = Path("b.mp4")
    assert _overlap_ratio(_candidate(first, 0, 10), _candidate(second, 0, 10)) == 0.0
    assert _overlap_ratio(_candidate(first, 0, 10), _candidate(first, 5, 15)) == pytest.approx(0.5)
    # Degenerate zero-length candidates must not raise ZeroDivisionError.
    assert _overlap_ratio(_candidate(first, 5, 5), _candidate(first, 0, 10)) == 1.0
    assert _overlap_ratio(_candidate(first, 50, 50), _candidate(first, 0, 10)) == 0.0


def test_export_names_are_source_and_time_derived_and_stable(tmp_path: Path) -> None:
    first = _candidate(tmp_path / "one" / "clip.mp4", 2.0, 7.0)
    second = _candidate(tmp_path / "two" / "clip.mp4", 2.0, 7.0)
    # Same stem in different directories must not collide.
    assert _export_name(first) != _export_name(second)
    # The same candidate always names the same file, so re-running is idempotent.
    assert _export_name(first) == _export_name(first)
    assert _export_name(first).endswith("-00002000-00007000.mp4")
    assert _export_name(first).startswith("clip-")


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
        ClipDetectionSettings(
            clip_duration=10, clip_count=2, analysis_interval=1, motion_weight=0, snap=False
        ),
        cache=False,
    )
    signals = SourceSignals(
        scene=SignalSeries.from_events([(12.0, 0.9), (14.0, 0.8), (32.0, 0.4)]),
        audio=SignalSeries.from_events(
            (float(second), finder._normalize_db(-14.0 if 10 <= second < 20 else -45.0))
            for second in range(40)
        ),
    )
    monkeypatch.setattr(finder, "_decode_signals", lambda path, info, reporter=None: signals)
    candidates = finder.analyze(source)
    assert len(candidates) == 2
    assert candidates[0].start <= 12 < candidates[0].end
    assert candidates[0].score > candidates[1].score
    assert all(candidate.duration == pytest.approx(10) for candidate in candidates)
    assert candidates[0].as_clip().keep_audio
    assert candidates[0].source == source


def test_fewer_candidates_than_requested_emits_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    source = tmp_path / "short.mp4"
    source.touch()
    monkeypatch.setattr(
        "rot.clips.probe",
        lambda path: MediaInfo(path, 6, 1920, 1080, True, False),
    )
    finder = VideoClipFinder(
        ClipDetectionSettings(clip_duration=30, clip_count=5, motion_weight=0), cache=False
    )
    monkeypatch.setattr(
        finder, "_decode_signals", lambda path, info, reporter=None: SourceSignals()
    )
    with caplog.at_level("WARNING", logger="rot"):
        result = finder.find(source, tmp_path / "exports", export=False)
    assert len(result.candidates) == 1
    assert "1 of 5" in caplog.text
    assert result.warnings and "1 of 5" in result.warnings[0]


def test_single_source_result_exposes_source_and_multi_source_raises(tmp_path: Path) -> None:
    single = ClipSearchResult(candidates=(), sources=(tmp_path / "a.mp4",))
    assert single.source == tmp_path / "a.mp4"
    multi = ClipSearchResult(candidates=(), sources=(tmp_path / "a.mp4", tmp_path / "b.mp4"))
    with pytest.raises(ClipAnalysisError, match="2 sources"):
        _ = multi.source


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


def test_escape_filter_value_escapes_separators() -> None:
    assert _escape_filter_value("/tmp/a:b/scene.txt") == "/tmp/a\\:b/scene.txt"
    assert _escape_filter_value("a\\b") == "a\\\\b"
    for character in ":,;[]'":
        assert _escape_filter_value(f"x{character}y") == f"x\\{character}y"


def test_filter_graph_omits_audio_branch_without_audio_stream(tmp_path: Path) -> None:
    targets = {name: tmp_path / f"{name}.txt" for name in ("scene", "motion", "audio")}
    finder = VideoClipFinder(ClipDetectionSettings(method="hybrid"), cache=False)

    silent = MediaInfo(tmp_path / "s.mp4", 10, 1920, 1080, True, False)
    graph, output = finder._build_graph(silent, targets)
    # Referencing [0:a] on a source without an audio stream is a hard FFmpeg error.
    assert "[0:a]" not in graph
    assert output == "[probe]"
    assert "split=2" in graph
    assert "signalstats" in graph

    with_audio = MediaInfo(tmp_path / "a.mp4", 10, 1920, 1080, True, True, sample_rate=48_000)
    graph, output = finder._build_graph(with_audio, targets)
    assert "[0:a]" in graph
    # Exactly one branch is labelled for -map; the others must terminate in a sink.
    assert graph.count("[probe]") == 1
    assert "anullsink" in graph
    assert "nullsink" in graph

    # Audio-only detection maps the audio branch instead, since no video branch exists.
    audio_only = VideoClipFinder(ClipDetectionSettings(method="audio"), cache=False)
    graph, output = audio_only._build_graph(with_audio, targets)
    assert output == "[aprobe]"
    assert "[0:v]" not in graph


def test_discover_videos_filters_extensions_and_recursion(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.mp4").touch()
    (tmp_path / "UPPER.MP4").touch()
    (tmp_path / "notes.txt").touch()
    (tmp_path / ".hidden.mp4").touch()
    (tmp_path / "sub" / "nested.mkv").touch()

    found = discover_videos(tmp_path)
    assert found == tuple(
        sorted({tmp_path / "top.mp4", tmp_path / "UPPER.MP4", tmp_path / "sub" / "nested.mkv"})
    )
    assert discover_videos(tmp_path, recursive=False) == tuple(
        sorted({tmp_path / "top.mp4", tmp_path / "UPPER.MP4"})
    )
    assert discover_videos(tmp_path, extensions={".mkv"}) == (tmp_path / "sub" / "nested.mkv",)
    with pytest.raises(ConfigurationError, match="directory"):
        discover_videos(tmp_path / "top.mp4")


def test_analyze_many_ranks_across_sources_and_skips_unreadable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    good = tmp_path / "good.mp4"
    better = tmp_path / "better.mp4"
    unreadable = tmp_path / "unreadable.mp4"
    for path in (good, better, unreadable):
        path.touch()

    def fake_analyze(source: Path, *, reporter: object = None) -> tuple[ClipCandidate, ...]:
        if source == unreadable:
            raise ProbeError(f"Could not probe {source}")
        score = 0.9 if source == better else 0.4
        return (
            ClipCandidate(
                source=source, start=0, end=10, score=score, scene_score=score,
                motion_score=0.0, audio_score=score,
            ),
        )

    finder = VideoClipFinder(ClipDetectionSettings(clip_count=5), cache=False)
    monkeypatch.setattr(finder, "analyze", fake_analyze)
    result = finder.analyze_many([good, better, unreadable])

    assert [candidate.source for candidate in result.candidates] == [better, good]
    assert result.sources == (good, better)
    assert len(result.skipped) == 1
    assert result.skipped[0].path == unreadable
    assert "Could not probe" in result.skipped[0].reason
    assert any("Skipped 1" in warning for warning in result.warnings)
    # A multi-source result has no single source to report.
    with pytest.raises(ClipAnalysisError, match="2 sources"):
        _ = result.source


def test_analyze_many_raises_when_every_source_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "bad.mp4"
    source.touch()
    finder = VideoClipFinder(cache=False)

    def fail(path: Path, *, reporter: object = None) -> tuple[ClipCandidate, ...]:
        raise ClipAnalysisError("not a video")

    monkeypatch.setattr(finder, "analyze", fail)
    with pytest.raises(ClipAnalysisError, match="No source could be analyzed"):
        finder.analyze_many([source])


def test_analyze_many_dependency_error_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "a.mp4"
    source.touch()
    finder = VideoClipFinder(cache=False)

    def fail(path: Path, *, reporter: object = None) -> tuple[ClipCandidate, ...]:
        raise DependencyError("ffmpeg was not found on PATH")

    monkeypatch.setattr(finder, "analyze", fail)
    # A missing FFmpeg is an environment failure, not a bad file, and must not be reported
    # as a skipped source.
    with pytest.raises(DependencyError):
        finder.analyze_many([source])


def test_max_per_source_caps_results_from_one_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = tmp_path / "one.mp4"
    second = tmp_path / "two.mp4"
    for path in (first, second):
        path.touch()

    def fake_analyze(source: Path, *, reporter: object = None) -> tuple[ClipCandidate, ...]:
        return tuple(
            ClipCandidate(
                source=source, start=index * 20, end=index * 20 + 10, score=0.5,
                scene_score=0.5, motion_score=0.0, audio_score=0.5,
            )
            for index in range(4)
        )

    finder = VideoClipFinder(ClipDetectionSettings(clip_count=6, max_per_source=1), cache=False)
    monkeypatch.setattr(finder, "analyze", fake_analyze)
    result = finder.analyze_many([first, second])
    assert len(result.candidates) == 2
    assert {candidate.source for candidate in result.candidates} == {first, second}


def test_signal_cache_round_trips_and_reuses_decoded_signals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(
        "rot.clips.probe",
        lambda path: MediaInfo(path, 40, 1920, 1080, True, True, sample_rate=48_000),
    )
    decodes = 0
    signals = SourceSignals(scene=SignalSeries.from_events([(5.0, 0.5)]))

    def decode(path: Path, info: object, *, reporter: object = None) -> SourceSignals:
        nonlocal decodes
        decodes += 1
        return signals

    cache = SignalCache(tmp_path / "cache")

    def build(**kwargs: object) -> VideoClipFinder:
        finder = VideoClipFinder(ClipDetectionSettings(**kwargs), cache=cache)  # type: ignore[arg-type]
        monkeypatch.setattr(finder, "_decode_signals", decode)
        return finder

    build(clip_count=2).analyze(source)
    assert decodes == 1
    build(clip_count=2).analyze(source)
    assert decodes == 1
    # Ranking-only settings must not invalidate the cached signals.
    build(clip_count=4, clip_duration=12).analyze(source)
    assert decodes == 1
    # A decode-affecting setting must.
    build(clip_count=2, analysis_interval=0.25).analyze(source)
    assert decodes == 2


def test_signal_cache_failure_degrades_to_decoding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(
        "rot.clips.probe",
        lambda path: MediaInfo(path, 40, 1920, 1080, True, False),
    )
    cache = SignalCache(tmp_path / "cache")
    finder = VideoClipFinder(ClipDetectionSettings(clip_count=1), cache=cache)
    monkeypatch.setattr(
        finder, "_decode_signals", lambda path, info, reporter=None: SourceSignals()
    )
    key = cache.key(source, finder.settings)
    cache.root.mkdir(parents=True)
    (cache.root / f"{key}.json").write_text("{ not json")
    # A corrupt entry must be treated as a miss rather than failing the analysis.
    assert finder.analyze(source)
    assert cache.load("missing-key") is None


def test_signal_cache_can_be_disabled(tmp_path: Path) -> None:
    disabled = SignalCache(tmp_path / "cache", enabled=False)
    disabled.store("key", SourceSignals())
    assert disabled.load("key") is None
    assert not (tmp_path / "cache").exists()


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


def test_twitch_clip_ids_are_parsed_from_supported_urls() -> None:
    clip_id = "AwkwardHelplessSalamander-SwiftRage_1"
    assert _twitch_clip_id(clip_id) == clip_id
    assert _twitch_clip_id(f"https://clips.twitch.tv/{clip_id}") == clip_id
    assert _twitch_clip_id(f"https://www.twitch.tv/twitchdev/clip/{clip_id}") == clip_id
    assert _twitch_clip_id(f"https://clips.twitch.tv/embed?clip={clip_id}") == clip_id

    for invalid in (
        "https://example.com/clip/id",
        "https://www.twitch.tv/twitchdev",
        "https://clips.twitch.tv/one/two",
        "bad/id",
    ):
        with pytest.raises(ConfigurationError, match="Twitch"):
            _twitch_clip_id(invalid)


def test_twitch_downloader_uses_official_api_and_writes_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []
    media_url = "https://production.assets.clips.twitchcdn.net/signed"

    class _Response:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

    class _StreamResponse(_Response):
        def __enter__(self) -> _StreamResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def iter_bytes(self) -> tuple[bytes, ...]:
            return (b"official-", b"clip")

    class _HTTPError(Exception):
        pass

    class _HTTPX:
        HTTPError = _HTTPError

        @staticmethod
        def get(url: str, **kwargs: object) -> _Response:
            calls.append(("get", url, kwargs))
            if url.endswith("/validate"):
                return _Response(
                    200,
                    {
                        "client_id": "client-id",
                        "user_id": "editor-id",
                        "scopes": ["editor:manage:clips"],
                    },
                )
            if url.endswith("/clips"):
                return _Response(200, {"data": [{"broadcaster_id": "broadcaster-id"}]})
            return _Response(
                200,
                {
                    "data": [
                        {
                            "clip_id": "ClipSlug",
                            "landscape_download_url": media_url,
                            "portrait_download_url": None,
                        }
                    ]
                },
            )

        @staticmethod
        def stream(method: str, url: str, **kwargs: object) -> _StreamResponse:
            calls.append((method.lower(), url, kwargs))
            return _StreamResponse(200, {})

    monkeypatch.setattr("rot.clips._twitch_httpx", lambda: _HTTPX)
    destination = tmp_path / "nested" / "source.mp4"
    finder = TwitchClipFinder(client_id="client-id", access_token="secret-token")
    output = finder.download("https://clips.twitch.tv/ClipSlug", destination)

    assert output.read_bytes() == b"official-clip"
    assert not list(destination.parent.glob(".source-*.mp4"))
    validate_headers = calls[0][2]["headers"]
    assert validate_headers == {"Authorization": "Bearer secret-token"}
    assert calls[1][2]["params"] == {"id": "ClipSlug"}
    assert calls[2][2]["params"] == {
        "broadcaster_id": "broadcaster-id",
        "editor_id": "editor-id",
        "clip_id": "ClipSlug",
    }
    assert calls[3][1] == media_url


def test_twitch_downloader_validates_credentials_scope_and_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigurationError, match="client_id"):
        TwitchClipFinder(client_id=" ", access_token="token")
    with pytest.raises(ConfigurationError, match="access_token"):
        TwitchClipFinder(client_id="client", access_token=" ")

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"client_id": "client", "user_id": "editor", "scopes": []}

    class _HTTPError(Exception):
        pass

    class _HTTPX:
        HTTPError = _HTTPError

        @staticmethod
        def get(*args: object, **kwargs: object) -> _Response:
            return _Response()

    monkeypatch.setattr("rot.clips._twitch_httpx", lambda: _HTTPX)
    finder = TwitchClipFinder(client_id="client", access_token="token")
    with pytest.raises(DownloadError, match="manage:clips"):
        finder.download("ClipSlug", tmp_path / "source.mp4")
    with pytest.raises(ConfigurationError, match="variant"):
        finder.download(
            "ClipSlug", tmp_path / "source.mp4", variant="square"  # type: ignore[arg-type]
        )


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
    candidate = ClipCandidate(
        source=source, start=2, end=7, score=0.8, scene_score=0.7, motion_score=0.5,
        audio_score=0.9,
    )
    outputs = VideoClipFinder().export([candidate], tmp_path / "exports")
    assert outputs[0].read_bytes() == b"clip"
    joined = " ".join(commands[0])
    assert "-ss 2" in joined
    assert "-t 5" in joined
    assert "-c:v libx264" in joined
    assert "-c:a aac" in joined
    # A second export of the same candidate targets the same path and refuses to clobber it.
    with pytest.raises(ClipAnalysisError, match="already exists"):
        VideoClipFinder().export([candidate], tmp_path / "exports")


def test_extraction_error_names_the_source_and_includes_stderr_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Failed:
        stdout = io.StringIO("")

        def wait(self) -> int:
            return 1

    def popen(command: list[str], **kwargs: object) -> _Failed:
        stderr = kwargs["stderr"]
        assert hasattr(stderr, "write")
        stderr.write("\n".join(f"line {index}" for index in range(40)))  # type: ignore[union-attr]
        return _Failed()

    monkeypatch.setattr("rot.clips.subprocess.Popen", popen)
    source = tmp_path / "broken.mp4"
    source.touch()
    with pytest.raises(ClipAnalysisError) as excinfo:
        VideoClipFinder()._run_extraction(["ffmpeg"], source=source, total_duration=10.0)
    message = str(excinfo.value)
    assert "broken.mp4" in message
    assert "line 39" in message
    # The tail keeps context rather than only the final line.
    assert "line 25" in message
    assert "line 5" not in message
