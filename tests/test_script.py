from pathlib import Path

import pytest

from rot import RotScriptParser, ScriptError


def test_script_parser_reads_metadata_and_comments() -> None:
    script = RotScriptParser().parse(
        """
        # comment
        @alex [id=hook, gap=0.4]: Hello world
        @sam [audio='recordings/a line.wav']: Absolutely not
        """
    )
    assert len(script.utterances) == 2
    assert script.utterances[0].id == "hook"
    assert script.utterances[0].gap_after == 0.4
    assert script.utterances[1].audio == Path("recordings/a line.wav")


@pytest.mark.parametrize(
    "source",
    ["plain text", "@alex [bad=value]: text", "@alex [gap=nope]: text", "# empty"],
)
def test_script_parser_rejects_invalid_source(source: str) -> None:
    with pytest.raises(ScriptError):
        RotScriptParser().parse(source)


def test_script_parser_rejects_duplicate_ids() -> None:
    with pytest.raises(ScriptError, match="duplicate id"):
        RotScriptParser().parse("@a [id=x]: One\n@b [id=x]: Two")


def test_metadata_preserves_commas_inside_quotes() -> None:
    script = RotScriptParser().parse('@a [audio="a,b.wav", id=x]: Hello')
    assert script.utterances[0].audio == Path("a,b.wav")
