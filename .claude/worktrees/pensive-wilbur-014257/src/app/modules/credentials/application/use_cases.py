"""Credentials use-cases (06-domain-models §3).

``AddUserCredential`` encrypts a raw provider key through the ``SecretsProvider``
(Vault Transit, shared ``tenant-secrets`` key — 05 §3.2) and stores only the
``CipherRef`` (INV-C2). ``RevokeCredential`` flips status idempotently.
``ResolveCredential`` implements the ``CredentialResolver`` inbound port: user
key first, then platform, no cross-provider fallback (D-16), decrypting inside
the platform boundary only. Domain-rule violations map to the shared error
hierarchy here; the domain itself stays framework-free.

``ListCredentials`` + ``CredentialUseCases`` (6.1-و-2) are the API-facing
surface. The bundle carries the three client-reachable faces ONLY —
``ResolveCredential`` is pointedly absent, because it is the one face that
DECRYPTS (INV-C2): it exists for the provider resolver inside the platform
boundary, and a bundle the API layer holds must not be able to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.secrets_provider import SecretsProvider
from app.framework.types import Uuid
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.errors import CredentialError
from app.modules.credentials.domain.events import (
    CredentialAdded,
    CredentialEvent,
    CredentialRevoked,
)
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)
from app.modules.credentials.ports.inbound import ResolvedKey
from app.modules.credentials.ports.repository import CredentialRepository

# Unified Transit key shared by credentials + integrations (05 §3.2, SEC-07).
_TENANT_SECRETS_KEY = "tenant-secrets"

# 03 §4's catalog codes for this module: "مزوّد غير مدعوم" (422) and "لا مفتاح
# مستخدم/منصّة للمزوّد (لا Fallback، D-16)" (409). Both are now raised — see
# `ResolveCredential` for why the second one changed the STATUS too (6.2).
_PROVIDER_UNKNOWN = "credentials.provider_unknown"
_NONE_AVAILABLE = "credentials.none_available"


def _mask(raw_key: str) -> str:
    """A non-secret display hint: the last four characters, masked."""
    return f"****{raw_key[-4:]}"


class AddUserCredential:
    """Store a user's provider key, encrypted at rest. Refuses a duplicate active key."""

    def __init__(self, credentials: CredentialRepository, secrets: SecretsProvider) -> None:
        self._credentials = credentials
        self._secrets = secrets

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        provider: str,
        raw_key: str,
        label: str | None = None,
    ) -> tuple[Credential, tuple[CredentialEvent, ...]]:
        """Authorship comes from ``ctx``, not from an argument (6.1-و-2 — the
        files/media/conversations precedent, ``created_by=ctx.user_id``).

        It used to be an explicit ``created_by`` parameter, which made "who
        added this key" something a caller could NAME rather than something
        the authenticated request already knows. For a credential that is the
        wrong seam to leave open: the audit trail on a secret should not be
        forgeable by the layer that hands the secret over. ``ctx.user_id`` is
        typed optional (a system/worker context has none) and the entity's
        field is not, so an absent author falls to ``""`` — the same "unknown
        author" value the SQL adapter already hydrates from a NULL column.
        """
        try:
            provider_ref = ProviderRef(provider)
        except CredentialError as exc:
            raise ValidationError(str(exc), code=_PROVIDER_UNKNOWN) from exc
        if not raw_key.strip():
            raise ValidationError("credential value must not be empty")
        existing = await self._credentials.find_active(ctx, provider_ref, CredentialScope.USER)
        if existing is not None:
            raise ConflictError("an active credential for this provider already exists")
        ciphertext = await self._secrets.encrypt(_TENANT_SECRETS_KEY, raw_key.encode("utf-8"))
        now = utc_now()
        credential = Credential(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            provider=provider_ref,
            scope=CredentialScope.USER,
            label=(label.strip() if label and label.strip() else _mask(raw_key)),
            ciphertext_ref=CipherRef(ciphertext=ciphertext, key_name=_TENANT_SECRETS_KEY),
            status=CredentialStatus.ACTIVE,
            created_by=ctx.user_id or "",
            created_at=now,
            updated_at=now,
            version=1,
        )
        await self._credentials.add(ctx, credential)
        event = CredentialAdded(credential.id, provider_ref.value, CredentialScope.USER.value, now)
        return credential, (event,)


class RevokeCredential:
    """Revoke a stored credential. Idempotent; re-revoking emits no new event."""

    def __init__(self, credentials: CredentialRepository) -> None:
        self._credentials = credentials

    async def execute(
        self, ctx: ExecutionContext, *, credential_id: Uuid
    ) -> tuple[Credential, tuple[CredentialEvent, ...]]:
        credential = await self._credentials.get(ctx, credential_id)
        if credential is None:
            raise NotFoundError("credential not found")
        already_revoked = credential.status is CredentialStatus.REVOKED
        credential.revoke(utc_now())
        await self._credentials.save(ctx, credential)
        events: tuple[CredentialEvent, ...] = (
            () if already_revoked else (CredentialRevoked(credential.id, credential.updated_at),)
        )
        return credential, events


class ListCredentials:
    """This workspace's stored credentials — metadata only (06 §3).

    There is nothing to redact at this layer and that is the point: the
    aggregate holds a ``CipherRef``, never a plaintext secret (INV-C2), so
    "the secret is never returned" is a property of the DATA MODEL rather
    than a filter in the DTO mapper that a future field could slip past.

    Revoked rows are included: revocation is a status transition, not a
    deletion, and hiding them would make ``DELETE`` look lossy and leave a
    client unable to explain why a provider stopped working.
    """

    def __init__(self, credentials: CredentialRepository) -> None:
        self._credentials = credentials

    async def execute(self, ctx: ExecutionContext) -> tuple[Credential, ...]:
        return tuple(await self._credentials.list(ctx))


class ResolveCredential:
    """Implements ``CredentialResolver`` (02 §2): user key first, then platform (D-16).

    **Not part of ``CredentialUseCases``** — this is the decrypting face, and
    it belongs to the provider resolver inside the platform boundary.

    The tension §3.63 deferred is resolved here (6.2): the "no key at all"
    outcome is ``credentials.none_available``/409, 03 §4's own entry, not the
    inherited ``common.not_found``/404. 404 was answering a question nobody
    asked — the provider exists (an unknown one is already
    ``credentials.provider_unknown``/422 two lines up) and so does the
    workspace; what is missing is a key the caller can go and add. That is a
    conflict with the workspace's current state, and 409 says "do something
    and retry" where 404 said "give up". The old taxonomy's point survives
    intact: the failure still names CREDENTIALS, never the provider.
    """

    def __init__(self, credentials: CredentialRepository, secrets: SecretsProvider) -> None:
        self._credentials = credentials
        self._secrets = secrets

    async def resolve(self, ctx: ExecutionContext, provider: str) -> ResolvedKey:
        try:
            provider_ref = ProviderRef(provider)
        except CredentialError as exc:
            raise ValidationError(str(exc), code=_PROVIDER_UNKNOWN) from exc
        credential = await self._credentials.find_active(ctx, provider_ref, CredentialScope.USER)
        if credential is None:
            credential = await self._credentials.find_active(
                ctx, provider_ref, CredentialScope.PLATFORM
            )
        if credential is None:
            raise ConflictError(
                f"no active credential for provider {provider_ref.value}",
                code=_NONE_AVAILABLE,
            )
        plaintext = await self._secrets.decrypt(
            credential.ciphertext_ref.key_name, credential.ciphertext_ref.ciphertext
        )
        return ResolvedKey(
            provider=provider_ref.value,
            api_key=plaintext.decode("utf-8"),
            scope=credential.scope.value,
        )


@dataclass(frozen=True, slots=True)
class CredentialUseCases:
    """The module's API-facing bundle (the ``FileUseCases``/``UsageUseCases``
    precedent): ONE field on ``ApiServices`` per module.

    Three faces, matching 03 §1's ``GET · POST · DELETE`` exactly.
    ``ResolveCredential`` is deliberately not here — see its docstring. What
    a bundle omits is what the API layer structurally cannot reach.
    """

    list: ListCredentials
    add: AddUserCredential
    revoke: RevokeCredential
