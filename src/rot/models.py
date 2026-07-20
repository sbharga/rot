"""Typed public models used throughout rot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .errors import ConfigurationError

Fit = Literal["cover", "contain", "custom", "stretch"]
Fill = Literal["black", "blur"]
Anchor = Literal[
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """A render-stage progress update."""

    stage: str
    completed: float
    total: float = 1.0
    message: str = ""

    @property
    def fraction(self) -> float:
        return 0.0 if self.total <= 0 else min(1.0, max(0.0, self.completed / self.total))


ProgressCallback = Callable[[ProgressEvent], None]
StageProgressCallback = Callable[[str, float, float, str], None]


@dataclass(frozen=True, slots=True)
class WordTiming:
    text: str
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ConfigurationError(f"Invalid word timing for {self.text!r}")


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    path: Path
    duration: float | None = None


@runtime_checkable
class VoiceProvider(Protocol):
    """Interface implemented by text-to-speech providers."""

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        language: str,
        progress: StageProgressCallback | None = None,
    ) -> SynthesizedAudio: ...


@runtime_checkable
class WordAligner(Protocol):
    """Interface implemented by known-transcript audio aligners."""

    def align(
        self,
        audio_path: Path,
        text: str,
        *,
        language: str,
        progress: StageProgressCallback | None = None,
    ) -> tuple[WordTiming, ...]: ...


@runtime_checkable
class ScriptParser(Protocol):
    def parse(self, source: str) -> Script: ...


@runtime_checkable
class CaptionRenderer(Protocol):
    """Interface for caption sidecar renderers consumed by the media engine."""

    def render(
        self,
        path: Path,
        utterances: list[Utterance],
        theme: CaptionTheme,
        *,
        width: int,
        height: int,
    ) -> Path: ...


@dataclass(frozen=True, slots=True)
class FilterNode:
    """A safe FFmpeg filter description used by custom effect plugins."""

    name: str
    arguments: tuple[tuple[str, str | int | float], ...] = ()

    def __post_init__(self) -> None:
        if not self.name.replace("_", "").isalnum():
            raise ConfigurationError(f"Unsafe FFmpeg filter name: {self.name!r}")
        for key, value in self.arguments:
            if not key.replace("_", "").isalnum():
                raise ConfigurationError(f"Unsafe FFmpeg filter option: {key!r}")
            if isinstance(value, str) and any(char in value for char in ";[]\n\r"):
                raise ConfigurationError(f"Unsafe FFmpeg filter value for {key!r}")


@runtime_checkable
class Effect(Protocol):
    @property
    def name(self) -> str: ...

    def filters(self, *, duration: float, width: int, height: int) -> tuple[FilterNode, ...]: ...


@dataclass(frozen=True, slots=True)
class EffectSpec:
    name: str
    options: tuple[tuple[str, str | int | float], ...] = ()

    @classmethod
    def create(cls, name: str, **options: str | int | float) -> EffectSpec:
        return cls(name=name, options=tuple(sorted(options.items())))


@dataclass(slots=True)
class Clip:
    """A video or still-image source on the primary background track."""

    source: Path | str
    trim_start: float = 0.0
    trim_end: float | None = None
    duration: float | None = None
    loop: bool = True
    fit: Fit = "cover"
    anchor: Anchor = "center"
    keep_audio: bool = False
    volume: float = 1.0
    speed: float = 1.0
    effects: list[EffectSpec | Effect] = field(default_factory=list)
    transition: str = "cut"
    transition_duration: float = 0.3
    id: str | None = None
    fit_amount: float = 0.5
    fill: Fill = "black"
    fill_blur: float = 40.0

    def __post_init__(self) -> None:
        self.source = Path(self.source)
        if self.trim_start < 0:
            raise ConfigurationError("trim_start cannot be negative")
        if self.trim_end is not None and self.trim_end <= self.trim_start:
            raise ConfigurationError("trim_end must be greater than trim_start")
        if self.duration is not None and self.duration <= 0:
            raise ConfigurationError("duration must be positive")
        if self.fit not in {"cover", "contain", "custom", "stretch"}:
            raise ConfigurationError(f"Unknown clip fit {self.fit!r}")
        if not 0 <= self.fit_amount <= 1:
            raise ConfigurationError("fit_amount must be between 0 and 1")
        if self.fill not in {"black", "blur"}:
            raise ConfigurationError(f"Unknown clip fill {self.fill!r}")
        if self.fill_blur <= 0:
            raise ConfigurationError("fill_blur must be positive")
        if self.fill == "blur" and self.fit not in {"contain", "custom"}:
            raise ConfigurationError("Blur fill requires fit='contain' or fit='custom'")
        if self.speed <= 0:
            raise ConfigurationError("speed must be positive")
        if self.volume < 0:
            raise ConfigurationError("volume cannot be negative")
        if self.transition_duration <= 0:
            raise ConfigurationError("transition_duration must be positive")
        if self.id is not None:
            self.id = self.id.strip()
            if not self.id:
                raise ConfigurationError("Clip id cannot be empty")


@dataclass(slots=True)
class Speaker:
    name: str
    voice: VoiceProvider | None = None
    portrait: Path | str | None = None
    language: str = "en"
    portrait_position: Anchor = "bottom-right"
    portrait_width: int = 420
    portrait_animation: str = "pop"

    def __post_init__(self) -> None:
        if not self.name or any(char.isspace() for char in self.name):
            raise ConfigurationError("Speaker names must be non-empty and contain no spaces")
        if self.portrait is not None:
            self.portrait = Path(self.portrait)
        if self.portrait_width <= 0:
            raise ConfigurationError("portrait_width must be positive")


@dataclass(slots=True)
class Utterance:
    speaker: str
    text: str
    id: str | None = None
    audio: Path | str | None = None
    gap_after: float = 0.15
    start: float | None = None
    end: float | None = None
    words: tuple[WordTiming, ...] = ()

    def __post_init__(self) -> None:
        self.text = self.text.strip()
        if not self.speaker or not self.text:
            raise ConfigurationError("Utterances require a speaker and text")
        if self.audio is not None:
            self.audio = Path(self.audio)
        if self.gap_after < 0:
            raise ConfigurationError("gap_after cannot be negative")


@dataclass(slots=True)
class Script:
    utterances: list[Utterance] = field(default_factory=list)

    def ids(self) -> set[str]:
        return {item.id for item in self.utterances if item.id is not None}


@dataclass(frozen=True, slots=True)
class CaptionTheme:
    name: str = "pop"
    font: str = "DejaVu Sans"
    font_size: int = 82
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE135"
    outline_color: str = "#000000"
    outline_width: int = 7
    shadow: int = 2
    position_y: int = 1310
    max_words: int = 5
    uppercase: bool = False

    def __post_init__(self) -> None:
        if self.font_size <= 0 or self.max_words <= 0:
            raise ConfigurationError("Caption font_size and max_words must be positive")

    @classmethod
    def preset(cls, name: str) -> CaptionTheme:
        presets: dict[str, dict[str, Any]] = {
            "classic": {"font_size": 72, "highlight_color": "#FFFFFF", "outline_width": 5},
            "pop": {},
            "karaoke": {"highlight_color": "#00E5FF", "max_words": 7},
            "bounce": {"highlight_color": "#FF4FD8", "font_size": 88, "max_words": 4},
        }
        if name not in presets:
            raise ConfigurationError(f"Unknown caption preset {name!r}")
        return cls(name=name, **presets[name])


@dataclass(slots=True)
class Overlay:
    source: Path | str
    at: float | None = None
    duration: float | None = None
    during: str | None = None
    speaker: str | None = None
    position: Anchor = "center"
    width: int | None = None
    opacity: float = 1.0
    animation: str = "pop"
    z_index: int = 0

    def __post_init__(self) -> None:
        self.source = Path(self.source)
        if self.at is not None and self.at < 0:
            raise ConfigurationError("Overlay start cannot be negative")
        if self.duration is not None and self.duration <= 0:
            raise ConfigurationError("Overlay duration must be positive")
        selectors = sum(value is not None for value in (self.at, self.during, self.speaker))
        if selectors != 1:
            raise ConfigurationError("Overlay needs exactly one of at, during, or speaker")
        if not 0 <= self.opacity <= 1:
            raise ConfigurationError("Overlay opacity must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class TextOverlay:
    """Styled, timeline-bound text rendered independently from captions."""

    text: str
    at: float | None = None
    duration: float | None = None
    during: str | None = None
    speaker: str | None = None
    during_clip: int | str | None = None
    position: Anchor = "top"
    font: str = "DejaVu Sans"
    font_size: int = 76
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline_width: int = 6
    shadow: int = 2
    bold: bool = True
    uppercase: bool = False
    margin_x: int = 70
    margin_y: int = 160
    z_index: int = 0

    def __post_init__(self) -> None:
        text = self.text.strip()
        if not text:
            raise ConfigurationError("Text overlay content cannot be empty")
        object.__setattr__(self, "text", text)
        if self.at is not None and self.at < 0:
            raise ConfigurationError("Text overlay start cannot be negative")
        if self.duration is not None and self.duration <= 0:
            raise ConfigurationError("Text overlay duration must be positive")
        if self.duration is not None and self.at is None:
            raise ConfigurationError("Text overlay duration can only be used with at")
        selectors = sum(
            value is not None for value in (self.at, self.during, self.speaker, self.during_clip)
        )
        if selectors != 1:
            raise ConfigurationError(
                "Text overlay needs exactly one of at, during, speaker, or during_clip"
            )
        if isinstance(self.during_clip, bool) or (
            isinstance(self.during_clip, int) and self.during_clip < 0
        ):
            raise ConfigurationError("Text overlay clip index cannot be negative")
        if isinstance(self.during_clip, str) and not self.during_clip.strip():
            raise ConfigurationError("Text overlay clip id cannot be empty")
        if isinstance(self.during_clip, str):
            object.__setattr__(self, "during_clip", self.during_clip.strip())
        if self.font_size <= 0:
            raise ConfigurationError("Text overlay font_size must be positive")
        if self.outline_width < 0 or self.shadow < 0:
            raise ConfigurationError("Text overlay outline_width and shadow cannot be negative")
        if self.margin_x < 0 or self.margin_y < 0:
            raise ConfigurationError("Text overlay margins cannot be negative")
        if not self.font.strip() or any(char in self.font for char in ",\r\n"):
            raise ConfigurationError("Text overlay font contains unsupported characters")
        if self.position not in {
            "center",
            "top",
            "bottom",
            "left",
            "right",
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
        }:
            raise ConfigurationError(f"Unknown text overlay position {self.position!r}")
        for name, value in (("color", self.color), ("outline_color", self.outline_color)):
            source = value.removeprefix("#")
            if len(source) != 6 or any(
                char not in "0123456789abcdefABCDEF" for char in source
            ):
                raise ConfigurationError(f"Text overlay {name} must use #RRGGBB format")


@dataclass(frozen=True, slots=True)
class RenderSettings:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    video_bitrate: str = "10M"
    min_video_bitrate: str = "8M"
    max_video_bitrate: str = "12M"
    buffer_size: str = "20M"
    audio_bitrate: str = "192k"
    audio_sample_rate: int = 48_000
    audio_channels: int = 2
    video_encoder: str = "libx264"
    preset: str = "veryfast"
    pixel_format: str = "yuv420p"
    overwrite: bool = False
    captions: bool = True
    caption_sidecar: bool = False
    normalize_audio: bool = False

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ConfigurationError("Canvas dimensions and fps must be positive")
        if self.audio_sample_rate != 48_000 or self.audio_channels != 2:
            raise ConfigurationError("Short-form preset requires 48 kHz stereo audio")


@dataclass(frozen=True, slots=True)
class RenderResult:
    output: Path
    duration: float
    warnings: tuple[str, ...] = ()
    command: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: Path
    duration: float
    width: int | None
    height: int | None
    has_video: bool
    has_audio: bool
    format_name: str = ""
    video_codec: str | None = None
    audio_codec: str | None = None
    pixel_format: str | None = None
    frame_rate: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    bit_rate: int | None = None
