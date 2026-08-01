"""Conversations domain errors (pure — 06-domain-models §4).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError`` / ``ConflictError``).
"""

from __future__ import annotations


class ConversationError(Exception):
    """Base for all conversations domain-rule violations."""


class InvalidConversationInput(ConversationError):
    """A value object failed a format/range invariant (→ 422 at the boundary)."""


class ConversationDeletedError(ConversationError):
    """INV-CV3: no appending to (or renaming) a soft-deleted conversation (→ 409)."""
