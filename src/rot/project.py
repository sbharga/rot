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
    Effect,
    EffectSpec,
    Overlay,
    ProgressCallback,
    RenderResult,
    RenderSettings,
    Script,
    ScriptParser,
    Speaker,
    TextOverlay,
    WordAligner,
)
from .script import RotScriptParser


class Project:
    """A mutable fluent builder for one short-form video."""

    def __init__(self, *, settings: RenderSettings | None = None) -> None:
        self.settings = settings or RenderSettings()
        self.clips: list[Clip] = []
        self.speakers: dict[str, Speaker] = {}
        self.script_data: Script | None = None
        self.caption_theme = CaptionTheme.preset("pop")
        self.caption_renderer: CaptionRenderer = AssCaptionRenderer()
        self.overlays: list[Overlay] = []
        self.text_overlays: list[TextOverlay] = []
        self.global_effects: list[Effect] = []
        self.aligner: WordAligner | None = None
        self.music: Path | None = None
        self.music_volume: float = 0.15

    @classmethod
    def short_form(cls) -> Project:
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
        anchor: str = "center",
        keep_audio: bool = False,
        volume: float = 1.0,
        speed: float = 1.0,
        clip_id: str | None = None,
    ) -> Project:
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
        anchor: str = "center",
        keep_audio: bool = False,
        volume: float = 1.0,
        speed: float = 1.0,
        transition: str = "cut",
        transition_duration: float = 0.3,
        clip_id: str | None = None,
    ) -> Project:
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
        position: str = "top",
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
        portrait_position: str = "bottom-right",
        portrait_width: int = 420,
        portrait_animation: str = "pop",
    ) -> Project:
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
        self.script_data = (parser or RotScriptParser()).parse(source)
        return self

    def script_file(self, path: str | Path, *, parser: ScriptParser | None = None) -> Project:
        selected = parser or RotScriptParser()
        if hasattr(selected, "parse_file"):
            self.script_data = selected.parse_file(path)
        else:
            self.script_data = selected.parse(Path(path).read_text(encoding="utf-8"))
        return self

    def captions(self, theme: str | CaptionTheme = "pop", **overrides: Any) -> Project:
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
        position: str = "center",
        width: int | None = None,
        opacity: float = 1.0,
        animation: str = "pop",
        z_index: int = 0,
    ) -> Project:
        if animation not in OVERLAY_ANIMATIONS:
            raise ConfigurationError(f"Unknown overlay animation {animation!r}")
        self.overlays.append(
            Overlay(
                source,
                at=at,
                duration=duration,
                during=during,
                speaker=speaker,
                position=position,  # type: ignore[arg-type]
                width=width,
                opacity=opacity,
                animation=animation,
                z_index=z_index,
            )
        )
        return self

    def effect(self, effect: str | EffectSpec | Effect, **options: str | int | float) -> Project:
        self.global_effects.append(normalize_effect(effect, **options))
        return self

    def soundtrack(self, source: str | Path, *, volume: float = 0.15) -> Project:
        if volume < 0:
            raise ConfigurationError("Soundtrack volume cannot be negative")
        self.music = Path(source)
        self.music_volume = volume
        return self

    def with_aligner(self, aligner: WordAligner) -> Project:
        self.aligner = aligner
        return self

    def with_caption_renderer(self, renderer: CaptionRenderer) -> Project:
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
        from .render import Renderer

        return Renderer(self).render(
            Path(output),
            progress=progress,
            overwrite=self.settings.overwrite if overwrite is None else overwrite,
            keep_workdir=keep_workdir,
        )
