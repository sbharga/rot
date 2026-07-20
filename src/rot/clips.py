"""YouTube ingestion and signal-based clip discovery."""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from .errors import ClipAnalysisError, ConfigurationError, DependencyError, DownloadError
from .models import Clip
from .probe import executable, probe

ClipDetectionMethod = Literal["scene", "audio", "hybrid"]

_PTS_RE = re.compile(r"pts_time:([-+0-9.eE]+)")
_SCENE_RE = re.compile(r"lavfi\.scene_score=([-+0-9.eE]+)")
_AUDIO_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=([-+0-9.eE]+|-inf)")
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".avi"}


@dataclass(frozen=True, slots=True)
class ClipDetectionSettings:
    """Controls how interesting short-form windows are selected from a source video."""

    method: ClipDetectionMethod = "hybrid"
    clip_duration: float = 30.0
    clip_count: int = 5
    scene_threshold: float = 0.30
    analysis_interval: float = 0.5
    audio_floor_db: float = -50.0
    audio_ceiling_db: float = -12.0
    max_overlap_ratio: float = 0.20

    def __post_init__(self) -> None:
        if self.method not in {"scene", "audio", "hybrid"}:
            raise ConfigurationError(f"Unknown clip detection method {self.method!r}")
        if self.clip_duration <= 0:
            raise ConfigurationError("clip_duration must be positive")
        if self.clip_count <= 0:
            raise ConfigurationError("clip_count must be positive")
        if not 0 < self.scene_threshold <= 1:
            raise ConfigurationError("scene_threshold must be greater than 0 and at most 1")
        if self.analysis_interval <= 0:
            raise ConfigurationError("analysis_interval must be positive")
        if self.audio_floor_db >= self.audio_ceiling_db:
            raise ConfigurationError("audio_floor_db must be lower than audio_ceiling_db")
        if not 0 <= self.max_overlap_ratio < 1:
            raise ConfigurationError("max_overlap_ratio must be at least 0 and less than 1")


@dataclass(frozen=True, slots=True)
class ClipCandidate:
    """A ranked source interval that can be exported or added directly to a Project."""

    start: float
    end: float
    score: float
    scene_score: float
    audio_score: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_clip(self, source: str | Path, *, keep_audio: bool = True) -> Clip:
        return Clip(
            source,
            trim_start=self.start,
            trim_end=self.end,
            loop=False,
            keep_audio=keep_audio,
        )


@dataclass(frozen=True, slots=True)
class ClipSearchResult:
    source: Path
    candidates: tuple[ClipCandidate, ...]
    exports: tuple[Path, ...] = ()

    def project_clips(self, *, keep_audio: bool = True) -> tuple[Clip, ...]:
        return tuple(
            candidate.as_clip(self.source, keep_audio=keep_audio)
            for candidate in self.candidates
        )


class VideoClipFinder:
    """Find and export high-energy windows from a local video using FFmpeg signals."""

    def __init__(self, settings: ClipDetectionSettings | None = None) -> None:
        self.settings = settings or ClipDetectionSettings()

    def analyze(self, source: str | Path) -> tuple[ClipCandidate, ...]:
        path = Path(source).expanduser().resolve()
        info = probe(path)
        if not info.has_video or info.duration <= 0:
            raise ClipAnalysisError(f"Clip analysis requires a non-empty video: {path}")

        duration = info.duration
        window_duration = min(self.settings.clip_duration, duration)
        scene_events: tuple[tuple[float, float], ...] = ()
        audio_samples: tuple[tuple[float, float], ...] = ()
        if self.settings.method in {"scene", "hybrid"}:
            scene_events = self._scene_events(path)
        if self.settings.method in {"audio", "hybrid"} and info.has_audio:
            sample_rate = info.sample_rate or 48_000
            audio_samples = self._audio_samples(path, sample_rate)
        if self.settings.method == "audio" and not info.has_audio:
            raise ClipAnalysisError(f"Audio clip detection requires an audio stream: {path}")

        starts = self._candidate_starts(
            duration,
            window_duration,
            scene_events=scene_events,
        )
        candidates = [
            self._score_window(
                start,
                min(duration, start + window_duration),
                scene_events,
                audio_samples,
            )
            for start in starts
        ]
        candidates.sort(key=lambda item: (-item.score, item.start))
        return self._select_diverse(candidates)

    def export(
        self,
        source: str | Path,
        candidates: tuple[ClipCandidate, ...] | list[ClipCandidate],
        output_dir: str | Path,
        *,
        overwrite: bool = False,
    ) -> tuple[Path, ...]:
        path = Path(source).expanduser().resolve()
        directory = Path(output_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        for index, candidate in enumerate(candidates, start=1):
            output = directory / f"clip-{index:02d}.mp4"
            if output.exists() and not overwrite:
                raise ClipAnalysisError(f"Clip output already exists: {output}")
            self._export_one(path, candidate, output)
            outputs.append(output)
        return tuple(outputs)

    def _scene_events(self, source: Path) -> tuple[tuple[float, float], ...]:
        threshold = self.settings.scene_threshold
        filter_graph = (
            f"scale=320:-2,select=gt(scene\\,{threshold}),"
            "metadata=print:key=lavfi.scene_score"
        )
        command = [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(source),
            "-vf",
            filter_graph,
            "-an",
            "-f",
            "null",
            "-",
        ]
        return _parse_signal_output(self._run_ffmpeg(command), _SCENE_RE)

    def _audio_samples(self, source: Path, sample_rate: int) -> tuple[tuple[float, float], ...]:
        sample_count = max(1, round(sample_rate * self.settings.analysis_interval))
        filter_graph = (
            f"asetnsamples=n={sample_count}:p=1,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level"
        )
        command = [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "info",
            "-i",
            str(source),
            "-af",
            filter_graph,
            "-vn",
            "-f",
            "null",
            "-",
        ]
        return _parse_signal_output(self._run_ffmpeg(command), _AUDIO_RE)

    @staticmethod
    def _run_ffmpeg(command: list[str]) -> str:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            message = detail[-1] if detail else "unknown FFmpeg error"
            raise ClipAnalysisError(f"FFmpeg clip analysis failed: {message}")
        return completed.stderr

    def _candidate_starts(
        self,
        duration: float,
        window_duration: float,
        *,
        scene_events: tuple[tuple[float, float], ...],
    ) -> tuple[float, ...]:
        last_start = max(0.0, duration - window_duration)
        if last_start == 0:
            return (0.0,)
        step = min(5.0, max(self.settings.analysis_interval, window_duration / 5))
        starts = {0.0, last_start}
        cursor = 0.0
        while cursor < last_start:
            starts.add(min(cursor, last_start))
            cursor += step
        anchor_limit = max(100, self.settings.clip_count * 20)
        strongest_scenes = sorted(scene_events, key=lambda event: event[1], reverse=True)[
            :anchor_limit
        ]
        for timestamp, _ in strongest_scenes:
            starts.add(min(last_start, max(0.0, timestamp - window_duration / 2)))
        return tuple(sorted(starts))

    def _score_window(
        self,
        start: float,
        end: float,
        scene_events: tuple[tuple[float, float], ...],
        audio_samples: tuple[tuple[float, float], ...],
    ) -> ClipCandidate:
        scene_values = [score for timestamp, score in scene_events if start <= timestamp < end]
        scene_score = min(1.0, sum(scene_values) / 2.0)

        audio_values = [value for timestamp, value in audio_samples if start <= timestamp < end]
        normalized_audio = [self._normalize_db(value) for value in audio_values]
        audio_score = 0.0
        if normalized_audio:
            audio_score = 0.7 * (sum(normalized_audio) / len(normalized_audio)) + 0.3 * max(
                normalized_audio
            )

        if self.settings.method == "scene":
            score = scene_score
        elif self.settings.method == "audio":
            score = audio_score
        elif audio_samples:
            score = 0.45 * scene_score + 0.55 * audio_score
        else:
            score = scene_score
        return ClipCandidate(
            start=round(start, 3),
            end=round(end, 3),
            score=round(score, 6),
            scene_score=round(scene_score, 6),
            audio_score=round(audio_score, 6),
        )

    def _normalize_db(self, value: float) -> float:
        if not math.isfinite(value):
            return 0.0
        settings = self.settings
        normalized = (value - settings.audio_floor_db) / (
            settings.audio_ceiling_db - settings.audio_floor_db
        )
        return min(1.0, max(0.0, normalized))

    def _select_diverse(self, candidates: list[ClipCandidate]) -> tuple[ClipCandidate, ...]:
        selected: list[ClipCandidate] = []
        for candidate in candidates:
            if all(
                _overlap_ratio(candidate, existing) <= self.settings.max_overlap_ratio
                for existing in selected
            ):
                selected.append(candidate)
                if len(selected) == self.settings.clip_count:
                    break
        return tuple(selected)

    @staticmethod
    def _export_one(source: Path, candidate: ClipCandidate, output: Path) -> None:
        temporary = output.with_name(f".{output.stem}-{uuid.uuid4().hex}.mp4")
        command = [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(candidate.start),
            "-i",
            str(source),
            "-t",
            str(candidate.duration),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        try:
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()
                message = detail[-1] if detail else "unknown FFmpeg error"
                raise ClipAnalysisError(f"Could not export {output.name}: {message}")
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


class YouTubeClipFinder(VideoClipFinder):
    """Download one YouTube video as MP4, then find and optionally export its best clips."""

    def download(
        self,
        url: str,
        output: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        _validate_youtube_url(url)
        destination = Path(output).expanduser().resolve()
        if destination.suffix.lower() != ".mp4":
            raise ConfigurationError("YouTube download output must use the .mp4 extension")
        if destination.exists() and not overwrite:
            raise DownloadError(f"Download output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            import yt_dlp
        except ImportError as exc:
            raise DependencyError(
                "YouTube downloads require the optional dependency: uv sync --extra youtube"
            ) from exc

        with tempfile.TemporaryDirectory(prefix="rot-youtube-", dir=destination.parent) as raw:
            workdir = Path(raw)
            template = str(workdir / "source.%(ext)s")
            options: dict[str, Any] = {
                "format": (
                    "bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]/"
                    "b[ext=mp4]/bv*+ba/b"
                ),
                "merge_output_format": "mp4",
                "outtmpl": template,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    downloader.extract_info(url, download=True)
            except Exception as exc:
                raise DownloadError(f"Could not download YouTube video: {exc}") from exc

            downloads = sorted(
                path
                for path in workdir.iterdir()
                if path.is_file()
                and path.suffix.lower() in _VIDEO_EXTENSIONS
                and not path.name.endswith(".part")
            )
            if not downloads:
                raise DownloadError("yt-dlp completed without producing a video file")
            downloaded = next((path for path in downloads if path.suffix.lower() == ".mp4"), None)
            if downloaded is None:
                downloaded = self._remux_to_mp4(downloads[0], workdir / "source.mp4")
            downloaded.replace(destination)
        return destination

    def find(
        self,
        url: str,
        output_dir: str | Path,
        *,
        export: bool = True,
        overwrite: bool = False,
    ) -> ClipSearchResult:
        directory = Path(output_dir).expanduser().resolve()
        source = self.download(url, directory / "source.mp4", overwrite=overwrite)
        candidates = self.analyze(source)
        exports = self.export(source, candidates, directory, overwrite=overwrite) if export else ()
        return ClipSearchResult(source, candidates, exports)

    @staticmethod
    def _remux_to_mp4(source: Path, output: Path) -> Path:
        command = [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            str(output),
        ]
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode == 0:
            return output
        fallback = [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(output),
        ]
        transcoded = subprocess.run(fallback, text=True, capture_output=True, check=False)
        if transcoded.returncode != 0:
            raise DownloadError(
                f"Could not convert downloaded video to MP4: {transcoded.stderr.strip()}"
            )
        return output


def _parse_signal_output(text: str, value_pattern: re.Pattern[str]) -> tuple[tuple[float, float], ...]:
    values: list[tuple[float, float]] = []
    timestamp: float | None = None
    for line in text.splitlines():
        time_match = _PTS_RE.search(line)
        if time_match:
            timestamp = float(time_match.group(1))
            continue
        value_match = value_pattern.search(line)
        if value_match and timestamp is not None:
            raw = value_match.group(1)
            values.append((timestamp, float("-inf") if raw == "-inf" else float(raw)))
            timestamp = None
    return tuple(values)


def _overlap_ratio(first: ClipCandidate, second: ClipCandidate) -> float:
    overlap = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    return overlap / min(first.duration, second.duration)


def _validate_youtube_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    youtube_host = (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )
    if parsed.scheme not in {"http", "https"} or not youtube_host:
        raise ConfigurationError("Expected an http(s) YouTube or youtu.be URL")
