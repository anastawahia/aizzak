"""Media domain errors (pure — 06-domain-models §8).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError`` / ``ConflictError``) — the files/knowledge
error-module precedent.
"""

from __future__ import annotations


class MediaError(Exception):
    """Base for all media domain-rule violations."""


class InvalidMediaInput(MediaError):
    """A value object failed a format/range/shape invariant (→ 422 at the
    boundary) — e.g. an ``AgentKey``/``GenParams`` that does not parse, or a
    ``MediaJob.complete`` call missing its ``result_file_id`` (INV-MJ1)."""


class MediaJobStateError(MediaError):
    """An invalid ``MediaJob`` status transition was attempted (→ 409 at the
    boundary) — e.g. completing/failing a job that is not currently
    ``running``, or starting one that is already ``succeeded``/``failed``
    (INV-MJ2: transitions are one-way, and re-entrant only from ``running``)."""
