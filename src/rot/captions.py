"""ASS and SRT caption generation."""

from __future__ import annotations

from pathlib import Path

from .models import CaptionTheme, TextOverlay, Utterance, WordTiming


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


def write_ass(
    path: str | Path,
    utterances: list[Utterance],
    theme: CaptionTheme,
    *,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    destination = Path(path)
    margin_v = max(0, height - theme.position_y)
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
Style: Default,{theme.font},{theme.font_size},{primary},{highlight},{outline},&H60000000,-1,0,0,0,100,100,0,0,1,{theme.outline_width},{theme.shadow},2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    animation = _animation(theme)
    for utterance in utterances:
        words = list(utterance.words)
        if not words or utterance.start is None or utterance.end is None:
            continue
        for index, active in enumerate(words):
            chunk_start = (index // theme.max_words) * theme.max_words
            chunk = words[chunk_start : chunk_start + theme.max_words]
            rendered: list[str] = []
            for word in chunk:
                content = _escape_ass(word.text.upper() if theme.uppercase else word.text)
                color = highlight if word is active else primary
                rendered.append(f"{{\\c{color}}}{content}")
            tags = f"{{{animation}}}" if animation else ""
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
    alignments = {
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
    styles: list[str] = []
    events: list[str] = []
    for index, (overlay, intervals) in enumerate(overlays):
        style = f"TextOverlay{index}"
        primary = _ass_color(overlay.color)
        outline = _ass_color(overlay.outline_color)
        styles.append(
            f"Style: {style},{overlay.font},{overlay.font_size},{primary},{primary},{outline},"
            f"&H60000000,{-1 if overlay.bold else 0},0,0,0,100,100,0,0,1,"
            f"{overlay.outline_width},{overlay.shadow},{alignments[overlay.position]},"
            f"{overlay.margin_x},{overlay.margin_x},{overlay.margin_y},1"
        )
        text = overlay.text.upper() if overlay.uppercase else overlay.text
        escaped = _escape_ass(text)
        for start, end in intervals:
            if end <= start:
                continue
            events.append(
                f"Dialogue: {overlay.z_index},{_ass_time(start)},{_ass_time(end)},"
                f"{style},,0,0,0,,{escaped}"
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
