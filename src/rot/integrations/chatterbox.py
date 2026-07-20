"""Optional Resemble AI Chatterbox voice adapter."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from ..errors import ConfigurationError, DependencyError, VoiceError
from ..models import StageProgressCallback, SynthesizedAudio


@dataclass(frozen=True, slots=True)
class ChatterboxVoice:
    """Generate local speech with Chatterbox while preserving its neural watermark."""

    reference_audio: Path | str | None = None
    variant: Literal["turbo", "english", "multilingual"] = "turbo"
    device: str = "auto"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    multilingual_version: Literal["v2", "v3"] = "v3"

    _models: ClassVar[dict[tuple[str, str, str], Any]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __post_init__(self) -> None:
        if self.reference_audio is not None:
            object.__setattr__(self, "reference_audio", Path(self.reference_audio).expanduser())
        if self.variant == "turbo" and self.reference_audio is None:
            raise ConfigurationError("Chatterbox Turbo requires a reference_audio clip")

    def __repr__(self) -> str:
        reference = str(self.reference_audio) if self.reference_audio is not None else "default"
        return (
            f"ChatterboxVoice(variant={self.variant!r},reference={reference!r},"
            f"device={self.device!r},multilingual_version={self.multilingual_version!r})"
        )

    def synthesize(
        self,
        text: str,
        output_path: Path,
        *,
        language: str,
        progress: StageProgressCallback | None = None,
    ) -> SynthesizedAudio:
        try:
            import torch
            import torchaudio
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            from chatterbox.tts import ChatterboxTTS
            from chatterbox.tts_turbo import ChatterboxTurboTTS
        except ImportError as exc:
            raise DependencyError(
                "Chatterbox is not installed. Run 'uv sync --extra tts'."
            ) from exc
        device = self.device
        if device == "auto":
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
        if progress is not None:
            progress("tts", 0, 1, f"Loading Chatterbox {self.variant}")
        key = (self.variant, device, self.multilingual_version)
        with self._lock:
            model = self._models.get(key)
            if model is None:
                if self.variant == "turbo":
                    model = ChatterboxTurboTTS.from_pretrained(device=device)
                elif self.variant == "multilingual":
                    model = ChatterboxMultilingualTTS.from_pretrained(
                        device=device, t3_model=self.multilingual_version
                    )
                else:
                    model = ChatterboxTTS.from_pretrained(device=device)
                self._models[key] = model
        reference = str(self.reference_audio) if self.reference_audio is not None else None
        try:
            if self.variant == "multilingual":
                waveform = model.generate(text, language_id=language, audio_prompt_path=reference)
            elif self.variant == "english":
                kwargs: dict[str, Any] = {
                    "exaggeration": self.exaggeration,
                    "cfg_weight": self.cfg_weight,
                }
                if reference is not None:
                    kwargs["audio_prompt_path"] = reference
                waveform = model.generate(text, **kwargs)
            else:
                waveform = model.generate(text, audio_prompt_path=reference)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            torchaudio.save(str(output_path), waveform.detach().cpu(), model.sr)
        except Exception as exc:
            raise VoiceError(f"Chatterbox generation failed: {exc}") from exc
        if progress is not None:
            progress("tts", 1, 1, "Chatterbox speech generated")
        return SynthesizedAudio(output_path)
