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
    """A render-stage progress update.

    Attributes:
        stage: Stable stage name such as ``speech`` or ``render``.
        completed: Completed units within the stage.
        total: Total stage units.
        message: Human-readable status message.
    """

    stage: str
    completed: float
    total: float = 1.0
    message: str = ""

    @property
    def fraction(self) -> float:
        """Return completed progress clamped to the inclusive 0-to-1 range."""

        return 0.0 if self.total <= 0 else min(1.0, max(0.0, self.completed / self.total))


ProgressCallback = Callable[[ProgressEvent], None]
StageProgressCallback = Callable[[str, float, float, str], None]


@dataclass(frozen=True, slots=True)
class WordTiming:
    """Timing for one displayed word.

    Attributes:
        text: Displayed word text.
        start: Absolute start time in seconds.
        end: Absolute end time in seconds.
    """

    text: str
    start: float
    end: float

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ConfigurationError(f"Invalid word timing for {self.text!r}")


@dataclass(frozen=True, slots=True)
class SynthesizedAudio:
    """Audio returned by a voice provider.

    Attributes:
        path: Generated audio file.
        duration: Optional known duration in seconds.
    """

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
    ) -> SynthesizedAudio:
        """Generate speech audio.

        Args:
            text: Text to speak.
            output_path: Requested destination file.
            language: Speaker language identifier.
            progress: Optional stage progress callback.
        """

        ...


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
    ) -> tuple[WordTiming, ...]:
        """Align known text to an audio file.

        Args:
            audio_path: Speech audio path.
            text: Known transcript.
            language: Transcript language identifier.
            progress: Optional stage progress callback.
        """

        ...


@runtime_checkable
class ScriptParser(Protocol):
    """Interface for converting source text into a validated dialogue script."""

    def parse(self, source: str) -> Script:
        """Parse source text into a Script.

        Args:
            source: Parser-specific source text.
        """

        ...


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
    ) -> Path:
        """Write an ASS-compatible caption sidecar.

        Args:
            path: Requested output path.
            utterances: Timed dialogue lines.
            theme: Caption styling.
            width: Output canvas width.
            height: Output canvas height.
        """

        ...


@dataclass(frozen=True, slots=True)
class FilterNode:
    """A safe FFmpeg filter description used by custom effect plugins.

    Attributes:
        name: FFmpeg filter name.
        arguments: Ordered filter option name/value pairs.
    """

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
    """Interface for a validated visual-effect provider."""

    @property
    def name(self) -> str:
        """Return the effect's stable display name."""

        ...

    def filters(self, *, duration: float, width: int, height: int) -> tuple[FilterNode, ...]:
        """Return validated filters for a render.

        Args:
            duration: Effected timeline duration.
            width: Output width.
            height: Output height.
        """

        ...


@dataclass(frozen=True, slots=True)
class EffectSpec:
    """Serializable effect name and options.

    Attributes:
        name: Built-in effect name.
        options: Sorted effect option name/value pairs.
    """

    name: str
    options: tuple[tuple[str, str | int | float], ...] = ()

    @classmethod
    def create(cls, name: str, **options: str | int | float) -> EffectSpec:
        """Create a deterministic specification from keyword options.

        Args:
            name: Effect name.
            **options: Effect-specific option values.
        """

        return cls(name=name, options=tuple(sorted(options.items())))


@dataclass(slots=True)
class Clip:
    """A video or still-image source on the primary background track.

    Attributes:
        source: Source media path.
        trim_start: Source start time in seconds.
        trim_end: Optional source end time in seconds.
        duration: Optional rendered duration.
        loop: Whether a short video repeats.
        fit: ``cover``, ``contain``, ``custom``, or ``stretch``.
        anchor: Crop and placement anchor.
        keep_audio: Whether source audio is mixed into the output.
        volume: Source-audio gain.
        speed: Playback-speed multiplier.
        effects: Effects applied only to this clip.
        transition: Transition from this clip to its successor.
        transition_duration: Transition overlap in seconds.
        id: Stable identifier used by timeline selectors.
        fit_amount: Custom-fit interpolation from contain to cover.
        fill: ``black`` or ``blur`` letterbox fill.
        fill_blur: Blurred-fill strength.
    """

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
        if self.anchor not in {
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
            raise ConfigurationError(f"Unknown clip anchor {self.anchor!r}")
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


def transition_overlap(clip: Clip, duration: float, next_duration: float) -> float:
    """Return the seconds ``clip`` shares with the clip that follows it.

    A transition consumes this many seconds from the tail of ``clip`` and the head of its
    successor *simultaneously*, so the pair occupies ``duration + next_duration - overlap``
    on the timeline. Each clip's own length caps the overlap at half, which guarantees that
    the transitions on a clip's two ends can never together consume more than the clip
    itself.

    This is the single owner of the arithmetic. The visual layout, the audio delay cursor,
    and the xfade offset must all derive from it; deriving any of them independently is how
    the video track and the audio track drift apart.
    """

    if clip.transition == "cut":
        return 0.0
    return min(clip.transition_duration, duration / 2, next_duration / 2)


@dataclass(slots=True)
class Speaker:
    """Dialogue speaker configuration.

    Attributes:
        name: Unique script speaker name.
        voice: Optional text-to-speech provider.
        portrait: Optional static portrait image.
        language: Provider language identifier.
        portrait_position: Portrait canvas anchor.
        portrait_width: Portrait width in pixels.
        portrait_animation: Portrait entrance animation.
    """

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
        if self.portrait_position not in {
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
            raise ConfigurationError(f"Unknown portrait position {self.portrait_position!r}")
        if self.portrait_animation not in {"none", "pop", "fade", "slide", "bounce"}:
            raise ConfigurationError(
                f"Unknown portrait animation {self.portrait_animation!r}"
            )


@dataclass(slots=True)
class Utterance:
    """One ordered line of dialogue.

    Attributes:
        speaker: Registered speaker name.
        text: Spoken and captioned text.
        id: Optional stable line identifier.
        audio: Optional prerecorded audio path.
        gap_after: Silence after the line in seconds.
        start: Resolved absolute start time.
        end: Resolved absolute end time.
        words: Resolved word timings.
    """

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
    """An ordered collection of dialogue utterances.

    Attributes:
        utterances: Lines in playback order.
    """

    utterances: list[Utterance] = field(default_factory=list)

    def ids(self) -> set[str]:
        """Return all non-null utterance identifiers."""

        return {item.id for item in self.utterances if item.id is not None}


@dataclass(frozen=True, slots=True)
class CaptionTheme:
    """Styling and grouping for burned-in dialogue captions.

    Attributes:
        name: Theme name controlling built-in animation.
        font: Fontconfig font family.
        font_size: Font size in output pixels.
        primary_color: Inactive word color in ``#RRGGBB`` form.
        highlight_color: Active word color in ``#RRGGBB`` form.
        outline_color: Outline color in ``#RRGGBB`` form.
        outline_width: Outline thickness in pixels.
        shadow: Shadow depth in pixels.
        position_y: Baseline position in pixels.
        max_words: Maximum words displayed in one group.
        uppercase: Whether captions are uppercased.
    """

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
        """Load ``classic``, ``pop``, ``karaoke``, or ``bounce`` styling.

        Args:
            name: Built-in preset name.
        """

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
    """A static image displayed over selected portions of the video timeline.

    Attributes:
        source: Static image path.
        at: Optional absolute start time.
        duration: Optional absolute duration; defaults to two seconds during preparation.
        during: Optional dialogue line identifier.
        speaker: Optional speaker selector.
        during_clip: Optional clip ID or zero-based index.
        position: Canvas anchor.
        width: Rendered width or the default 560 pixels.
        opacity: Alpha multiplier from 0 through 1.
        animation: Entrance/exit animation.
        z_index: Ordering among image overlays.
    """

    source: Path | str
    at: float | None = None
    duration: float | None = None
    during: str | None = None
    speaker: str | None = None
    during_clip: int | str | None = None
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
        if self.duration is not None and self.at is None:
            raise ConfigurationError("Overlay duration can only be used with at")
        selectors = sum(
            value is not None
            for value in (self.at, self.during, self.speaker, self.during_clip)
        )
        if selectors != 1:
            raise ConfigurationError(
                "Overlay needs exactly one of at, during, speaker, or during_clip"
            )
        if isinstance(self.during_clip, bool) or (
            isinstance(self.during_clip, int) and self.during_clip < 0
        ):
            raise ConfigurationError("Overlay clip index cannot be negative")
        if isinstance(self.during_clip, str):
            self.during_clip = self.during_clip.strip()
            if not self.during_clip:
                raise ConfigurationError("Overlay clip id cannot be empty")
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
            raise ConfigurationError(f"Unknown overlay position {self.position!r}")
        if self.width is not None and self.width <= 0:
            raise ConfigurationError("Overlay width must be positive")
        if not 0 <= self.opacity <= 1:
            raise ConfigurationError("Overlay opacity must be between 0 and 1")
        if self.animation not in {"none", "pop", "fade", "slide", "bounce"}:
            raise ConfigurationError(f"Unknown overlay animation {self.animation!r}")


@dataclass(frozen=True, slots=True)
class Soundtrack:
    """A single background-music bed mixed beneath a project.

    ``trim_start`` and ``trim_end`` select the source segment. When ``loop`` is true, only
    that segment repeats. Fades are measured on the rendered timeline, and ``ducking``
    enables smooth sidechain compression under dialogue.

    Attributes:
        source: Audio-bearing media path.
        volume: Nonnegative gain.
        trim_start: Selected source start in seconds.
        trim_end: Optional selected source end in seconds.
        loop: Whether the selected segment repeats.
        fade_in: Fade-in duration in seconds.
        fade_out: Fade-out duration in seconds.
        ducking: Whether dialogue sidechain-compresses the music.
    """

    source: Path | str
    volume: float = 0.15
    trim_start: float = 0.0
    trim_end: float | None = None
    loop: bool = True
    fade_in: float = 0.0
    fade_out: float = 0.0
    ducking: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", Path(self.source))
        if self.volume < 0:
            raise ConfigurationError("Soundtrack volume cannot be negative")
        if self.trim_start < 0:
            raise ConfigurationError("Soundtrack trim start cannot be negative")
        if self.trim_end is not None and self.trim_end <= self.trim_start:
            raise ConfigurationError("Soundtrack trim end must be greater than trim start")
        if self.fade_in < 0 or self.fade_out < 0:
            raise ConfigurationError("Soundtrack fades cannot be negative")


@dataclass(frozen=True, slots=True)
class TextOverlay:
    """Styled, timeline-bound text rendered independently from captions.

    Attributes:
        text: Display text.
        at: Optional absolute start time.
        duration: Optional absolute duration; omitted means until video end.
        during: Optional dialogue line identifier.
        speaker: Optional speaker selector.
        during_clip: Optional clip ID or zero-based index.
        position: Canvas anchor.
        font: Fontconfig font family.
        font_size: Font size in pixels.
        color: Text color in ``#RRGGBB`` form.
        outline_color: Outline color in ``#RRGGBB`` form.
        outline_width: Outline thickness in pixels.
        shadow: Shadow depth in pixels.
        bold: Whether to use bold text.
        uppercase: Whether to uppercase the display text.
        margin_x: Horizontal safe-area margin.
        margin_y: Vertical safe-area margin.
        z_index: Ordering among text overlays.
    """

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
    """Output encoding and caption settings.

    Attributes:
        width: Output width in pixels.
        height: Output height in pixels.
        fps: Constant output frame rate.
        video_bitrate: Target video bitrate.
        min_video_bitrate: Minimum video rate target.
        max_video_bitrate: Maximum video rate target.
        buffer_size: Encoder rate-control buffer size.
        audio_bitrate: AAC bitrate.
        audio_sample_rate: Output sample rate; fixed at 48 kHz.
        audio_channels: Output channel count; fixed at stereo.
        video_encoder: FFmpeg H.264 encoder name.
        preset: Encoder speed/quality preset.
        pixel_format: Output pixel format.
        overwrite: Default output replacement policy.
        captions: Whether to burn dialogue captions.
        caption_sidecar: Whether to also write SRT.
        normalize_audio: Whether to apply EBU-style loudness normalization.
    """

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
    """Metadata from a completed render.

    Attributes:
        output: Final output path.
        duration: Output duration in seconds.
        warnings: Nonfatal render warnings.
        command: Executed FFmpeg argument vector.
    """

    output: Path
    duration: float
    warnings: tuple[str, ...] = ()
    command: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """FFprobe metadata for a media asset.

    Attributes:
        path: Resolved media path.
        duration: Duration in seconds, or zero for a still image.
        width: Video width when present.
        height: Video height when present.
        has_video: Whether the asset has a video stream.
        has_audio: Whether the asset has an audio stream.
        format_name: Container or demuxer format.
        video_codec: Video codec name.
        audio_codec: Audio codec name.
        pixel_format: Video pixel format.
        frame_rate: Average video frame rate.
        sample_rate: Audio sample rate.
        channels: Audio channel count.
        color_primaries: Video color primaries.
        color_transfer: Video transfer characteristic.
        color_space: Video matrix coefficients.
        bit_rate: Overall bitrate when reported.
    """

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
