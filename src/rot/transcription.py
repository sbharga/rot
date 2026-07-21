"""Clip-audio extraction and cached speech-to-text orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import ConfigurationError, RotError, TranscriptionError
from .models import (
    Clip,
    ClipTranscript,
    ClipTranscription,
    MediaInfo,
    StageProgressCallback,
    Transcriber,
    Transcript,
    TranscriptSegment,
    WordTiming,
)
from .probe import executable


def _atempo(speed: float) -> str:
    parts: list[str] = []
    remaining = speed
    while remaining > 2:
        parts.append("atempo=2")
        remaining /= 2
    while remaining < 0.5:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.8f}".rstrip("0").rstrip("."))
    return ",".join(parts)


def _language(clip: Clip) -> str | None:
    if isinstance(clip.transcribe, ClipTranscription):
        return clip.transcribe.language
    return None


def _pass_duration(clip: Clip, info: MediaInfo) -> tuple[float, float]:
    source_end = clip.trim_end if clip.trim_end is not None else info.duration
    source_duration = max(0.0, source_end - clip.trim_start)
    rendered_duration = source_duration / clip.speed
    if clip.duration is not None:
        rendered_duration = min(rendered_duration, clip.duration)
        source_duration = rendered_duration * clip.speed
    if source_duration <= 0 or rendered_duration <= 0:
        raise ConfigurationError(f"Clip has no audio duration to transcribe: {clip.source}")
    return source_duration, rendered_duration


def _cache_path(
    clip: Clip,
    info: MediaInfo,
    source_duration: float,
    language: str | None,
    transcriber: Transcriber,
) -> Path:
    source = Path(info.path).expanduser().resolve()
    stat = source.stat()
    identity = (
        f"v1\0{source}\0{stat.st_size}\0{stat.st_mtime_ns}\0{clip.trim_start}\0"
        f"{source_duration}\0{clip.speed}\0{language}\0{transcriber!r}"
    )
    digest = hashlib.sha256(identity.encode()).hexdigest()
    cache_root = (
        Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "rot"
        / "transcription"
    )
    return cache_root / f"{digest}.json"


def _serialize(transcript: Transcript) -> dict[str, Any]:
    return {
        "language": transcript.language,
        "segments": [
            {
                "text": segment.text,
                "start": segment.start,
                "end": segment.end,
                "words": [
                    {"text": word.text, "start": word.start, "end": word.end}
                    for word in segment.words
                ],
            }
            for segment in transcript.segments
        ],
    }


def _deserialize(payload: dict[str, Any]) -> Transcript:
    segments: list[TranscriptSegment] = []
    for raw_segment in payload.get("segments", []):
        words = tuple(
            WordTiming(str(word["text"]), float(word["start"]), float(word["end"]))
            for word in raw_segment.get("words", [])
        )
        segments.append(
            TranscriptSegment(
                str(raw_segment["text"]),
                float(raw_segment["start"]),
                float(raw_segment["end"]),
                words,
            )
        )
    language = payload.get("language")
    return Transcript(tuple(segments), str(language) if language is not None else None)


def _read_cache(path: Path) -> Transcript | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return _deserialize(payload)
    except (OSError, ValueError, KeyError, TypeError, ConfigurationError):
        return None


def _write_cache(path: Path, transcript: Transcript) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(_serialize(transcript), sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return


def _extract_audio(
    clip: Clip,
    source_duration: float,
    destination: Path,
) -> None:
    command = [
        executable("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{clip.trim_start:.8f}".rstrip("0").rstrip("."),
        "-t",
        f"{source_duration:.8f}".rstrip("0").rstrip("."),
        "-i",
        str(Path(clip.source).expanduser().resolve()),
        "-vn",
        "-af",
        _atempo(clip.speed),
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not destination.is_file():
        detail = completed.stderr.strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise TranscriptionError(f"Could not extract clip audio{suffix}")


def transcribe_clip(
    clip: Clip,
    info: MediaInfo,
    clip_index: int,
    transcriber: Transcriber,
    workdir: Path,
    progress: StageProgressCallback | None = None,
) -> ClipTranscript:
    """Transcribe one opted-in clip, reading and populating the persistent cache."""

    if not info.has_audio or info.duration <= 0:
        raise ConfigurationError(f"Transcribed clip needs an audio stream: {clip.source}")
    source_duration, _ = _pass_duration(clip, info)
    language = _language(clip)
    cache_path = _cache_path(clip, info, source_duration, language, transcriber)
    transcript = _read_cache(cache_path)
    if transcript is None:
        audio_path = workdir / f"transcription-{clip_index}.wav"
        _extract_audio(clip, source_duration, audio_path)
        try:
            transcript = transcriber.transcribe(
                audio_path,
                language=language,
                progress=progress,
            )
        except Exception as exc:
            if isinstance(exc, RotError):
                raise
            raise TranscriptionError(f"Clip transcription failed: {exc}") from exc
        if not isinstance(transcript, Transcript):
            raise TranscriptionError("Transcriber must return a Transcript")
        _write_cache(cache_path, transcript)
    return ClipTranscript(
        clip_index,
        clip.id,
        Path(info.path).expanduser().resolve(),
        transcript,
    )
