from pathlib import Path

import pytest

from rot import Clip, MediaInfo, RenderSettings, Soundtrack, Utterance
from rot.ffmpeg import FFmpegCompiler, PreparedMedia
from rot.render import _clip_timeline_intervals


def test_compiler_emits_platform_contract(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    clip_path = tmp_path / "background.mp4"
    audio_path = tmp_path / "speech.wav"
    info = MediaInfo(clip_path, 10, 1920, 1080, True, True, "mp4")
    utterance = Utterance("alex", "hello", start=0, end=2)
    media = PreparedMedia(
        clips=[(Clip(clip_path, duration=2), info, 2)],
        utterances=[(utterance, audio_path)],
        overlays=[],
        portraits=[],
        duration=2,
        text_overlay_file=tmp_path / "titles.ass",
    )
    command = FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4")
    joined = " ".join(command)
    assert "scale=1080:1920" in joined
    assert "-c:v libx264" in joined
    assert "-b:v 10M" in joined
    assert "-minrate 8M" in joined
    assert "-maxrate 12M" in joined
    assert "-pix_fmt yuv420p" in joined
    assert "-r 30" in joined
    assert "-color_primaries bt709" in joined
    assert "-c:a aac" in joined
    assert "-ar 48000" in joined
    assert "-ac 2" in joined
    assert "-progress pipe:1" in joined
    assert "ass=filename=" in joined
    assert "titles.ass" in joined


def test_compiler_uses_xfade(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    first = Clip(tmp_path / "a.mp4", duration=2, transition="slide-left")
    second = Clip(tmp_path / "b.mp4", duration=2)
    info_a = MediaInfo(Path(first.source), 2, 1080, 1920, True, False)
    info_b = MediaInfo(Path(second.source), 2, 1080, 1920, True, False)
    media = PreparedMedia(
        clips=[(first, info_a, 2), (second, info_b, 2)],
        utterances=[],
        overlays=[],
        portraits=[],
        duration=3.7,
    )
    command = FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4")
    assert "xfade=transition=slideleft:duration=0.3:offset=1.7" in " ".join(command)
    intervals = _clip_timeline_intervals(media.clips)
    assert intervals[0] == pytest.approx((0.0, 1.85))
    assert intervals[1] == pytest.approx((1.85, 3.7))


def test_compiler_trims_loops_fades_and_ducks_music(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    clip_path = tmp_path / "background.mp4"
    speech_path = tmp_path / "speech.wav"
    music_path = tmp_path / "music.wav"
    utterance = Utterance("alex", "hello", start=0.25, end=1.25)
    media = PreparedMedia(
        clips=[
            (
                Clip(clip_path, duration=2),
                MediaInfo(clip_path, 2, 1080, 1920, True, False),
                2,
            )
        ],
        utterances=[(utterance, speech_path)],
        overlays=[],
        portraits=[],
        duration=2,
        music=Soundtrack(
            music_path,
            trim_start=1,
            trim_end=1.5,
            fade_in=0.1,
            fade_out=0.2,
            ducking=True,
        ),
        music_info=MediaInfo(music_path, 3, None, None, False, True),
    )
    command = " ".join(FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4"))
    assert "atrim=start=1:end=1.5" in command
    assert "aloop=loop=-1:size=24000" in command
    assert "afade=t=in:st=0:d=0.1" in command
    assert "afade=t=out:st=1.8:d=0.2" in command
    assert "asplit=2[speechprogram][speechsidechain]" in command
    assert "sidechaincompress=threshold=0.03:ratio=8:attack=20:release=250" in command


def test_compiler_plays_non_looping_music_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    clip_path = tmp_path / "background.mp4"
    music_path = tmp_path / "music.wav"
    media = PreparedMedia(
        clips=[
            (
                Clip(clip_path, duration=3),
                MediaInfo(clip_path, 3, 1080, 1920, True, False),
                3,
            )
        ],
        utterances=[],
        overlays=[],
        portraits=[],
        duration=3,
        music=Soundtrack(music_path, loop=False),
        music_info=MediaInfo(music_path, 1, None, None, False, True),
    )
    command = " ".join(FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4"))
    assert "aloop=" not in command
    assert "atrim=duration=1" in command


def test_clip_label_intervals_follow_cut_order(tmp_path: Path) -> None:
    clips: list[tuple[Clip, MediaInfo, float]] = []
    for index, duration in enumerate((1.0, 2.0, 3.0)):
        path = tmp_path / f"{index}.mp4"
        clip = Clip(path, duration=duration)
        clips.append((clip, MediaInfo(path, duration, 1080, 1920, True, False), duration))
    assert _clip_timeline_intervals(clips) == ((0.0, 1.0), (1.0, 3.0), (3.0, 6.0))


def test_compiler_emits_custom_fit_between_contain_and_cover(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    path = tmp_path / "horizontal.mp4"
    clip = Clip(path, duration=2, fit="custom", fit_amount=0.4, anchor="top-left")
    info = MediaInfo(path, 2, 1920, 1080, True, False)
    media = PreparedMedia(
        clips=[(clip, info, 2)],
        utterances=[],
        overlays=[],
        portraits=[],
        duration=2,
    )
    command = " ".join(FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4"))
    assert "min(1080/iw,1920/ih)" in command
    assert "max(1080/iw,1920/ih)" in command
    assert "*0.4" in command
    assert "crop=w='min(iw,1080)':h='min(ih,1920)':x='0':y='0'" in command
    assert "pad=1080:1920:x='0':y='0':color=black" in command


def test_compiler_builds_blurred_fill_behind_custom_fit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("rot.ffmpeg.executable", lambda name: name)
    path = tmp_path / "horizontal.mp4"
    clip = Clip(
        path,
        duration=2,
        fit="custom",
        fit_amount=0.4,
        fill="blur",
        fill_blur=40,
    )
    info = MediaInfo(path, 2, 1920, 1080, True, False)
    media = PreparedMedia(
        clips=[(clip, info, 2)],
        utterances=[],
        overlays=[],
        portraits=[],
        duration=2,
    )
    command = " ".join(FFmpegCompiler(RenderSettings()).compile(media, tmp_path / "out.mp4"))
    assert "split=2[vfillbgsource0][vfillfgsource0]" in command
    assert "crop=270:480" in command
    assert "gblur=sigma=10:steps=2" in command
    assert "[vfillbg0][vfillfg0]overlay=x=(W-w)/2:y=(H-h)/2:shortest=1" in command
    assert "pad=1080:1920" not in command
