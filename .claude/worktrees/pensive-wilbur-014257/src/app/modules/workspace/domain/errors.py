"""Workspace domain errors (pure — 06-domain-models §1).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError`` / ``ConflictError``).
"""

from __future__ import annotations


class WorkspaceError(Exception):
    """Base for all workspace domain-rule violations."""


class InvalidWorkspaceInput(WorkspaceError):
    """A value object failed a format/range invariant (→ 422 at the boundary)."""


class WorkspaceArchivedError(WorkspaceError):
    """INV-W2: an archived workspace rejects mutation except reactivation (→ 409)."""
