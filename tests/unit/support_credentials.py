"""In-memory credentials wiring shared by the API router tests (6.1-و-2).

Not a ``test_*`` module, so pytest never collects it — the
``support_workspace_usage`` precedent: every router test file constructs
``ApiServices``, and a per-file copy of these fakes is how copies drift.

Two fakes, each faithful on exactly what the routes ride on:

* ``InMemoryCredentialRepository.list`` returns the caller's OWN rows only,
  newest first — the tenant scoping and the ordering the SQL adapter gets
  from ``WHERE workspace_id`` + a descending UUIDv7 sort. Platform rows
  (``workspace_id is None``) are seedable and are deliberately invisible to
  ``list``/``get``, so a test can prove the listing does not leak them.
* ``RecordingSecrets`` is a base64 round-trip standing in for Vault Transit
  (the ``test_credentials_use_cases`` fake) that additionally REMEMBERS every
  plaintext it was handed. That is what lets a test assert the raw key
  reached the encryptor and nothing else — the ciphertext is checked not to
  contain it, and so is the response body.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Json
from app.modules.credentials.application.use_cases import (
    AddUserCredential,
    CredentialUseCases,
    ListCredentials,
    RevokeCredential,
)
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)

SEEDED_CREATED_AT = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


@dataclass
class RecordingSecrets:
    """A structural ``SecretsProvider``: base64 in place of Transit, plus a
    log of every plaintext encrypted."""

    encrypted: list[bytes] = field(default_factory=list)

    async def get_secret(self, path: str) -> Json:
        return {}

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        self.encrypted.append(plaintext)
        return f"vault:v1:{base64.b64encode(plaintext).decode('ascii')}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        return base64.b64decode(ciphertext.split(":", 2)[2])


@dataclass
class InMemoryCredentialRepository:
    """A structural ``CredentialRepository`` over one dict."""

    rows: dict[str, Credential] = field(default_factory=dict)

    async def add(self, ctx: ExecutionContext, credential: Credential) -> None:
        self.rows[credential.id] = credential

    async def get(self, ctx: ExecutionContext, credential_id: str) -> Credential | None:
        row = self.rows.get(credential_id)
        # Strict tenant-only, exactly like the SQL adapter's `get` (R7): a
        # platform row's NULL workspace never equals a real tenant id.
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def find_active(
        self, ctx: ExecutionContext, provider: ProviderRef, scope: CredentialScope
    ) -> Credential | None:
        for row in self.rows.values():
            if row.status is not CredentialStatus.ACTIVE:
                continue
            if row.provider.value != provider.value or row.scope is not scope:
                continue
            if scope is CredentialScope.USER and row.workspace_id != ctx.workspace_id:
                continue
            return row
        return None

    async def list(self, ctx: ExecutionContext) -> list[Credential]:
        return sorted(
            (row for row in self.rows.values() if row.workspace_id == ctx.workspace_id),
            key=lambda row: row.id,
            reverse=True,
        )

    async def save(self, ctx: ExecutionContext, credential: Credential) -> None:
        self.rows[credential.id] = credential


@dataclass(frozen=True, slots=True)
class CredentialsStack:
    """The bundle plus the fakes a test asserts against."""

    credentials: CredentialUseCases
    repository: InMemoryCredentialRepository
    secrets: RecordingSecrets


def seed_credential(
    *,
    credential_id: str,
    workspace_id: str | None,
    provider: str = "openai",
    scope: CredentialScope = CredentialScope.USER,
    status: CredentialStatus = CredentialStatus.ACTIVE,
    label: str = "****seed",
) -> Credential:
    return Credential(
        id=credential_id,
        workspace_id=workspace_id,
        provider=ProviderRef(provider),
        scope=scope,
        label=label,
        ciphertext_ref=CipherRef(ciphertext="vault:v1:c2VlZA==", key_name="tenant-secrets"),
        status=status,
        created_by="seeder",
        created_at=SEEDED_CREATED_AT,
        updated_at=SEEDED_CREATED_AT,
        version=1,
    )


def build_credentials() -> CredentialsStack:
    """One repository behind every face — the same single-instance wiring the
    Composition Root builds (and the reason a key added through ``POST`` is
    immediately the one ``GET`` reports)."""
    repository = InMemoryCredentialRepository()
    secrets = RecordingSecrets()
    return CredentialsStack(
        credentials=CredentialUseCases(
            list=ListCredentials(repository),
            add=AddUserCredential(repository, secrets),
            revoke=RevokeCredential(repository),
        ),
        repository=repository,
        secrets=secrets,
    )
