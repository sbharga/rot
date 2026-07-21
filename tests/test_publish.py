from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import rot.publish as publishing
from rot import (
    ConfigurationError,
    InstagramPublisher,
    InstagramReel,
    PublishError,
    PublishJob,
    PublishPreflight,
    PublishResult,
    TikTokPublisher,
    TikTokVideo,
    YouTubePublisher,
    YouTubeShort,
    publish_all,
)
from rot.cli import _publishing_jobs


class _Response:
    def __init__(
        self,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return self._body


def _video(tmp_path: Path, size: int = 32) -> Path:
    path = tmp_path / "short.mp4"
    path.write_bytes(b"x" * size)
    return path


def _youtube() -> YouTubeShort:
    return YouTubeShort(
        "A short",
        "private",
        made_for_kids=False,
        contains_synthetic_media=True,
        has_paid_product_placement=False,
    )


def _tiktok() -> TikTokVideo:
    return TikTokVideo(
        "SELF_ONLY",
        allow_comments=True,
        allow_duet=False,
        allow_stitch=False,
        brand_organic=False,
        branded_content=False,
        ai_generated=True,
        caption="A short #rot",
    )


def test_metadata_requires_explicit_safe_values() -> None:
    with pytest.raises(ConfigurationError, match="title"):
        YouTubeShort("", "private", False, False, False)
    with pytest.raises(ConfigurationError, match="privacy"):
        TikTokVideo("invalid", True, True, True, False, False, False)  # type: ignore[arg-type]
    assert "secret" not in repr(YouTubePublisher("secret"))


def test_http_errors_redact_tokens_and_do_not_retry_permanent_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class _HTTPX:
        class HTTPError(Exception):
            pass

        @staticmethod
        def request(*args: object, **kwargs: object) -> _Response:
            nonlocal calls
            calls += 1
            return _Response(400, {"error": {"message": "bad secret-token"}})

    monkeypatch.setattr(publishing, "_httpx", lambda: _HTTPX)
    with pytest.raises(PublishError) as excinfo:
        publishing._retry_request(
            "youtube",
            publishing.StaticTokenProvider("secret-token"),
            "GET",
            "https://example.test",
        )
    assert "secret-token" not in str(excinfo.value)
    assert "[redacted]" in str(excinfo.value)
    assert calls == 1


def test_youtube_resumable_upload_and_processing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _video(tmp_path)
    monkeypatch.setattr(publishing, "_validate_media", lambda *args: (path, object()))
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(
        platform: str, provider: object, method: str, url: str, **kwargs: Any
    ) -> _Response:
        calls.append((method, url, kwargs))
        if method == "POST":
            return _Response(headers={"Location": "https://upload.youtube.test/session"})
        if method == "PUT":
            return _Response(201, {"id": "youtube-id"})
        return _Response(body={"items": [{"status": {"uploadStatus": "processed"}}]})

    monkeypatch.setattr(publishing, "_request", request)
    result = YouTubePublisher("token").publish(
        path, _youtube(), consent=True, progress=False, poll_interval=0
    )
    assert result.url == "https://youtu.be/youtube-id"
    assert calls[0][2]["json"]["status"]["containsSyntheticMedia"] is True
    assert calls[1][2]["headers"]["Content-Range"] == "bytes 0-31/32"


def test_instagram_resumable_container_publish(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _video(tmp_path)
    monkeypatch.setattr(publishing, "_validate_media", lambda *args: (path, object()))

    def request(
        platform: str, provider: object, method: str, url: str, **kwargs: Any
    ) -> _Response:
        if method == "GET" and url.endswith("/ig-user"):
            return _Response(body={"username": "creator"})
        if method == "POST" and url.endswith("/ig-user/media"):
            return _Response(body={"id": "container", "uri": "https://upload.meta.test"})
        if url == "https://upload.meta.test":
            return _Response()
        if method == "GET" and url.endswith("/container"):
            return _Response(body={"status_code": "FINISHED"})
        if method == "POST" and url.endswith("/media_publish"):
            return _Response(body={"id": "media-id"})
        if method == "GET" and url.endswith("/media-id"):
            return _Response(body={"permalink": "https://instagram.test/reel/media-id"})
        raise AssertionError((method, url))

    monkeypatch.setattr(publishing, "_request", request)
    result = InstagramPublisher("token", "ig-user").publish(
        path, InstagramReel("caption"), consent=True, progress=False, poll_interval=0
    )
    assert result.post_id == "media-id"
    assert result.account_name == "creator"
    assert result.url == "https://instagram.test/reel/media-id"


def test_tiktok_creator_preflight_chunk_upload_and_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _video(tmp_path)
    media = type("Media", (), {"duration": 20.0})()
    monkeypatch.setattr(publishing, "_validate_media", lambda *args: (path, media))
    payloads: list[dict[str, Any]] = []

    def request(
        platform: str, provider: object, method: str, url: str, **kwargs: Any
    ) -> _Response:
        if url.endswith("creator_info/query/"):
            return _Response(
                body={
                    "data": {
                        "creator_nickname": "Tik Creator",
                        "privacy_level_options": ["SELF_ONLY"],
                        "comment_disabled": False,
                        "duet_disabled": False,
                        "stitch_disabled": False,
                        "max_video_post_duration_sec": 60,
                    },
                    "error": {"code": "ok"},
                }
            )
        if url.endswith("video/init/"):
            payloads.append(kwargs["json"])
            return _Response(
                body={
                    "data": {"publish_id": "publish-id", "upload_url": "https://upload.tt"},
                    "error": {"code": "ok"},
                }
            )
        if url == "https://upload.tt":
            return _Response(201)
        if url.endswith("status/fetch/"):
            return _Response(
                body={
                    "data": {
                        "status": "PUBLISH_COMPLETE",
                        "publicly_available_post_id": [123],
                    },
                    "error": {"code": "ok"},
                }
            )
        raise AssertionError((method, url))

    monkeypatch.setattr(publishing, "_request", request)
    result = TikTokPublisher("token").publish(
        path, _tiktok(), consent=True, progress=False, poll_interval=0
    )
    assert result.remote_id == "publish-id"
    assert result.post_id == "123"
    assert payloads[0]["post_info"]["is_aigc"] is True
    assert payloads[0]["source_info"]["total_chunk_count"] == 1


def test_tiktok_rejects_account_setting_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _video(tmp_path)
    media = type("Media", (), {"duration": 20.0})()
    monkeypatch.setattr(publishing, "_validate_media", lambda *args: (path, media))
    monkeypatch.setattr(
        publishing,
        "_request",
        lambda *args, **kwargs: _Response(
            body={
                "data": {
                    "privacy_level_options": ["SELF_ONLY"],
                    "comment_disabled": True,
                    "max_video_post_duration_sec": 60,
                },
                "error": {"code": "ok"},
            }
        ),
    )
    with pytest.raises(ConfigurationError, match="comments"):
        TikTokPublisher("token").preflight(path, _tiktok())


def test_batch_continues_after_platform_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _video(tmp_path)
    monkeypatch.setattr(
        YouTubePublisher,
        "preflight",
        lambda self, video, metadata: PublishPreflight("youtube"),
    )
    monkeypatch.setattr(
        InstagramPublisher,
        "preflight",
        lambda self, video, metadata: PublishPreflight("instagram"),
    )
    monkeypatch.setattr(
        YouTubePublisher,
        "_publish",
        lambda self, *args: PublishResult("youtube", "yt"),
    )

    def fail(*args: object) -> PublishResult:
        raise PublishError("Meta unavailable")

    monkeypatch.setattr(InstagramPublisher, "_publish", fail)
    result = publish_all(
        path,
        [
            PublishJob(YouTubePublisher("token"), _youtube()),
            PublishJob(InstagramPublisher("token", "id"), InstagramReel()),
        ],
        consent=True,
        progress=False,
    )
    assert [item.platform for item in result.results] == ["youtube"]
    assert result.failures[0].platform == "instagram"


def test_toml_config_uses_environment_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "publish.toml"
    config.write_text(
        """
[youtube]
title = "Test"
privacy = "private"
made_for_kids = false
contains_synthetic_media = true
has_paid_product_placement = false
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ROT_YOUTUBE_ACCESS_TOKEN", "secret")
    jobs = _publishing_jobs(config)
    assert len(jobs) == 1
    assert isinstance(jobs[0].metadata, YouTubeShort)
    assert "secret" not in repr(jobs[0].publisher)


def test_toml_rejects_credentials_and_unknown_keys(tmp_path: Path) -> None:
    config = tmp_path / "publish.toml"
    config.write_text('[youtube]\ntoken = "do-not-store-this"\n', encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Unknown.*token"):
        _publishing_jobs(config)
