"""Optional Stable-TS known-transcript word alignment."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from ..errors import AlignmentError, DependencyError
from ..models import StageProgressCallback, WordTiming


@dataclass(frozen=True, slots=True)
class StableTSAligner:
    model: str = "base"
    device: str | None = None
    backend: Literal["whisper", "faster-whisper"] = "whisper"
    failure_threshold: float = 0.25

    _models: ClassVar[dict[tuple[str, str | None, str], Any]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def align(
        self,
        audio_path: Path,
        text: str,
        *,
        language: str,
        progress: StageProgressCallback | None = None,
    ) -> tuple[WordTiming, ...]:
        try:
            import stable_whisper
        except ImportError as exc:
            raise DependencyError(
                "Stable-TS is not installed. Run 'uv sync --extra align'."
            ) from exc
        key = (self.model, self.device, self.backend)
        if progress is not None:
            progress("align", 0, 1, f"Loading Stable-TS {self.model}")
        with self._lock:
            model = self._models.get(key)
            if model is None:
                if self.backend == "faster-whisper":
                    model = stable_whisper.load_faster_whisper(
                        self.model, device=self.device or "auto"
                    )
                else:
                    kwargs = {"device": self.device} if self.device is not None else {}
                    model = stable_whisper.load_model(self.model, **kwargs)
                self._models[key] = model
        try:
            result = model.align(
                str(audio_path),
                text,
                language=language,
                original_split=True,
                failure_threshold=self.failure_threshold,
            )
        except Exception as exc:
            raise AlignmentError(f"Stable-TS alignment failed: {exc}") from exc
        if result is None:
            raise AlignmentError("Stable-TS could not align the transcript")
        words: list[WordTiming] = []
        if hasattr(result, "all_words"):
            for word in result.all_words():
                value = str(getattr(word, "word", "")).strip()
                start = getattr(word, "start", None)
                end = getattr(word, "end", None)
                if value and start is not None and end is not None:
                    words.append(WordTiming(value, float(start), float(end)))
        if not words and hasattr(result, "to_dict"):
            for segment in result.to_dict().get("segments", []):
                for word in segment.get("words", []):
                    if word.get("start") is not None and word.get("end") is not None:
                        words.append(
                            WordTiming(
                                str(word.get("word", "")).strip(),
                                float(word["start"]),
                                float(word["end"]),
                            )
                        )
        if not words:
            raise AlignmentError("Stable-TS returned no word timings")
        if progress is not None:
            progress("align", 1, 1, "Word alignment complete")
        return tuple(words)
