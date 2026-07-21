---
layout: default
title: Publishing
parent: Guides
nav_order: 4
---

# Publishing

`rot` publishes an already-rendered MP4 to YouTube Shorts, Instagram Reels, and TikTok. Rendering
and publishing stay separate so a render can be reviewed before an irreversible network action.

## Install and authorize

```console
uv sync --extra publish
```

Create developer applications directly with each platform and obtain user access tokens. The
publishers use these official permissions and account types:

| Platform | Requirement |
| --- | --- |
| YouTube | OAuth `youtube.upload`; unverified API projects can be restricted to private uploads. |
| Instagram | Professional account using Instagram Login with `instagram_business_basic` and `instagram_business_content_publish`. |
| TikTok | Content Posting API with `video.publish`; unaudited clients are restricted to private posts. |

TikTok requires apps to query current creator settings, show the destination account, collect post
settings, and obtain express consent before uploading. Its [Content Sharing Guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines/)
also say internal-only uploader utilities are not acceptable production clients. App approval is
external to `rot` and is not guaranteed.

## Command line

Set credentials in the process environment. Do not put them in TOML or commit them:

```console
export ROT_YOUTUBE_ACCESS_TOKEN=...
export ROT_INSTAGRAM_ACCESS_TOKEN=...
export ROT_INSTAGRAM_USER_ID=...
export ROT_TIKTOK_ACCESS_TOKEN=...
```

`ROT_INSTAGRAM_API_VERSION` optionally overrides the tested Graph API version. Create
`publish.toml`:

```toml
[youtube]
title = "Five endings ranked"
description = "The last one changes everything."
privacy = "private" # private, unlisted, or public
made_for_kids = false
contains_synthetic_media = true
has_paid_product_placement = false
tags = ["shorts", "ranking"]
category_id = "24"

[instagram]
caption = "Five endings ranked. Which one wins? #ranking"
share_to_feed = true

[tiktok]
caption = "Five endings ranked #ranking"
privacy = "SELF_ONLY"
allow_comments = true
allow_duet = false
allow_stitch = false
brand_organic = false
branded_content = false
ai_generated = true
```

Only sections present in the file are targeted. Unknown sections and keys fail validation, which
also prevents accidentally placing a token in the metadata file.

```console
uv run rot publish final.mp4 --config publish.toml
```

The command validates the MP4 and all target accounts before displaying a confirmation. In a
non-interactive environment, `--yes` is required and means the caller explicitly approved that
specific post. `--json` also requires `--yes`. Exit code `1` means some targets succeeded and some
failed; exit code `2` means no target completed or configuration was invalid.

## Python

```python
import os

from rot import (
    InstagramPublisher,
    InstagramReel,
    PublishJob,
    TikTokPublisher,
    TikTokVideo,
    YouTubePublisher,
    YouTubeShort,
    publish_all,
)

jobs = [
    PublishJob(
        YouTubePublisher(os.environ["ROT_YOUTUBE_ACCESS_TOKEN"]),
        YouTubeShort(
            title="Five endings ranked",
            privacy="private",
            made_for_kids=False,
            contains_synthetic_media=True,
            has_paid_product_placement=False,
        ),
    ),
    PublishJob(
        InstagramPublisher(
            os.environ["ROT_INSTAGRAM_ACCESS_TOKEN"],
            os.environ["ROT_INSTAGRAM_USER_ID"],
        ),
        InstagramReel("Five endings ranked #ranking"),
    ),
    PublishJob(
        TikTokPublisher(os.environ["ROT_TIKTOK_ACCESS_TOKEN"]),
        TikTokVideo(
            privacy="SELF_ONLY",
            allow_comments=True,
            allow_duet=False,
            allow_stitch=False,
            brand_organic=False,
            branded_content=False,
            ai_generated=True,
            caption="Five endings ranked #ranking",
        ),
    ),
]

result = publish_all("final.mp4", jobs, consent=True)
for published in result.results:
    print(published.platform, published.url or published.post_id or published.remote_id)
for failure in result.failures:
    print(failure.platform, failure.message)
```

Pass a custom `TokenProvider` instead of a string when the host application owns refresh-token
rotation. `rot` retries a request once with the refreshed access token but never persists the new
credential. A processing timeout includes the platform operation ID so the remote job can be
inspected without starting an ambiguous duplicate post.
