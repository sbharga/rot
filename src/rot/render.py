"""Project validation, preparation, caching, and rendering."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import uuid
import warnings as python_warnings
from pathlib import Path
from typing import TYPE_CHECKING

from .captions import estimate_word_timings, write_srt, write_text_overlays_ass
from .errors import ConfigurationError, RenderError, VoiceError
from .ffmpeg import FFmpegCompiler, PreparedMedia, run_ffmpeg
from .models import (
    Clip,
    MediaInfo,
    Overlay,
    ProgressCallback,
    RenderResult,
    Speaker,
    SynthesizedAudio,
    TextOverlay,
    Utterance,
    WordTiming,
)
from .probe import probe
from .progress import ProgressReporter

logger = logging.getLogger("rot")

if TYPE_CHECKING:
    from .project import Project


def _clip_timeline_intervals(
    clips: list[tuple[Clip, MediaInfo, float]],
) -> tuple[tuple[float, float], ...]:
    """Return non-overlapping label intervals, switching midway through transitions."""

    if not clips:
        return ()
    starts: list[float] = []
    overlaps: list[float] = []
    cursor = 0.0
    for position, (clip, _, clip_duration) in enumerate(clips):
        starts.append(cursor)
        overlap = 0.0
        if position < len(clips) - 1 and clip.transition != "cut":
            overlap = min(
                clip.transition_duration,
                clip_duration / 2,
                clips[position + 1][2] / 2,
            )
        overlaps.append(overlap)
        cursor += clip_duration - overlap
    boundaries = [0.0]
    boundaries.extend(
        starts[position] + overlaps[position - 1] / 2
        for position in range(1, len(clips))
    )
    boundaries.append(cursor)
    return tuple(zip(boundaries, boundaries[1:], strict=False))


class Renderer:
    def __init__(self, project: Project) -> None:
        self.project = project

    def render(
        self,
        output: Path,
        *,
        progress: bool | ProgressCallback,
        overwrite: bool,
        keep_workdir: bool,
    ) -> RenderResult:
        project = self.project
        output = output.expanduser().resolve()
        if output.suffix.lower() != ".mp4":
            raise ConfigurationError("Output must use the .mp4 extension")
        if output.exists() and not overwrite:
            raise ConfigurationError(
                f"Output already exists: {output}; pass overwrite=True to replace it"
            )
        sidecar = output.with_suffix(".srt")
        if self.project.settings.caption_sidecar and sidecar.exists() and not overwrite:
            raise ConfigurationError(
                f"Caption sidecar already exists: {sidecar}; pass overwrite=True to replace it"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="rot-render-"))
        temp_output = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.tmp.mp4")
        warning_messages: list[str] = []
        try:
            with ProgressReporter(progress) as reporter:
                reporter.emit("validate", 0, 1, "Validating project")
                self._validate()
                reporter.emit("validate", 1, 1, "Project valid")
                media = self._prepare(workdir, reporter, warning_messages)
                reporter.emit("compile", 0, 1, "Compiling FFmpeg graph")
                command = FFmpegCompiler(project.settings).compile(media, temp_output)
                reporter.emit("compile", 1, 1, "FFmpeg graph ready")
                run_ffmpeg(command, media.duration, reporter.emit)
                if not temp_output.is_file() or temp_output.stat().st_size == 0:
                    raise RenderError("FFmpeg completed without creating a valid output file")
                self._validate_output(probe(temp_output))
                os.replace(temp_output, output)
                if project.settings.caption_sidecar and project.script_data is not None:
                    write_srt(output.with_suffix(".srt"), project.script_data.utterances)
                reporter.emit("done", 1, 1, f"Wrote {output}")
                return RenderResult(output, media.duration, tuple(warning_messages), command)
        finally:
            if temp_output.exists():
                temp_output.unlink()
            if keep_workdir:
                logger.info("Kept render work directory: %s", workdir)
            else:
                shutil.rmtree(workdir, ignore_errors=True)

    def _validate(self) -> None:
        project = self.project
        if not project.clips:
            raise ConfigurationError("Add a background before rendering")
        clip_ids = [clip.id for clip in project.clips if clip.id is not None]
        if len(clip_ids) != len(set(clip_ids)):
            raise ConfigurationError("Clip ids must be unique")
        for clip in project.clips:
            if not Path(clip.source).expanduser().is_file():
                raise ConfigurationError(f"Background does not exist: {clip.source}")
        if project.script_data is not None:
            for utterance in project.script_data.utterances:
                speaker = project.speakers.get(utterance.speaker)
                if speaker is None:
                    raise ConfigurationError(f"Script uses unknown speaker {utterance.speaker!r}")
                if utterance.audio is None and speaker.voice is None:
                    raise ConfigurationError(
                        f"Speaker {utterance.speaker!r} needs a voice or prerecorded line audio"
                    )
                if utterance.audio is not None and not Path(utterance.audio).expanduser().is_file():
                    raise ConfigurationError(f"Line audio does not exist: {utterance.audio}")
        known_ids = project.script_data.ids() if project.script_data is not None else set()
        for image_overlay in project.overlays:
            if not Path(image_overlay.source).expanduser().is_file():
                raise ConfigurationError(f"Overlay does not exist: {image_overlay.source}")
            if image_overlay.during is not None and image_overlay.during not in known_ids:
                raise ConfigurationError(
                    f"Overlay targets unknown line id {image_overlay.during!r}"
                )
            if image_overlay.speaker is not None and image_overlay.speaker not in project.speakers:
                raise ConfigurationError(
                    f"Overlay targets unknown speaker {image_overlay.speaker!r}"
                )
        for text_overlay in project.text_overlays:
            if text_overlay.during is not None and text_overlay.during not in known_ids:
                raise ConfigurationError(
                    f"Text overlay targets unknown line id {text_overlay.during!r}"
                )
            if text_overlay.speaker is not None and text_overlay.speaker not in project.speakers:
                raise ConfigurationError(
                    f"Text overlay targets unknown speaker {text_overlay.speaker!r}"
                )
            if (
                isinstance(text_overlay.during_clip, int)
                and text_overlay.during_clip >= len(project.clips)
            ):
                raise ConfigurationError(
                    f"Text overlay targets missing clip index {text_overlay.during_clip}"
                )
            if (
                isinstance(text_overlay.during_clip, str)
                and text_overlay.during_clip not in clip_ids
            ):
                raise ConfigurationError(
                    f"Text overlay targets unknown clip id {text_overlay.during_clip!r}"
                )
        for speaker in project.speakers.values():
            if speaker.portrait is not None and not Path(speaker.portrait).expanduser().is_file():
                raise ConfigurationError(f"Portrait does not exist: {speaker.portrait}")
        if project.music is not None and not project.music.expanduser().is_file():
            raise ConfigurationError(f"Soundtrack does not exist: {project.music}")

    def _prepare(
        self,
        workdir: Path,
        reporter: ProgressReporter,
        warning_messages: list[str],
    ) -> PreparedMedia:
        project = self.project
        utterance_audio: list[tuple[Utterance, Path]] = []
        cursor = 0.0
        utterances = project.script_data.utterances if project.script_data is not None else []
        total_lines = max(1, len(utterances))
        for index, utterance in enumerate(utterances):
            reporter.emit(
                "speech", index, total_lines, f"Preparing line {index + 1}/{len(utterances)}"
            )
            speaker = project.speakers[utterance.speaker]
            audio_path = (
                Path(utterance.audio).expanduser().resolve()
                if utterance.audio is not None
                else self._synthesize(utterance, speaker, workdir, reporter)
            )
            info = probe(audio_path)
            if info.duration <= 0:
                raise VoiceError(f"Speech audio has no measurable duration: {audio_path}")
            utterance.start = cursor
            utterance.end = cursor + info.duration
            utterance.words = self._align_or_estimate(
                utterance, audio_path, speaker.language, warning_messages, reporter
            )
            utterance_audio.append((utterance, audio_path))
            cursor = utterance.end + utterance.gap_after
        if utterances:
            cursor = max(0.001, cursor - utterances[-1].gap_after)
        reporter.emit("speech", total_lines, total_lines, "Speech ready")

        clip_infos: list[tuple[Clip, MediaInfo, float]] = []
        for clip in project.clips:
            info = probe(Path(clip.source).expanduser())
            if not info.has_video:
                raise ConfigurationError(f"Background is not a video or image: {clip.source}")
            available = max(0.0, (info.duration - clip.trim_start) / clip.speed)
            if clip.trim_end is not None:
                available = (clip.trim_end - clip.trim_start) / clip.speed
            duration = clip.duration
            if duration is None:
                if len(project.clips) == 1 and cursor > 0:
                    duration = cursor
                elif available > 0:
                    duration = available
                elif cursor > 0:
                    duration = cursor
                else:
                    raise ConfigurationError(
                        f"Still image {clip.source} needs an explicit duration when there is no script"
                    )
            if info.duration > 0 and not clip.loop and duration > available + 0.01:
                raise ConfigurationError(
                    f"Clip {clip.source} is shorter than its requested duration and looping is disabled"
                )
            clip_infos.append((clip, info, duration))

        visual_duration = sum(item[2] for item in clip_infos)
        for position, (clip, _, _) in enumerate(clip_infos[:-1]):
            if clip.transition != "cut":
                visual_duration -= min(
                    clip.transition_duration,
                    clip_infos[position][2] / 2,
                    clip_infos[position + 1][2] / 2,
                )
        duration = cursor if cursor > 0 else visual_duration
        if duration > visual_duration + 0.01:
            last_clip, last_info, last_duration = clip_infos[-1]
            if last_clip.loop:
                clip_infos[-1] = (last_clip, last_info, last_duration + duration - visual_duration)
                visual_duration = duration
            else:
                raise ConfigurationError(
                    "Background track is shorter than the dialogue and looping is disabled"
                )
        duration = min(duration, visual_duration) if cursor > 0 else visual_duration

        overlays: list[tuple[Overlay, tuple[tuple[float, float], ...]]] = []
        for image_overlay in sorted(project.overlays, key=lambda value: value.z_index):
            intervals: tuple[tuple[float, float], ...]
            if image_overlay.at is not None:
                intervals = (
                    (
                        image_overlay.at,
                        min(duration, image_overlay.at + (image_overlay.duration or 2.0)),
                    ),
                )
            elif image_overlay.during is not None:
                line = next(item for item in utterances if item.id == image_overlay.during)
                intervals = ((line.start or 0.0, line.end or duration),)
            else:
                intervals = tuple(
                    (item.start or 0.0, item.end or duration)
                    for item in utterances
                    if item.speaker == image_overlay.speaker
                )
            overlays.extend((image_overlay, (interval,)) for interval in intervals)

        portraits: list[tuple[Path, str, int, str, tuple[tuple[float, float], ...]]] = []
        for speaker in project.speakers.values():
            if speaker.portrait is None:
                continue
            intervals = tuple(
                (item.start or 0.0, item.end or duration)
                for item in utterances
                if item.speaker == speaker.name
            )
            for interval in intervals:
                portraits.append(
                    (
                        Path(speaker.portrait).expanduser().resolve(),
                        speaker.portrait_position,
                        speaker.portrait_width,
                        speaker.portrait_animation,
                        (interval,),
                    )
                )

        text_overlay_items: list[tuple[TextOverlay, tuple[tuple[float, float], ...]]] = []
        clip_intervals = _clip_timeline_intervals(clip_infos)
        clip_ids = {clip.id: index for index, (clip, _, _) in enumerate(clip_infos) if clip.id}
        for text_overlay in sorted(project.text_overlays, key=lambda value: value.z_index):
            text_intervals: tuple[tuple[float, float], ...]
            if text_overlay.at is not None:
                if text_overlay.at >= duration:
                    raise ConfigurationError(
                        f"Text overlay starts after the video ends at {text_overlay.at:g}s"
                    )
                text_intervals = (
                    (
                        text_overlay.at,
                        min(duration, text_overlay.at + (text_overlay.duration or duration)),
                    ),
                )
            elif text_overlay.during is not None:
                line = next(item for item in utterances if item.id == text_overlay.during)
                text_intervals = ((line.start or 0.0, line.end or duration),)
            elif text_overlay.speaker is not None:
                text_intervals = tuple(
                    (item.start or 0.0, item.end or duration)
                    for item in utterances
                    if item.speaker == text_overlay.speaker
                )
            else:
                target = text_overlay.during_clip
                assert target is not None
                index = target if isinstance(target, int) else clip_ids[target]
                text_intervals = (clip_intervals[index],)
            clipped = tuple(
                (max(0.0, float(start)), min(duration, float(end)))
                for start, end in text_intervals
                if start < duration and end > 0
            )
            if clipped:
                text_overlay_items.append((text_overlay, clipped))

        text_overlay_file = None
        if text_overlay_items:
            text_overlay_file = write_text_overlays_ass(
                workdir / "text-overlays.ass",
                text_overlay_items,
                width=project.settings.width,
                height=project.settings.height,
            )

        caption_file = None
        if project.settings.captions and utterances:
            caption_target = workdir / "captions.ass"
            rendered_caption = project.caption_renderer.render(
                caption_target,
                utterances,
                project.caption_theme,
                width=project.settings.width,
                height=project.settings.height,
            )
            if not rendered_caption.is_file():
                raise ConfigurationError("Caption renderer did not create an ASS file")
            if rendered_caption.resolve() != caption_target.resolve():
                shutil.copy2(rendered_caption, caption_target)
            caption_file = caption_target
        return PreparedMedia(
            clips=clip_infos,
            utterances=utterance_audio,
            overlays=overlays,
            portraits=portraits,
            duration=duration,
            text_overlay_file=text_overlay_file,
            caption_file=caption_file,
            music=project.music.expanduser().resolve() if project.music else None,
            music_volume=project.music_volume,
            effects=project.global_effects,
        )

    def _synthesize(
        self, utterance: Utterance, speaker: Speaker, workdir: Path, reporter: ProgressReporter
    ) -> Path:
        voice = speaker.voice
        if voice is None:
            raise VoiceError(f"Speaker {speaker.name!r} has no voice provider")
        cache_root = (
            Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rot" / "speech"
        )
        digest = hashlib.sha256(
            f"v1\0{voice!r}\0{speaker.language}\0{utterance.text}".encode()
        ).hexdigest()
        cache_path = cache_root / f"{digest}.wav"
        if cache_path.is_file():
            return cache_path
        destination = workdir / f"speech-{digest}.wav"
        try:
            result = voice.synthesize(
                utterance.text,
                destination,
                language=speaker.language,
                progress=reporter.emit,
            )
        except Exception as exc:
            if isinstance(exc, VoiceError):
                raise
            raise VoiceError(f"Voice generation failed for {utterance.speaker}: {exc}") from exc
        if isinstance(result, SynthesizedAudio):
            destination = result.path
        if not destination.is_file():
            raise VoiceError(f"Voice provider did not create {destination}")
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            cached_temp = cache_path.with_suffix(".tmp.wav")
            shutil.copy2(destination, cached_temp)
            os.replace(cached_temp, cache_path)
            return cache_path
        except OSError:
            logger.debug("Speech cache is unavailable", exc_info=True)
            return destination

    def _align_or_estimate(
        self,
        utterance: Utterance,
        audio_path: Path,
        language: str,
        warning_messages: list[str],
        reporter: ProgressReporter,
    ) -> tuple[WordTiming, ...]:
        assert utterance.start is not None and utterance.end is not None
        if self.project.aligner is not None:
            try:
                local_words = self.project.aligner.align(
                    audio_path,
                    utterance.text,
                    language=language,
                    progress=reporter.emit,
                )
                return tuple(
                    WordTiming(word.text, word.start + utterance.start, word.end + utterance.start)
                    for word in local_words
                )
            except Exception as exc:
                logger.warning("Word alignment failed; using estimated timings: %s", exc)
        message = (
            f"Captions for line {utterance.id or utterance.speaker!r} use estimated word timing; "
            "install rot[align] and call with_aligner(StableTSAligner()) for accurate syncing."
        )
        if message not in warning_messages:
            warning_messages.append(message)
            python_warnings.warn(message, RuntimeWarning, stacklevel=3)
        return estimate_word_timings(utterance.text, utterance.start, utterance.end)

    def _validate_output(self, info: MediaInfo) -> None:
        settings = self.project.settings
        checks = {
            "width": (info.width, settings.width),
            "height": (info.height, settings.height),
            "video codec": (info.video_codec, "h264"),
            "pixel format": (info.pixel_format, settings.pixel_format),
            "audio codec": (info.audio_codec, "aac"),
            "audio sample rate": (info.sample_rate, settings.audio_sample_rate),
            "audio channels": (info.channels, settings.audio_channels),
            "color primaries": (info.color_primaries, "bt709"),
            "color transfer": (info.color_transfer, "bt709"),
            "color space": (info.color_space, "bt709"),
        }
        failures = [
            f"{name}={actual!r} (expected {expected!r})"
            for name, (actual, expected) in checks.items()
            if actual != expected
        ]
        if info.frame_rate is None or abs(info.frame_rate - settings.fps) > 0.01:
            failures.append(f"frame rate={info.frame_rate!r} (expected {settings.fps})")
        if failures:
            raise RenderError("Rendered file failed output validation: " + "; ".join(failures))
