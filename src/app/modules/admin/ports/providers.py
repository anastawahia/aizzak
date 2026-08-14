"""The platform's own provider keys — the one credential scope no workspace owns.

``credentials`` already owns the ``Credential`` aggregate, but that module's
whole surface is tenant-scoped by construction: its repository filters on
``ctx.workspace_id``, its ``list`` deliberately refuses to enumerate platform
rows ("they are not the workspace's to manage"), and its router answers
``scope='platform'`` with a 403. A platform key has no ``ctx`` to be scoped by
— it belongs to the deployment — so it is reached here, through the same
narrowly-sentinelled admin session every other cross-tenant write already uses,
rather than by widening a tenant port until it could see rows it must not.

**Revoke, never delete.** ``revoke`` flips a status and leaves the row, exactly
as the tenant-facing ``RevokeCredential`` does, and the RLS policy backing this
port carries no DELETE at all
(``migrations/versions/credentials/0003_platform_admin_keys.py``). A key that
was once live stays visible as revoked, which is what lets an operator explain
a provider that went quiet last Tuesday.

**``StoredCipher`` is not a secret.** It is the ``CipherRef`` pair the
credential row already holds (INV-C2) — Vault ciphertext plus the Transit key
that will open it — and it exists so the probe can decrypt INSIDE the platform
boundary. No face here returns plaintext.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.framework.types import Uuid

KeyPresence = Literal["absent", "active"]


@dataclass(frozen=True, slots=True)
class PlatformProviderKey:
    """One platform-scope credential as an operator may see it — metadata only.

    ``label`` is the non-secret display hint the credentials module already
    mints (the masked last four characters when the operator names none), so
    two rotations of the same provider stay distinguishable without either of
    them being readable.
    """

    id: Uuid
    provider: str
    label: str
    status: str  # 'active' | 'revoked'
    created_by: Uuid | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredCipher:
    """A stored key's Vault reference — ciphertext plus its Transit key name."""

    ciphertext: str
    key_name: str


@dataclass(frozen=True, slots=True)
class PlatformKeyChange:
    """What one audited key transition did.

    ``changed=False`` is the idempotent path — revoking a provider that has no
    active key — and it carries no ``audit_id`` because nothing happened; the
    ledger records actions, not attempts.
    """

    provider: str
    key: PlatformProviderKey | None
    previous_status: KeyPresence
    changed: bool
    audit_id: Uuid | None


class PlatformCredentialStore(Protocol):
    """Read and rotate the platform-scope credentials, audited atomically."""

    async def active_keys(self) -> tuple[PlatformProviderKey, ...]: ...

    async def active_cipher(self, provider: str) -> StoredCipher | None: ...

    async def store(
        self,
        *,
        provider: str,
        ciphertext: str,
        key_name: str,
        label: str,
        actor_user_id: Uuid,
        reason: str,
    ) -> PlatformKeyChange:
        """Replace this provider's platform key in one transaction.

        Storing over a live key is a ROTATION, not a second key: the previous
        row is revoked and the new one inserted together, so no window exists
        in which the fleet could resolve either of two active platform rows.
        """
        ...

    async def revoke(
        self, *, provider: str, actor_user_id: Uuid, reason: str
    ) -> PlatformKeyChange: ...
