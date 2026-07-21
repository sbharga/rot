"""Curated visual effects and safe custom effect support."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ConfigurationError
from .models import Effect, EffectSpec, FilterNode

TRANSITIONS = frozenset({"cut", "fade", "crossfade", "slide-left", "slide-right", "zoom"})
OVERLAY_ANIMATIONS = frozenset({"none", "pop", "fade", "slide", "bounce"})
BUILTIN_EFFECTS = frozenset(
    {"zoom", "pan", "punch-zoom", "shake", "blur", "grayscale", "saturation"}
)


@dataclass(frozen=True, slots=True)
class BuiltinEffect:
    """A validated built-in visual effect.

    Attributes:
        name: Built-in effect name.
        options: Sorted effect option name/value pairs.
    """

    name: str
    options: tuple[tuple[str, str | int | float], ...] = ()

    @classmethod
    def create(cls, name: str, **options: str | int | float) -> BuiltinEffect:
        """Validate and construct a built-in effect.

        Args:
            name: Effect name.
            **options: Effect-specific values.
        """

        if name not in BUILTIN_EFFECTS:
            raise ConfigurationError(f"Unknown effect {name!r}")
        return cls(name, tuple(sorted(options.items())))

    def filters(self, *, duration: float, width: int, height: int) -> tuple[FilterNode, ...]:
        """Compile the effect into safe FFmpeg filter nodes.

        Args:
            duration: Effected duration in seconds.
            width: Output width.
            height: Output height.
        """

        values = dict(self.options)
        if self.name == "blur":
            return (FilterNode("boxblur", (("luma_radius", values.get("radius", 8)),)),)
        if self.name == "grayscale":
            return (FilterNode("hue", (("s", 0),)),)
        if self.name == "saturation":
            return (FilterNode("eq", (("saturation", values.get("amount", 1.5)),)),)
        if self.name in {"zoom", "punch-zoom"}:
            amount = values.get("amount", 1.08 if self.name == "zoom" else 1.18)
            frames = max(1, int(duration * 30))
            return (
                FilterNode(
                    "zoompan",
                    (
                        ("z", f"min(zoom+({float(amount) - 1.0})/{frames},{amount})"),
                        ("d", 1),
                        ("s", f"{width}x{height}"),
                        ("fps", 30),
                    ),
                ),
            )
        if self.name == "pan":
            return (
                FilterNode("scale", (("w", round(width * 1.1)), ("h", -1))),
                FilterNode(
                    "crop",
                    (("w", width), ("h", height), ("x", f"(iw-ow)*t/{duration}")),
                ),
            )
        if self.name == "shake":
            strength = values.get("strength", 8)
            return (
                FilterNode(
                    "scale",
                    (("w", f"{width}+2*{strength}"), ("h", f"{height}+2*{strength}")),
                ),
                FilterNode(
                    "crop",
                    (
                        ("w", width),
                        ("h", height),
                        ("x", f"{strength}*sin(40*t)+{strength}"),
                        ("y", f"{strength}*cos(37*t)+{strength}"),
                    ),
                ),
            )
        raise ConfigurationError(f"Effect {self.name!r} has no compiler")


def normalize_effect(effect: str | EffectSpec | Effect, **options: str | int | float) -> Effect:
    if isinstance(effect, str):
        return BuiltinEffect.create(effect, **options)
    if isinstance(effect, EffectSpec):
        return BuiltinEffect.create(effect.name, **dict(effect.options))
    return effect
