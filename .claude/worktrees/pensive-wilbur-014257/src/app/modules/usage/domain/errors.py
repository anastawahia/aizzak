"""Usage domain errors (pure — 06-domain-models §10).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError``/``ConflictError``) — the media/knowledge
error-module precedent.
"""

from __future__ import annotations


class UsageError(Exception):
    """Base for all usage domain-rule violations."""


class InvalidUsageInput(UsageError):
    """A value object/entity failed a format/range/shape invariant (→ 422 at
    the boundary) — e.g. a ``LimitRule`` whose ``scope_key`` doesn't match
    its ``scope`` (workspace-scoped rules require ``scope_key == '*'``;
    agent/provider-scoped rules require a specific, non-wildcard key), a
    negative ``limit_value``, or a ``UsageRecord`` with negative
    ``tokens``/``cost_micros`` (INV-U2)."""
