"""The fluent project-building API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .captions import AssCaptionRenderer
from .effects import OVERLAY_ANIMATIONS, TRANSITIONS, normalize_effect
from .errors import ConfigurationError
from .models import (
    CaptionRenderer,
    CaptionTheme,
    Clip,
    ClipTranscript,
    ClipTranscription,
    Effect,
    EffectSpec,
    Facecam,
    Overlay,
    Placement,
    ProgressCallback,
    RenderResult,
    RenderSettings,
    Script,
    ScriptParser,
    Soundtrack,
    Speaker,
    TextOverlay,
    Transcriber,
    WordAligner,
)
from .script import RotScriptParser


class Project:
    """A mutable fluent builder for one short-form video.

    Args:
        settings: Output and encoding settings. Defaults to the short-form preset.
    """

    def __init__(self, *, settings: RenderSettings | None = None) -> None:
        self.settings = settings or RenderSettings()
        self.clips: list[Clip] = []
        self.speakers: dict[str, Speaker] = {}
        self.script_data: Script | None = None
        self.caption_theme = CaptionTheme.preset("pop")
        self.clip_caption_theme = CaptionTheme(
            name="pop",
            position=Placement(0.5, 0.08, anchor="top"),
        )
        self.caption_renderer: CaptionRenderer = AssCaptionRenderer()
        self.overlays: list[Overlay] = []
        self.text_overlays: list[TextOverlay] = []
        self.global_effects: list[Effect] = []
        self.aligner: WordAligner | None = None
        self.transcriber: Transcriber | None = None
        self.music: Soundtrack | None = None

    @classmethod
    def short_form(cls) -> Project:
        """Create a project using the standard vertical-video output contract."""

        return cls(settings=RenderSettings())

    def background(
        self,
        source: str | Path | Clip,
        *,
        trim: tuple[float, float | None] | None = None,
        duration: float | None = None,
        loop: bool = True,
        fit: str = "cover",
        fit_amount: float = 0.5,
        fill: str = "black",
        fill_blur: float = 40.0,
        facecam: Facecam | None = None,
        focus: tuple[float, float] | None = None,
        position: Placement | None = None,
        transcribe: bool | ClipTranscription = False,
        anchor: str = "center",
        keep_audio: bool = False,
        volume: float = 1.0,
        speed: float = 1.0,
        clip_id: str | None = None,
    ) -> Project:
        """Replace the visual track with its first video or still-image source.

        Args:
            source: Media path or an existing :class:`Clip`.
            trim: Optional source start and end times in seconds.
            duration: Rendered clip duration. Required for a still without dialogue.
            loop: Repeat a short video to fill its requested duration.
            fit: Canvas fitting mode: ``cover``, ``contain``, ``custom``, or ``stretch``.
            fit_amount: Custom-fit interpolation from contain (0) to cover (1).
            fill: Letterbox treatment, either ``black`` or ``blur``.
            fill_blur: Blur radius for a blurred fill.
            facecam: Optional crop extracted and placed over a custom-fit clip.
            focus: Optional normalized source focal point for cropping.
            position: Optional normalized placement for a contain/custom foreground.
            transcribe: Opt into clip speech-to-text, optionally with per-clip settings.
            anchor: Crop or placement anchor.
            keep_audio: Mix the source audio into the output.
            volume: Source-audio gain.
            speed: Playback-speed multiplier.
            clip_id: Optional stable identifier for timeline selectors.

        Returns:
            This project for fluent chaining.
        """

        self.clips.clear()
        return self.add_clip(
            source,
            trim=trim,
            duration=duration,
            loop=loop,
            fit=fit,
            fit_amount=fit_amount,
            fill=fill,
            fill_blur=fill_blur,
            facecam=facecam,
            focus=focus,
            position=position,
            transcribe=transcribe,
            anchor=anchor,
            keep_audio=keep_audio,
            volume=volume,
            speed=speed,
            clip_id=clip_id,
        )

    def add_clip(
        self,
        source: str | Path | Clip,
        *,
        trim: tuple[float, float | None] | None = None,
        duration: float | None = None,
        loop: bool = True,
        fit: str = "cover",
        fit_amount: float = 0.5,
        fill: str = "black",
        fill_blur: float = 40.0,
        facecam: Facecam | None = None,
        focus: tuple[float, float] | None = None,
        position: Placement | None = None,
        transcribe: bool | ClipTranscription = False,
        anchor: str = "center",
        keep_audio: bool = False,
        volume: float = 1.0,
        speed: float = 1.0,
        transition: str = "cut",
        transition_duration: float = 0.3,
        clip_id: str | None = None,
    ) -> Project:
        """Append a video or still image to the primary visual track.

        Args:
            source: Media path or an existing :class:`Clip`.
            trim: Optional source start and end times in seconds.
            duration: Rendered duration; required for stills in multi-clip projects.
            loop: Repeat a short video to fill its requested duration.
            fit: Canvas fitting mode.
            fit_amount: Custom-fit interpolation from contain (0) to cover (1).
            fill: Letterbox treatment, either ``black`` or ``blur``.
            fill_blur: Blur radius for a blurred fill.
            facecam: Optional crop extracted and placed over a custom-fit clip.
            focus: Optional normalized source focal point for cropping.
            position: Optional normalized placement for a contain/custom foreground.
            transcribe: Opt into clip speech-to-text, optionally with per-clip settings.
            anchor: Crop or placement anchor.
            keep_audio: Mix the source audio into the output.
            volume: Source-audio gain.
            speed: Playback-speed multiplier.
            transition: Transition from the preceding clip into this clip.
            transition_duration: Transition overlap in seconds.
            clip_id: Optional stable identifier for timeline selectors.

        Returns:
            This project for fluent chaining.

        Raises:
            ConfigurationError: If transition options or identifiers are invalid.
        """

        if transition not in TRANSITIONS:
            raise ConfigurationError(f"Unknown transition {transition!r}")
        if transition_duration <= 0:
            raise ConfigurationError("Transition duration must be positive")
        if self.clips and transition != "cut":
            self.clips[-1].transition = transition
            self.clips[-1].transition_duration = transition_duration
        if isinstance(source, Clip):
            clip = source
            if clip_id is not None:
                clip.id = clip_id.strip()
                if not clip.id:
                    raise ConfigurationError("Clip id cannot be empty")
        else:
            start, end = trim or (0.0, None)
            clip = Clip(
                source,
                trim_start=start,
                trim_end=end,
                duration=duration,
                loop=loop,
                fit=fit,  # type: ignore[arg-type]
                fit_amount=fit_amount,
                fill=fill,  # type: ignore[arg-type]
                fill_blur=fill_blur,
                facecam=facecam,
                focus=focus,
                position=position,
                transcribe=transcribe,
                anchor=anchor,  # type: ignore[arg-type]
                keep_audio=keep_audio,
                volume=volume,
                speed=speed,
                transition="cut",
                transition_duration=transition_duration,
                id=clip_id,
            )
        if clip.transition not in TRANSITIONS:
            raise ConfigurationError(f"Unknown transition {clip.transition!r}")
        self.clips.append(clip)
        return self

    def overlay_text(
        self,
        text: str,
        *,
        at: float | None = None,
        duration: float | None = None,
        during: str | None = None,
        speaker: str | None = None,
        during_clip: int | str | None = None,
        position: str | Placement = "top",
        font: str = "DejaVu Sans",
        font_size: int = 76,
        color: str = "#FFFFFF",
        outline_color: str = "#000000",
        outline_width: int = 6,
        shadow: int = 2,
        bold: bool = True,
        uppercase: bool = False,
        margin_x: int = 70,
        margin_y: int = 160,
        z_index: int = 0,
    ) -> Project:
        """Add styled text independently from dialogue captions.

        Args:
            text: Text to display.
            at: Absolute start time in seconds.
            duration: Duration for an absolute-time overlay; omitted means until video end.
            during: Dialogue line identifier to follow.
            speaker: Speaker whose utterances should show the text.
            during_clip: Clip identifier or zero-based clip index to follow.
            position: Named canvas anchor or normalized :class:`Placement`.
            font: Fontconfig font family.
            font_size: Font size in output pixels.
            color: Text color in ``#RRGGBB`` form.
            outline_color: Outline color in ``#RRGGBB`` form.
            outline_width: Outline thickness in pixels.
            shadow: Shadow depth in pixels.
            bold: Use the font's bold weight.
            uppercase: Convert displayed text to uppercase.
            margin_x: Horizontal safe-area margin in pixels.
            margin_y: Vertical safe-area margin in pixels.
            z_index: Ordering relative to other text overlays.

        Returns:
            This project for fluent chaining.
        """

        self.text_overlays.append(
            TextOverlay(
                text,
                at=at,
                duration=duration,
                during=during,
                speaker=speaker,
                during_clip=during_clip,
                position=position,  # type: ignore[arg-type]
                font=font,
                font_size=font_size,
                color=color,
                outline_color=outline_color,
                outline_width=outline_width,
                shadow=shadow,
                bold=bold,
                uppercase=uppercase,
                margin_x=margin_x,
                margin_y=margin_y,
                z_index=z_index,
            )
        )
        return self

    def transition(self, name: str, *, duration: float = 0.3) -> Project:
        """Set the transition from the latest clip to the clip that follows it.

        Args:
            name: ``cut``, ``fade``, ``crossfade``, ``slide-left``, ``slide-right``, or ``zoom``.
            duration: Transition overlap in seconds.

        Returns:
            This project for fluent chaining.

        Raises:
            ConfigurationError: If no clip exists or the transition is invalid.
        """

        if not self.clips:
            raise ConfigurationError("Add a clip before setting its transition")
        if name not in TRANSITIONS:
            raise ConfigurationError(f"Unknown transition {name!r}")
        if duration <= 0:
            raise ConfigurationError("Transition duration must be positive")
        self.clips[-1].transition = name
        self.clips[-1].transition_duration = duration
        return self

    def add_speaker(
        self,
        name: str,
        *,
        voice: Any = None,
        portrait: str | Path | None = None,
        language: str = "en",
        portrait_position: str | Placement = "bottom-right",
        portrait_width: int = 420,
        portrait_animation: str = "pop",
    ) -> Project:
        """Register a dialogue speaker and optional animated portrait.

        Args:
            name: Unique whitespace-free script speaker name.
            voice: Voice provider used when a line has no prerecorded audio.
            portrait: Static image shown while this speaker talks.
            language: Language identifier passed to voice and alignment providers.
            portrait_position: Named portrait anchor or normalized :class:`Placement`.
            portrait_width: Portrait width in output pixels.
            portrait_animation: ``none``, ``pop``, ``fade``, ``slide``, or ``bounce``.

        Returns:
            This project for fluent chaining.
        """

        if name in self.speakers:
            raise ConfigurationError(f"Speaker {name!r} is already registered")
        if portrait_animation not in OVERLAY_ANIMATIONS:
            raise ConfigurationError(f"Unknown portrait animation {portrait_animation!r}")
        self.speakers[name] = Speaker(
            name,
            voice=voice,
            portrait=portrait,
            language=language,
            portrait_position=portrait_position,  # type: ignore[arg-type]
            portrait_width=portrait_width,
            portrait_animation=portrait_animation,
        )
        return self

    def script(self, source: str, *, parser: ScriptParser | None = None) -> Project:
        """Parse dialogue source text and attach it to the project.

        Args:
            source: Parser-specific script text.
            parser: Custom parser; defaults to :class:`RotScriptParser`.

        Returns:
            This project for fluent chaining.
        """

        self.script_data = (parser or RotScriptParser()).parse(source)
        return self

    def script_file(self, path: str | Path, *, parser: ScriptParser | None = None) -> Project:
        """Load and parse a UTF-8 dialogue file.

        Args:
            path: Script file path.
            parser: Custom parser; defaults to :class:`RotScriptParser`.

        Returns:
            This project for fluent chaining.
        """

        selected = parser or RotScriptParser()
        if hasattr(selected, "parse_file"):
            self.script_data = selected.parse_file(path)
        else:
            self.script_data = selected.parse(Path(path).read_text(encoding="utf-8"))
        return self

    def captions(self, theme: str | CaptionTheme = "pop", **overrides: Any) -> Project:
        """Configure burned-in dialogue captions.

        Args:
            theme: Preset name or complete caption theme.
            **overrides: CaptionTheme fields that override the selected theme.

        Returns:
            This project for fluent chaining.
        """

        base = CaptionTheme.preset(theme) if isinstance(theme, str) else theme
        if overrides:
            values = {field: getattr(base, field) for field in base.__dataclass_fields__}
            values.update(overrides)
            base = CaptionTheme(**values)
        self.caption_theme = base
        return self

    def overlay_image(
        self,
        source: str | Path,
        *,
        at: float | None = None,
        duration: float | None = None,
        during: str | None = None,
        speaker: str | None = None,
        during_clip: int | str | None = None,
        position: str | Placement = "center",
        width: int | None = None,
        opacity: float = 1.0,
        animation: str = "pop",
        z_index: int = 0,
    ) -> Project:
        """Add a static image overlay bound to one timeline selector.

        Args:
            source: FFmpeg-decodable static image path.
            at: Absolute start time in seconds.
            duration: Absolute overlay duration; defaults to two seconds.
            during: Dialogue line identifier to follow.
            speaker: Speaker whose utterances should show the image.
            during_clip: Clip identifier or zero-based clip index to follow.
            position: Named canvas anchor or normalized :class:`Placement`.
            width: Rendered width in pixels; defaults to 560.
            opacity: Alpha multiplier from 0 through 1.
            animation: ``none``, ``pop``, ``fade``, ``slide``, or ``bounce``.
            z_index: Ordering relative to other image overlays.

        Returns:
            This project for fluent chaining.
        """

        if animation not in OVERLAY_ANIMATIONS:
            raise ConfigurationError(f"Unknown overlay animation {animation!r}")
        self.overlays.append(
            Overlay(
                source,
                at=at,
                duration=duration,
                during=during,
                speaker=speaker,
                during_clip=during_clip,
                position=position,  # type: ignore[arg-type]
                width=width,
                opacity=opacity,
                animation=animation,
                z_index=z_index,
            )
        )
        return self

    def effect(self, effect: str | EffectSpec | Effect, **options: str | int | float) -> Project:
        """Apply a built-in or custom effect to the finished visual track.

        Args:
            effect: Built-in name, effect specification, or Effect implementation.
            **options: Effect-specific numeric or string options.

        Returns:
            This project for fluent chaining.
        """

        self.global_effects.append(normalize_effect(effect, **options))
        return self

    def soundtrack(
        self,
        source: str | Path,
        *,
        volume: float = 0.15,
        trim: tuple[float, float | None] | None = None,
        loop: bool = True,
        fade_in: float = 0.0,
        fade_out: float = 0.0,
        ducking: bool = False,
    ) -> Project:
        """Configure the single background-music bed.

        Calling this method again replaces the previous soundtrack.

        Args:
            source: Audio-bearing media path.
            volume: Nonnegative music gain.
            trim: Optional source start and end times in seconds.
            loop: Repeat only the selected source segment to fill the video.
            fade_in: Fade-in duration in seconds.
            fade_out: Fade-out duration at the effective end of the bed.
            ducking: Smoothly sidechain-compress music under dialogue.

        Returns:
            This project for fluent chaining.
        """

        start, end = trim or (0.0, None)
        self.music = Soundtrack(
            source,
            volume=volume,
            trim_start=start,
            trim_end=end,
            loop=loop,
            fade_in=fade_in,
            fade_out=fade_out,
            ducking=ducking,
        )
        return self

    def with_aligner(self, aligner: WordAligner) -> Project:
        """Use a known-transcript word aligner for caption timing.

        Args:
            aligner: WordAligner implementation.

        Returns:
            This project for fluent chaining.
        """

        self.aligner = aligner
        return self

    def with_transcriber(self, transcriber: Transcriber) -> Project:
        """Use a custom speech-to-text provider for opted-in clips.

        Args:
            transcriber: Provider implementing :class:`Transcriber`.

        Returns:
            This project for fluent chaining.
        """

        self.transcriber = transcriber
        return self

    def clip_captions(
        self, theme: str | CaptionTheme = "pop", **overrides: Any
    ) -> Project:
        """Configure captions generated from opted-in clip audio.

        Args:
            theme: Preset name or complete caption theme.
            **overrides: CaptionTheme fields that override the selected theme.

        Returns:
            This project for fluent chaining.
        """

        if isinstance(theme, str):
            selected = CaptionTheme.preset(theme)
            values = {
                field: getattr(selected, field) for field in selected.__dataclass_fields__
            }
            if "position" not in overrides:
                values["position"] = self.clip_caption_theme.position
            base = CaptionTheme(**values)
        else:
            base = theme
        if overrides:
            values = {field: getattr(base, field) for field in base.__dataclass_fields__}
            values.update(overrides)
            base = CaptionTheme(**values)
        self.clip_caption_theme = base
        return self

    def transcribe_clips(
        self, *, progress: bool | ProgressCallback = True
    ) -> tuple[ClipTranscript, ...]:
        """Transcribe every opted-in clip and return cached clip-local results.

        Args:
            progress: Rich progress display, callback, or ``False``.

        Returns:
            One structured result per opted-in clip, in project order.
        """

        from .render import Renderer

        return Renderer(self).transcribe_clips(progress=progress)

    def with_caption_renderer(self, renderer: CaptionRenderer) -> Project:
        """Replace the default ASS caption renderer.

        Args:
            renderer: CaptionRenderer implementation.

        Returns:
            This project for fluent chaining.
        """

        self.caption_renderer = renderer
        return self

    def render(
        self,
        output: str | Path,
        *,
        progress: bool | ProgressCallback = True,
        overwrite: bool | None = None,
        keep_workdir: bool = False,
    ) -> RenderResult:
        """Validate, encode, and atomically write the project.

        Args:
            output: Destination ``.mp4`` path.
            progress: Rich progress display, callback, or ``False``.
            overwrite: Permit replacing output and sidecar files.
            keep_workdir: Preserve temporary render files for debugging.

        Returns:
            Render metadata including the output path and FFmpeg command.
        """

        from .render import Renderer

        return Renderer(self).render(
            Path(output),
            progress=progress,
            overwrite=self.settings.overwrite if overwrite is None else overwrite,
            keep_workdir=keep_workdir,
        )
