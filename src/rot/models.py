"""Typed public models used throughout rot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from math import isfinite
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

_ANCHORS = {
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
}


@dataclass(frozen=True, slots=True)
class Placement:
    """A normalized point used to place an element on the output canvas.

    Attributes:
        x: Horizontal coordinate from 0 (left) through 1 (right).
        y: Vertical coordinate from 0 (top) through 1 (bottom).
        anchor: Point on the element attached to the coordinate.
    """

    x: float
    y: float
    anchor: Anchor = "center"

    def __post_init__(self) -> None:
        if not isfinite(self.x) or not isfinite(self.y) or not 0 <= self.x <= 1 or not 0 <= self.y <= 1:
            raise ConfigurationError("Placement coordinates must be between 0 and 1")
        if self.anchor not in _ANCHORS:
            raise ConfigurationError(f"Unknown placement anchor {self.anchor!r}")


@dataclass(frozen=True, slots=True)
class NormalizedRect:
    """A rectangle expressed as fractions of a source or output frame.

    Attributes:
        x: Left edge from 0 through 1.
        y: Top edge from 0 through 1.
        width: Positive width that remains inside the frame.
        height: Positive height that remains inside the frame.
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if not all(isfinite(value) for value in values):
            raise ConfigurationError("Normalized rectangle values must be finite")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ConfigurationError(
                "Normalized rectangle x/y must be nonnegative and dimensions must be positive"
            )
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ConfigurationError("Normalized rectangle must remain within the frame")


@dataclass(frozen=True, slots=True)
class Facecam:
    """A crop extracted from a clip and placed back onto its output canvas.

    Attributes:
        crop: Normalized rectangle locating the facecam in the source frame.
        destination: Normalized output rectangle filled by the facecam using cover fitting.
    """

    crop: NormalizedRect
    destination: NormalizedRect

    def __post_init__(self) -> None:
        if not isinstance(self.crop, NormalizedRect) or not isinstance(
            self.destination, NormalizedRect
        ):
            raise ConfigurationError("Facecam crop and destination must be NormalizedRect values")


@dataclass(frozen=True, slots=True)
class _InlineStyle:
    color: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font: str | None = None
    font_size: int | None = None


@dataclass(frozen=True, slots=True)
class _TextRun:
    text: str
    style: _InlineStyle


def _normalize_inline_color(value: str) -> str:
    source = value.removeprefix("#")
    if len(source) == 3 and all(char in "0123456789abcdefABCDEF" for char in source):
        source = "".join(char * 2 for char in source)
    if len(source) != 6 or any(char not in "0123456789abcdefABCDEF" for char in source):
        raise ConfigurationError("Inline text color must use #RGB or #RRGGBB format")
    return f"#{source.upper()}"


def _parse_inline_text(value: str) -> tuple[str, tuple[_TextRun, ...]]:
    """Parse the deliberately small, safe BBCode subset used by rendered text."""

    source = value.strip()
    runs: list[_TextRun] = []
    buffer: list[str] = []
    style = _InlineStyle()
    stack: list[tuple[str, _InlineStyle]] = []

    def flush() -> None:
        if buffer:
            runs.append(_TextRun("".join(buffer), style))
            buffer.clear()

    index = 0
    while index < len(source):
        if source.startswith("[[", index):
            buffer.append("[")
            index += 2
            continue
        if source.startswith("]]", index):
            buffer.append("]")
            index += 2
            continue
        if source[index] != "[":
            buffer.append(source[index])
            index += 1
            continue
        end = source.find("]", index + 1)
        if end < 0:
            raise ConfigurationError("Inline text contains an unclosed tag")
        token = source[index + 1 : end]
        if not token:
            raise ConfigurationError("Inline text contains an empty tag")
        flush()
        if token.startswith("/"):
            name = token[1:]
            if not stack or stack[-1][0] != name:
                raise ConfigurationError(f"Inline text has mismatched closing tag {token!r}")
            _, style = stack.pop()
        else:
            name, separator, raw = token.partition("=")
            previous = style
            if name == "b" and not separator:
                style = _InlineStyle(style.color, True, style.italic, style.underline, style.font, style.font_size)
            elif name == "i" and not separator:
                style = _InlineStyle(style.color, style.bold, True, style.underline, style.font, style.font_size)
            elif name == "u" and not separator:
                style = _InlineStyle(style.color, style.bold, style.italic, True, style.font, style.font_size)
            elif name == "color" and separator:
                style = _InlineStyle(_normalize_inline_color(raw), style.bold, style.italic, style.underline, style.font, style.font_size)
            elif name == "font" and separator:
                font = raw.strip()
                if not font or any(char in font for char in ",\\{}[]\r\n"):
                    raise ConfigurationError("Inline text font contains unsupported characters")
                style = _InlineStyle(style.color, style.bold, style.italic, style.underline, font, style.font_size)
            elif name == "size" and separator:
                try:
                    font_size = int(raw)
                except ValueError as exc:
                    raise ConfigurationError("Inline text size must be a positive integer") from exc
                if font_size <= 0:
                    raise ConfigurationError("Inline text size must be a positive integer")
                style = _InlineStyle(style.color, style.bold, style.italic, style.underline, style.font, font_size)
            else:
                raise ConfigurationError(f"Unknown inline text tag {token!r}")
            stack.append((name, previous))
        index = end + 1
    flush()
    if stack:
        raise ConfigurationError(f"Inline text has unclosed tag {stack[-1][0]!r}")
    plain = "".join(run.text for run in runs)
    if not plain.strip():
        raise ConfigurationError("Rendered text content cannot be empty")
    return plain, tuple(runs)


def _valid_position(value: Anchor | Placement) -> bool:
    return isinstance(value, Placement) or value in _ANCHORS


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
class TranscriptSegment:
    """One timed speech-to-text segment using clip-local output time.

    Attributes:
        text: Plain transcribed text.
        start: Segment start in seconds.
        end: Segment end in seconds.
        words: Optional word-level timings in the same time domain.
    """

    text: str
    start: float
    end: float
    words: tuple[WordTiming, ...] = ()

    def __post_init__(self) -> None:
        text = self.text.strip()
        if not text:
            raise ConfigurationError("Transcript segment text cannot be empty")
        object.__setattr__(self, "text", text)
        if self.start < 0 or self.end <= self.start:
            raise ConfigurationError("Transcript segment timing is invalid")
        if any(word.start < self.start or word.end > self.end for word in self.words):
            raise ConfigurationError("Transcript word timings must remain inside their segment")


@dataclass(frozen=True, slots=True)
class Transcript:
    """Structured speech-to-text output for one non-looped clip pass.

    Attributes:
        segments: Ordered timed transcript segments.
        language: Detected or requested language identifier when known.
    """

    segments: tuple[TranscriptSegment, ...] = ()
    language: str | None = None

    def __post_init__(self) -> None:
        if self.language is not None and not self.language.strip():
            raise ConfigurationError("Transcript language cannot be empty")
        previous_end = 0.0
        for segment in self.segments:
            if segment.start < previous_end:
                raise ConfigurationError("Transcript segments must be ordered and non-overlapping")
            previous_end = segment.end

    @property
    def text(self) -> str:
        """Return segment text joined with spaces."""

        return " ".join(segment.text for segment in self.segments)


@dataclass(frozen=True, slots=True)
class ClipTranscript:
    """A transcript associated with one clip in a project.

    Attributes:
        clip_index: Zero-based project clip index.
        clip_id: Optional stable clip identifier.
        source: Resolved clip source path.
        transcript: Clip-local transcript for one rendered source pass.
    """

    clip_index: int
    clip_id: str | None
    source: Path
    transcript: Transcript

    def __post_init__(self) -> None:
        if self.clip_index < 0:
            raise ConfigurationError("Transcript clip index cannot be negative")

    @property
    def text(self) -> str:
        """Return the transcript's plain text."""

        return self.transcript.text


@dataclass(frozen=True, slots=True)
class ClipTranscription:
    """Per-clip speech-to-text options.

    Attributes:
        language: Optional language identifier; ``None`` enables provider detection.
    """

    language: str | None = None

    def __post_init__(self) -> None:
        if self.language is not None:
            language = self.language.strip()
            if not language:
                raise ConfigurationError("Transcription language cannot be empty")
            object.__setattr__(self, "language", language)


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
class Transcriber(Protocol):
    """Interface for converting clip audio into timed text."""

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: StageProgressCallback | None = None,
    ) -> Transcript:
        """Transcribe one clip-local audio pass.

        Args:
            audio_path: Prepared audio path.
            language: Optional language identifier or ``None`` for detection.
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
        facecam: Optional source crop and output destination for an extracted facecam.
        focus: Optional normalized source point retained by cropping.
        position: Optional normalized placement of the fitted foreground.
        transcribe: Whether and how to transcribe this clip's audio.
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
    facecam: Facecam | None = None
    focus: tuple[float, float] | None = None
    position: Placement | None = None
    transcribe: bool | ClipTranscription = False

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
        if self.facecam is not None and self.fit != "custom":
            raise ConfigurationError("Facecam extraction requires fit='custom'")
        if self.focus is not None:
            if self.fit not in {"cover", "custom"}:
                raise ConfigurationError("Clip focus requires fit='cover' or fit='custom'")
            if len(self.focus) != 2 or not all(
                isfinite(value) and 0 <= value <= 1 for value in self.focus
            ):
                raise ConfigurationError("Clip focus coordinates must be between 0 and 1")
        if self.position is not None and self.fit not in {"contain", "custom"}:
            raise ConfigurationError("Clip position requires fit='contain' or fit='custom'")
        if not isinstance(self.transcribe, (bool, ClipTranscription)):
            raise ConfigurationError("Clip transcribe must be a boolean or ClipTranscription")
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
        portrait_position: Named canvas anchor or normalized placement.
        portrait_width: Portrait width in pixels.
        portrait_animation: Portrait entrance animation.
    """

    name: str
    voice: VoiceProvider | None = None
    portrait: Path | str | None = None
    language: str = "en"
    portrait_position: Anchor | Placement = "bottom-right"
    portrait_width: int = 420
    portrait_animation: str = "pop"

    def __post_init__(self) -> None:
        if not self.name or any(char.isspace() for char in self.name):
            raise ConfigurationError("Speaker names must be non-empty and contain no spaces")
        if self.portrait is not None:
            self.portrait = Path(self.portrait)
        if self.portrait_width <= 0:
            raise ConfigurationError("portrait_width must be positive")
        if not _valid_position(self.portrait_position):
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
        styled_runs: Parsed inline formatting runs used by the built-in caption renderer.
    """

    speaker: str
    text: str
    id: str | None = None
    audio: Path | str | None = None
    gap_after: float = 0.15
    start: float | None = None
    end: float | None = None
    words: tuple[WordTiming, ...] = ()
    styled_runs: tuple[_TextRun, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        text, runs = _parse_inline_text(self.text)
        self.text = text
        self.styled_runs = runs
        if not self.speaker:
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
        position: Optional normalized canvas placement overriding ``position_y``.
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
    position: Placement | None = None
    max_words: int = 5
    uppercase: bool = False

    def __post_init__(self) -> None:
        if self.font_size <= 0 or self.max_words <= 0:
            raise ConfigurationError("Caption font_size and max_words must be positive")
        if self.position is not None and not isinstance(self.position, Placement):
            raise ConfigurationError("Caption position must be a Placement")

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
        position: Named canvas anchor or normalized placement.
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
    position: Anchor | Placement = "center"
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
        if not _valid_position(self.position):
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
        position: Named canvas anchor or normalized placement.
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
        styled_runs: Parsed inline formatting runs used by the built-in renderer.
    """

    text: str
    at: float | None = None
    duration: float | None = None
    during: str | None = None
    speaker: str | None = None
    during_clip: int | str | None = None
    position: Anchor | Placement = "top"
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
    styled_runs: tuple[_TextRun, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        text, runs = _parse_inline_text(self.text)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "styled_runs", runs)
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
        if not _valid_position(self.position):
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
        transcripts: Clip transcripts used by the render.
    """

    output: Path
    duration: float
    warnings: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    transcripts: tuple[ClipTranscript, ...] = ()


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
