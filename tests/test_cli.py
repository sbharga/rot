from pathlib import Path

from rot import Project
from rot.cli import _load_project


def test_loads_named_project_from_trusted_python_file(tmp_path: Path) -> None:
    source = tmp_path / "video.py"
    source.write_text(
        "from rot import Project\ncustom = Project.short_form()\n",
        encoding="utf-8",
    )
    assert isinstance(_load_project(f"{source}:custom"), Project)
