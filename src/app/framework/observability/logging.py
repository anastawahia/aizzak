"""Structured JSON logging (10-code-standards §10, 07-nfr-slo §7).

One JSON object per line, always carrying the ambient ``correlation_id`` /
``workspace_id`` / ``event_id`` / ``request_id`` when present. ``extra=``
fields on a log call are merged in (after redaction). Configure once at process
start via ``configure_logging`` from the composition root / worker entrypoint.

**Capacity 0.6 added two things and changed nothing else.** ``event_id``, the
third identifier the plan names for the aggregated store; and the tenant
identifier is now written PSEUDONYMISED (``pseudonymity``'s ``ws-…``) wherever
it appears -- both from the context variable and from an ``extra=`` field named
``workspace_id``. Doing it HERE rather than at each call site is the point: a
formatter is the one place every line must pass through, so the guarantee holds
for the five ``extra={"workspace_id": …}`` call sites that exist today and for
every one written after this. A rule enforced at call sites is a rule that
holds until someone forgets.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.framework.observability.context import (
    correlation_id_var,
    event_id_var,
    request_id_var,
    workspace_id_var,
)
from app.framework.observability.pseudonymity import pseudonymous_id
from app.framework.observability.redaction import redact

# The one field name whose value never reaches the log store in the clear.
# Named once, used for both the context variable and the `extra=` sweep below.
WORKSPACE_FIELD = "workspace_id"

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
            ("event_id", event_id_var),
            (WORKSPACE_FIELD, workspace_id_var),
        ):
            value = ctx_var.get()
            if value is not None:
                payload[var_name] = value

        extras = {key: val for key, val in record.__dict__.items() if key not in _RESERVED}
        if extras:
            payload.update(redact(extras))

        # AFTER the merge, deliberately: an `extra={"workspace_id": …}` field
        # overwrites the context variable a line above, so pseudonymising the
        # context value alone would leave the raw id on precisely the lines
        # that named a tenant explicitly. One sweep at the end covers both
        # sources and cannot be bypassed by adding a call site.
        raw_workspace = payload.get(WORKSPACE_FIELD)
        if raw_workspace is not None:
            payload[WORKSPACE_FIELD] = pseudonymous_id(str(raw_workspace))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


# Loggers that install handlers of their OWN and refuse to propagate, so the
# root handler below never sees them (capacity 0.6). Every one is a server
# whose lines describe THIS process: gunicorn's worker lifecycle, uvicorn's
# startup and its per-request access line.
#
# ⚠️ Found by running it, not by reading it. After 0.6 shipped the collector,
# the app's most numerous lines in the store were
# `127.0.0.1:39340 - "GET /health HTTP/1.1" 200` -- gunicorn's plain-text
# access format, which Alloy's JSON stage cannot parse and which carries no
# correlation id. The plan's «سجلٌّ بصيغة JSON لكلّ خدمة» was true of every
# line this codebase writes and false of the majority of lines the container
# emitted, and nothing in a code review would have shown that.
_ADOPTED_LOGGERS: tuple[str, ...] = (
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent).

    And ADOPT the server loggers: each of them ships its own handler and sets
    ``propagate = False``, so clearing the root's handlers leaves them writing
    their own plain-text format straight to stdout. Clearing theirs and
    re-enabling propagation routes them through the one formatter, which is
    what makes "every line this container emits is JSON" true of the container
    rather than only of the application code inside it.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _ADOPTED_LOGGERS:
        adopted = logging.getLogger(name)
        adopted.handlers.clear()
        adopted.propagate = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger. Thin wrapper to keep imports consistent."""
    return logging.getLogger(name)
