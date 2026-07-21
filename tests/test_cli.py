from pathlib import Path

import pytest

import rot.cli as cli
from rot import (
    ClipSearchResult,
    ConfigurationError,
    Project,
    PublishBatchResult,
    PublishFailure,
    PublishResult,
)
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
    assert _clip_target("https://clips.twitch.tv/ClipSlug")[0] == "twitch"
    assert _clip_target(str(tmp_path)) == ("folder", tmp_path)
    assert _clip_target(str(video)) == ("file", video)

    with pytest.raises(ConfigurationError, match="Unsupported"):
        _clip_target("http://example.com/v")

    with pytest.raises(ConfigurationError) as excinfo:
        _clip_target(str(tmp_path / "missing.mp4"))
    # The error should name all three accepted forms rather than just one.
    message = str(excinfo.value)
    assert "URL" in message and "file" in message and "folder" in message


def test_twitch_clip_cli_loads_credentials_and_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Finder:
        def __init__(self, settings: object, **kwargs: object) -> None:
            captured["credentials"] = kwargs

        def find(self, target: str, output_dir: str, **kwargs: object) -> ClipSearchResult:
            captured["target"] = target
            captured["output_dir"] = output_dir
            captured["options"] = kwargs
            return ClipSearchResult(())

    monkeypatch.setenv("ROT_TWITCH_CLIENT_ID", "client-id")
    monkeypatch.setenv("ROT_TWITCH_ACCESS_TOKEN", "access-token")
    monkeypatch.setattr(cli, "TwitchClipFinder", _Finder)

    assert (
        cli.run(
            [
                "clips",
                "https://clips.twitch.tv/ClipSlug",
                "--download-only",
                "--twitch-variant",
                "portrait",
                "--json",
            ]
        )
        == 0
    )
    assert captured["credentials"] == {
        "client_id": "client-id",
        "access_token": "access-token",
        "cache": True,
    }
    assert captured["options"] == {
        "export": False,
        "overwrite_download": False,
        "overwrite_exports": False,
        "progress": True,
        "variant": "portrait",
    }


def test_publish_cli_returns_partial_failure_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_publishing_jobs", lambda path: [object()])
    monkeypatch.setattr(
        cli,
        "publish_all",
        lambda *args, **kwargs: PublishBatchResult(
            (PublishResult("youtube", "yt-id"),),
            (PublishFailure("instagram", "failed"),),
        ),
    )
    assert cli.run(["publish", "short.mp4", "--config", "publish.toml", "--yes", "--json"]) == 1


def test_publish_json_requires_explicit_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_publishing_jobs", lambda path: [])
    assert cli.run(["publish", "short.mp4", "--config", "publish.toml", "--json"]) == 2
