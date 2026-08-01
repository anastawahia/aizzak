"""Files domain errors (pure — 06-domain-models §6).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError`` / ``ConflictError``).
"""

from __future__ import annotations


class FileError(Exception):
    """Base for all files domain-rule violations."""


class InvalidFileInput(FileError):
    """A value object failed a format/range invariant (→ 422 at the boundary)."""


class FileStateError(FileError):
    """An invalid status transition was attempted — e.g. completing an already
    quarantined or soft-deleted file (→ 409 at the boundary)."""
