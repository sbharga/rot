from pathlib import Path

import pytest

from rot import ConfigurationError, Project
from rot.cli import _clip_target, _load_project


def test_loads_named_project_from_trusted_python_file(tmp_path: Path) -> None:
    source = tmp_path / "video.py"
    source.write_text(
        "from rot import Project\ncustom = Project.short_form()\n",
        encoding="utf-8",
    )
    assert isinstance(_load_project(f"{source}:custom"), Project)


def test_clip_target_dispatches_url_folder_and_file(tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.touch()

    assert _clip_target("https://youtu.be/abc")[0] == "youtube"
    assert _clip_target("http://example.com/v")[0] == "youtube"
    assert _clip_target(str(tmp_path)) == ("folder", tmp_path)
    assert _clip_target(str(video)) == ("file", video)

    with pytest.raises(ConfigurationError) as excinfo:
        _clip_target(str(tmp_path / "missing.mp4"))
    # The error should name all three accepted forms rather than just one.
    message = str(excinfo.value)
    assert "URL" in message and "file" in message and "folder" in message
