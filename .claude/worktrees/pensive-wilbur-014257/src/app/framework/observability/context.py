"""Ambient correlation identifiers for logging (07-nfr-slo §7, 10 §10).

These context variables are set at the API/worker boundary and read by the log
formatter so every line carries ``correlation_id``/``workspace_id`` without
threading them through business signatures. They are an *observability* aid
only — authoritative identity always travels explicitly in ``ExecutionContext``.
"""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
workspace_id_var: ContextVar[str | None] = ContextVar("workspace_id", default=None)
