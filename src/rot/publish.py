"""Typed publishing through the official short-form platform APIs."""

from __future__ import annotations

import mimetypes
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from .errors import ConfigurationError, DependencyError, PublishError, PublishTimeoutError
from .models import MediaInfo, ProgressCallback
from .probe import probe
from .progress import ProgressReporter

Platform = Literal["youtube", "instagram", "tiktok"]
YouTubePrivacy = Literal["private", "unlisted", "public"]
TikTokPrivacy = Literal[
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
]


@runtime_checkable
class TokenProvider(Protocol):
    """Supplies access tokens and may refresh them after an authorization failure."""

    def access_token(self) -> str: ...

    def refresh_access_token(self) -> str | None: ...


@dataclass(slots=True)
class StaticTokenProvider:
    """An in-memory token provider that deliberately does not persist credentials."""

    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ConfigurationError("Access token cannot be empty")

    def access_token(self) -> str:
        return self.token

    def refresh_access_token(self) -> None:
        return None


type Token = str | TokenProvider


@dataclass(frozen=True, slots=True)
class YouTubeShort:
    title: str
    privacy: YouTubePrivacy
    made_for_kids: bool
    contains_synthetic_media: bool
    has_paid_product_placement: bool
    description: str = ""
    tags: tuple[str, ...] = ()
    category_id: str = "22"

    def __post_init__(self) -> None:
        title = self.title.strip()
        if not title or len(title) > 100:
            raise ConfigurationError("YouTube title must contain 1-100 characters")
        if self.privacy not in {"private", "unlisted", "public"}:
            raise ConfigurationError(f"Unknown YouTube privacy {self.privacy!r}")
        if not self.category_id.isdigit():
            raise ConfigurationError("YouTube category_id must be numeric")
        if any(not tag.strip() for tag in self.tags):
            raise ConfigurationError("YouTube tags cannot be empty")
        object.__setattr__(self, "title", title)


@dataclass(frozen=True, slots=True)
class InstagramReel:
    caption: str = ""
    share_to_feed: bool = True

    def __post_init__(self) -> None:
        if len(self.caption) > 2_200:
            raise ConfigurationError("Instagram caption cannot exceed 2200 characters")


@dataclass(frozen=True, slots=True)
class TikTokVideo:
    privacy: TikTokPrivacy
    allow_comments: bool
    allow_duet: bool
    allow_stitch: bool
    brand_organic: bool
    branded_content: bool
    ai_generated: bool
    caption: str = ""

    def __post_init__(self) -> None:
        allowed = {
            "PUBLIC_TO_EVERYONE",
            "MUTUAL_FOLLOW_FRIENDS",
            "FOLLOWER_OF_CREATOR",
            "SELF_ONLY",
        }
        if self.privacy not in allowed:
            raise ConfigurationError(f"Unknown TikTok privacy {self.privacy!r}")
        if len(self.caption.encode("utf-16-le")) // 2 > 2_200:
            raise ConfigurationError("TikTok caption cannot exceed 2200 UTF-16 code units")


type PublishMetadata = YouTubeShort | InstagramReel | TikTokVideo


@dataclass(frozen=True, slots=True)
class PublishPreflight:
    platform: Platform
    account_name: str | None = None
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class PublishResult:
    platform: Platform
    remote_id: str
    status: str = "published"
    post_id: str | None = None
    url: str | None = None
    account_name: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishFailure:
    platform: Platform
    message: str
    remote_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublishBatchResult:
    results: tuple[PublishResult, ...] = ()
    failures: tuple[PublishFailure, ...] = ()

    @property
    def successful(self) -> bool:
        return bool(self.results) and not self.failures


class Publisher(Protocol):
    platform: Platform

    def accepts(self, metadata: PublishMetadata) -> bool: ...

    def preflight(self, video: str | Path, metadata: PublishMetadata) -> PublishPreflight: ...

    def publish(
        self,
        video: str | Path,
        metadata: PublishMetadata,
        *,
        consent: bool,
        progress: bool | ProgressCallback = True,
        wait_timeout: float = 900.0,
        poll_interval: float = 2.0,
    ) -> PublishResult: ...


@dataclass(frozen=True, slots=True)
class PublishJob:
    publisher: Publisher
    metadata: PublishMetadata

    def __post_init__(self) -> None:
        if not self.publisher.accepts(self.metadata):
            raise ConfigurationError(
                f"{self.publisher.platform} publisher received incompatible metadata"
            )


def _httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise DependencyError(
            "Publishing requires the optional dependency: uv sync --extra publish"
        ) from exc
    return httpx


def _message(response: Any) -> str:
    try:
        body = response.json()
    except (ValueError, TypeError):
        return "request rejected"
    if not isinstance(body, dict):
        return "request rejected"
    error = body.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "request rejected")
    return str(body.get("message") or "request rejected")


class _RequestError(PublishError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.status_code is None or self.status_code == 429 or self.status_code >= 500


def _json(response: Any, platform: Platform) -> dict[str, Any]:
    try:
        value = response.json()
    except (ValueError, TypeError) as exc:
        raise PublishError(f"{platform.title()} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise PublishError(f"{platform.title()} returned an invalid response")
    return value


def _token_provider(token: Token) -> TokenProvider:
    return StaticTokenProvider(token) if isinstance(token, str) else token


def _request(
    platform: Platform,
    provider: TokenProvider,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    retry_auth: bool = True,
    timeout: float = 60.0,
    **kwargs: Any,
) -> Any:
    httpx = _httpx()
    token = provider.access_token().strip()
    if not token:
        raise PublishError(f"{platform.title()} access token is empty")
    request_headers = dict(headers or {})
    request_headers.setdefault("Authorization", f"Bearer {token}")
    try:
        response = httpx.request(method, url, headers=request_headers, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        raise _RequestError(
            f"{platform.title()} request failed: {type(exc).__name__}"
        ) from exc
    if response.status_code == 401 and retry_auth:
        refreshed = provider.refresh_access_token()
        if refreshed:
            token = refreshed
            request_headers["Authorization"] = f"Bearer {refreshed}"
            try:
                response = httpx.request(
                    method, url, headers=request_headers, timeout=timeout, **kwargs
                )
            except httpx.HTTPError as exc:
                raise _RequestError(
                    f"{platform.title()} request failed after token refresh: {type(exc).__name__}"
                ) from exc
    if response.status_code >= 400:
        message = _message(response).replace(token, "[redacted]")
        retry_after = response.headers.get("Retry-After")
        try:
            retry_delay = float(retry_after) if retry_after is not None else None
        except ValueError:
            retry_delay = None
        raise _RequestError(
            f"{platform.title()} returned HTTP {response.status_code}: {message}",
            status_code=response.status_code,
            retry_after=retry_delay,
        )
    return response


def _retry_request(
    platform: Platform,
    provider: TokenProvider,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    **kwargs: Any,
) -> Any:
    last: PublishError | None = None
    for attempt in range(attempts):
        try:
            return _request(platform, provider, method, url, **kwargs)
        except PublishError as exc:
            last = exc
            if attempt + 1 == attempts or (
                isinstance(exc, _RequestError) and not exc.retryable
            ):
                break
            delay = exc.retry_after if isinstance(exc, _RequestError) else None
            time.sleep(
                min(60.0, max(0.0, delay))
                if delay is not None
                else min(8.0, 0.5 * (2**attempt))
            )
    assert last is not None
    raise last


def _validated_path(video: str | Path) -> Path:
    path = Path(video).expanduser()
    if not path.is_file():
        raise ConfigurationError(f"Publishing input does not exist: {path}")
    if path.suffix.lower() != ".mp4":
        raise ConfigurationError("Publishing currently requires an MP4 file")
    if path.stat().st_size <= 0:
        raise ConfigurationError("Publishing input cannot be empty")
    return path


def _validate_media(video: str | Path, platform: Platform) -> tuple[Path, MediaInfo]:
    path = _validated_path(video)
    info = probe(path)
    if not info.has_video or info.width is None or info.height is None:
        raise ConfigurationError("Publishing input must contain a video stream")
    if info.width > info.height:
        raise ConfigurationError("Short-form publishing requires square or vertical video")
    if info.video_codec != "h264":
        raise ConfigurationError("Publishing input must use H.264 video")
    if not info.has_audio or info.audio_codec != "aac" or info.sample_rate != 48_000:
        raise ConfigurationError("Publishing input must use AAC audio at 48 kHz")
    if info.frame_rate is None or not 23 <= info.frame_rate <= 60:
        raise ConfigurationError("Publishing input frame rate must be between 23 and 60 fps")
    size = path.stat().st_size
    if platform == "youtube" and info.duration > 180:
        raise ConfigurationError("YouTube Shorts cannot exceed 180 seconds")
    if platform == "instagram":
        if not 3 <= info.duration <= 900:
            raise ConfigurationError("Instagram Reels must be between 3 and 900 seconds")
        if size > 1_000_000_000 or info.width > 1920:
            raise ConfigurationError("Instagram Reel exceeds the 1 GB or 1920-pixel limit")
    if platform == "tiktok":
        if info.width < 360 or info.height < 360 or info.width > 4096 or info.height > 4096:
            raise ConfigurationError("TikTok dimensions must be between 360 and 4096 pixels")
        if size > 4_000_000_000:
            raise ConfigurationError("TikTok video exceeds the 4 GB limit")
    return path, info


class _BasePublisher:
    platform: Platform
    _provider: TokenProvider

    def _check_options(self, consent: bool, wait_timeout: float, poll_interval: float) -> None:
        if not consent:
            raise ConfigurationError("Publishing requires explicit consent=True")
        if wait_timeout <= 0 or poll_interval < 0:
            raise ConfigurationError("wait_timeout must be positive and poll_interval non-negative")


class YouTubePublisher(_BasePublisher):
    platform: Platform = "youtube"

    def __init__(self, token: Token, *, chunk_size: int = 8 * 1024 * 1024) -> None:
        if chunk_size <= 0 or chunk_size % (256 * 1024):
            raise ConfigurationError("YouTube chunk_size must be a positive multiple of 256 KiB")
        self._provider = _token_provider(token)
        self.chunk_size = chunk_size

    def __repr__(self) -> str:
        return f"YouTubePublisher(chunk_size={self.chunk_size!r})"

    def accepts(self, metadata: PublishMetadata) -> bool:
        return isinstance(metadata, YouTubeShort)

    def preflight(self, video: str | Path, metadata: PublishMetadata) -> PublishPreflight:
        if not isinstance(metadata, YouTubeShort):
            raise ConfigurationError("YouTubePublisher requires YouTubeShort metadata")
        _validate_media(video, "youtube")
        return PublishPreflight(
            "youtube", details={"privacy": metadata.privacy, "title": metadata.title}
        )

    def publish(
        self,
        video: str | Path,
        metadata: PublishMetadata,
        *,
        consent: bool,
        progress: bool | ProgressCallback = True,
        wait_timeout: float = 900.0,
        poll_interval: float = 2.0,
    ) -> PublishResult:
        self._check_options(consent, wait_timeout, poll_interval)
        check = self.preflight(video, metadata)
        assert isinstance(metadata, YouTubeShort)
        path = _validated_path(video)
        return self._publish(path, metadata, check, progress, wait_timeout, poll_interval)

    def _publish(
        self,
        path: Path,
        metadata: YouTubeShort,
        check: PublishPreflight,
        progress: bool | ProgressCallback,
        wait_timeout: float,
        poll_interval: float,
    ) -> PublishResult:
        size = path.stat().st_size
        body = {
            "snippet": {
                "title": metadata.title,
                "description": metadata.description,
                "tags": list(metadata.tags),
                "categoryId": metadata.category_id,
            },
            "status": {
                "privacyStatus": metadata.privacy,
                "selfDeclaredMadeForKids": metadata.made_for_kids,
                "containsSyntheticMedia": metadata.contains_synthetic_media,
            },
            "paidProductPlacementDetails": {
                "hasPaidProductPlacement": metadata.has_paid_product_placement
            },
        }
        endpoint = (
            "https://www.googleapis.com/upload/youtube/v3/videos"
            "?uploadType=resumable&part=snippet,status,paidProductPlacementDetails"
        )
        headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        }
        with ProgressReporter(progress) as reporter:
            reporter.emit("youtube:initialize", 0, 1, "Starting YouTube upload")
            response = _request(
                "youtube", self._provider, "POST", endpoint, headers=headers, json=body
            )
            upload_url = response.headers.get("Location")
            if not upload_url:
                raise PublishError("YouTube did not return a resumable upload URL")
            offset = 0
            resource: dict[str, Any] | None = None
            with path.open("rb") as source:
                while offset < size:
                    source.seek(offset)
                    chunk = source.read(min(self.chunk_size, size - offset))
                    end = offset + len(chunk) - 1
                    upload = _retry_request(
                        "youtube",
                        self._provider,
                        "PUT",
                        upload_url,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                        content=chunk,
                        timeout=120.0,
                    )
                    if upload.status_code in {200, 201}:
                        resource = _json(upload, "youtube")
                        offset = size
                    elif upload.status_code == 308:
                        match = re.search(r"(\d+)$", upload.headers.get("Range", ""))
                        offset = int(match.group(1)) + 1 if match else end + 1
                    else:
                        raise PublishError(
                            f"YouTube returned unexpected upload status {upload.status_code}"
                        )
                    reporter.emit("youtube:upload", offset, size, "Uploading to YouTube")
            video_id = str((resource or {}).get("id", ""))
            if not video_id:
                raise PublishError("YouTube upload completed without a video id")
            deadline = time.monotonic() + wait_timeout
            while time.monotonic() < deadline:
                status_response = _retry_request(
                    "youtube",
                    self._provider,
                    "GET",
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "status", "id": video_id},
                )
                items = _json(status_response, "youtube").get("items", [])
                status = items[0].get("status", {}) if items else {}
                upload_status = status.get("uploadStatus")
                reporter.emit("youtube:processing", 0, 1, "YouTube is processing the video")
                if upload_status == "processed":
                    return PublishResult(
                        "youtube",
                        video_id,
                        post_id=video_id,
                        url=f"https://youtu.be/{video_id}",
                        warnings=check.warnings,
                    )
                if upload_status in {"failed", "rejected", "deleted"}:
                    reason = status.get("failureReason") or status.get("rejectionReason")
                    raise PublishError(f"YouTube processing {upload_status}: {reason or 'unknown'}")
                time.sleep(poll_interval)
        raise PublishTimeoutError(
            "YouTube processing did not finish before the timeout",
            platform="youtube",
            remote_id=video_id,
        )


class InstagramPublisher(_BasePublisher):
    platform: Platform = "instagram"

    def __init__(self, token: Token, user_id: str, *, api_version: str = "v25.0") -> None:
        if not user_id.strip():
            raise ConfigurationError("Instagram user_id cannot be empty")
        if not re.fullmatch(r"v\d+\.\d+", api_version):
            raise ConfigurationError("Instagram api_version must look like v25.0")
        self._provider = _token_provider(token)
        self.user_id = user_id.strip()
        self.api_version = api_version

    def __repr__(self) -> str:
        return f"InstagramPublisher(user_id={self.user_id!r}, api_version={self.api_version!r})"

    @property
    def _base(self) -> str:
        return f"https://graph.instagram.com/{self.api_version}"

    def accepts(self, metadata: PublishMetadata) -> bool:
        return isinstance(metadata, InstagramReel)

    def preflight(self, video: str | Path, metadata: PublishMetadata) -> PublishPreflight:
        if not isinstance(metadata, InstagramReel):
            raise ConfigurationError("InstagramPublisher requires InstagramReel metadata")
        _validate_media(video, "instagram")
        response = _request(
            "instagram",
            self._provider,
            "GET",
            f"{self._base}/{self.user_id}",
            params={"fields": "username"},
        )
        account = str(_json(response, "instagram").get("username") or self.user_id)
        return PublishPreflight(
            "instagram",
            account_name=account,
            details={"share_to_feed": metadata.share_to_feed},
        )

    def publish(
        self,
        video: str | Path,
        metadata: PublishMetadata,
        *,
        consent: bool,
        progress: bool | ProgressCallback = True,
        wait_timeout: float = 900.0,
        poll_interval: float = 2.0,
    ) -> PublishResult:
        self._check_options(consent, wait_timeout, poll_interval)
        check = self.preflight(video, metadata)
        assert isinstance(metadata, InstagramReel)
        return self._publish(
            _validated_path(video), metadata, check, progress, wait_timeout, poll_interval
        )

    def _publish(
        self,
        path: Path,
        metadata: InstagramReel,
        check: PublishPreflight,
        progress: bool | ProgressCallback,
        wait_timeout: float,
        poll_interval: float,
    ) -> PublishResult:
        with ProgressReporter(progress) as reporter:
            reporter.emit("instagram:initialize", 0, 1, "Creating Instagram Reel container")
            response = _request(
                "instagram",
                self._provider,
                "POST",
                f"{self._base}/{self.user_id}/media",
                data={
                    "media_type": "REELS",
                    "upload_type": "resumable",
                    "caption": metadata.caption,
                    "share_to_feed": str(metadata.share_to_feed).lower(),
                },
            )
            container = _json(response, "instagram")
            container_id = str(container.get("id", ""))
            if not container_id:
                raise PublishError("Instagram did not return a container id")
            upload_url = str(
                container.get("uri")
                or f"https://rupload.facebook.com/ig-api-upload/{self.api_version}/{container_id}"
            )
            size = path.stat().st_size
            with path.open("rb") as source:
                upload = _request(
                    "instagram",
                    self._provider,
                    "POST",
                    upload_url,
                    headers={
                        "Authorization": f"OAuth {self._provider.access_token()}",
                        "offset": "0",
                        "file_size": str(size),
                        "Content-Type": "application/octet-stream",
                    },
                    content=source,
                    timeout=300.0,
                )
            reporter.emit("instagram:upload", 1, 1, "Uploaded Reel to Instagram")
            if upload.status_code >= 300:
                raise PublishError(f"Instagram upload returned HTTP {upload.status_code}")
            deadline = time.monotonic() + wait_timeout
            while time.monotonic() < deadline:
                status_response = _retry_request(
                    "instagram",
                    self._provider,
                    "GET",
                    f"{self._base}/{container_id}",
                    params={"fields": "status_code,status"},
                )
                status = _json(status_response, "instagram")
                code = status.get("status_code")
                reporter.emit("instagram:processing", 0, 1, "Instagram is processing the Reel")
                if code == "FINISHED":
                    break
                if code in {"ERROR", "EXPIRED"}:
                    raise PublishError(f"Instagram container {str(code).lower()}: {status.get('status')}")
                time.sleep(poll_interval)
            else:
                raise PublishTimeoutError(
                    "Instagram processing did not finish before the timeout",
                    platform="instagram",
                    remote_id=container_id,
                )
            published = _request(
                "instagram",
                self._provider,
                "POST",
                f"{self._base}/{self.user_id}/media_publish",
                data={"creation_id": container_id},
            )
            media_id = str(_json(published, "instagram").get("id", ""))
            if not media_id:
                raise PublishError("Instagram published without returning a media id")
            details = _retry_request(
                "instagram",
                self._provider,
                "GET",
                f"{self._base}/{media_id}",
                params={"fields": "permalink"},
            )
            permalink = _json(details, "instagram").get("permalink")
            reporter.emit("instagram:publish", 1, 1, "Published Instagram Reel")
            return PublishResult(
                "instagram",
                container_id,
                post_id=media_id,
                url=str(permalink) if permalink else None,
                account_name=check.account_name,
                warnings=check.warnings,
            )


class TikTokPublisher(_BasePublisher):
    platform: Platform = "tiktok"
    _base = "https://open.tiktokapis.com/v2/post/publish"

    def __init__(self, token: Token) -> None:
        self._provider = _token_provider(token)

    def __repr__(self) -> str:
        return "TikTokPublisher()"

    def accepts(self, metadata: PublishMetadata) -> bool:
        return isinstance(metadata, TikTokVideo)

    def preflight(self, video: str | Path, metadata: PublishMetadata) -> PublishPreflight:
        if not isinstance(metadata, TikTokVideo):
            raise ConfigurationError("TikTokPublisher requires TikTokVideo metadata")
        _, info = _validate_media(video, "tiktok")
        response = _request(
            "tiktok",
            self._provider,
            "POST",
            f"{self._base}/creator_info/query/",
            headers={"Content-Type": "application/json; charset=UTF-8"},
            json={},
        )
        envelope = _json(response, "tiktok")
        error = envelope.get("error", {})
        if isinstance(error, dict) and error.get("code") not in {None, "ok"}:
            raise PublishError(f"TikTok creator lookup failed: {error.get('message') or error.get('code')}")
        data = envelope.get("data", {})
        if not isinstance(data, dict):
            raise PublishError("TikTok returned invalid creator information")
        options = data.get("privacy_level_options", [])
        if metadata.privacy not in options:
            raise ConfigurationError(
                f"TikTok account does not allow privacy level {metadata.privacy!r}"
            )
        maximum = data.get("max_video_post_duration_sec")
        if isinstance(maximum, (int, float)) and info.duration > maximum:
            raise ConfigurationError(
                f"TikTok account allows videos up to {maximum:g} seconds; got {info.duration:g}"
            )
        conflicts = (
            (metadata.allow_comments, data.get("comment_disabled"), "comments"),
            (metadata.allow_duet, data.get("duet_disabled"), "duet"),
            (metadata.allow_stitch, data.get("stitch_disabled"), "stitch"),
        )
        for enabled, disabled, label in conflicts:
            if enabled and disabled is True:
                raise ConfigurationError(f"TikTok account has {label} disabled")
        account = str(data.get("creator_nickname") or data.get("creator_username") or "TikTok")
        return PublishPreflight(
            "tiktok",
            account_name=account,
            details={
                **data,
                "selected_privacy": metadata.privacy,
                "allow_comments": metadata.allow_comments,
                "allow_duet": metadata.allow_duet,
                "allow_stitch": metadata.allow_stitch,
                "ai_generated": metadata.ai_generated,
            },
        )

    def publish(
        self,
        video: str | Path,
        metadata: PublishMetadata,
        *,
        consent: bool,
        progress: bool | ProgressCallback = True,
        wait_timeout: float = 900.0,
        poll_interval: float = 2.0,
    ) -> PublishResult:
        self._check_options(consent, wait_timeout, poll_interval)
        check = self.preflight(video, metadata)
        assert isinstance(metadata, TikTokVideo)
        return self._publish(
            _validated_path(video), metadata, check, progress, wait_timeout, poll_interval
        )

    @staticmethod
    def _chunks(size: int) -> tuple[int, int]:
        maximum = 64 * 1024 * 1024
        if size <= maximum:
            return size, 1
        chunk_size = maximum
        return chunk_size, max(1, size // chunk_size)

    def _publish(
        self,
        path: Path,
        metadata: TikTokVideo,
        check: PublishPreflight,
        progress: bool | ProgressCallback,
        wait_timeout: float,
        poll_interval: float,
    ) -> PublishResult:
        size = path.stat().st_size
        chunk_size, chunk_count = self._chunks(size)
        payload = {
            "post_info": {
                "title": metadata.caption,
                "privacy_level": metadata.privacy,
                "disable_comment": not metadata.allow_comments,
                "disable_duet": not metadata.allow_duet,
                "disable_stitch": not metadata.allow_stitch,
                "brand_organic_toggle": metadata.brand_organic,
                "brand_content_toggle": metadata.branded_content,
                "is_aigc": metadata.ai_generated,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        }
        with ProgressReporter(progress) as reporter:
            reporter.emit("tiktok:initialize", 0, 1, "Starting TikTok upload")
            response = _request(
                "tiktok",
                self._provider,
                "POST",
                f"{self._base}/video/init/",
                headers={"Content-Type": "application/json; charset=UTF-8"},
                json=payload,
            )
            envelope = _json(response, "tiktok")
            error = envelope.get("error", {})
            if isinstance(error, dict) and error.get("code") not in {None, "ok"}:
                raise PublishError(f"TikTok rejected the post: {error.get('message') or error.get('code')}")
            data = envelope.get("data", {})
            publish_id = str(data.get("publish_id", "")) if isinstance(data, dict) else ""
            upload_url = str(data.get("upload_url", "")) if isinstance(data, dict) else ""
            if not publish_id or not upload_url:
                raise PublishError("TikTok did not return publish and upload identifiers")
            offset = 0
            with path.open("rb") as source:
                for index in range(chunk_count):
                    length = chunk_size if index < chunk_count - 1 else size - offset
                    chunk = source.read(length)
                    end = offset + len(chunk) - 1
                    upload = _retry_request(
                        "tiktok",
                        self._provider,
                        "PUT",
                        upload_url,
                        headers={
                            "Content-Type": mimetypes.guess_type(path.name)[0] or "video/mp4",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                        content=chunk,
                        timeout=180.0,
                    )
                    expected = 201 if index == chunk_count - 1 else 206
                    if upload.status_code != expected:
                        raise PublishError(
                            f"TikTok chunk upload returned HTTP {upload.status_code}; expected {expected}"
                        )
                    offset = end + 1
                    reporter.emit("tiktok:upload", offset, size, "Uploading to TikTok")
            deadline = time.monotonic() + wait_timeout
            while time.monotonic() < deadline:
                status_response = _retry_request(
                    "tiktok",
                    self._provider,
                    "POST",
                    f"{self._base}/status/fetch/",
                    headers={"Content-Type": "application/json; charset=UTF-8"},
                    json={"publish_id": publish_id},
                )
                status_envelope = _json(status_response, "tiktok")
                data = status_envelope.get("data", {})
                status = data.get("status") if isinstance(data, dict) else None
                reporter.emit("tiktok:processing", 0, 1, "TikTok is processing the video")
                if status == "PUBLISH_COMPLETE":
                    post_ids = data.get("publicly_available_post_id", [])
                    if not post_ids:
                        post_ids = data.get("publicaly_available_post_id", [])
                    if not post_ids:
                        post_ids = data.get("post_id", [])
                    post_id = str(post_ids[0]) if isinstance(post_ids, list) and post_ids else None
                    return PublishResult(
                        "tiktok",
                        publish_id,
                        post_id=post_id,
                        account_name=check.account_name,
                        warnings=check.warnings,
                    )
                if status == "FAILED":
                    raise PublishError(f"TikTok publishing failed: {data.get('fail_reason')}")
                time.sleep(poll_interval)
        raise PublishTimeoutError(
            "TikTok processing did not finish before the timeout",
            platform="tiktok",
            remote_id=publish_id,
        )


def publish_all(
    video: str | Path,
    jobs: Sequence[PublishJob],
    *,
    consent: bool,
    progress: bool | ProgressCallback = True,
    wait_timeout: float = 900.0,
    poll_interval: float = 2.0,
    on_preflight: Callable[[tuple[PublishPreflight, ...]], bool | None] | None = None,
) -> PublishBatchResult:
    """Preflight every target, then publish every valid job in the supplied order."""

    if not jobs:
        raise ConfigurationError("At least one publishing job is required")
    if wait_timeout <= 0 or poll_interval < 0:
        raise ConfigurationError("wait_timeout must be positive and poll_interval non-negative")
    valid: list[tuple[PublishJob, PublishPreflight]] = []
    failures: list[PublishFailure] = []
    for job in jobs:
        try:
            valid.append((job, job.publisher.preflight(video, job.metadata)))
        except (ConfigurationError, PublishError) as exc:
            failures.append(PublishFailure(job.publisher.platform, str(exc)))
    callback_consent = on_preflight(tuple(item[1] for item in valid)) if on_preflight else None
    if valid and not (consent or callback_consent is True):
        raise ConfigurationError("Publishing requires explicit consent")
    results: list[PublishResult] = []
    for job, check in valid:
        try:
            publisher = job.publisher
            if isinstance(publisher, YouTubePublisher) and isinstance(  # noqa: SIM114
                job.metadata, YouTubeShort
            ):
                result = publisher._publish(
                    _validated_path(video),
                    job.metadata,
                    check,
                    progress,
                    wait_timeout,
                    poll_interval,
                )
            elif isinstance(publisher, InstagramPublisher) and isinstance(  # noqa: SIM114
                job.metadata, InstagramReel
            ):
                result = publisher._publish(
                    _validated_path(video),
                    job.metadata,
                    check,
                    progress,
                    wait_timeout,
                    poll_interval,
                )
            elif isinstance(publisher, TikTokPublisher) and isinstance(job.metadata, TikTokVideo):
                result = publisher._publish(
                    _validated_path(video),
                    job.metadata,
                    check,
                    progress,
                    wait_timeout,
                    poll_interval,
                )
            else:
                raise ConfigurationError("Unsupported publisher job")
            results.append(result)
        except PublishTimeoutError as exc:
            failures.append(PublishFailure(job.publisher.platform, str(exc), exc.remote_id))
        except (ConfigurationError, PublishError) as exc:
            failures.append(PublishFailure(job.publisher.platform, str(exc)))
    return PublishBatchResult(tuple(results), tuple(failures))


__all__ = [
    "InstagramPublisher",
    "InstagramReel",
    "PublishBatchResult",
    "PublishFailure",
    "PublishJob",
    "PublishPreflight",
    "PublishResult",
    "StaticTokenProvider",
    "TikTokPublisher",
    "TikTokVideo",
    "TokenProvider",
    "YouTubePublisher",
    "YouTubeShort",
    "publish_all",
]
