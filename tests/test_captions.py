from dataclasses import replace
from pathlib import Path

from rot import CaptionTheme, Placement, TextOverlay, Utterance, WordTiming
from rot.captions import estimate_word_timings, write_ass, write_srt, write_text_overlays_ass


def test_estimated_words_fill_utterance() -> None:
    words = estimate_word_timings("tiny enormous", 2.0, 5.0)
    assert words[0].start == 2.0
    assert words[-1].end == 5.0
    assert words[1].end - words[1].start > words[0].end - words[0].start


def test_writes_ass_and_srt(tmp_path: Path) -> None:
    utterance = Utterance("alex", "Hello {world}", start=0, end=1)
    utterance.words = (WordTiming("Hello", 0, 0.4), WordTiming("{world}", 0.4, 1))
    ass = write_ass(tmp_path / "captions.ass", [utterance], CaptionTheme.preset("pop"))
    srt = write_srt(tmp_path / "captions.srt", [utterance])
    ass_text = ass.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass_text
    assert r"\{world\}" in ass_text
    assert "Dialogue: 0,0:00:00.00,0:00:00.40" in ass_text
    assert "00:00:00,000 --> 00:00:01,000" in srt.read_text(encoding="utf-8")


def test_writes_timeline_text_independently_from_captions(tmp_path: Path) -> None:
    overlay = TextOverlay(
        "#5 — Cats {everywhere}\nSeriously",
        during_clip="rank-5",
        position="top-left",
        uppercase=True,
        z_index=3,
    )
    path = write_text_overlays_ass(
        tmp_path / "text.ass",
        [(overlay, ((0.0, 2.5),))],
    )
    content = path.read_text(encoding="utf-8")
    assert "TextOverlay0" in content
    assert ",7,70,70,160,1" in content
    assert r"#5 — CATS \{EVERYWHERE\}\NSERIOUSLY" in content
    assert "Dialogue: 3,0:00:00.00,0:00:02.50" in content


def test_writes_inline_styles_and_normalized_positions(tmp_path: Path) -> None:
    overlay = TextOverlay(
        "Plain [color=#f00][i]red[/i][/color]",
        during_clip=0,
        position=Placement(0.25, 0.5, anchor="left"),
        bold=False,
    )
    content = write_text_overlays_ass(
        tmp_path / "rich.ass",
        [(overlay, ((0.0, 1.0),))],
    ).read_text(encoding="utf-8")
    assert r"\an4\pos(270,960)" in content
    assert r"\c&H000000FF\fnDejaVu Sans\fs76\b0\i1" in content
    assert "[color=" not in content


def test_caption_highlight_temporarily_overrides_inline_color(tmp_path: Path) -> None:
    utterance = Utterance("alex", "plain [color=#0f0]green[/color]", start=0, end=1)
    utterance.words = (WordTiming("plain", 0, 0.5), WordTiming("green", 0.5, 1))
    theme = CaptionTheme.preset("pop")
    content = write_ass(
        tmp_path / "rich-captions.ass",
        [utterance],
        replace(theme, position=Placement(0.5, 0.25)),
    ).read_text(encoding="utf-8")
    assert r"\an5\pos(540,480)" in content
    assert r"\c&H0000FF00" in content
    assert r"\c&H0035E1FF" in content
    assert "[color=" not in content
