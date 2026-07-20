from pathlib import Path

import pytest

from rot import ConfigurationError, MediaInfo, Project, RenderError
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
