"""Memory domain errors (pure — 06-domain-models §5).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError``).
"""

from __future__ import annotations


class MemoryError(Exception):
    """Base for all memory domain-rule violations."""


class InvalidMemoryInput(MemoryError):
    """A value object failed a format/range invariant (→ 422 at the boundary)."""
