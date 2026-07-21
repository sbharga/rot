"""ASS and SRT caption generation."""

from __future__ import annotations

import re
from pathlib import Path

from .models import (
    CaptionTheme,
    Placement,
    TextOverlay,
    Utterance,
    WordTiming,
    _InlineStyle,
    _TextRun,
)

_ALIGNMENTS = {
    "bottom-left": 1,
    "bottom": 2,
    "bottom-right": 3,
    "left": 4,
    "center": 5,
    "right": 6,
    "top-left": 7,
    "top": 8,
    "top-right": 9,
}


class AssCaptionRenderer:
    """High-performance native ASS/libass caption renderer."""

    def render(
        self,
        path: Path,
        utterances: list[Utterance],
        theme: CaptionTheme,
        *,
        width: int,
        height: int,
    ) -> Path:
        """Write timed captions as an ASS sidecar.

        Args:
            path: Requested output path.
            utterances: Timed dialogue lines.
            theme: Caption styling.
            width: Output canvas width.
            height: Output canvas height.
        """

        return write_ass(path, utterances, theme, width=width, height=height)


def _ass_color(value: str) -> str:
    source = value.removeprefix("#")
    if len(source) != 6 or any(char not in "0123456789abcdefABCDEF" for char in source):
        raise ValueError(f"Expected #RRGGBB color, got {value!r}")
    red, green, blue = source[0:2], source[2:4], source[4:6]
    return f"&H00{blue}{green}{red}".upper()


def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remaining = divmod(centiseconds, 360_000)
    minutes, remaining = divmod(remaining, 6_000)
    secs, cs = divmod(remaining, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remaining = divmod(milliseconds, 3_600_000)
    minutes, remaining = divmod(remaining, 60_000)
    secs, ms = divmod(remaining, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _animation(theme: CaptionTheme) -> str:
    if theme.name == "pop":
        return r"\fscx80\fscy80\t(0,90,\fscx105\fscy105)\t(90,150,\fscx100\fscy100)"
    if theme.name == "bounce":
        return r"\fscx75\fscy75\frz-2\t(0,80,\fscx112\fscy112\frz2)\t(80,170,\fscx100\fscy100\frz0)"
    return ""


def _placement_tags(placement: Placement | None, width: int, height: int) -> str:
    if placement is None:
        return ""
    x = round(placement.x * width)
    y = round(placement.y * height)
    return f"\\an{_ALIGNMENTS[placement.anchor]}\\pos({x},{y})"


def _inline_tags(
    style: _InlineStyle,
    *,
    color: str,
    font: str,
    font_size: int,
    bold: bool,
    force_color: str | None = None,
) -> str:
    selected_color = force_color or (_ass_color(style.color) if style.color is not None else color)
    selected_font = style.font or font
    selected_size = style.font_size or font_size
    selected_bold = bold if style.bold is None else style.bold
    return (
        f"\\c{selected_color}\\fn{selected_font}\\fs{selected_size}"
        f"\\b{1 if selected_bold else 0}\\i{1 if style.italic else 0}"
        f"\\u{1 if style.underline else 0}"
    )


def _render_runs(
    runs: tuple[_TextRun, ...],
    *,
    color: str,
    font: str,
    font_size: int,
    bold: bool,
    uppercase: bool,
    force_color: str | None = None,
) -> str:
    rendered: list[str] = []
    for run in runs:
        tags = _inline_tags(
            run.style,
            color=force_color or color,
            font=font,
            font_size=font_size,
            bold=bold,
            force_color=force_color,
        )
        text = run.text.upper() if uppercase else run.text
        rendered.append(f"{{{tags}}}{_escape_ass(text)}")
    return "".join(rendered)


def _styled_words(runs: tuple[_TextRun, ...]) -> list[tuple[_TextRun, ...]]:
    words: list[tuple[_TextRun, ...]] = []
    current: list[_TextRun] = []
    for run in runs:
        for part in re.split(r"(\s+)", run.text):
            if not part:
                continue
            if part.isspace():
                if current:
                    words.append(tuple(current))
                    current = []
            else:
                current.append(_TextRun(part, run.style))
    if current:
        words.append(tuple(current))
    return words


def write_ass(
    path: str | Path,
    utterances: list[Utterance],
    theme: CaptionTheme,
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    destination = Path(path)
    margin_v = 0 if theme.position is not None else max(0, height - theme.position_y)
    primary = _ass_color(theme.primary_color)
    highlight = _ass_color(theme.highlight_color)
    outline = _ass_color(theme.outline_color)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{theme.font},{theme.font_size},{primary},{highlight},{outline},&H60000000,-1,0,0,0,100,100,0,0,1,{theme.outline_width},{theme.shadow},{_ALIGNMENTS[theme.position.anchor] if theme.position is not None else 2},70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    animation = _animation(theme)
    for utterance in utterances:
        words = list(utterance.words)
        if not words or utterance.start is None or utterance.end is None:
            continue
        styled_words = _styled_words(utterance.styled_runs)
        styles_match_timings = len(styled_words) == len(words)
        for index, active in enumerate(words):
            chunk_start = (index // theme.max_words) * theme.max_words
            chunk = words[chunk_start : chunk_start + theme.max_words]
            rendered: list[str] = []
            for word_index, word in enumerate(chunk, chunk_start):
                runs = (
                    styled_words[word_index]
                    if styles_match_timings
                    else (_TextRun(word.text, _InlineStyle()),)
                )
                rendered.append(
                    _render_runs(
                        runs,
                        color=primary,
                        font=theme.font,
                        font_size=theme.font_size,
                        bold=True,
                        uppercase=theme.uppercase,
                        force_color=highlight if word is active else None,
                    )
                )
            event_tags = _placement_tags(theme.position, width, height) + animation
            tags = f"{{{event_tags}}}" if event_tags else ""
            events.append(
                "Dialogue: 0,"
                f"{_ass_time(active.start)},{_ass_time(active.end)},Default,{utterance.speaker},"
                f"0,0,0,,{tags}{' '.join(rendered)}"
            )
    destination.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return destination


def write_srt(path: str | Path, utterances: list[Utterance]) -> Path:
    destination = Path(path)
    cues: list[str] = []
    counter = 1
    for utterance in utterances:
        if utterance.start is None or utterance.end is None:
            continue
        cues.append(
            f"{counter}\n{_srt_time(utterance.start)} --> {_srt_time(utterance.end)}\n"
            f"{utterance.text}\n"
        )
        counter += 1
    destination.write_text("\n".join(cues), encoding="utf-8")
    return destination


def write_text_overlays_ass(
    path: str | Path,
    overlays: list[tuple[TextOverlay, tuple[tuple[float, float], ...]]],
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Write independently styled, non-caption text overlays to an ASS track."""

    destination = Path(path)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""
    styles: list[str] = []
    events: list[str] = []
    for index, (overlay, intervals) in enumerate(overlays):
        style = f"TextOverlay{index}"
        primary = _ass_color(overlay.color)
        outline = _ass_color(overlay.outline_color)
        placement = overlay.position if isinstance(overlay.position, Placement) else None
        if placement is not None:
            anchor = placement.anchor
        else:
            assert isinstance(overlay.position, str)
            anchor = overlay.position
        styles.append(
            f"Style: {style},{overlay.font},{overlay.font_size},{primary},{primary},{outline},"
            f"&H60000000,{-1 if overlay.bold else 0},0,0,0,100,100,0,0,1,"
            f"{overlay.outline_width},{overlay.shadow},{_ALIGNMENTS[anchor]},"
            f"{0 if placement is not None else overlay.margin_x},"
            f"{0 if placement is not None else overlay.margin_x},"
            f"{0 if placement is not None else overlay.margin_y},1"
        )
        rendered_text = _render_runs(
            overlay.styled_runs,
            color=primary,
            font=overlay.font,
            font_size=overlay.font_size,
            bold=overlay.bold,
            uppercase=overlay.uppercase,
        )
        placement_tags = _placement_tags(placement, width, height)
        if placement_tags:
            rendered_text = f"{{{placement_tags}}}{rendered_text}"
        for start, end in intervals:
            if end <= start:
                continue
            events.append(
                f"Dialogue: {overlay.z_index},{_ass_time(start)},{_ass_time(end)},"
                f"{style},,0,0,0,,{rendered_text}"
            )
    events_header = """

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    destination.write_text(
        header + "\n".join(styles) + events_header + "\n".join(events) + "\n",
        encoding="utf-8",
    )
    return destination


def estimate_word_timings(text: str, start: float, end: float) -> tuple[WordTiming, ...]:
    words = text.split()
    if not words:
        return ()
    weights = [max(1, sum(char.isalnum() for char in word)) for word in words]
    total_weight = sum(weights)
    available = max(0.001, end - start)
    cursor = start
    result: list[WordTiming] = []
    for index, (word, weight) in enumerate(zip(words, weights, strict=True)):
        word_end = end if index == len(words) - 1 else cursor + available * weight / total_weight
        result.append(WordTiming(word, cursor, word_end))
        cursor = word_end
    return tuple(result)
