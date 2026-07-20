from pathlib import Path

import pytest

from rot import CaptionRenderer, ConfigurationError, MediaInfo, Project, RenderError, Script
from rot.render import Renderer


def test_missing_background_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="background"):
        Renderer(Project.short_form())._validate()


def test_unknown_speaker_is_rejected(tmp_path: Path) -> None:
    background = tmp_path / "background.mp4"
    background.touch()
    project = Project.short_form().background(background).script("@ghost [audio=x.wav]: boo")
    with pytest.raises(ConfigurationError, match="unknown speaker"):
        Renderer(project)._validate()


def test_existing_output_is_protected(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    output.touch()
    with pytest.raises(ConfigurationError, match="already exists"):
        Project.short_form().render(output, progress=False)


def test_output_contract_is_checked(tmp_path: Path) -> None:
    project = Project.short_form()
    info = MediaInfo(tmp_path / "bad.mp4", 1, 720, 1280, True, True)
    with pytest.raises(RenderError, match="failed output validation"):
        Renderer(project)._validate_output(info)


def test_duplicate_clip_ids_and_unknown_text_targets_are_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.touch()
    second.touch()
    duplicate = (
        Project.short_form()
        .background(first, clip_id="same")
        .add_clip(second, clip_id="same")
    )
    with pytest.raises(ConfigurationError, match="unique"):
        Renderer(duplicate)._validate()

    missing = Project.short_form().background(first).overlay_text(
        "Missing", during_clip="unknown"
    )
    with pytest.raises(ConfigurationError, match="unknown clip id"):
        Renderer(missing)._validate()


def test_with_caption_renderer_replaces_the_default() -> None:
    class RecordingRenderer:
        def render(self, path, utterances, theme, *, width, height):  # noqa: ANN001
            path.write_text("recorded", encoding="utf-8")
            return path

    renderer: CaptionRenderer = RecordingRenderer()
    project = Project.short_form().with_caption_renderer(renderer)
    assert project.caption_renderer is renderer


def test_script_accepts_a_custom_parser() -> None:
    class FixedParser:
        def parse(self, source: str) -> Script:
            return Script(utterances=[])

    project = Project.short_form().script("anything", parser=FixedParser())
    assert project.script_data is not None
    assert project.script_data.utterances == []
