"""FFmpeg environment discovery and media probing."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import DependencyError, ProbeError
from .models import MediaInfo


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise DependencyError(
            f"{name} was not found on PATH. Install FFmpeg and run 'rot doctor' for details."
        )
    return path


def probe(path: str | Path) -> MediaInfo:
    source = Path(path).expanduser()
    if not source.is_file():
        raise ProbeError(f"Media asset does not exist: {source}")
    command = [
        executable("ffprobe"),
        "-v",
        "error",
        "-show_entries",
        (
            "format=duration,format_name,bit_rate:"
            "stream=index,codec_type,codec_name,width,height,duration,pix_fmt,avg_frame_rate,"
            "sample_rate,channels,color_primaries,color_transfer,color_space"
        ),
        "-of",
        "json",
        str(source),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode:
        detail = (
            completed.stderr.strip().splitlines()[-1]
            if completed.stderr.strip()
            else "unknown error"
        )
        raise ProbeError(f"Could not probe {source}: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"FFprobe returned invalid JSON for {source}") from exc
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration_text = payload.get("format", {}).get("duration")
    if duration_text in (None, "N/A"):
        durations = [
            item.get("duration") for item in streams if item.get("duration") not in (None, "N/A")
        ]
        duration_text = max((float(value) for value in durations), default=0.0)
    try:
        duration = max(0.0, float(duration_text or 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    rate_text = video.get("avg_frame_rate") if video else None
    frame_rate = None
    if rate_text and rate_text != "0/0":
        try:
            numerator, denominator = str(rate_text).split("/", 1)
            frame_rate = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            frame_rate = None
    bit_rate_text = payload.get("format", {}).get("bit_rate")
    return MediaInfo(
        path=source.resolve(),
        duration=duration,
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        has_video=video is not None,
        has_audio=audio is not None,
        format_name=str(payload.get("format", {}).get("format_name", "")),
        video_codec=str(video.get("codec_name")) if video and video.get("codec_name") else None,
        audio_codec=str(audio.get("codec_name")) if audio and audio.get("codec_name") else None,
        pixel_format=str(video.get("pix_fmt")) if video and video.get("pix_fmt") else None,
        frame_rate=frame_rate,
        sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        channels=int(audio["channels"]) if audio and audio.get("channels") else None,
        color_primaries=(
            str(video.get("color_primaries")) if video and video.get("color_primaries") else None
        ),
        color_transfer=(
            str(video.get("color_transfer")) if video and video.get("color_transfer") else None
        ),
        color_space=str(video.get("color_space")) if video and video.get("color_space") else None,
        bit_rate=int(bit_rate_text) if bit_rate_text not in (None, "N/A") else None,
    )


@dataclass(frozen=True, slots=True)
class DoctorReport:
    ffmpeg: str | None
    ffprobe: str | None
    libass: bool
    h264: bool
    aac: bool
    filters: tuple[str, ...]
    encoders: tuple[str, ...]

    @property
    def healthy(self) -> bool:
        return all((self.ffmpeg, self.ffprobe, self.libass, self.h264, self.aac))

    @property
    def music_filters(self) -> bool:
        """Whether FFmpeg can loop, fade, and duck background music."""

        return all(name in self.filters for name in ("aloop", "afade", "sidechaincompress"))


def doctor() -> DoctorReport:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None:
        return DoctorReport(None, ffprobe, False, False, False, (), ())
    filter_run = subprocess.run(
        [ffmpeg, "-hide_banner", "-filters"], text=True, capture_output=True, check=False
    )
    encoder_run = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"], text=True, capture_output=True, check=False
    )
    filter_text = filter_run.stdout + filter_run.stderr
    encoder_text = encoder_run.stdout + encoder_run.stderr
    available_filters = tuple(
        name
        for name in (
            "ass",
            "subtitles",
            "xfade",
            "overlay",
            "loudnorm",
            "aloop",
            "afade",
            "sidechaincompress",
        )
        if name in filter_text
    )
    available_encoders = tuple(
        name for name in ("libx264", "h264_nvenc", "h264_qsv", "aac") if name in encoder_text
    )
    return DoctorReport(
        ffmpeg,
        ffprobe,
        "ass" in available_filters or "subtitles" in available_filters,
        any(name in available_encoders for name in ("libx264", "h264_nvenc", "h264_qsv")),
        "aac" in available_encoders,
        available_filters,
        available_encoders,
    )
