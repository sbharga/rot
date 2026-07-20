"""Progress callback utilities."""

from __future__ import annotations

import logging
from collections.abc import Callable

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)

from .models import ProgressEvent

logger = logging.getLogger("rot")


class ProgressReporter:
    def __init__(self, value: bool | Callable[[ProgressEvent], None]) -> None:
        self.callback = value if callable(value) else None
        self.enabled = value is True
        self._progress: Progress | None = None
        self._task: TaskID | None = None
        self._stage = ""

    def __enter__(self) -> ProgressReporter:
        if self.enabled:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold cyan]{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                transient=False,
            )
            self._progress.start()
            self._task = self._progress.add_task("Preparing", total=1.0)
        return self

    def __exit__(self, *_: object) -> None:
        if self._progress is not None:
            self._progress.stop()

    def emit(self, stage: str, completed: float, total: float = 1.0, message: str = "") -> None:
        event = ProgressEvent(stage, completed, total, message)
        logger.info("%s: %s", stage, message or f"{event.fraction:.0%}")
        if self.callback is not None:
            self.callback(event)
        if self._progress is not None and self._task is not None:
            if stage != self._stage:
                self._progress.update(self._task, description=message or stage.title(), total=total)
                self._stage = stage
            self._progress.update(self._task, completed=min(total, completed), total=total)
