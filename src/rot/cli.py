"""Command-line interface for rot."""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .clips import (
    ClipDetectionSettings,
    FolderClipFinder,
    VideoClipFinder,
    YouTubeClipFinder,
)
from .errors import ConfigurationError, RotError
from .integrations import OpenRouterParser
from .probe import doctor, probe
from .project import Project

console = Console()
error_console = Console(stderr=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rot", description="Create short-form vertical videos")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("--json-logs", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    render = commands.add_parser("render", help="Render a Project from a trusted Python file")
    render.add_argument("project", help="FILE.py[:object], default object: project")
    render.add_argument("-o", "--output", default="output.mp4")
    render.add_argument("-f", "--force", action="store_true")
    render.add_argument("--no-progress", action="store_true")
    render.add_argument("--keep-workdir", action="store_true")

    probe_command = commands.add_parser("probe", help="Inspect a media asset")
    probe_command.add_argument("asset")
    probe_command.add_argument("--json", action="store_true")

    commands.add_parser("doctor", help="Check FFmpeg and optional integrations")

    parse_command = commands.add_parser("parse", help="Parse free-form text with OpenRouter")
    parse_command.add_argument("input")
    parse_command.add_argument("-o", "--output")
    parse_command.add_argument("--model", required=True)
    parse_command.add_argument("--speaker", action="append", default=[])

    clips = commands.add_parser(
        "clips", help="Find the best clips in a YouTube video, local file, or folder"
    )
    clips.add_argument("target", metavar="TARGET", help="YouTube URL, video file, or folder")
    clips.add_argument("-o", "--output-dir", default="youtube-clips")
    clips.add_argument("--method", choices=("hybrid", "scene", "motion", "audio"), default="hybrid")
    clips.add_argument("--duration", type=float, default=30.0)
    clips.add_argument("--count", type=int, default=5)
    clips.add_argument("--scene-threshold", type=float, default=0.30)
    clips.add_argument("--max-overlap", type=float, default=0.20)
    clips.add_argument("--scene-weight", type=float, default=0.35)
    clips.add_argument("--motion-weight", type=float, default=0.20)
    clips.add_argument("--audio-weight", type=float, default=0.45)
    clips.add_argument("--no-snap", action="store_true")
    clips.add_argument("--no-cache", action="store_true")
    clips.add_argument("--no-recursive", action="store_true")
    clips.add_argument("--max-per-source", type=int)
    clips.add_argument("--download-only", action="store_true")
    clips.add_argument("--json", action="store_true")
    clips.add_argument("--overwrite-downloads", action="store_true")
    clips.add_argument("--overwrite-exports", action="store_true")
    clips.add_argument("-f", "--force", action="store_true", help="Overwrite downloads and exports")
    return parser


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {"level": record.levelname, "logger": record.name, "message": record.getMessage()},
            ensure_ascii=False,
        )


def _configure_logging(verbose: int, json_logs: bool) -> None:
    level = logging.DEBUG if verbose > 1 else logging.INFO if verbose else logging.WARNING
    if json_logs:
        handler: logging.Handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
    else:
        handler = RichHandler(console=error_console, show_path=verbose > 1, rich_tracebacks=True)
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _load_project(specification: str) -> Project:
    source, separator, object_name = specification.partition(":")
    path = Path(source).expanduser().resolve()
    if not path.is_file():
        raise RotError(f"Project file does not exist: {path}")
    name = f"rot_user_project_{abs(hash(path))}"
    module_spec = importlib.util.spec_from_file_location(name, path)
    if module_spec is None or module_spec.loader is None:
        raise RotError(f"Cannot import project file: {path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.path.insert(0, str(path.parent))
    try:
        module_spec.loader.exec_module(module)
    except Exception as exc:
        raise RotError(f"Project file raised {type(exc).__name__}: {exc}") from exc
    finally:
        sys.path.pop(0)
    selected_name = object_name if separator else "project"
    value = getattr(module, selected_name, None)
    if not isinstance(value, Project):
        raise RotError(f"{path} must expose a Project named {selected_name}")
    return value


def _render(args: argparse.Namespace) -> int:
    project = _load_project(args.project)
    result = project.render(
        args.output,
        progress=not args.no_progress,
        overwrite=args.force,
        keep_workdir=args.keep_workdir,
    )
    console.print(f"[bold green]Created[/] {result.output} ({result.duration:.2f}s)")
    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/] {warning}")
    return 0


def _probe(args: argparse.Namespace) -> int:
    info = probe(args.asset)
    values = {
        "path": str(info.path),
        "duration": info.duration,
        "width": info.width,
        "height": info.height,
        "has_video": info.has_video,
        "has_audio": info.has_audio,
        "format": info.format_name,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "pixel_format": info.pixel_format,
        "frame_rate": info.frame_rate,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "color_primaries": info.color_primaries,
        "color_transfer": info.color_transfer,
        "color_space": info.color_space,
        "bit_rate": info.bit_rate,
    }
    if args.json:
        console.print_json(data=values)
    else:
        table = Table(title="Media information")
        table.add_column("Field")
        table.add_column("Value")
        for key, value in values.items():
            table.add_row(key, str(value))
        console.print(table)
    return 0


def _doctor(_: argparse.Namespace) -> int:
    report = doctor()
    table = Table(title="rot environment")
    table.add_column("Capability")
    table.add_column("Status")
    rows = {
        "FFmpeg": report.ffmpeg,
        "FFprobe": report.ffprobe,
        "ASS/libass captions": report.libass,
        "H.264 encoder": report.h264,
        "AAC encoder": report.aac,
        "Chatterbox extra": importlib.util.find_spec("chatterbox") is not None,
        "Kokoro extra": importlib.util.find_spec("kokoro") is not None,
        "Stable-TS extra": importlib.util.find_spec("stable_whisper") is not None,
        "OpenRouter extra": importlib.util.find_spec("httpx") is not None,
        "YouTube extra": importlib.util.find_spec("yt_dlp") is not None,
    }
    for name, value in rows.items():
        okay = bool(value)
        display = str(value) if isinstance(value, str) else "available" if okay else "missing"
        table.add_row(name, f"[green]{display}[/]" if okay else f"[red]{display}[/]")
    console.print(table)
    if not report.healthy:
        error_console.print("Install a full FFmpeg build with libass, libx264, and AAC support.")
    return 0 if report.healthy else 1


def _parse(args: argparse.Namespace) -> int:
    source = Path(args.input).read_text(encoding="utf-8")
    script = OpenRouterParser(model=args.model, speakers=tuple(args.speaker)).parse(source)
    lines: list[str] = []
    for utterance in script.utterances:
        metadata = f" [id={utterance.id}]" if utterance.id else ""
        lines.append(f"@{utterance.speaker}{metadata}: {utterance.text}")
    rendered = "\n".join(lines) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
        console.print(f"[green]Wrote[/] {args.output}")
    else:
        console.print(rendered, end="")
    return 0


def _clip_target(target: str) -> tuple[str, str | Path]:
    """Classify a clips TARGET as a YouTube URL, a local folder, or a local file."""
    if urlparse(target).scheme in {"http", "https"}:
        return "youtube", target
    path = Path(target).expanduser()
    if path.is_dir():
        return "folder", path
    if path.is_file():
        return "file", path
    raise ConfigurationError(f"Not a URL, video file, or folder: {target}")


def _clips(args: argparse.Namespace) -> int:
    settings = ClipDetectionSettings(
        method=args.method,
        clip_duration=args.duration,
        clip_count=args.count,
        scene_threshold=args.scene_threshold,
        max_overlap_ratio=args.max_overlap,
        scene_weight=args.scene_weight,
        motion_weight=args.motion_weight,
        audio_weight=args.audio_weight,
        snap=not args.no_snap,
        max_per_source=args.max_per_source,
    )
    kind, target = _clip_target(args.target)
    cache = not args.no_cache
    export = not args.download_only
    if kind != "youtube" and args.download_only:
        raise ConfigurationError("--download-only only applies to YouTube URLs")

    if kind == "youtube":
        result = YouTubeClipFinder(settings, cache=cache).find(
            str(target),
            args.output_dir,
            export=export,
            overwrite_download=args.force or args.overwrite_downloads,
            overwrite_exports=args.force or args.overwrite_exports,
            progress=True,
        )
    elif kind == "folder":
        result = FolderClipFinder(settings, cache=cache).find(
            target,
            args.output_dir,
            overwrite=args.force or args.overwrite_exports,
            progress=True,
            recursive=not args.no_recursive,
        )
    else:
        result = VideoClipFinder(settings, cache=cache).find(
            target,
            args.output_dir,
            overwrite=args.force or args.overwrite_exports,
            progress=True,
        )
    values = [
        {
            "rank": index,
            "source": str(candidate.source),
            "start": candidate.start,
            "end": candidate.end,
            "score": candidate.score,
            "scene_score": candidate.scene_score,
            "motion_score": candidate.motion_score,
            "audio_score": candidate.audio_score,
            "output": str(result.exports[index - 1]) if result.exports else None,
        }
        for index, candidate in enumerate(result.candidates, start=1)
    ]
    if args.json:
        console.print_json(
            data={
                "sources": [str(path) for path in result.sources],
                "clips": values,
                "skipped": [
                    {"path": str(item.path), "reason": item.reason} for item in result.skipped
                ],
                "warnings": list(result.warnings),
            }
        )
    else:
        if len(result.sources) == 1:
            console.print(f"[bold green]Analyzed[/] {result.sources[0]}")
        else:
            console.print(f"[bold green]Analyzed[/] {len(result.sources)} sources")
        table = Table(title="Suggested clips")
        table.add_column("Rank")
        table.add_column("Source")
        table.add_column("Time")
        table.add_column("Score")
        table.add_column("Scene")
        table.add_column("Motion")
        table.add_column("Audio")
        table.add_column("Output")
        for value in values:
            table.add_row(
                str(value["rank"]),
                Path(str(value["source"])).name,
                f'{value["start"]:.2f}s–{value["end"]:.2f}s',
                f'{value["score"]:.3f}',
                f'{value["scene_score"]:.3f}',
                f'{value["motion_score"]:.3f}',
                f'{value["audio_score"]:.3f}',
                str(value["output"] or "not exported"),
            )
        console.print(table)
        for warning in result.warnings:
            console.print(f"[yellow]{warning}[/]")
    return 0


def run(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure_logging(args.verbose, args.json_logs)
    try:
        return {
            "render": _render,
            "probe": _probe,
            "doctor": _doctor,
            "parse": _parse,
            "clips": _clips,
        }[args.command](args)
    except RotError as exc:
        error_console.print(f"[bold red]error:[/] {exc}")
        return 2
    except KeyboardInterrupt:
        error_console.print("[yellow]Cancelled[/]")
        return 130


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
