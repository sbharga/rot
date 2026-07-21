from pathlib import Path

import pytest

from rot import (
    CaptionTheme,
    Clip,
    ClipTranscription,
    ConfigurationError,
    Facecam,
    FilterNode,
    NormalizedRect,
    Overlay,
    Placement,
    Project,
    RenderSettings,
    Soundtrack,
    TextOverlay,
    Transcript,
    TranscriptSegment,
    WordTiming,
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


def test_clip_focus_position_and_transcription_are_validated() -> None:
    clip = Clip(
        "horizontal.mp4",
        fit="custom",
        focus=(0.8, 0.35),
        position=Placement(0.5, 0.2, anchor="top"),
        transcribe=ClipTranscription(language="en"),
    )
    assert clip.focus == (0.8, 0.35)
    assert clip.position == Placement(0.5, 0.2, anchor="top")
    assert clip.transcribe == ClipTranscription("en")
    with pytest.raises(ConfigurationError, match="focus"):
        Clip("video.mp4", fit="contain", focus=(0.5, 0.5))
    with pytest.raises(ConfigurationError, match="position"):
        Clip("video.mp4", fit="cover", position=Placement(0.5, 0.5))
    with pytest.raises(ConfigurationError, match="between 0 and 1"):
        Clip("video.mp4", fit="cover", focus=(1.1, 0.5))


def test_structured_transcripts_validate_and_join_text() -> None:
    segment = TranscriptSegment(
        "Hello world",
        0,
        1,
        (WordTiming("Hello", 0, 0.4), WordTiming("world", 0.4, 1)),
    )
    assert Transcript((segment,), language="en").text == "Hello world"
    with pytest.raises(ConfigurationError, match="inside"):
        TranscriptSegment("bad", 1, 2, (WordTiming("bad", 0, 1),))


def test_normalized_placement_and_facecam_are_validated() -> None:
    placement = Placement(0.25, 0.75, anchor="bottom-left")
    facecam = Facecam(
        crop=NormalizedRect(0, 0, 0.25, 0.4),
        destination=NormalizedRect(0.6, 0.05, 0.35, 0.3),
    )
    assert placement.anchor == "bottom-left"
    assert Clip("stream.mp4", fit="custom", facecam=facecam).facecam == facecam
    with pytest.raises(ConfigurationError, match="between 0 and 1"):
        Placement(1.1, 0.5)
    with pytest.raises(ConfigurationError, match="within the frame"):
        NormalizedRect(0.8, 0, 0.3, 0.5)
    with pytest.raises(ConfigurationError, match="fit='custom'"):
        Clip("stream.mp4", fit="contain", facecam=facecam)


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
    assert Overlay("image.png", during_clip="intro").during_clip == "intro"
    with pytest.raises(ConfigurationError, match="width"):
        Overlay("image.png", at=1, width=0)


def test_soundtrack_validates_trim_fades_and_volume() -> None:
    music = Soundtrack(
        "music.mp3",
        volume=0.2,
        trim_start=1,
        trim_end=5,
        loop=False,
        fade_in=0.5,
        fade_out=1,
        ducking=True,
    )
    assert music.source == Path("music.mp3")
    assert music.ducking is True
    with pytest.raises(ConfigurationError, match="volume"):
        Soundtrack("music.mp3", volume=-0.1)
    with pytest.raises(ConfigurationError, match="trim end"):
        Soundtrack("music.mp3", trim_start=2, trim_end=1)
    with pytest.raises(ConfigurationError, match="fades"):
        Soundtrack("music.mp3", fade_in=-1)


def test_text_overlay_requires_one_selector_and_valid_style() -> None:
    with pytest.raises(ConfigurationError, match="exactly one"):
        TextOverlay("Title")
    with pytest.raises(ConfigurationError, match="exactly one"):
        TextOverlay("Title", at=0, during_clip=0)
    with pytest.raises(ConfigurationError, match="clip index"):
        TextOverlay("Title", during_clip=-1)
    with pytest.raises(ConfigurationError, match="#RRGGBB"):
        TextOverlay("Title", during_clip=0, color="white")


def test_inline_text_is_parsed_to_plain_text_and_style_runs() -> None:
    overlay = TextOverlay(
        "This [[tag]] is [color=#0f0][i]green[/i][/color]",
        during_clip=0,
        position=Placement(0.5, 0.2),
    )
    assert overlay.text == "This [tag] is green"
    assert any(run.style.color == "#00FF00" for run in overlay.styled_runs)
    assert any(run.style.italic is True for run in overlay.styled_runs)
    with pytest.raises(ConfigurationError, match="mismatched"):
        TextOverlay("[b]bad[/i]", during_clip=0)
    with pytest.raises(ConfigurationError, match="Unknown inline text tag"):
        TextOverlay("[blink]bad[/blink]", during_clip=0)


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
        .soundtrack(
            tmp_path / "music.mp3",
            trim=(1, 4),
            loop=False,
            fade_in=0.2,
            fade_out=0.3,
            ducking=True,
        )
    )
    assert project.clips[0].trim_start == 1
    assert project.clips[0].transition == "fade"
    assert project.caption_theme.uppercase is True
    assert project.caption_theme.position is None
    assert project.clip_caption_theme.position == Placement(0.5, 0.08, anchor="top")
    assert project.overlays[0].speaker == "alex"
    assert project.text_overlays[0].during_clip == "first"
    assert project.music is not None and project.music.trim_end == 4


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
