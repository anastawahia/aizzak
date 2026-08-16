"""Spaces domain errors (pure — 06-domain-models §6).

Module-local hierarchy, the ``files`` shape exactly: the domain imports no
framework code (import-linter contract 2 keeps ``app.modules.*.domain``
stdlib-only), and the application layer catches these at its boundary and
maps them onto the shared framework hierarchy (``ValidationError`` /
``ConflictError``).
"""

from __future__ import annotations


class SpaceError(Exception):
    """Base for all spaces domain-rule violations."""


class InvalidSpaceInput(SpaceError):
    """A value object failed a format/range invariant (→ 422 at the boundary)."""


class SpaceStateError(SpaceError):
    """A write was attempted against a soft-deleted space (→ 409 at the
    boundary). A space has no status machine — it exists or it is deleted —
    so this is the one state rule there is."""
