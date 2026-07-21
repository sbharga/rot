"""Deterministic speaker script parsing."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from .errors import ConfigurationError, ScriptError
from .models import Script, Utterance

_LINE = re.compile(r"^@(?P<speaker>[A-Za-z0-9_.-]+)(?:\s+\[(?P<meta>.*)\])?\s*:\s*(?P<text>.+)$")


class RotScriptParser:
    """Parse rot's small, deterministic dialogue format."""

    def parse(self, source: str) -> Script:
        """Parse deterministic ``@speaker [key=value]: text`` lines.

        Args:
            source: UTF-8 script content.
        """

        utterances: list[Utterance] = []
        used_ids: set[str] = set()
        for number, raw_line in enumerate(source.splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = _LINE.match(line)
            if not match:
                raise ScriptError(f"Line {number}: expected '@speaker: dialogue'")
            metadata = self._metadata(match.group("meta"), number)
            unknown = set(metadata) - {"id", "audio", "gap"}
            if unknown:
                raise ScriptError(f"Line {number}: unknown metadata {sorted(unknown)!r}")
            line_id = metadata.get("id")
            if line_id and line_id in used_ids:
                raise ScriptError(f"Line {number}: duplicate id {line_id!r}")
            if line_id:
                used_ids.add(line_id)
            try:
                gap = float(metadata.get("gap", "0.15"))
            except ValueError as exc:
                raise ScriptError(f"Line {number}: gap must be a number") from exc
            try:
                utterances.append(
                    Utterance(
                        speaker=match.group("speaker"),
                        text=match.group("text"),
                        id=line_id,
                        audio=metadata.get("audio"),
                        gap_after=gap,
                    )
                )
            except ConfigurationError as exc:
                raise ScriptError(f"Line {number}: {exc}") from exc
        if not utterances:
            raise ScriptError("The script contains no dialogue")
        return Script(utterances)

    def parse_file(self, path: str | Path) -> Script:
        """Parse a file and resolve relative line-audio paths beside it.

        Args:
            path: UTF-8 ``.rot`` file path.
        """

        source_path = Path(path)
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ScriptError(f"Cannot read script {source_path}: {exc}") from exc
        script = self.parse(text)
        for item in script.utterances:
            audio_path = Path(item.audio) if item.audio is not None else None
            if audio_path is not None and not audio_path.is_absolute():
                item.audio = source_path.parent / audio_path
        return script

    @staticmethod
    def _metadata(source: str | None, line: int) -> dict[str, str]:
        if source is None or not source.strip():
            return {}
        try:
            lexer = shlex.shlex(source, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.whitespace += ","
            tokens = list(lexer)
        except ValueError as exc:
            raise ScriptError(f"Line {line}: invalid metadata: {exc}") from exc
        result: dict[str, str] = {}
        for token in tokens:
            if "=" not in token:
                raise ScriptError(f"Line {line}: metadata must use key=value")
            key, value = token.split("=", 1)
            if not key or not value:
                raise ScriptError(f"Line {line}: invalid metadata {token!r}")
            result[key] = value
        return result
