from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rot import (
    Clip,
    ClipTranscript,
    ConfigurationError,
    MediaInfo,
    Project,
    Transcript,
    TranscriptSegment,
    WordTiming,
)
from rot.progress import ProgressReporter
from rot.render import Renderer, _clip_transcript_utterances
from rot.transcription import transcribe_clip


class _Transcriber:
    def __init__(self, transcript: Transcript) -> None:
        self.transcript = transcript
        self.calls = 0

    def transcribe(self, audio_path, *, language=None, progress=None):  # noqa: ANN001
        assert Path(audio_path).is_file()
        self.calls += 1
        return self.transcript


def _transcript() -> Transcript:
    return Transcript(
        (
            TranscriptSegment(
                "Hello there",
                0.2,
                1.0,
                (WordTiming("Hello", 0.2, 0.55), WordTiming("there", 0.55, 1.0)),
            ),
        ),
        "en",
    )


def test_transcription_extracts_trimmed_sped_audio_and_uses_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        commands.append(list(command))
        Path(command[-1]).write_bytes(b"wav")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("rot.transcription.subprocess.run", fake_run)
    monkeypatch.setattr("rot.transcription.executable", lambda name: name)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    clip = Clip(
        source,
        trim_start=2,
        trim_end=6,
        speed=2,
        loop=False,
        transcribe=True,
        id="intro",
    )
    info = MediaInfo(source, 10, 320, 180, True, True)
    provider = _Transcriber(_transcript())
    first_workdir = tmp_path / "first"
    second_workdir = tmp_path / "second"
    first_workdir.mkdir()
    second_workdir.mkdir()

    first = transcribe_clip(clip, info, 0, provider, first_workdir)
    second = transcribe_clip(clip, info, 0, provider, second_workdir)

    assert first == second
    assert first.clip_id == "intro"
    assert provider.calls == 1
    assert len(commands) == 1
    assert commands[0][commands[0].index("-ss") + 1] == "2"
    assert commands[0][commands[0].index("-t") + 1] == "4"
    assert commands[0][commands[0].index("-af") + 1] == "atempo=2"


def test_transcription_requires_an_audio_stream(tmp_path: Path) -> None:
    source = tmp_path / "silent.mp4"
    source.touch()
    clip = Clip(source, transcribe=True)
    info = MediaInfo(source, 2, 320, 180, True, False)
    with pytest.raises(ConfigurationError, match="audio stream"):
        transcribe_clip(clip, info, 0, _Transcriber(_transcript()), tmp_path)


def test_empty_transcription_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "quiet.mp4"
    source.touch()
    clip = Clip(source, transcribe=True, id="quiet")
    info = MediaInfo(source, 2, 320, 180, True, True)
    project = Project.short_form().background(clip).with_transcriber(_Transcriber(Transcript()))
    monkeypatch.setattr(
        "rot.render.transcribe_clip",
        lambda *args, **kwargs: ClipTranscript(0, "quiet", source, Transcript()),
    )
    warnings: list[str] = []
    with pytest.warns(RuntimeWarning, match="no detectable speech"):
        results = Renderer(project)._transcribe_selected(
            [(0, clip, info)],
            tmp_path,
            ProgressReporter(False),
            warnings,
        )
    assert len(results) == 1
    assert warnings == ["Clip 'quiet' contains no detectable speech"]


def test_clip_transcripts_repeat_and_respect_transition_ownership(tmp_path: Path) -> None:
    first_path = tmp_path / "first.mp4"
    second_path = tmp_path / "second.mp4"
    first = Clip(first_path, duration=4, loop=True, transition="fade", transcribe=True)
    second = Clip(second_path, duration=2, loop=False)
    clips = [
        (first, MediaInfo(first_path, 2, 320, 180, True, True), 4),
        (second, MediaInfo(second_path, 2, 320, 180, True, True), 2),
    ]
    transcript = ClipTranscript(0, None, first_path, _transcript())
    cues = _clip_transcript_utterances((transcript,), clips)

    assert len(cues) == 2
    assert [cue.start for cue in cues] == pytest.approx([0.2, 2.2])
    assert cues[0].words[0].start == pytest.approx(0.2)
    assert cues[1].words[1].end == pytest.approx(3.0)


def test_prepare_renders_transcribed_and_script_captions_in_separate_lanes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    background = tmp_path / "background.mp4"
    speech = tmp_path / "speech.wav"
    background.touch()
    speech.touch()

    def fake_probe(path: Path) -> MediaInfo:
        if Path(path) == speech:
            return MediaInfo(speech, 1, None, None, False, True)
        return MediaInfo(background, 2, 320, 180, True, True)

    monkeypatch.setattr("rot.render.probe", fake_probe)
    monkeypatch.setattr(
        "rot.render.transcribe_clip",
        lambda clip, info, clip_index, transcriber, workdir, progress: ClipTranscript(
            clip_index, clip.id, background, _transcript()
        ),
    )
    project = (
        Project.short_form()
        .background(background, duration=2, loop=False, transcribe=True)
        .with_transcriber(_Transcriber(_transcript()))
        .add_speaker("narrator")
        .script(f"@narrator [audio={speech}]: Script narration")
    )
    warnings: list[str] = []
    media = Renderer(project)._prepare(tmp_path, ProgressReporter(False), warnings)

    assert media.clip_caption_file is not None and media.clip_caption_file.is_file()
    assert media.caption_file is not None and media.caption_file.is_file()
    assert len(media.caption_utterances) == 2
    clip_ass = media.clip_caption_file.read_text(encoding="utf-8")
    assert r"\an8\pos(540,154)" in clip_ass
    assert "Dialogue: 0,0:00:00.20,0:00:00.55" in clip_ass
    assert r"\c&H0035E1FF" in clip_ass
