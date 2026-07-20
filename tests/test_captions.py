from pathlib import Path

from rot import CaptionTheme, TextOverlay, Utterance, WordTiming
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
