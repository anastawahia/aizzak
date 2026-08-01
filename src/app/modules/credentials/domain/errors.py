"""Credentials domain errors (pure — 06-domain-models §3).

Module-local hierarchy: the domain imports no framework code (import-linter
contract 2 keeps ``app.modules.*.domain`` stdlib-only). The application layer
catches these at its boundary and maps them onto the shared framework error
hierarchy (``ValidationError``).
"""

from __future__ import annotations


class CredentialError(Exception):
    """Base for all credentials domain-rule violations."""


class InvalidCredentialInput(CredentialError):
    """A value object failed a format invariant (→ 422 at the boundary)."""


class CredentialScopeMismatch(CredentialError):
    """INV-C1: ``scope`` and ``workspace_id`` nullability disagree (→ 422)."""
