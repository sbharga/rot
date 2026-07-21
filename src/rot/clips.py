"""Remote-video ingestion and signal-based clip discovery."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import subprocess
import tempfile
import uuid
from bisect import bisect_left
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from .errors import (
    ClipAnalysisError,
    ConfigurationError,
    DependencyError,
    DownloadError,
    ProbeError,
)
from .models import Clip, MediaInfo
from .probe import executable, probe
from .progress import ProgressReporter

logger = logging.getLogger("rot")

ClipDetectionMethod = Literal["hybrid", "scene", "motion", "audio"]
SignalName = Literal["scene", "motion", "audio"]

_PTS_RE = re.compile(r"pts_time:([-+0-9.eE]+)")
_SCENE_RE = re.compile(r"lavfi\.scene_score=([-+0-9.eE]+)")
_MOTION_RE = re.compile(r"lavfi\.signalstats\.YDIF=([-+0-9.eE]+)")
_AUDIO_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=([-+0-9.eE]+|-inf)")
_VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".mov",
        ".webm",
        ".flv",
        ".avi",
        ".m4v",
        ".wmv",
        ".mpg",
        ".mpeg",
        ".ts",
    }
)
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PROGRESS_RE = re.compile(r"out_time_us=(\d+)")
# Bump whenever the extraction filter graph or signal parsing changes.
_CACHE_VERSION = "clips-1"


@dataclass(frozen=True, slots=True)
class SignalSeries:
    """A time-sorted scalar signal supporting fast half-open window aggregates.

    Windows are half-open ``[start, end)``. Range sums come from a prefix array and range
    maxima from a sparse table, so scoring many candidate windows costs O(1) per query
    instead of rescanning every sample.
    """

    times: tuple[float, ...]
    values: tuple[float, ...]
    prefix: tuple[float, ...]
    sparse: tuple[tuple[float, ...], ...]

    @classmethod
    def from_events(cls, events: Iterable[tuple[float, float]]) -> SignalSeries:
        """Build a series, dropping non-finite samples and collapsing duplicate timestamps.

        Duplicate timestamps keep the last value because FFmpeg metadata filters can reprint
        a key for the same frame.
        """
        collected: dict[float, float] = {}
        for timestamp, value in events:
            if math.isfinite(timestamp) and math.isfinite(value):
                collected[timestamp] = value
        if not collected:
            return cls((), (), (0.0,), ())
        ordered = sorted(collected.items())
        times = tuple(item[0] for item in ordered)
        values = tuple(item[1] for item in ordered)

        prefix = [0.0]
        for value in values:
            prefix.append(prefix[-1] + value)

        levels: list[tuple[float, ...]] = [values]
        width = 1
        while width * 2 <= len(values):
            previous = levels[-1]
            span = width
            levels.append(
                tuple(
                    max(previous[index], previous[index + span])
                    for index in range(len(values) - span * 2 + 1)
                )
            )
            width *= 2
        return cls(times, values, tuple(prefix), tuple(levels))

    def __len__(self) -> int:
        return len(self.times)

    def _bounds(self, start: float, end: float) -> tuple[int, int]:
        if end <= start or not self.times:
            return (0, 0)
        return (bisect_left(self.times, start), bisect_left(self.times, end))

    def count_range(self, start: float, end: float) -> int:
        low, high = self._bounds(start, end)
        return max(0, high - low)

    def sum_range(self, start: float, end: float) -> float:
        low, high = self._bounds(start, end)
        if high <= low:
            return 0.0
        return self.prefix[high] - self.prefix[low]

    def mean_range(self, start: float, end: float) -> float:
        low, high = self._bounds(start, end)
        if high <= low:
            return 0.0
        return (self.prefix[high] - self.prefix[low]) / (high - low)

    def max_range(self, start: float, end: float) -> float:
        low, high = self._bounds(start, end)
        if high <= low:
            return 0.0
        level = (high - low).bit_length() - 1
        span = 1 << level
        table = self.sparse[level]
        return max(table[low], table[high - span])

    def local_minima(self, *, below: float) -> tuple[float, ...]:
        """Timestamps of samples at or under ``below`` that are no larger than their neighbours."""
        last = len(self.values) - 1
        return tuple(
            self.times[index]
            for index, value in enumerate(self.values)
            if value <= below
            and (index == 0 or value <= self.values[index - 1])
            and (index == last or value <= self.values[index + 1])
        )


EMPTY_SERIES = SignalSeries.from_events(())


@dataclass(frozen=True, slots=True)
class SourceSignals:
    """The extracted per-source signals used for ranking."""

    scene: SignalSeries = EMPTY_SERIES
    motion: SignalSeries = EMPTY_SERIES
    audio: SignalSeries = EMPTY_SERIES


@dataclass(frozen=True, slots=True)
class SignalCache:
    """Stores extracted signals on disk so re-running a search skips decoding.

    Every failure here degrades to a cache miss: a broken or unwritable cache must never be
    able to fail an analysis.
    """

    root: Path
    enabled: bool = True

    @classmethod
    def default(cls, *, enabled: bool = True) -> SignalCache:
        base = os.environ.get("XDG_CACHE_HOME")
        root = Path(base) if base else Path.home() / ".cache"
        return cls(root / "rot" / "clip-signals", enabled)

    def key(self, path: Path, settings: ClipDetectionSettings) -> str:
        stat = path.stat()
        payload = "\0".join(
            str(part)
            for part in (
                _CACHE_VERSION,
                path.resolve(),
                stat.st_mtime_ns,
                stat.st_size,
                *settings.cache_key_fields,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load(self, key: str) -> SourceSignals | None:
        if not self.enabled:
            return None
        try:
            payload = json.loads(self._path(key).read_text())
            if payload.get("version") != _CACHE_VERSION:
                return None
            return SourceSignals(
                scene=SignalSeries.from_events(payload["scene"]),
                motion=SignalSeries.from_events(payload["motion"]),
                audio=SignalSeries.from_events(payload["audio"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            logger.debug("Ignoring unreadable clip signal cache entry", exc_info=True)
            return None

    def store(self, key: str, signals: SourceSignals) -> None:
        if not self.enabled:
            return
        payload = {
            "version": _CACHE_VERSION,
            "scene": _serialize(signals.scene),
            "motion": _serialize(signals.motion),
            "audio": _serialize(signals.audio),
        }
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root / f".{key}-{uuid.uuid4().hex}.tmp"
            temporary.write_text(json.dumps(payload))
            os.replace(temporary, self._path(key))
        except OSError:
            logger.debug("Could not write clip signal cache entry", exc_info=True)


@dataclass(frozen=True, slots=True)
class ClipDetectionSettings:
    """Controls how interesting short-form windows are selected from source video.

    Attributes:
        method: ``hybrid``, ``scene``, ``motion``, or ``audio`` ranking.
        clip_duration: Desired candidate length in seconds.
        clip_count: Maximum selected candidate count.
        max_overlap_ratio: Maximum overlap between selected candidates.
        max_per_source: Optional per-source limit during folder analysis.
        scene_threshold: FFmpeg scene-change threshold.
        analysis_interval: Sampling interval in seconds.
        analysis_width: Even analysis-frame width.
        motion_fps: Motion sampling frame rate.
        audio_floor_db: RMS value normalized to zero.
        audio_ceiling_db: RMS value normalized to one.
        audio_mean_weight: Mean-energy contribution.
        audio_peak_weight: Peak-energy contribution.
        scene_half_saturation: Scene-score saturation constant.
        motion_reference: Motion normalization reference.
        scene_weight: Hybrid scene contribution.
        motion_weight: Hybrid motion contribution.
        audio_weight: Hybrid audio contribution.
        boundary_penalty: Penalty for energetic window edges.
        edge_probe: Edge-analysis duration in seconds.
        snap: Whether selected edges snap to nearby boundaries.
        snap_window: Maximum boundary movement in seconds.
        snap_silence_level: Normalized audio trough threshold.
    """

    # Selection.
    method: ClipDetectionMethod = "hybrid"
    clip_duration: float = 30.0
    clip_count: int = 5
    max_overlap_ratio: float = 0.20
    max_per_source: int | None = None

    # Signal extraction.
    scene_threshold: float = 0.30
    analysis_interval: float = 0.5
    analysis_width: int = 320
    motion_fps: float = 15.0

    # Normalization.
    audio_floor_db: float = -50.0
    audio_ceiling_db: float = -12.0
    audio_mean_weight: float = 0.70
    audio_peak_weight: float = 0.30
    scene_half_saturation: float = 0.25
    motion_reference: float = 12.0

    # Blending.
    scene_weight: float = 0.35
    motion_weight: float = 0.20
    audio_weight: float = 0.45

    # Boundary quality.
    boundary_penalty: float = 0.15
    edge_probe: float = 0.40
    snap: bool = True
    snap_window: float = 1.0
    snap_silence_level: float = 0.25

    def __post_init__(self) -> None:
        if self.method not in {"hybrid", "scene", "motion", "audio"}:
            raise ConfigurationError(f"Unknown clip detection method {self.method!r}")
        if self.clip_duration <= 0:
            raise ConfigurationError("clip_duration must be positive")
        if self.clip_count <= 0:
            raise ConfigurationError("clip_count must be positive")
        if not 0 < self.scene_threshold <= 1:
            raise ConfigurationError("scene_threshold must be greater than 0 and at most 1")
        if self.analysis_interval <= 0:
            raise ConfigurationError("analysis_interval must be positive")
        if self.analysis_width < 64 or self.analysis_width % 2:
            raise ConfigurationError("analysis_width must be an even number of at least 64")
        if self.motion_fps <= 0:
            raise ConfigurationError("motion_fps must be positive")
        if self.audio_floor_db >= self.audio_ceiling_db:
            raise ConfigurationError("audio_floor_db must be lower than audio_ceiling_db")
        if self.audio_mean_weight < 0 or self.audio_peak_weight < 0:
            raise ConfigurationError("audio_mean_weight and audio_peak_weight must not be negative")
        if self.audio_mean_weight + self.audio_peak_weight <= 0:
            raise ConfigurationError("audio_mean_weight and audio_peak_weight must not both be 0")
        if self.scene_half_saturation <= 0:
            raise ConfigurationError("scene_half_saturation must be positive")
        if self.motion_reference <= 0:
            raise ConfigurationError("motion_reference must be positive")
        if min(self.scene_weight, self.motion_weight, self.audio_weight) < 0:
            raise ConfigurationError("signal weights must not be negative")
        if self.method == "hybrid" and not any(
            (self.scene_weight, self.motion_weight, self.audio_weight)
        ):
            raise ConfigurationError(
                "hybrid detection needs a positive scene, motion, or audio weight"
            )
        if not 0 <= self.boundary_penalty < 1:
            raise ConfigurationError("boundary_penalty must be at least 0 and less than 1")
        if self.edge_probe <= 0:
            raise ConfigurationError("edge_probe must be positive")
        if self.snap_window < 0:
            raise ConfigurationError("snap_window must not be negative")
        if not 0 <= self.snap_silence_level <= 1:
            raise ConfigurationError("snap_silence_level must be between 0 and 1")
        if not 0 <= self.max_overlap_ratio < 1:
            raise ConfigurationError("max_overlap_ratio must be at least 0 and less than 1")
        if self.max_per_source is not None and self.max_per_source < 1:
            raise ConfigurationError("max_per_source must be at least 1 when set")

    @property
    def signal_weights(self) -> dict[SignalName, float]:
        """Resolve ``method`` into per-signal blend weights."""
        if self.method == "scene":
            return {"scene": self.scene_weight, "motion": 0.0, "audio": 0.0}
        if self.method == "motion":
            return {"scene": 0.0, "motion": self.motion_weight, "audio": 0.0}
        if self.method == "audio":
            return {"scene": 0.0, "motion": 0.0, "audio": self.audio_weight}
        return {
            "scene": self.scene_weight,
            "motion": self.motion_weight,
            "audio": self.audio_weight,
        }

    @property
    def cache_key_fields(self) -> tuple[object, ...]:
        """The settings that change decoding, and therefore the extracted signals."""
        return (
            self.scene_threshold,
            self.analysis_interval,
            self.analysis_width,
            self.motion_fps,
        )


@dataclass(frozen=True, slots=True)
class ClipCandidate:
    """A ranked source interval that can be exported or added to a Project.

    Attributes:
        source: Source video path.
        start: Candidate start time.
        end: Candidate end time.
        score: Combined ranking score.
        scene_score: Normalized scene-change contribution.
        motion_score: Normalized motion contribution.
        audio_score: Normalized audio contribution.
    """

    source: Path
    start: float
    end: float
    score: float
    scene_score: float
    motion_score: float
    audio_score: float

    @property
    def duration(self) -> float:
        """Return candidate duration in seconds."""

        return self.end - self.start

    def as_clip(self, *, keep_audio: bool = True) -> Clip:
        """Convert the candidate to a trim-aware Clip.

        Args:
            keep_audio: Preserve the source audio during project rendering.
        """

        return Clip(
            self.source,
            trim_start=self.start,
            trim_end=self.end,
            loop=False,
            keep_audio=keep_audio,
        )


@dataclass(frozen=True, slots=True)
class SkippedSource:
    """A source that could not be analyzed.

    Attributes:
        path: Skipped media path.
        reason: Human-readable failure reason.
    """

    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class ClipSearchResult:
    """Candidates, exports, and diagnostics from a clip search.

    Attributes:
        candidates: Ranked selected intervals.
        sources: Successfully analyzed source paths.
        exports: Encoded candidate paths.
        skipped: Sources that could not be analyzed.
        warnings: Nonfatal search warnings.
    """

    candidates: tuple[ClipCandidate, ...]
    sources: tuple[Path, ...] = ()
    exports: tuple[Path, ...] = ()
    skipped: tuple[SkippedSource, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def source(self) -> Path:
        """The only analyzed source; raises when the search spanned several."""
        if len(self.sources) != 1:
            raise ClipAnalysisError(
                f"This result covers {len(self.sources)} sources; read .sources instead"
            )
        return self.sources[0]

    def project_clips(self, *, keep_audio: bool = True) -> tuple[Clip, ...]:
        """Convert every candidate into a project Clip.

        Args:
            keep_audio: Preserve candidate source audio.
        """

        return tuple(candidate.as_clip(keep_audio=keep_audio) for candidate in self.candidates)


class VideoClipFinder:
    """Find and export high-energy windows from a local video.

    Args:
        settings: Detection and ranking settings.
        cache: Enable the default signal cache or provide a custom SignalCache.
    """

    def __init__(
        self,
        settings: ClipDetectionSettings | None = None,
        *,
        cache: bool | SignalCache = True,
    ) -> None:
        self.settings = settings or ClipDetectionSettings()
        self._cache = cache if isinstance(cache, SignalCache) else SignalCache.default(
            enabled=cache
        )

    def analyze(
        self, source: str | Path, *, reporter: ProgressReporter | None = None
    ) -> tuple[ClipCandidate, ...]:
        """Analyze one local video without exporting it.

        Args:
            source: Local video path.
            reporter: Optional internal progress reporter.
        """

        path = Path(source).expanduser().resolve()
        info = probe(path)
        if not info.has_video or info.duration <= 0:
            raise ClipAnalysisError(f"Clip analysis requires a non-empty video: {path}")
        if self.settings.method == "audio" and not info.has_audio:
            raise ClipAnalysisError(f"Audio clip detection requires an audio stream: {path}")

        signals = self._extract_signals(path, info, reporter=reporter)
        candidates = self._rank(path, info.duration, signals)
        if len(candidates) < self.settings.clip_count:
            logger.warning(
                "Found %d of %d requested clips in %s; "
                "try a shorter --duration or a higher --max-overlap",
                len(candidates),
                self.settings.clip_count,
                path.name,
            )
        return candidates

    def analyze_many(
        self,
        sources: Iterable[str | Path],
        *,
        reporter: ProgressReporter | None = None,
    ) -> ClipSearchResult:
        """Rank clips across several sources, skipping unreadable files.

        Args:
            sources: Local video paths.
            reporter: Optional internal progress reporter.
        """
        paths = [Path(source).expanduser().resolve() for source in sources]
        per_source = self.settings.max_per_source or self.settings.clip_count
        collected: list[ClipCandidate] = []
        analyzed: list[Path] = []
        skipped: list[SkippedSource] = []

        for index, path in enumerate(paths):
            if reporter is not None:
                reporter.emit("analyze", index, len(paths), f"Analyzing {path.name}")
            try:
                # Overlap is only meaningful within one file, so each source is reduced to its
                # own best windows before the results compete globally.
                candidates = self.analyze(path)
            except (ProbeError, ClipAnalysisError, OSError) as exc:
                # A single unreadable file must not abort a library scan, but a missing FFmpeg
                # is an environment failure and is deliberately allowed to propagate.
                logger.debug("Skipping %s: %s", path, exc)
                skipped.append(SkippedSource(path, str(exc)))
                continue
            analyzed.append(path)
            collected.extend(candidates[:per_source])
        if reporter is not None:
            reporter.emit("analyze", len(paths), len(paths), f"Analyzed {len(paths)} sources")

        if not analyzed:
            detail = "; ".join(f"{item.path.name}: {item.reason}" for item in skipped[:3])
            raise ClipAnalysisError(f"No source could be analyzed. {detail}")

        collected.sort(key=lambda item: (-item.score, str(item.source), item.start))
        selected = self._select_diverse(collected)
        warnings: tuple[str, ...] = ()
        if len(selected) < self.settings.clip_count:
            message = (
                f"Found {len(selected)} of {self.settings.clip_count} requested clips "
                f"across {len(analyzed)} sources"
            )
            logger.warning(message)
            warnings = (message,)
        if skipped:
            warnings += (f"Skipped {len(skipped)} unreadable source(s)",)
        return ClipSearchResult(selected, tuple(analyzed), (), tuple(skipped), warnings)

    def find(
        self,
        source: str | Path,
        output_dir: str | Path,
        *,
        export: bool = True,
        overwrite: bool = False,
        progress: bool = False,
    ) -> ClipSearchResult:
        """Analyze one video and optionally export its candidates.

        Args:
            source: Local video path.
            output_dir: Export directory.
            export: Encode selected candidates when true.
            overwrite: Permit replacing candidate files.
            progress: Display analysis progress.
        """

        path = Path(source).expanduser().resolve()
        with ProgressReporter(progress) as reporter:
            candidates = self.analyze(path, reporter=reporter)
        warnings: tuple[str, ...] = ()
        if len(candidates) < self.settings.clip_count:
            warnings = (
                f"Found {len(candidates)} of {self.settings.clip_count} requested clips in "
                f"{path.name}",
            )
        exports = self.export(candidates, output_dir, overwrite=overwrite) if export else ()
        return ClipSearchResult(candidates, (path,), exports, (), warnings)

    def export(
        self,
        candidates: tuple[ClipCandidate, ...] | list[ClipCandidate],
        output_dir: str | Path,
        *,
        overwrite: bool = False,
    ) -> tuple[Path, ...]:
        """Encode candidates as deterministic H.264/AAC MP4 files.

        Args:
            candidates: Candidate intervals to export.
            output_dir: Destination directory.
            overwrite: Permit replacing existing exports.
        """

        directory = Path(output_dir).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        outputs: list[Path] = []
        for candidate in candidates:
            output = directory / _export_name(candidate)
            if output.exists() and not overwrite:
                raise ClipAnalysisError(f"Clip output already exists: {output}")
            self._export_one(candidate, output)
            outputs.append(output)
        return tuple(outputs)

    # Signal extraction -------------------------------------------------------------------

    def _extract_signals(
        self, path: Path, info: MediaInfo, *, reporter: ProgressReporter | None = None
    ) -> SourceSignals:
        try:
            key = self._cache.key(path, self.settings)
        except OSError:
            key = ""
        if key:
            cached = self._cache.load(key)
            if cached is not None:
                logger.debug("Reusing cached clip signals for %s", path.name)
                return cached
        signals = self._decode_signals(path, info, reporter=reporter)
        if key:
            self._cache.store(key, signals)
        return signals

    def _wanted_signals(self, info: MediaInfo) -> tuple[bool, bool, bool]:
        weights = self.settings.signal_weights
        # The scene series also anchors candidate starts and boundary snapping, so it is
        # extracted whenever any visual signal is in play.
        want_scene = weights["scene"] > 0 or weights["motion"] > 0
        want_motion = weights["motion"] > 0
        want_audio = weights["audio"] > 0 and info.has_audio
        return want_scene, want_motion, want_audio

    def _build_graph(
        self, info: MediaInfo, targets: dict[str, Path]
    ) -> tuple[str, str]:
        """Return the filter graph and the label to map, for one merged extraction pass."""
        settings = self.settings
        want_scene, want_motion, want_audio = self._wanted_signals(info)
        chains: list[str] = []
        output = ""

        if want_scene or want_motion:
            head = f"[0:v]scale={settings.analysis_width}:-2"
            if want_scene and want_motion:
                chains.append(f"{head},split=2[sc][mo]")
                scene_in, motion_in = "[sc]", "[mo]"
            elif want_scene:
                chains.append(f"{head}[sc]")
                scene_in, motion_in = "[sc]", ""
            else:
                chains.append(f"{head}[mo]")
                scene_in, motion_in = "", "[mo]"

            if want_scene:
                output = "[probe]"
                chains.append(
                    f"{scene_in}select=gt(scene\\,{settings.scene_threshold}),"
                    f"metadata=print:key=lavfi.scene_score:"
                    f"file={_escape_filter_value(str(targets['scene']))}[probe]"
                )
            if want_motion:
                # YDIF is a per-frame-pair difference, so its magnitude scales with the frame
                # interval. Pinning the rate keeps motion_reference meaningful across sources.
                tail = ",nullsink"
                if not output:
                    output = "[probe]"
                    tail = "[probe]"
                chains.append(
                    f"{motion_in}fps={settings.motion_fps},signalstats,"
                    f"metadata=print:key=lavfi.signalstats.YDIF:"
                    f"file={_escape_filter_value(str(targets['motion']))}{tail}"
                )

        if want_audio:
            sample_rate = info.sample_rate or 48_000
            samples = max(1, round(sample_rate * settings.analysis_interval))
            tail = ",anullsink"
            if not output:
                output = "[aprobe]"
                tail = "[aprobe]"
            chains.append(
                f"[0:a]asetnsamples=n={samples}:p=1,astats=metadata=1:reset=1,"
                f"ametadata=print:key=lavfi.astats.Overall.RMS_level:"
                f"file={_escape_filter_value(str(targets['audio']))}{tail}"
            )
        return ";".join(chains), output

    def _decode_signals(
        self, path: Path, info: MediaInfo, *, reporter: ProgressReporter | None = None
    ) -> SourceSignals:
        want_scene, want_motion, want_audio = self._wanted_signals(info)
        if not (want_scene or want_motion or want_audio):
            return SourceSignals()

        with tempfile.TemporaryDirectory(prefix="rot-signals-") as raw:
            workdir = Path(raw)
            targets = {name: workdir / f"{name}.txt" for name in ("scene", "motion", "audio")}
            graph, output = self._build_graph(info, targets)
            command = [
                executable("ffmpeg"),
                "-hide_banner",
                "-loglevel",
                "info",
                "-nostats",
                "-progress",
                "pipe:1",
                "-i",
                str(path),
                "-filter_complex",
                graph,
                "-map",
                output,
                "-f",
                "null",
                os.devnull,
            ]
            self._run_extraction(
                command, source=path, total_duration=info.duration, reporter=reporter
            )

            scene = self._read_series(targets["scene"], _SCENE_RE) if want_scene else EMPTY_SERIES
            motion = EMPTY_SERIES
            if want_motion:
                raw_motion = self._read_events(targets["motion"], _MOTION_RE)
                _require_samples(raw_motion, "motion", path, info.duration)
                motion = SignalSeries.from_events(
                    (timestamp, min(1.0, max(0.0, value / self.settings.motion_reference)))
                    for timestamp, value in raw_motion
                )
            audio = EMPTY_SERIES
            if want_audio:
                raw_audio = self._read_events(targets["audio"], _AUDIO_RE)
                _require_samples(raw_audio, "audio", path, info.duration)
                audio = SignalSeries.from_events(
                    (timestamp, self._normalize_db(value)) for timestamp, value in raw_audio
                )
        return SourceSignals(scene=scene, motion=motion, audio=audio)

    @staticmethod
    def _read_events(target: Path, pattern: re.Pattern[str]) -> tuple[tuple[float, float], ...]:
        try:
            text = target.read_text()
        except OSError:
            return ()
        return _parse_signal_output(text, pattern)

    def _read_series(self, target: Path, pattern: re.Pattern[str]) -> SignalSeries:
        return SignalSeries.from_events(self._read_events(target, pattern))

    @staticmethod
    def _run_extraction(
        command: list[str],
        *,
        source: Path,
        total_duration: float,
        reporter: ProgressReporter | None = None,
    ) -> None:
        # stderr goes to a file rather than a pipe so reading progress from stdout cannot
        # deadlock on a full stderr buffer, and so the full log survives for diagnostics.
        with tempfile.TemporaryFile("w+") as errors:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=errors, text=True
            )
            if process.stdout is not None:
                for line in process.stdout:
                    match = _PROGRESS_RE.search(line)
                    if match and reporter is not None and total_duration > 0:
                        reporter.emit(
                            "analyze",
                            min(total_duration, int(match.group(1)) / 1_000_000),
                            total_duration,
                            f"Analyzing {source.name}",
                        )
                process.stdout.close()
            returncode = process.wait()
            if returncode != 0:
                errors.seek(0)
                raise ClipAnalysisError(
                    f"FFmpeg signal analysis failed for {source.name} "
                    f"(exit {returncode}):\n{_stderr_tail(errors.read())}"
                )

    # Ranking -----------------------------------------------------------------------------

    def _rank(
        self, source: Path, duration: float, signals: SourceSignals
    ) -> tuple[ClipCandidate, ...]:
        window_duration = min(self.settings.clip_duration, duration)
        starts = self._candidate_starts(duration, window_duration, signals.scene)
        candidates = [
            self._score_window(
                source, start, min(duration, start + window_duration), signals
            )
            for start in starts
        ]
        candidates.sort(key=lambda item: (-item.score, item.start))
        selected = self._select_diverse(candidates)
        if self.settings.snap:
            selected = self._snap_to_boundaries(selected, signals, duration)
        return selected

    def _candidate_starts(
        self, duration: float, window_duration: float, scene: SignalSeries
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
        strongest = sorted(
            zip(scene.times, scene.values, strict=True),
            key=lambda event: event[1],
            reverse=True,
        )[:anchor_limit]
        for timestamp, _ in strongest:
            starts.add(min(last_start, max(0.0, timestamp - window_duration / 2)))
        return tuple(sorted(starts))

    def _score_window(
        self, source: Path, start: float, end: float, signals: SourceSignals
    ) -> ClipCandidate:
        settings = self.settings
        window = max(end - start, 1e-9)

        # Cut density rather than a raw sum, so the score does not depend on window length,
        # and a saturating curve so busy windows stay ordered instead of all clamping to 1.
        density = signals.scene.sum_range(start, end) / window
        scene_score = density / (density + settings.scene_half_saturation)

        # Mean, not max: a hard cut spikes YDIF, and taking the peak would make motion
        # largely redundant with the scene signal.
        motion_score = signals.motion.mean_range(start, end)

        audio_score = 0.0
        if signals.audio.count_range(start, end):
            mean_weight = settings.audio_mean_weight / (
                settings.audio_mean_weight + settings.audio_peak_weight
            )
            audio_score = mean_weight * signals.audio.mean_range(start, end) + (
                1 - mean_weight
            ) * signals.audio.max_range(start, end)

        scores: dict[SignalName, float] = {
            "scene": scene_score,
            "motion": motion_score,
            "audio": audio_score,
        }
        series: dict[SignalName, SignalSeries] = {
            "scene": signals.scene,
            "motion": signals.motion,
            "audio": signals.audio,
        }
        # Renormalize over the signals actually present, so a source without audio falls back
        # to visual-only scoring with correctly rescaled weights.
        weights = {
            name: weight
            for name, weight in settings.signal_weights.items()
            if weight > 0 and len(series[name])
        }
        total = sum(weights.values())
        score = (
            sum(weight * scores[name] for name, weight in weights.items()) / total
            if total
            else 0.0
        )
        score *= 1 - settings.boundary_penalty * self._edge_energy(signals.audio, start, end)

        return ClipCandidate(
            source=source,
            start=round(start, 3),
            end=round(end, 3),
            score=round(score, 6),
            scene_score=round(scene_score, 6),
            motion_score=round(motion_score, 6),
            audio_score=round(audio_score, 6),
        )

    def _edge_energy(self, audio: SignalSeries, start: float, end: float) -> float:
        """Mean audio level straddling both cut points, so quiet edges rank slightly higher."""
        if not len(audio):
            return 0.0
        half = self.settings.edge_probe / 2
        return (
            audio.mean_range(start - half, start + half)
            + audio.mean_range(end - half, end + half)
        ) / 2

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

    def _snap_to_boundaries(
        self,
        candidates: tuple[ClipCandidate, ...],
        signals: SourceSignals,
        duration: float,
    ) -> tuple[ClipCandidate, ...]:
        """Shift selected clips onto a nearby cut or audio trough so they start cleanly.

        Deliberately applied after selection and without re-scoring: a shift of at most
        ``snap_window`` cannot move a candidate past the overlap gate at realistic clip
        lengths, and re-scoring here would make selection order depend on its own output.
        """
        boundaries = sorted(
            set(signals.scene.times)
            | set(signals.audio.local_minima(below=self.settings.snap_silence_level))
        )
        if not boundaries:
            return candidates
        snapped: list[ClipCandidate] = []
        for candidate in candidates:
            boundary = _nearest(boundaries, candidate.start, self.settings.snap_window)
            if boundary is None:
                snapped.append(candidate)
                continue
            delta = boundary - candidate.start
            start = candidate.start + delta
            end = candidate.end + delta
            if start < 0 or end > duration:
                snapped.append(candidate)
                continue
            snapped.append(replace(candidate, start=round(start, 3), end=round(end, 3)))
        return tuple(snapped)

    # Export ------------------------------------------------------------------------------

    @staticmethod
    def _export_one(candidate: ClipCandidate, output: Path) -> None:
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
            str(candidate.source),
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
                raise ClipAnalysisError(
                    f"Could not export {output.name}:\n{_stderr_tail(completed.stderr)}"
                )
            temporary.replace(output)
        finally:
            # A successful replace already moved the file; this only clears a failed attempt.
            temporary.unlink(missing_ok=True)


class FolderClipFinder(VideoClipFinder):
    """Rank clips across a local library of existing footage."""

    def find(
        self,
        root: str | Path,
        output_dir: str | Path,
        *,
        export: bool = True,
        overwrite: bool = False,
        progress: bool = False,
        recursive: bool = True,
        extensions: Collection[str] | None = None,
    ) -> ClipSearchResult:
        """Rank videos found beneath a directory and optionally export winners.

        Args:
            root: Video-library directory.
            output_dir: Export directory.
            export: Encode selected candidates when true.
            overwrite: Permit replacing exports.
            progress: Display analysis progress.
            recursive: Search subdirectories.
            extensions: Optional accepted suffixes.
        """

        sources = discover_videos(root, recursive=recursive, extensions=extensions)
        if not sources:
            searched = ", ".join(sorted(extensions or _VIDEO_EXTENSIONS))
            raise ClipAnalysisError(
                f"No video files under {Path(root).expanduser()} matched: {searched}"
            )
        with ProgressReporter(progress) as reporter:
            result = self.analyze_many(sources, reporter=reporter)
        if not export:
            return result
        exports = self.export(result.candidates, output_dir, overwrite=overwrite)
        return replace(result, exports=exports)


def discover_videos(
    root: str | Path,
    *,
    recursive: bool = True,
    extensions: Collection[str] | None = None,
    follow_symlinks: bool = False,
) -> tuple[Path, ...]:
    """Find videos beneath a directory, sorted and deduplicated.

    Args:
        root: Directory to search.
        recursive: Search subdirectories.
        extensions: Optional accepted suffixes.
        follow_symlinks: Include symbolic-link files.
    """
    directory = Path(root).expanduser()
    if not directory.is_dir():
        raise ConfigurationError(f"Clip discovery needs a directory: {directory}")
    suffixes = {suffix.lower() for suffix in (extensions or _VIDEO_EXTENSIONS)}
    paths = directory.rglob("*") if recursive else directory.glob("*")
    found: dict[Path, None] = {}
    for path in paths:
        if path.name.startswith(".") or any(part.startswith(".") for part in path.parts):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if path.is_symlink() and not follow_symlinks:
            continue
        if not path.is_file():
            continue
        found[path.resolve()] = None
    return tuple(sorted(found))


class YouTubeClipFinder(VideoClipFinder):
    """Download one YouTube video as MP4, then find and optionally export its best clips."""

    def download(
        self,
        url: str,
        output: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Download one permitted YouTube source as MP4.

        Args:
            url: YouTube video URL.
            output: Destination MP4 path.
            overwrite: Permit replacing an existing download.
        """

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

    def find(  # type: ignore[override]
        self,
        url: str,
        output_dir: str | Path,
        *,
        export: bool = True,
        overwrite_download: bool = False,
        overwrite_exports: bool = False,
        progress: bool = False,
    ) -> ClipSearchResult:
        """Download, analyze, and optionally export a YouTube source.

        Args:
            url: YouTube video URL.
            output_dir: Download and export directory.
            export: Encode candidate clips when true.
            overwrite_download: Permit replacing ``source.mp4``.
            overwrite_exports: Permit replacing candidate files.
            progress: Display analysis progress.
        """

        directory = Path(output_dir).expanduser().resolve()
        source = self.download(url, directory / "source.mp4", overwrite=overwrite_download)
        return super().find(
            source, directory, export=export, overwrite=overwrite_exports, progress=progress
        )

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


TwitchClipVariant = Literal["landscape", "portrait"]


class TwitchClipFinder(VideoClipFinder):
    """Download one authorized Twitch clip, then find and export its best windows.

    Twitch's official download API only permits a broadcaster or authorized channel editor to
    download clips for that channel. The supplied user token must include either
    ``channel:manage:clips`` or ``editor:manage:clips``.

    Args:
        settings: Detection and ranking settings.
        client_id: Client ID belonging to the OAuth application that issued ``access_token``.
        access_token: Scoped Twitch user access token.
        cache: Enable the default signal cache or provide a custom SignalCache.
        timeout: Per-request network timeout in seconds.
    """

    _API = "https://api.twitch.tv/helix"
    _VALIDATE = "https://id.twitch.tv/oauth2/validate"
    _SCOPES = frozenset({"channel:manage:clips", "editor:manage:clips"})

    def __init__(
        self,
        settings: ClipDetectionSettings | None = None,
        *,
        client_id: str,
        access_token: str,
        cache: bool | SignalCache = True,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(settings, cache=cache)
        self.client_id = client_id.strip()
        self._access_token = access_token.strip()
        self.timeout = timeout
        if not self.client_id:
            raise ConfigurationError("Twitch client_id must not be empty")
        if not self._access_token:
            raise ConfigurationError("Twitch access_token must not be empty")
        if timeout <= 0:
            raise ConfigurationError("Twitch timeout must be positive")

    def download(
        self,
        clip: str,
        output: str | Path,
        *,
        overwrite: bool = False,
        variant: TwitchClipVariant = "landscape",
    ) -> Path:
        """Download one permitted Twitch clip as MP4 through the official API.

        Args:
            clip: Twitch clip ID or supported Twitch clip URL.
            output: Destination MP4 path.
            overwrite: Permit replacing an existing download.
            variant: Exact official media variant to download.
        """

        clip_id = _twitch_clip_id(clip)
        if variant not in {"landscape", "portrait"}:
            raise ConfigurationError(f"Unknown Twitch clip variant {variant!r}")
        destination = Path(output).expanduser().resolve()
        if destination.suffix.lower() != ".mp4":
            raise ConfigurationError("Twitch download output must use the .mp4 extension")
        if destination.exists() and not overwrite:
            raise DownloadError(f"Download output already exists: {destination}")

        editor_id = self._validate_token()
        metadata = self._get_json(f"{self._API}/clips", params={"id": clip_id})
        clips = metadata.get("data")
        if not isinstance(clips, list) or not clips or not isinstance(clips[0], dict):
            raise DownloadError(f"Twitch clip was not found: {clip_id}")
        broadcaster_id = clips[0].get("broadcaster_id")
        if not isinstance(broadcaster_id, str) or not broadcaster_id:
            raise DownloadError("Twitch clip metadata did not include a broadcaster ID")

        payload = self._get_json(
            f"{self._API}/clips/downloads",
            params={
                "broadcaster_id": broadcaster_id,
                "editor_id": editor_id,
                "clip_id": clip_id,
            },
        )
        downloads = payload.get("data")
        if not isinstance(downloads, list) or not downloads or not isinstance(downloads[0], dict):
            raise DownloadError("Twitch did not return a clip download URL")
        download_url = downloads[0].get(f"{variant}_download_url")
        if not isinstance(download_url, str) or not download_url:
            raise DownloadError(f"Twitch clip has no {variant} download available")

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.stem}-{uuid.uuid4().hex}.mp4")
        httpx = _twitch_httpx()
        try:
            try:
                with httpx.stream(
                    "GET", download_url, timeout=self.timeout, follow_redirects=True
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise DownloadError(
                            f"Twitch media download failed with HTTP {response.status_code}"
                        )
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
            except httpx.HTTPError as exc:
                # Transport errors may include the temporary signed media URL. Keep it out of
                # user-facing messages because its query string is an ephemeral credential.
                raise DownloadError("Could not download Twitch clip media") from exc
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise DownloadError("Twitch media download produced an empty file")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def find(  # type: ignore[override]
        self,
        clip: str,
        output_dir: str | Path,
        *,
        export: bool = True,
        overwrite_download: bool = False,
        overwrite_exports: bool = False,
        progress: bool = False,
        variant: TwitchClipVariant = "landscape",
    ) -> ClipSearchResult:
        """Download, analyze, and optionally export one Twitch clip.

        Args:
            clip: Twitch clip ID or supported Twitch clip URL.
            output_dir: Download and export directory.
            export: Encode candidate windows when true.
            overwrite_download: Permit replacing ``source.mp4``.
            overwrite_exports: Permit replacing candidate files.
            progress: Display analysis progress.
            variant: Exact official media variant to download.
        """

        directory = Path(output_dir).expanduser().resolve()
        source = self.download(
            clip,
            directory / "source.mp4",
            overwrite=overwrite_download,
            variant=variant,
        )
        return super().find(
            source, directory, export=export, overwrite=overwrite_exports, progress=progress
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Client-Id": self.client_id,
        }

    def _validate_token(self) -> str:
        payload = self._get_json(self._VALIDATE, include_client_id=False)
        token_client_id = payload.get("client_id")
        if token_client_id != self.client_id:
            raise DownloadError("Twitch access token belongs to a different client ID")
        editor_id = payload.get("user_id")
        if not isinstance(editor_id, str) or not editor_id:
            raise DownloadError("Twitch clip downloads require a user access token")
        scopes = payload.get("scopes")
        granted = (
            {scope for scope in scopes if isinstance(scope, str)}
            if isinstance(scopes, list)
            else set()
        )
        if not granted.intersection(self._SCOPES):
            expected = " or ".join(sorted(self._SCOPES))
            raise DownloadError(f"Twitch access token must include {expected}")
        return editor_id

    def _get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        include_client_id: bool = True,
    ) -> dict[str, Any]:
        httpx = _twitch_httpx()
        headers = self._headers
        if not include_client_id:
            headers.pop("Client-Id")
        try:
            response = httpx.get(endpoint, headers=headers, params=params, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise DownloadError(f"Could not reach the Twitch API: {exc}") from exc
        if not 200 <= response.status_code < 300:
            detail = _twitch_error_message(response)
            suffix = f": {detail}" if detail else ""
            raise DownloadError(f"Twitch API returned HTTP {response.status_code}{suffix}")
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise DownloadError("Twitch API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise DownloadError("Twitch API returned an invalid response")
        return payload


def _parse_signal_output(
    text: str, value_pattern: re.Pattern[str]
) -> tuple[tuple[float, float], ...]:
    values: list[tuple[float, float]] = []
    timestamp: float | None = None
    for line in text.splitlines():
        time_match = _PTS_RE.search(line)
        if time_match:
            timestamp = float(time_match.group(1))
            continue
        value_match = value_pattern.search(line)
        # The timestamp is only cleared by the next pts_time line, so one frame printing
        # several metadata keys keeps every value.
        if value_match and timestamp is not None:
            raw = value_match.group(1)
            values.append((timestamp, float("-inf") if raw == "-inf" else float(raw)))
    return tuple(values)


def _serialize(series: SignalSeries) -> list[list[float]]:
    return [[time, value] for time, value in zip(series.times, series.values, strict=True)]


def _escape_filter_value(text: str) -> str:
    """Escape a value for use inside an FFmpeg filtergraph option."""
    escaped = text.replace("\\", "\\\\")
    for character in ":,;[]'":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _require_samples(
    events: tuple[tuple[float, float], ...], name: str, source: Path, duration: float
) -> None:
    """Fail loudly when a per-frame signal came back empty.

    ``signalstats`` and ``astats`` emit a sample per frame or window regardless of content, so
    an empty result means the filter never ran rather than that the source was uneventful. The
    scene signal is deliberately not checked this way: a source with no hard cuts legitimately
    produces no scene events.
    """
    if not events and duration > 0:
        raise ClipAnalysisError(
            f"FFmpeg produced no {name} samples for {source.name}; "
            "the analysis filter graph did not run as expected"
        )


def _stderr_tail(stderr: str, limit: int = 20) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return "\n".join(lines[-limit:]) if lines else "unknown FFmpeg error"


def _nearest(boundaries: list[float], target: float, window: float) -> float | None:
    """The boundary closest to ``target`` within ``window``, or None."""
    index = bisect_left(boundaries, target)
    best: float | None = None
    for candidate in boundaries[max(0, index - 1) : index + 1]:
        if abs(candidate - target) <= window and (
            best is None or abs(candidate - target) < abs(best - target)
        ):
            best = candidate
    return best


def _export_name(candidate: ClipCandidate) -> str:
    """A deterministic, source-derived filename, so re-running a search is idempotent."""
    stem = _UNSAFE_NAME_RE.sub("-", candidate.source.stem).strip("-.")[:48] or "clip"
    digest = hashlib.sha256(str(candidate.source).encode()).hexdigest()[:6]
    start = int(candidate.start * 1000)
    end = int(candidate.end * 1000)
    return f"{stem}-{digest}-{start:08d}-{end:08d}.mp4"


def _overlap_ratio(first: ClipCandidate, second: ClipCandidate) -> float:
    if first.source != second.source:
        return 0.0
    overlap = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    smallest = min(first.duration, second.duration)
    if smallest <= 0:
        # A degenerate zero-length candidate has no ratio to compute, so treat it as fully
        # overlapping when its single point falls inside the other interval.
        point, other = (first, second) if first.duration <= 0 else (second, first)
        return 1.0 if other.start <= point.start <= other.end else 0.0
    return overlap / smallest


def _validate_youtube_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _is_youtube_host(parsed.hostname):
        raise ConfigurationError("Expected an http(s) YouTube or youtu.be URL")


def _is_youtube_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def _is_twitch_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in {"twitch.tv", "www.twitch.tv", "clips.twitch.tv"}


def _twitch_clip_id(clip: str) -> str:
    value = clip.strip()
    if not value:
        raise ConfigurationError("Twitch clip ID or URL must not be empty")
    parsed = urlparse(value)
    if not parsed.scheme and not parsed.netloc:
        if re.fullmatch(r"[A-Za-z0-9_-]+", value):
            return value
        raise ConfigurationError("Invalid Twitch clip ID")
    if parsed.scheme not in {"http", "https"} or not _is_twitch_host(parsed.hostname):
        raise ConfigurationError("Expected a Twitch clip ID or http(s) Twitch clip URL")

    host = (parsed.hostname or "").lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    clip_id: str | None = None
    if host == "clips.twitch.tv":
        if parts and parts[0] == "embed":
            values = parse_qs(parsed.query).get("clip", [])
            clip_id = values[0] if values else None
        elif len(parts) == 1:
            clip_id = parts[0]
    elif len(parts) == 3 and parts[1].lower() == "clip":
        clip_id = parts[2]
    if clip_id is None or not re.fullmatch(r"[A-Za-z0-9_-]+", clip_id):
        raise ConfigurationError("Expected a URL for one Twitch clip")
    return clip_id


def _twitch_httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise DependencyError(
            "Twitch downloads require the optional dependency: uv sync --extra twitch"
        ) from exc
    return httpx


def _twitch_error_message(response: Any) -> str:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    message = payload.get("message")
    return message[:300] if isinstance(message, str) else ""
