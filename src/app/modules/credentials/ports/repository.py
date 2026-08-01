"""Credentials persistence port (02-port-contracts §2).

Outbound repository for the ``Credential`` aggregate. Tenant-scoped reads apply
the RLS guard + ``WHERE workspace_id`` via ``ctx`` (DD-04); platform-scoped rows
(``workspace_id IS NULL``) are reachable through ``find_active`` for resolution.
``save`` uses an optimistic lock on ``version`` (a stale write conflicts at the
adapter). ``find_active`` backs both the duplicate check and resolution;
``list`` (added in 6.1-و-2 for ``GET /api/v1/credentials``) is the strictly
tenant-scoped enumeration — it deliberately does NOT reach the platform rows
``find_active`` can see.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.value_objects import CredentialScope, ProviderRef


class CredentialRepository(Protocol):
    """Tenant-scoped persistence for provider credentials."""

    async def add(self, ctx: ExecutionContext, credential: Credential) -> None: ...

    async def get(self, ctx: ExecutionContext, credential_id: Uuid) -> Credential | None: ...

    async def find_active(
        self, ctx: ExecutionContext, provider: ProviderRef, scope: CredentialScope
    ) -> Credential | None: ...

    async def list(self, ctx: ExecutionContext) -> Sequence[Credential]:
        """This workspace's own credentials, newest first — revoked rows
        included (revocation is a status, not a deletion).

        Strictly tenant-scoped like ``get`` (R7): platform rows
        (``workspace_id IS NULL``) are NOT enumerated here even though the
        ``platform_credentials_read`` policy would let them through at the RLS
        layer. They are not the workspace's to manage, and a listing that
        mixed them in would invite a client to try revoking one.

        Unpaginated by design: a workspace holds one active key per provider
        over a closed provider vocabulary, so the set is bounded by
        construction. 03 §2 lists ``listCredentials`` among the wrapped-but-
        unpaginated collections (``meta.next_cursor = null``).
        """
        ...

    async def save(self, ctx: ExecutionContext, credential: Credential) -> None: ...
