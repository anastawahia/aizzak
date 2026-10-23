"""Capture log lines exactly as the platform writes them.

``caplog`` keeps ``LogRecord`` objects and formats them later, but the
identifiers on a line come from context variables that the formatter reads at
FORMAT time -- by then the request's context is gone and every id would read
as unbound. This handler runs the real ``JsonFormatter`` inside ``emit``, which
is called synchronously in the context of the code that logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.framework.observability.logging import JsonFormatter


class JsonLines(logging.Handler):
    """Every line emitted while installed, parsed back from the real format."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(JsonFormatter())
        self.lines: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))

    def named(self, message: str) -> list[dict[str, Any]]:
        return [line for line in self.lines if line["message"] == message]


@contextmanager
def json_lines(level: int = logging.INFO) -> Iterator[JsonLines]:
    """Install a ``JsonLines`` on the root logger for the block."""
    root = logging.getLogger()
    handler = JsonLines()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(level)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
