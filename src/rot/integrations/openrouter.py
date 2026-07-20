"""OpenRouter free-form script parser using strict structured outputs."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from ..errors import DependencyError, ParserError
from ..models import Script, Utterance


@dataclass(frozen=True, slots=True)
class OpenRouterParser:
    model: str
    speakers: tuple[str, ...] = ()
    api_key: str | None = None
    endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    timeout: float = 60.0
    retries: int = 2

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ParserError("OpenRouter requires an explicit model id")

    def parse(self, source: str) -> Script:
        if not source.strip():
            raise ParserError("Cannot parse an empty script")
        try:
            import httpx
        except ImportError as exc:
            raise DependencyError(
                "The OpenRouter integration is not installed. Run 'uv sync --extra openrouter'."
            ) from exc
        key = self.api_key or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ParserError("Set OPENROUTER_API_KEY or pass api_key to OpenRouterParser")
        speaker_schema: dict[str, Any] = {
            "type": "string",
            "minLength": 1,
            "pattern": "^[A-Za-z0-9_.-]+$",
        }
        if self.speakers:
            speaker_schema["enum"] = list(self.speakers)
        schema = {
            "type": "object",
            "properties": {
                "utterances": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "speaker": speaker_schema,
                            "text": {"type": "string", "minLength": 1},
                            "id": {
                                "type": ["string", "null"],
                                "pattern": "^[A-Za-z0-9_-]+$",
                            },
                        },
                        "required": ["speaker", "text", "id"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["utterances"],
            "additionalProperties": False,
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Convert the user's short-form video script into ordered spoken utterances. "
                        "Preserve wording and speaker attribution. IDs must be short unique slugs for "
                        "important beats, or null. Never invent asset paths, audio paths, or speakers "
                        "outside the allowed list."
                    ),
                },
                {"role": "user", "content": source},
            ],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rot_script", "strict": True, "schema": schema},
            },
            "provider": {"require_parameters": True},
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        response = None
        for attempt in range(self.retries + 1):
            try:
                response = httpx.post(
                    self.endpoint, headers=headers, json=payload, timeout=self.timeout
                )
            except httpx.HTTPError as exc:
                if attempt == self.retries:
                    raise ParserError(f"OpenRouter request failed: {type(exc).__name__}") from exc
                time.sleep(0.5 * (2**attempt))
                continue
            if response.status_code != 429 and response.status_code < 500:
                break
            if attempt < self.retries:
                time.sleep(0.5 * (2**attempt))
        assert response is not None
        if response.is_error:
            try:
                message = response.json().get("error", {}).get("message", "request rejected")
            except (ValueError, AttributeError):
                message = "request rejected"
            raise ParserError(f"OpenRouter returned HTTP {response.status_code}: {message}")
        try:
            envelope = response.json()
            content = envelope["choices"][0]["message"]["content"]
            document = json.loads(content) if isinstance(content, str) else content
            raw_utterances = document["utterances"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ParserError("OpenRouter returned an invalid structured script") from exc
        result: list[Utterance] = []
        used_ids: set[str] = set()
        for index, item in enumerate(raw_utterances, 1):
            if not isinstance(item, dict) or not isinstance(item.get("speaker"), str):
                raise ParserError(f"OpenRouter utterance {index} is invalid")
            speaker = item["speaker"]
            if not speaker.replace("_", "").replace("-", "").replace(".", "").isalnum():
                raise ParserError(f"OpenRouter returned invalid speaker {speaker!r}")
            if self.speakers and item["speaker"] not in self.speakers:
                raise ParserError(f"OpenRouter used unknown speaker {item['speaker']!r}")
            line_id = item.get("id")
            invalid_id = line_id is not None and (
                not isinstance(line_id, str)
                or not line_id.replace("_", "").replace("-", "").isalnum()
            )
            if invalid_id or line_id in used_ids:
                raise ParserError(f"OpenRouter returned invalid or duplicate id {line_id!r}")
            if line_id:
                used_ids.add(line_id)
            result.append(Utterance(item["speaker"], str(item.get("text", "")), id=line_id))
        if not result:
            raise ParserError("OpenRouter returned an empty script")
        return Script(result)
