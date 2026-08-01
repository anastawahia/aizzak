"""Knowledge domain errors (pure — 06-domain-models §7).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError`` / ``ConflictError``) — the files/memory error
module precedent.
"""

from __future__ import annotations


class KnowledgeError(Exception):
    """Base for all knowledge domain-rule violations."""


class InvalidKnowledgeInput(KnowledgeError):
    """A value object failed a format/range invariant (→ 422 at the boundary)."""


class DocumentStateError(KnowledgeError):
    """An invalid ``Document`` status transition was attempted (→ 409 at the
    boundary) — e.g. completing/failing a document that is not currently
    ``indexing``, or starting one that is already ``indexed``/``failed``
    (INV-K2: transitions are one-way)."""
