"""Structured JSON logging (10-code-standards §10, 07-nfr-slo §7).

One JSON object per line, always carrying the ambient ``correlation_id`` /
``workspace_id`` / ``request_id`` when present. ``extra=`` fields on a log call
are merged in (after redaction). Configure once at process start via
``configure_logging`` from the composition root / worker entrypoint.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.framework.observability.context import (
    correlation_id_var,
    request_id_var,
    workspace_id_var,
)
from app.framework.observability.redaction import redact

# Attributes present on every ``LogRecord``; anything else is treated as an
# explicit ``extra=`` field supplied by the caller.
_RESERVED: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


class JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single-line JSON object with correlation ids."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for var_name, ctx_var in (
            ("request_id", request_id_var),
            ("correlation_id", correlation_id_var),
            ("workspace_id", workspace_id_var),
        ):
            value = ctx_var.get()
            if value is not None:
                payload[var_name] = value

        extras = {key: val for key, val in record.__dict__.items() if key not in _RESERVED}
        if extras:
            payload.update(redact(extras))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Thin wrapper to keep imports consistent."""
    return logging.getLogger(name)
