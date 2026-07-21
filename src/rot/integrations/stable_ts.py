"""Optional Stable-TS known-transcript word alignment."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from ..errors import AlignmentError, DependencyError, TranscriptionError
from ..models import (
    StageProgressCallback,
    Transcript,
    TranscriptSegment,
    WordTiming,
)

_MODELS: dict[tuple[str, str | None, str], Any] = {}
_MODEL_LOCK = threading.Lock()


def _load_model(
    model_name: str,
    device: str | None,
    backend: str,
    progress: StageProgressCallback | None,
    *,
    stage: str,
) -> Any:
    try:
        import stable_whisper
    except ImportError as exc:
        raise DependencyError(
            f"Stable-TS is not installed. Run 'uv sync --extra {stage}'."
        ) from exc
    key = (model_name, device, backend)
    if progress is not None:
        progress(stage, 0, 1, f"Loading Stable-TS {model_name}")
    with _MODEL_LOCK:
        model = _MODELS.get(key)
        if model is None:
            if backend == "faster-whisper":
                model = stable_whisper.load_faster_whisper(
                    model_name, device=device or "auto"
                )
            else:
                kwargs = {"device": device} if device is not None else {}
                model = stable_whisper.load_model(model_name, **kwargs)
            _MODELS[key] = model
    return model


@dataclass(frozen=True, slots=True)
class StableTSAligner:
    """Align a known transcript with Stable-TS.

    Attributes:
        model: Whisper model name or path.
        device: Optional inference device.
        backend: ``whisper`` or ``faster-whisper``.
        failure_threshold: Stable-TS alignment failure threshold.
    """

    model: str = "base"
    device: str | None = None
    backend: Literal["whisper", "faster-whisper"] = "whisper"
    failure_threshold: float = 0.25

    _models: ClassVar[dict[tuple[str, str | None, str], Any]] = _MODELS
    _lock: ClassVar[threading.Lock] = _MODEL_LOCK

    def align(
        self,
        audio_path: Path,
        text: str,
        *,
        language: str,
        progress: StageProgressCallback | None = None,
    ) -> tuple[WordTiming, ...]:
        """Align known text to speech audio.

        Args:
            audio_path: Speech audio path.
            text: Known transcript.
            language: Transcript language identifier.
            progress: Optional stage progress callback.
        """

        model = _load_model(self.model, self.device, self.backend, progress, stage="align")
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


@dataclass(frozen=True, slots=True)
class StableTSTranscriber:
    """Transcribe clip audio with Stable-TS and word-level timestamps.

    Attributes:
        model: Whisper model name or path.
        device: Optional inference device.
        backend: ``whisper`` or ``faster-whisper``.
    """

    model: str = "base"
    device: str | None = None
    backend: Literal["whisper", "faster-whisper"] = "whisper"

    _models: ClassVar[dict[tuple[str, str | None, str], Any]] = _MODELS

    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
        progress: StageProgressCallback | None = None,
    ) -> Transcript:
        """Return clip-local segments and words for prepared audio.

        Args:
            audio_path: Prepared mono audio path.
            language: Optional language identifier or ``None`` for detection.
            progress: Optional stage progress callback.
        """

        model = _load_model(
            self.model, self.device, self.backend, progress, stage="transcribe"
        )
        kwargs: dict[str, Any] = {"word_timestamps": True}
        if language is not None:
            kwargs["language"] = language
        try:
            result = model.transcribe(str(audio_path), **kwargs)
        except Exception as exc:
            raise TranscriptionError(f"Stable-TS transcription failed: {exc}") from exc
        if result is None or not hasattr(result, "to_dict"):
            raise TranscriptionError("Stable-TS returned an invalid transcription result")
        payload = result.to_dict()
        segments: list[TranscriptSegment] = []
        for raw_segment in payload.get("segments", []):
            text = str(raw_segment.get("text", "")).strip()
            if not text:
                continue
            words = tuple(
                WordTiming(
                    str(word.get("word", "")).strip(),
                    float(word["start"]),
                    float(word["end"]),
                )
                for word in raw_segment.get("words", [])
                if str(word.get("word", "")).strip()
                and word.get("start") is not None
                and word.get("end") is not None
            )
            start_value = raw_segment.get("start")
            end_value = raw_segment.get("end")
            if (start_value is None or end_value is None) and not words:
                continue
            start = float(start_value) if start_value is not None else words[0].start
            end = float(end_value) if end_value is not None else words[-1].end
            segments.append(TranscriptSegment(text, start, end, words))
        detected = payload.get("language") or getattr(result, "language", None) or language
        if progress is not None:
            progress("transcribe", 1, 1, "Clip transcription complete")
        return Transcript(tuple(segments), str(detected) if detected is not None else None)
