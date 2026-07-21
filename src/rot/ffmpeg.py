"""Safe FFmpeg filter-graph compilation and process execution."""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .effects import BuiltinEffect
from .errors import ConfigurationError, RenderError
from .models import (
    Clip,
    ClipTranscript,
    Facecam,
    FilterNode,
    MediaInfo,
    Overlay,
    Placement,
    RenderSettings,
    Soundtrack,
    Utterance,
    transition_overlap,
)
from .probe import executable


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _filter_value(value: str | int | float) -> str:
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, int):
        return str(value)
    return value.replace("\\", r"\\").replace(":", r"\:").replace(",", r"\,")


def _node(node: FilterNode) -> str:
    if not node.arguments:
        return node.name
    args = ":".join(f"{key}={_filter_value(value)}" for key, value in node.arguments)
    return f"{node.name}={args}"


def _anchor(anchor: str | Placement, width: int, height: int) -> tuple[str, str]:
    if isinstance(anchor, Placement):
        point_x = _number(anchor.x * width)
        point_y = _number(anchor.y * height)
        horizontal_alignment, vertical_alignment = _alignment(anchor.anchor)
        x_positions = {
            "left": point_x,
            "center": f"{point_x}-w/2",
            "right": f"{point_x}-w",
        }
        y_positions = {
            "top": point_y,
            "center": f"{point_y}-h/2",
            "bottom": f"{point_y}-h",
        }
        return x_positions[horizontal_alignment], y_positions[vertical_alignment]
    horizontal_positions = {
        "left": "80",
        "center": "(W-w)/2",
        "right": "W-w-80",
    }
    vertical_positions = {"top": "120", "center": "(H-h)/2", "bottom": "H-h-260"}
    if "left" in anchor:
        x = horizontal_positions["left"]
    elif "right" in anchor:
        x = horizontal_positions["right"]
    else:
        x = horizontal_positions["center"]
    if "top" in anchor:
        y = vertical_positions["top"]
    elif "bottom" in anchor:
        y = vertical_positions["bottom"]
    else:
        y = vertical_positions["center"]
    return x, y


def _animated_image_filters(
    width: int,
    opacity: float,
    animation: str,
    interval: tuple[float, float],
) -> str:
    start, end = interval
    entrance = min(0.15, max(0.04, (end - start) / 3))
    filters = [
        f"scale={width}:-1",
        "format=rgba",
        f"colorchannelmixer=aa={_number(opacity)}",
    ]
    if animation == "fade":
        exit_duration = min(0.12, max(0.04, (end - start) / 3))
        filters.extend(
            [
                f"fade=t=in:st={_number(start)}:d={_number(entrance)}:alpha=1",
                f"fade=t=out:st={_number(end - exit_duration)}:d={_number(exit_duration)}:alpha=1",
            ]
        )
    elif animation == "pop":
        expression = (
            f"if(lt(t,{_number(start)}),0.01,"
            f"if(lt(t,{_number(start + entrance)}),"
            f"0.72+0.28*(t-{_number(start)})/{_number(entrance)},1))"
        )
        filters.append(f"scale=w='iw*{expression}':h=-1:eval=frame")
    elif animation == "bounce":
        midpoint = start + entrance * 0.6
        expression = (
            f"if(lt(t,{_number(start)}),0.01,"
            f"if(lt(t,{_number(midpoint)}),"
            f"0.7+0.45*(t-{_number(start)})/{_number(entrance * 0.6)},"
            f"if(lt(t,{_number(start + entrance)}),"
            f"1.15-0.15*(t-{_number(midpoint)})/{_number(entrance * 0.4)},1)))"
        )
        filters.append(f"scale=w='iw*{expression}':h=-1:eval=frame")
    return ",".join(filters)


def _animated_x(base_x: str, animation: str, interval: tuple[float, float]) -> str:
    if animation != "slide":
        return base_x
    start, end = interval
    entrance = min(0.15, max(0.04, (end - start) / 3))
    expression = (
        f"if(lt(t,{_number(start + entrance)}),"
        f"-w+(({base_x})+w)*(t-{_number(start)})/{_number(entrance)},{base_x})"
    )
    return f"'{expression}'"


def _alignment(anchor: str) -> tuple[str, str]:
    horizontal = "left" if "left" in anchor else "right" if "right" in anchor else "center"
    vertical = "top" if "top" in anchor else "bottom" if "bottom" in anchor else "center"
    return horizontal, vertical


def _cover_filter(
    anchor: str,
    width: int,
    height: int,
    focus: tuple[float, float] | None = None,
) -> str:
    if focus is not None:
        focus_x, focus_y = (_number(value) for value in focus)
        x = f"min(max(iw*{focus_x}-ow/2,0),iw-ow)"
        y = f"min(max(ih*{focus_y}-oh/2,0),ih-oh)"
    else:
        x, y = {
            "center": ("(iw-ow)/2", "(ih-oh)/2"),
            "top": ("(iw-ow)/2", "0"),
            "bottom": ("(iw-ow)/2", "ih-oh"),
            "left": ("0", "(ih-oh)/2"),
            "right": ("iw-ow", "(ih-oh)/2"),
            "top-left": ("0", "0"),
            "top-right": ("iw-ow", "0"),
            "bottom-left": ("0", "ih-oh"),
            "bottom-right": ("iw-ow", "ih-oh"),
        }[anchor]
    scaled = f"scale={width}:{height}:force_original_aspect_ratio=increase"
    if focus is None:
        return f"{scaled},crop={width}:{height}:{x}:{y}"
    return f"{scaled},crop=w={width}:h={height}:x='{x}':y='{y}'"


def _custom_foreground_filter(clip: Clip, width: int, height: int) -> str:
    amount = _number(clip.fit_amount)
    contain = f"min({width}/iw,{height}/ih)"
    cover = f"max({width}/iw,{height}/ih)"
    factor = f"({contain}+({cover}-{contain})*{amount})"
    if clip.focus is not None:
        focus_x, focus_y = (_number(value) for value in clip.focus)
        crop_x = f"min(max(iw*{focus_x}-ow/2,0),iw-ow)"
        crop_y = f"min(max(ih*{focus_y}-oh/2,0),ih-oh)"
    else:
        horizontal, vertical = _alignment(clip.anchor)
        crop_x = {"left": "0", "center": "(iw-ow)/2", "right": "iw-ow"}[horizontal]
        crop_y = {"top": "0", "center": "(ih-oh)/2", "bottom": "ih-oh"}[vertical]
    return (
        f"scale=w='trunc(iw*{factor}/2)*2':h=-2,"
        f"crop=w='min(iw,{width})':h='min(ih,{height})':x='{crop_x}':y='{crop_y}'"
    )


def _foreground_fit_filter(clip: Clip, settings: RenderSettings) -> str:
    if clip.fit == "contain":
        return (
            f"scale={settings.width}:{settings.height}:"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
    return _custom_foreground_filter(clip, settings.width, settings.height)


def _placement_position(
    placement: Placement,
    *,
    canvas_width: str = "W",
    canvas_height: str = "H",
    element_width: str = "w",
    element_height: str = "h",
) -> tuple[str, str]:
    horizontal, vertical = _alignment(placement.anchor)
    point_x = _number(placement.x)
    point_y = _number(placement.y)
    x = {
        "left": f"{canvas_width}*{point_x}",
        "center": f"{canvas_width}*{point_x}-{element_width}/2",
        "right": f"{canvas_width}*{point_x}-{element_width}",
    }[horizontal]
    y = {
        "top": f"{canvas_height}*{point_y}",
        "center": f"{canvas_height}*{point_y}-{element_height}/2",
        "bottom": f"{canvas_height}*{point_y}-{element_height}",
    }[vertical]
    return (
        f"min(max({x},0),{canvas_width}-{element_width})",
        f"min(max({y},0),{canvas_height}-{element_height})",
    )


def _frame_position(anchor: str, placement: Placement | None = None) -> tuple[str, str]:
    if placement is not None:
        return _placement_position(placement)
    horizontal, vertical = _alignment(anchor)
    x = {"left": "0", "center": "(W-w)/2", "right": "W-w"}[horizontal]
    y = {"top": "0", "center": "(H-h)/2", "bottom": "H-h"}[vertical]
    return x, y


def _blur_background_filter(clip: Clip, settings: RenderSettings) -> str:
    small_width = max(2, (settings.width // 4) // 2 * 2)
    small_height = max(2, (settings.height // 4) // 2 * 2)
    sigma = _number(max(0.1, clip.fill_blur / 4))
    return (
        f"{_cover_filter(clip.anchor, small_width, small_height, clip.focus)},"
        f"gblur=sigma={sigma}:steps=2,scale={settings.width}:{settings.height}"
    )


def _even_pixels(fraction: float, dimension: int) -> int:
    return max(2, int(round(fraction * dimension)) // 2 * 2)


def _facecam_filter(facecam: Facecam, settings: RenderSettings) -> tuple[str, int, int]:
    crop = facecam.crop
    destination = facecam.destination
    width = _even_pixels(destination.width, settings.width)
    height = _even_pixels(destination.height, settings.height)
    x = round(destination.x * settings.width)
    y = round(destination.y * settings.height)
    crop_width = _number(crop.width)
    crop_height = _number(crop.height)
    crop_x = _number(crop.x)
    crop_y = _number(crop.y)
    filters = (
        f"crop=w='max(2,trunc(iw*{crop_width}/2)*2)':"
        f"h='max(2,trunc(ih*{crop_height}/2)*2)':"
        f"x='min(iw-ow,trunc(iw*{crop_x}/2)*2)':"
        f"y='min(ih-oh,trunc(ih*{crop_y}/2)*2)',"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    return filters, x, y


def _fit_filter(clip: Clip, settings: RenderSettings) -> str:
    width, height = settings.width, settings.height
    if clip.fit == "stretch":
        return f"scale={width}:{height}"
    if clip.fit == "contain":
        if clip.position is None:
            pad_x, pad_y = "(ow-iw)/2", "(oh-ih)/2"
        else:
            pad_x, pad_y = _placement_position(
                clip.position,
                canvas_width="ow",
                canvas_height="oh",
                element_width="iw",
                element_height="ih",
            )
        return (
            f"{_foreground_fit_filter(clip, settings)},"
            f"pad={width}:{height}:x='{pad_x}':y='{pad_y}':color=black"
        )
    if clip.fit == "custom":
        if clip.position is None:
            horizontal, vertical = _alignment(clip.anchor)
            pad_x = {"left": "0", "center": "(ow-iw)/2", "right": "ow-iw"}[horizontal]
            pad_y = {"top": "0", "center": "(oh-ih)/2", "bottom": "oh-ih"}[vertical]
        else:
            pad_x, pad_y = _placement_position(
                clip.position,
                canvas_width="ow",
                canvas_height="oh",
                element_width="iw",
                element_height="ih",
            )
        return (
            f"{_custom_foreground_filter(clip, width, height)},"
            f"pad={width}:{height}:x='{pad_x}':y='{pad_y}':color=black"
        )
    return _cover_filter(clip.anchor, width, height, clip.focus)


def _atempo(speed: float) -> str:
    parts: list[str] = []
    remaining = speed
    while remaining > 2:
        parts.append("atempo=2")
        remaining /= 2
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={_number(remaining)}")
    return ",".join(parts)


@dataclass(slots=True)
class PreparedMedia:
    clips: list[tuple[Clip, MediaInfo, float]]
    utterances: list[tuple[Utterance, Path]]
    overlays: list[tuple[Overlay, tuple[tuple[float, float], ...]]]
    portraits: list[tuple[Path, str | Placement, int, str, tuple[tuple[float, float], ...]]]
    duration: float
    text_overlay_file: Path | None = None
    clip_caption_file: Path | None = None
    caption_file: Path | None = None
    caption_utterances: tuple[Utterance, ...] = ()
    transcripts: tuple[ClipTranscript, ...] = ()
    music: Soundtrack | None = None
    music_info: MediaInfo | None = None
    effects: list[Any] = field(default_factory=list)


class FFmpegCompiler:
    def __init__(self, settings: RenderSettings) -> None:
        self.settings = settings

    def compile(self, media: PreparedMedia, output: Path) -> tuple[str, ...]:
        if not media.clips:
            raise ConfigurationError("A project needs at least one background clip or image")
        command: list[str] = [executable("ffmpeg"), "-hide_banner", "-y"]
        input_index = 0
        clip_indices: list[int] = []
        for clip, info, duration in media.clips:
            clip_indices.append(input_index)
            if info.duration <= 0:
                command.extend(
                    ["-loop", "1", "-framerate", str(self.settings.fps), "-t", _number(duration)]
                )
            elif clip.loop and duration > max(
                0.001, (info.duration - clip.trim_start) / clip.speed
            ):
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(info.path)])
            input_index += 1

        utterance_indices: list[int] = []
        for _, audio_path in media.utterances:
            utterance_indices.append(input_index)
            command.extend(["-i", str(audio_path)])
            input_index += 1

        overlay_indices: list[int] = []
        for overlay, _ in media.overlays:
            overlay_indices.append(input_index)
            command.extend(
                ["-loop", "1", "-framerate", str(self.settings.fps), "-i", str(overlay.source)]
            )
            input_index += 1

        portrait_indices: list[int] = []
        for portrait, _, _, _, _ in media.portraits:
            portrait_indices.append(input_index)
            command.extend(
                ["-loop", "1", "-framerate", str(self.settings.fps), "-i", str(portrait)]
            )
            input_index += 1

        music_index: int | None = None
        if media.music is not None:
            music_index = input_index
            command.extend(["-i", str(Path(media.music.source).expanduser().resolve())])
            input_index += 1

        silence_index = input_index
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                _number(media.duration),
                "-i",
                f"anullsrc=r={self.settings.audio_sample_rate}:cl=stereo",
            ]
        )

        graph: list[str] = []
        video_labels: list[str] = []
        clip_durations: list[float] = []
        for position, ((clip, _, duration), index) in enumerate(
            zip(media.clips, clip_indices, strict=True)
        ):
            label = f"vclip{position}"
            source_filters = [
                f"trim=start={_number(clip.trim_start)}:duration={_number(duration * clip.speed)}",
                f"setpts=(PTS-STARTPTS)/{_number(clip.speed)}",
            ]
            output_filters = [
                f"fps={self.settings.fps}",
                "setsar=1",
                f"format={self.settings.pixel_format}",
                f"trim=duration={_number(duration)}",
                "setpts=PTS-STARTPTS",
            ]
            for effect in clip.effects:
                provider = (
                    BuiltinEffect.create(effect.name, **dict(effect.options))
                    if hasattr(effect, "options")
                    else effect
                )
                output_filters.extend(
                    _node(node)
                    for node in provider.filters(
                        duration=duration, width=self.settings.width, height=self.settings.height
                    )
                )
            branch_count = 1 + int(clip.fill == "blur") + int(clip.facecam is not None)
            if clip.fill == "blur":
                branch_labels = [f"vfillbgsource{position}", f"vfillfgsource{position}"]
            else:
                branch_labels = [f"vsource{position}"]
            if clip.facecam is not None:
                branch_labels.append(f"vfacecamsource{position}")
            if branch_count > 1:
                graph.append(
                    f"[{index}:v]{','.join(source_filters)},split={branch_count}"
                    + "".join(f"[{branch}]" for branch in branch_labels)
                )
            foreground_index = 1 if clip.fill == "blur" else 0
            source = branch_labels[foreground_index] if branch_count > 1 else f"{index}:v"
            source_prefix = [] if branch_count > 1 else source_filters
            facecam_source = branch_labels[-1] if clip.facecam is not None else None
            if clip.fill == "blur":
                background = f"vfillbg{position}"
                foreground = f"vfillfg{position}"
                base = f"vfilled{position}"
                background_source = branch_labels[0]
                graph.append(
                    f"[{background_source}]{_blur_background_filter(clip, self.settings)}"
                    f"[{background}]"
                )
                graph.append(
                    f"[{source}]{_foreground_fit_filter(clip, self.settings)}"
                    f"[{foreground}]"
                )
                x, y = _frame_position(clip.anchor, clip.position)
                graph.append(
                    f"[{background}][{foreground}]overlay=x={x}:y={y}:shortest=1[{base}]"
                )
            else:
                base = f"vfitted{position}"
                filters = [*source_prefix, _fit_filter(clip, self.settings)]
                graph.append(f"[{source}]{','.join(filters)}[{base}]")
            if clip.facecam is not None:
                assert facecam_source is not None
                facecam_label = f"vfacecam{position}"
                composed = f"vfacecamcomposed{position}"
                facecam_filters, facecam_x, facecam_y = _facecam_filter(
                    clip.facecam, self.settings
                )
                graph.append(f"[{facecam_source}]{facecam_filters}[{facecam_label}]")
                graph.append(
                    f"[{base}][{facecam_label}]overlay=x={facecam_x}:y={facecam_y}:"
                    f"shortest=1[{composed}]"
                )
                base = composed
            graph.append(f"[{base}]{','.join(output_filters)}[{label}]")
            video_labels.append(label)
            clip_durations.append(duration)

        current = video_labels[0]
        timeline_duration = clip_durations[0]
        for position in range(1, len(video_labels)):
            previous_clip = media.clips[position - 1][0]
            transition = previous_clip.transition
            duration = transition_overlap(
                previous_clip, clip_durations[position - 1], clip_durations[position]
            )
            result = f"vjoin{position}"
            if transition == "cut":
                graph.append(f"[{current}][{video_labels[position]}]concat=n=2:v=1:a=0[{result}]")
                timeline_duration += clip_durations[position]
            else:
                xfade_name = {
                    "fade": "fade",
                    "crossfade": "fade",
                    "slide-left": "slideleft",
                    "slide-right": "slideright",
                    "zoom": "zoomin",
                }[transition]
                offset = max(0, timeline_duration - duration)
                graph.append(
                    f"[{current}][{video_labels[position]}]xfade=transition={xfade_name}:"
                    f"duration={_number(duration)}:offset={_number(offset)}[{result}]"
                )
                timeline_duration += clip_durations[position] - duration
            current = result

        if media.effects:
            global_filters: list[str] = []
            for effect in media.effects:
                global_filters.extend(
                    _node(node)
                    for node in effect.filters(
                        duration=media.duration,
                        width=self.settings.width,
                        height=self.settings.height,
                    )
                )
            result = "veffects"
            graph.append(f"[{current}]{','.join(global_filters)}[{result}]")
            current = result

        for position, ((overlay, intervals), index) in enumerate(
            zip(media.overlays, overlay_indices, strict=True)
        ):
            source_label = f"overlay{position}"
            width = overlay.width or 560
            graph.append(
                f"[{index}:v]"
                f"{_animated_image_filters(width, overlay.opacity, overlay.animation, intervals[0])}"
                f"[{source_label}]"
            )
            enabled = "+".join(
                f"between(t\\,{_number(start)}\\,{_number(end)})" for start, end in intervals
            )
            x, y = _anchor(overlay.position, self.settings.width, self.settings.height)
            x = _animated_x(x, overlay.animation, intervals[0])
            result = f"voverlay{position}"
            graph.append(
                f"[{current}][{source_label}]overlay=x={x}:y={y}:eof_action=repeat:"
                f"enable='{enabled}'[{result}]"
            )
            current = result

        for position, ((_, anchor, width, animation, intervals), index) in enumerate(
            zip(media.portraits, portrait_indices, strict=True)
        ):
            source_label = f"portrait{position}"
            graph.append(
                f"[{index}:v]{_animated_image_filters(width, 1.0, animation, intervals[0])}"
                f"[{source_label}]"
            )
            enabled = "+".join(
                f"between(t\\,{_number(start)}\\,{_number(end)})" for start, end in intervals
            )
            x, y = _anchor(anchor, self.settings.width, self.settings.height)
            x = _animated_x(x, animation, intervals[0])
            result = f"vportrait{position}"
            graph.append(
                f"[{current}][{source_label}]overlay=x={x}:y={y}:eof_action=repeat:"
                f"enable='{enabled}'[{result}]"
            )
            current = result

        if media.text_overlay_file is not None:
            escaped = (
                str(media.text_overlay_file)
                .replace("\\", r"\\")
                .replace(":", r"\:")
                .replace("'", r"\'")
            )
            result = "vtextoverlay"
            graph.append(f"[{current}]ass=filename='{escaped}'[{result}]")
            current = result

        if media.clip_caption_file is not None:
            escaped = (
                str(media.clip_caption_file)
                .replace("\\", r"\\")
                .replace(":", r"\:")
                .replace("'", r"\'")
            )
            result = "vclipcaption"
            graph.append(f"[{current}]ass=filename='{escaped}'[{result}]")
            current = result

        if media.caption_file is not None:
            escaped = (
                str(media.caption_file).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
            )
            result = "vcaption"
            graph.append(f"[{current}]ass=filename='{escaped}'[{result}]")
            current = result

        final_video = "vout"
        graph.append(
            f"[{current}]trim=duration={_number(media.duration)},setpts=PTS-STARTPTS,"
            f"format={self.settings.pixel_format},"
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
            f"[{final_video}]"
        )

        audio_labels: list[str] = []
        speech_labels: list[str] = []
        for position, (((utterance, _), index)) in enumerate(
            zip(media.utterances, utterance_indices, strict=True)
        ):
            if utterance.start is None or utterance.end is None:
                continue
            label = f"speech{position}"
            delay = round(utterance.start * 1000)
            graph.append(
                f"[{index}:a]aresample={self.settings.audio_sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={delay}|{delay},"
                f"atrim=duration={_number(media.duration)}[{label}]"
            )
            speech_labels.append(label)

        clip_cursor = 0.0
        for position, ((clip, info, duration), index) in enumerate(
            zip(media.clips, clip_indices, strict=True)
        ):
            if clip.keep_audio and info.has_audio:
                label = f"backgroundaudio{position}"
                delay = round(clip_cursor * 1000)
                graph.append(
                    f"[{index}:a]atrim=start={_number(clip.trim_start)}:duration={_number(duration * clip.speed)},"
                    f"asetpts=PTS-STARTPTS,{_atempo(clip.speed)},volume={_number(clip.volume)},"
                    f"aresample={self.settings.audio_sample_rate},aformat=channel_layouts=stereo,"
                    f"adelay={delay}|{delay}[{label}]"
                )
                audio_labels.append(label)
            overlap = (
                transition_overlap(clip, duration, media.clips[position + 1][2])
                if position < len(media.clips) - 1
                else 0.0
            )
            clip_cursor += duration - overlap

        if music_index is not None and media.music is not None and media.music_info is not None:
            music = media.music
            source_end = music.trim_end or media.music_info.duration
            segment_duration = source_end - music.trim_start
            audible_duration = media.duration if music.loop else min(media.duration, segment_duration)
            music_filters = [
                f"atrim=start={_number(music.trim_start)}:end={_number(source_end)}",
                "asetpts=PTS-STARTPTS",
                f"aresample={self.settings.audio_sample_rate}",
                "aformat=sample_fmts=fltp:channel_layouts=stereo",
            ]
            if music.loop:
                samples = max(1, round(segment_duration * self.settings.audio_sample_rate))
                music_filters.append(f"aloop=loop=-1:size={samples}")
            music_filters.extend(
                [
                    f"atrim=duration={_number(audible_duration)}",
                    f"volume={_number(music.volume)}",
                ]
            )
            if music.fade_in > 0:
                music_filters.append(f"afade=t=in:st=0:d={_number(music.fade_in)}")
            if music.fade_out > 0:
                fade_start = audible_duration - music.fade_out
                music_filters.append(
                    f"afade=t=out:st={_number(fade_start)}:d={_number(music.fade_out)}"
                )
            graph.append(f"[{music_index}:a]{','.join(music_filters)}[musicbed]")

            if music.ducking and speech_labels:
                speech_inputs = "".join(f"[{label}]" for label in speech_labels)
                graph.append(
                    f"{speech_inputs}amix=inputs={len(speech_labels)}:duration=longest:normalize=0,"
                    "asplit=2[speechprogram][speechsidechain]"
                )
                graph.append(
                    "[musicbed][speechsidechain]sidechaincompress="
                    "threshold=0.03:ratio=8:attack=20:release=250[music]"
                )
                audio_labels.extend(("speechprogram", "music"))
            else:
                audio_labels.extend(speech_labels)
                audio_labels.append("musicbed")
        else:
            audio_labels.extend(speech_labels)
        graph.append(f"[{silence_index}:a]atrim=duration={_number(media.duration)}[silence]")
        audio_labels.append("silence")
        mixed = "".join(f"[{label}]" for label in audio_labels)
        graph.append(
            f"{mixed}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
            f"aresample={self.settings.audio_sample_rate},aformat=channel_layouts=stereo"
            f"{',loudnorm=I=-14:LRA=11:TP=-1.0' if self.settings.normalize_audio else ''},"
            f"atrim=duration={_number(media.duration)}[aout]"
        )

        command.extend(["-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "[aout]"])
        command.extend(
            [
                "-c:v",
                self.settings.video_encoder,
                "-preset",
                self.settings.preset,
                "-profile:v",
                "high",
                "-level:v",
                "4.1",
                "-pix_fmt",
                self.settings.pixel_format,
                "-r",
                str(self.settings.fps),
                "-fps_mode",
                "cfr",
                "-b:v",
                self.settings.video_bitrate,
                "-minrate",
                self.settings.min_video_bitrate,
                "-maxrate",
                self.settings.max_video_bitrate,
                "-bufsize",
                self.settings.buffer_size,
                "-g",
                str(self.settings.fps * 2),
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-color_range",
                "tv",
                "-c:a",
                "aac",
                "-b:a",
                self.settings.audio_bitrate,
                "-ar",
                str(self.settings.audio_sample_rate),
                "-ac",
                str(self.settings.audio_channels),
                "-movflags",
                "+faststart",
                "-t",
                _number(media.duration),
                "-progress",
                "pipe:1",
                "-nostats",
                str(output),
            ]
        )
        return tuple(command)


def run_ffmpeg(command: tuple[str, ...], duration: float, callback: Any) -> None:
    process: subprocess.Popen[str] | None = None
    return_code = 0
    stderr_path: Path
    try:
        with tempfile.NamedTemporaryFile(
            prefix="rot-ffmpeg-", suffix=".log", delete=False
        ) as stderr_file:
            stderr_path = Path(stderr_file.name)
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
            )
            assert process.stdout is not None
            for raw_line in process.stdout:
                key, separator, value = raw_line.strip().partition("=")
                if not separator:
                    continue
                if key in {"out_time_us", "out_time_ms"}:
                    try:
                        seconds = int(value) / 1_000_000
                    except ValueError:
                        continue
                    callback("render", min(seconds, duration), duration, "Encoding video")
            return_code = process.wait()
        if return_code:
            detail = stderr_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(detail.strip().splitlines()[-20:])
            raise RenderError(f"FFmpeg exited with status {return_code}:\n{tail}")
        callback("render", duration, duration, "Encoding complete")
    except KeyboardInterrupt:
        if process is not None:
            process.terminate()
            process.wait(timeout=5)
        raise
    finally:
        if "stderr_path" in locals():
            with suppress(FileNotFoundError):
                os.unlink(stderr_path)
