from pathlib import Path

import pytest

from rot import (
    CaptionTheme,
    Clip,
    ConfigurationError,
    FilterNode,
    Overlay,
    Project,
    RenderSettings,
    TextOverlay,
)


def test_short_form_export_defaults() -> None:
    settings = RenderSettings()
    assert (settings.width, settings.height, settings.fps) == (1080, 1920, 30)
    assert settings.video_bitrate == "10M"
    assert settings.min_video_bitrate == "8M"
    assert settings.max_video_bitrate == "12M"
    assert settings.audio_sample_rate == 48_000
    assert settings.audio_channels == 2
    assert settings.pixel_format == "yuv420p"


def test_custom_fit_amount_is_normalized() -> None:
    assert Clip("horizontal.mp4", fit="custom", fit_amount=0.4).fit_amount == 0.4
    with pytest.raises(ConfigurationError, match="between 0 and 1"):
        Clip("horizontal.mp4", fit="custom", fit_amount=1.1)
    with pytest.raises(ConfigurationError, match="Unknown clip fit"):
        Clip("horizontal.mp4", fit="mystery")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError, match="requires"):
        Clip("horizontal.mp4", fit="cover", fill="blur")
    with pytest.raises(ConfigurationError, match="positive"):
        Clip("horizontal.mp4", fit="contain", fill="blur", fill_blur=0)


def test_caption_presets() -> None:
    assert CaptionTheme.preset("pop").highlight_color == "#FFE135"
    assert CaptionTheme.preset("karaoke").max_words == 7
    with pytest.raises(ConfigurationError):
        CaptionTheme.preset("boring")


def test_overlay_requires_one_selector() -> None:
    with pytest.raises(ConfigurationError):
        Overlay("image.png")
    with pytest.raises(ConfigurationError):
        Overlay("image.png", at=1, speaker="alex")


def test_text_overlay_requires_one_selector_and_valid_style() -> None:
    with pytest.raises(ConfigurationError, match="exactly one"):
        TextOverlay("Title")
    with pytest.raises(ConfigurationError, match="exactly one"):
        TextOverlay("Title", at=0, during_clip=0)
    with pytest.raises(ConfigurationError, match="clip index"):
        TextOverlay("Title", during_clip=-1)
    with pytest.raises(ConfigurationError, match="#RRGGBB"):
        TextOverlay("Title", during_clip=0, color="white")


def test_project_fluent_methods(tmp_path: Path) -> None:
    project = (
        Project.short_form()
        .background(tmp_path / "background.mp4", trim=(1, 3))
        .transition("fade", duration=0.2)
        .add_speaker("alex", portrait_position="bottom-left")
        .script("@alex [audio=line.wav]: hello")
        .captions("bounce", uppercase=True)
        .overlay_image(tmp_path / "image.png", speaker="alex")
        .overlay_text("Clip one", during_clip="first")
        .effect("blur", radius=2)
    )
    assert project.clips[0].trim_start == 1
    assert project.clips[0].transition == "fade"
    assert project.caption_theme.uppercase is True
    assert project.overlays[0].speaker == "alex"
    assert project.text_overlays[0].during_clip == "first"


def test_add_clip_accepts_an_incoming_transition(tmp_path: Path) -> None:
    project = (
        Project.short_form()
        .background(tmp_path / "first.mp4")
        .add_clip(tmp_path / "second.mp4", transition="zoom", transition_duration=0.4)
    )
    assert project.clips[0].transition == "zoom"
    assert project.clips[0].transition_duration == 0.4
    assert project.clips[1].transition == "cut"


def test_clip_ids_are_registered_by_background_and_add_clip(tmp_path: Path) -> None:
    project = (
        Project.short_form()
        .background(tmp_path / "first.mp4", clip_id="rank-5")
        .add_clip(tmp_path / "second.mp4", clip_id="rank-4")
    )
    assert [clip.id for clip in project.clips] == ["rank-5", "rank-4"]


def test_filter_nodes_reject_graph_injection() -> None:
    with pytest.raises(ConfigurationError, match="Unsafe FFmpeg filter value"):
        FilterNode("scale", (("w", "100;movie=secret"),))
