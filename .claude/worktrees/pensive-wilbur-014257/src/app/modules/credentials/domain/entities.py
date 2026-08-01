"""Credentials aggregate (pure — 06-domain-models §3).

``Credential`` stores one provider API key, encrypted at rest as a ``CipherRef``
(Vault Transit) — the raw secret never lives here (INV-C2). It is a mutable
aggregate whose only transition is ``revoke``; the optimistic ``version`` is
advanced by the repository on ``save`` (02-port-contracts §2). INV-C1
(``scope`` ↔ ``workspace_id`` nullability) is enforced at construction.
Identifiers are UUIDv7 text; timestamps are timezone-aware UTC (DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.credentials.domain.errors import CredentialScopeMismatch
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)


@dataclass(slots=True)
class Credential:
    """A single provider key, platform- or user-scoped, encrypted at rest."""

    id: str
    workspace_id: str | None
    provider: ProviderRef
    scope: CredentialScope
    label: str
    ciphertext_ref: CipherRef
    status: CredentialStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        # INV-C1: user credentials belong to a workspace; platform ones do not.
        if self.scope is CredentialScope.USER and not self.workspace_id:
            raise CredentialScopeMismatch("user credential requires a workspace_id")
        if self.scope is CredentialScope.PLATFORM and self.workspace_id is not None:
            raise CredentialScopeMismatch("platform credential must not set a workspace_id")

    def revoke(self, now: datetime) -> None:
        """Revoke the credential. Idempotent: revoking a revoked one is a no-op."""
        if self.status is CredentialStatus.REVOKED:
            return
        self.status = CredentialStatus.REVOKED
        self.updated_at = now
