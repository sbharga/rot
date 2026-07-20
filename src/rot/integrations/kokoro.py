"""Optional hexgrad Kokoro voice adapter."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from ..errors import ConfigurationError, DependencyError, VoiceError
from ..models import StageProgressCallback, SynthesizedAudio

_LANGUAGE_CODES = frozenset("abefhijpz")
_LANGUAGE_ALIASES = {
    "en": "a",
    "en-us": "a",
    "en-gb": "b",
    "es": "e",
    "es-es": "e",
    "fr": "f",
    "fr-fr": "f",
    "hi": "h",
    "hi-in": "h",
    "it": "i",
    "it-it": "i",
    "ja": "j",
    "ja-jp": "j",
    "pt": "p",
    "pt-br": "p",
    "zh": "z",
    "zh-cn": "z",
}


@dataclass(frozen=True, slots=True)
class KokoroVoice:
    """Generate local speech with Kokoro-82M.

    ``voice`` may be a built-in voice name, a comma-separated blend of voice
    names, or a local ``.pt`` voice pack. The speaker language is mapped to a
    Kokoro language pipeline unless ``lang_code`` is explicitly set.
    """

    voice: Path | str = "af_heart"
    speed: float = 1.0
    device: str = "auto"
    lang_code: str | None = None
    repo_id: str = "hexgrad/Kokoro-82M"
    split_pattern: str | None = r"\n+"

    _models: ClassVar[dict[tuple[str, str], Any]] = {}
    _pipelines: ClassVar[dict[tuple[str, str, str], Any]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __post_init__(self) -> None:
        voice = str(self.voice).strip()
        if not voice:
            raise ConfigurationError("Kokoro voice cannot be empty")
        if voice.endswith(".pt"):
            voice = str(Path(voice).expanduser())
        object.__setattr__(self, "voice", voice)
        if self.speed <= 0:
            raise ConfigurationError("Kokoro speed must be positive")
        if self.device not in {"auto", "cpu", "cuda", "mps"}:
            raise ConfigurationError("Kokoro device must be auto, cpu, cuda, or mps")
        if not self.repo_id.strip():
            raise ConfigurationError("Kokoro repo_id cannot be empty")
        if self.lang_code is not None:
            normalized = self._normalize_language(self.lang_code)
            object.__setattr__(self, "lang_code", normalized)

    def __repr__(self) -> str:
        return (
            f"KokoroVoice(voice={self.voice!r},speed={self.speed!r},device={self.device!r},"
            f"lang_code={self.lang_code!r},repo_id={self.repo_id!r})"
        )

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        language: str,
        progress: StageProgressCallback | None = None,
    ) -> SynthesizedAudio:
        lang_code = self.lang_code or self._language_for_voice(language)
        try:
            import numpy as np
            import soundfile as sf
            import torch
            from kokoro import KModel, KPipeline
        except ImportError as exc:
            raise DependencyError(
                "Kokoro is not installed. Run 'uv sync --extra kokoro'."
            ) from exc

        device = self._resolve_device(torch)
        if progress is not None:
            progress("tts", 0, 1, f"Loading Kokoro voice {self.voice}")

        try:
            pipeline_key = (self.repo_id, lang_code, device)
            with self._lock:
                pipeline = self._pipelines.get(pipeline_key)
                if pipeline is None:
                    model_key = (self.repo_id, device)
                    model = self._models.get(model_key)
                    if model is None:
                        model = KModel(repo_id=self.repo_id).to(device).eval()
                        self._models[model_key] = model
                    pipeline = KPipeline(
                        lang_code=lang_code,
                        repo_id=self.repo_id,
                        model=model,
                    )
                    self._pipelines[pipeline_key] = pipeline

            chunks: list[Any] = []
            for result in pipeline(
                text,
                voice=self.voice,
                speed=self.speed,
                split_pattern=self.split_pattern,
            ):
                audio = result.audio if hasattr(result, "audio") else result[2]
                if audio is None:
                    continue
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                chunk = np.asarray(audio, dtype=np.float32).reshape(-1)
                if chunk.size:
                    chunks.append(chunk)
            if not chunks:
                raise VoiceError("Kokoro generated no audio")
            waveform = np.concatenate(chunks)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(output_path), waveform, 24_000, subtype="PCM_16")
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError(f"Kokoro generation failed: {exc}") from exc

        if progress is not None:
            progress("tts", 1, 1, "Kokoro speech generated")
        return SynthesizedAudio(output_path, duration=len(waveform) / 24_000)

    def _language_for_voice(self, language: str) -> str:
        normalized = language.strip().lower().replace("_", "-")
        if normalized == "en":
            voice_name = str(self.voice).rsplit("/", 1)[-1]
            if voice_name[:1] in {"a", "b"}:
                return voice_name[0]
        return self._normalize_language(normalized)

    @staticmethod
    def _normalize_language(language: str) -> str:
        normalized = language.strip().lower().replace("_", "-")
        code = _LANGUAGE_ALIASES.get(normalized, normalized)
        if code not in _LANGUAGE_CODES:
            supported = "a, b, e, f, h, i, j, p, z"
            raise ConfigurationError(
                f"Unsupported Kokoro language {language!r}; use one of {supported} "
                "or a supported locale such as en-US, en-GB, es, fr, hi, it, ja, pt-BR, or zh"
            )
        return code

    def _resolve_device(self, torch: Any) -> str:
        if self.device == "cuda":
            if not torch.cuda.is_available():
                raise ConfigurationError("Kokoro CUDA was requested but is not available")
            return "cuda"
        if self.device == "mps":
            mps = getattr(torch.backends, "mps", None)
            if mps is None or not mps.is_available():
                raise ConfigurationError("Kokoro MPS was requested but is not available")
            if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") != "1":
                raise ConfigurationError(
                    "Kokoro MPS requires PYTORCH_ENABLE_MPS_FALLBACK=1"
                )
            return "mps"
        if self.device == "cpu":
            return "cpu"
        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(torch.backends, "mps", None)
        if (
            mps is not None
            and os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "1"
            and mps.is_available()
        ):
            return "mps"
        return "cpu"
