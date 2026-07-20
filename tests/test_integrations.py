from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from rot import (
    ChatterboxVoice,
    ConfigurationError,
    KokoroVoice,
    OpenRouterParser,
    ParserError,
    StableTSAligner,
)


class _Response:
    status_code = 200
    is_error = False

    def __init__(self, content: dict[str, object]) -> None:
        self.content = content

    def json(self) -> dict[str, object]:
        return {
            "choices": [{"message": {"content": json.dumps(self.content)}}],
        }


def _fake_httpx(monkeypatch: pytest.MonkeyPatch, content: dict[str, object]) -> dict[str, object]:
    captured: dict[str, object] = {}
    module = ModuleType("httpx")
    module.HTTPError = RuntimeError  # type: ignore[attr-defined]

    def post(*args: object, **kwargs: object) -> _Response:
        captured.update(kwargs)
        return _Response(content)

    module.post = post  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", module)
    return captured


def test_openrouter_uses_structured_output_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _fake_httpx(
        monkeypatch,
        {"utterances": [{"speaker": "alex", "text": "Hello", "id": "hook"}]},
    )
    script = OpenRouterParser(
        model="provider/model", speakers=("alex",), api_key="super-secret"
    ).parse("Alex says hello")
    assert script.utterances[0].id == "hook"
    payload = captured["json"]
    assert isinstance(payload, dict)
    assert payload["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert "super-secret" not in json.dumps(payload)


def test_openrouter_rejects_unknown_speaker(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_httpx(
        monkeypatch,
        {"utterances": [{"speaker": "intruder", "text": "Hello", "id": None}]},
    )
    with pytest.raises(ParserError, match="unknown speaker"):
        OpenRouterParser(model="provider/model", speakers=("alex",), api_key="key").parse("x")


def test_chatterbox_turbo_requires_reference() -> None:
    with pytest.raises(ConfigurationError, match="reference_audio"):
        ChatterboxVoice()
    voice = ChatterboxVoice(Path("reference.wav"))
    assert "reference.wav" in repr(voice)


def test_chatterbox_adapter_with_mocked_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Availability:
        @staticmethod
        def is_available() -> bool:
            return False

    torch = ModuleType("torch")
    torch.cuda = _Availability()  # type: ignore[attr-defined]
    torch.backends = type("Backends", (), {"mps": _Availability()})()  # type: ignore[attr-defined]
    torchaudio = ModuleType("torchaudio")

    def save(path: str, waveform: object, sample_rate: int) -> None:
        Path(path).write_bytes(b"mock-wave")

    torchaudio.save = save  # type: ignore[attr-defined]

    class _Wave:
        def detach(self) -> _Wave:
            return self

        def cpu(self) -> _Wave:
            return self

    class _Model:
        sr = 24_000

        @classmethod
        def from_pretrained(cls, **kwargs: object) -> _Model:
            return cls()

        def generate(self, text: str, **kwargs: object) -> _Wave:
            return _Wave()

    chatterbox = ModuleType("chatterbox")
    chatterbox.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    monkeypatch.setitem(sys.modules, "chatterbox", chatterbox)
    for module_name, class_name in (
        ("chatterbox.mtl_tts", "ChatterboxMultilingualTTS"),
        ("chatterbox.tts", "ChatterboxTTS"),
        ("chatterbox.tts_turbo", "ChatterboxTurboTTS"),
    ):
        module = ModuleType(module_name)
        setattr(module, class_name, _Model)
        monkeypatch.setitem(sys.modules, module_name, module)
    ChatterboxVoice._models.clear()
    output = tmp_path / "speech.wav"
    result = ChatterboxVoice(tmp_path / "reference.wav").synthesize("Hello", output, language="en")
    assert result.path == output
    assert output.read_bytes() == b"mock-wave"


def test_kokoro_validates_options_and_maps_languages() -> None:
    with pytest.raises(ConfigurationError, match="speed"):
        KokoroVoice(speed=0)
    with pytest.raises(ConfigurationError, match="device"):
        KokoroVoice(device="tpu")
    with pytest.raises(ConfigurationError, match="Unsupported Kokoro language"):
        KokoroVoice(lang_code="de")
    assert KokoroVoice("bf_emma")._language_for_voice("en") == "b"
    assert KokoroVoice()._language_for_voice("pt_BR") == "p"
    assert KokoroVoice(Path("~/custom.pt")).voice == Path("~/custom.pt").expanduser().as_posix()


def test_kokoro_adapter_with_mocked_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class _Array(list[float]):
        @property
        def size(self) -> int:
            return len(self)

        def reshape(self, _: int) -> _Array:
            return self

    numpy = ModuleType("numpy")
    numpy.float32 = object()  # type: ignore[attr-defined]
    numpy.asarray = lambda values, dtype=None: _Array(values)  # type: ignore[attr-defined]
    numpy.concatenate = (  # type: ignore[attr-defined]
        lambda chunks: _Array(value for chunk in chunks for value in chunk)
    )

    class _Availability:
        @staticmethod
        def is_available() -> bool:
            return False

    torch = ModuleType("torch")
    torch.cuda = _Availability()  # type: ignore[attr-defined]
    torch.backends = type("Backends", (), {"mps": _Availability()})()  # type: ignore[attr-defined]

    class _Model:
        def __init__(self, *, repo_id: str) -> None:
            captured["repo_id"] = repo_id

        def to(self, device: str) -> _Model:
            captured["device"] = device
            return self

        def eval(self) -> _Model:
            return self

    class _Result:
        def __init__(self, audio: object) -> None:
            self.audio = audio

    class _Pipeline:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def __call__(self, text: str, **kwargs: object) -> list[_Result]:
            captured["text"] = text
            captured.update(kwargs)
            return [
                _Result(_Array([0.1, 0.2])),
                _Result(_Array([0.3])),
            ]

    writes: list[tuple[str, object, int, str]] = []
    soundfile = ModuleType("soundfile")
    soundfile.write = (  # type: ignore[attr-defined]
        lambda path, audio, sample_rate, subtype: writes.append(
            (path, audio, sample_rate, subtype)
        )
        or Path(path).write_bytes(b"mock-wave")
    )
    kokoro = ModuleType("kokoro")
    kokoro.KModel = _Model  # type: ignore[attr-defined]
    kokoro.KPipeline = _Pipeline  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "soundfile", soundfile)
    monkeypatch.setitem(sys.modules, "kokoro", kokoro)
    KokoroVoice._models.clear()
    KokoroVoice._pipelines.clear()

    output = tmp_path / "speech.wav"
    result = KokoroVoice("bf_emma", speed=1.1).synthesize(
        "Hello", output, language="en"
    )

    assert result.path == output
    assert result.duration == 3 / 24_000
    assert output.read_bytes() == b"mock-wave"
    assert captured["lang_code"] == "b"
    assert captured["device"] == "cpu"
    assert captured["voice"] == "bf_emma"
    assert captured["speed"] == 1.1
    assert writes[0][2:] == (24_000, "PCM_16")
    assert list(writes[0][1]) == [0.1, 0.2, 0.3]  # type: ignore[arg-type]


def test_stable_ts_adapter_with_mocked_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Word:
        word = "Hello"
        start = 0.1
        end = 0.5

    class _Result:
        def all_words(self) -> list[_Word]:
            return [_Word()]

    class _Model:
        def align(self, *args: object, **kwargs: object) -> _Result:
            return _Result()

    stable = ModuleType("stable_whisper")
    stable.load_model = lambda *args, **kwargs: _Model()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "stable_whisper", stable)
    StableTSAligner._models.clear()
    words = StableTSAligner().align(Path("audio.wav"), "Hello", language="en")
    assert words[0].text == "Hello"
    assert (words[0].start, words[0].end) == (0.1, 0.5)
