"""The Credentials router — ``/api/v1/credentials`` (03-api-spec §1 · 06 §3)
— Phase 6.1-و-2.

Three routes over ``CredentialUseCases``, exactly 03 §1's ``GET · POST ·
DELETE``:

* ``GET /credentials`` — this workspace's stored keys, metadata only
  (API-04 envelope, ``next_cursor: null``);
* ``POST /credentials`` — store a provider key (201);
* ``DELETE /credentials/{id}`` — revoke it (204, idempotent).

**This is the module's only door that a client touches, and the decrypting
face is not behind it.** ``ResolveCredential`` — the one use-case that turns
a ``CipherRef`` back into a usable key — is not in the bundle ``ApiServices``
holds, so no request shape can reach it. The provider resolver calls it
inside the platform boundary, per D-16; a workspace can create and destroy
its keys through this router but can never read one back.

**DELETE revokes, it does not erase.** ``RevokeCredential`` flips status and
the row stays, which is what makes the operation idempotent (re-revoking
emits no second event) and what keeps ``GET`` able to explain why a provider
went quiet. 204 either way — a client deleting twice has got what it asked
for both times.

**Internal events, dropped here on purpose.** ``CredentialAdded``/
``CredentialRevoked`` carry no promotion asterisk in 04 §5: they are
in-memory domain events that never cross a stream in v1, so there is no
outbox row to write and no ``CompleteUploadService``-style unit of work to
wrap this in (the ``POST /conversations`` precedent, whose
``ConversationStarted`` is internal for the same reason).

**Auth on every route** via the router-level ``current_principal`` dependency
(03 §0). The owner/admin RBAC guard these three deserve — a member should not
be rotating a workspace's provider keys — is 6.4's, like every other router's.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.credentials import CredentialCreateIn, CredentialOut
from app.api.v1.dto.pagination import Page, PageMeta
from app.framework.errors import ForbiddenError
from app.modules.access.domain.value_objects import Permission
from app.modules.credentials.domain.entities import Credential

# Belt and braces, knowingly (the §3.56 precedent): every route builds `ctx`.
router = APIRouter(
    prefix="/credentials", tags=["credentials"], dependencies=[Depends(current_principal)]
)


def _to_credential_out(credential: Credential) -> CredentialOut:
    return CredentialOut(
        id=credential.id,
        provider=credential.provider.value,
        scope=credential.scope.value,
        label=credential.label,
        status=credential.status.value,
        created_at=credential.created_at,
    )


@router.get("", dependencies=[Depends(require(Permission.CREDENTIALS_READ))])
async def list_credentials(services: Services, ctx: Context) -> Page[CredentialOut]:
    """The workspace's own credentials, newest first, revoked rows included.

    Bounded and unpaginated (03 §2 lists ``listCredentials`` among the
    wrapped-but-unpaginated collections): one active key per provider over a
    closed provider vocabulary. Platform keys are absent — they are not this
    workspace's to see or revoke (see ``CredentialRepository.list``).
    """
    credentials = await services.credentials.list.execute(ctx)
    data = [_to_credential_out(credential) for credential in credentials]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.post("", status_code=201, dependencies=[Depends(require(Permission.CREDENTIALS_MANAGE))])
async def create_credential(
    body: CredentialCreateIn, services: Services, ctx: Context
) -> CredentialOut:
    """Store a provider key, encrypted at rest (201 + the bare resource).

    ``scope='platform'`` is refused with 403 rather than 422: it is a real
    scope, just not one a workspace principal may write. INV-C1 makes a
    platform row one with ``workspace_id IS NULL``, and no RLS-subject path
    inserts such a row (the SQL adapter's R3 note) — platform keys are seeded
    out of band by an operator. Answering «that is not a valid scope» would
    misdescribe the vocabulary; «that is not yours to create» is the truth.

    An unknown provider surfaces as the use-case's own
    ``credentials.provider_unknown``/422, a second active key for the same
    provider as ``common.conflict``/409.
    """
    if body.scope == "platform":
        raise ForbiddenError(
            "platform credentials are provisioned out of band, not through the API"
        )
    credential, _events = await services.credentials.add.execute(
        ctx, provider=body.provider, raw_key=body.secret, label=body.label
    )
    return _to_credential_out(credential)


@router.delete(
    "/{credential_id}",
    status_code=204,
    dependencies=[Depends(require(Permission.CREDENTIALS_MANAGE))],
)
async def delete_credential(credential_id: str, services: Services, ctx: Context) -> None:
    """Revoke a stored key (204, no body). Unknown or another tenant's ⇒ 404
    (the §3.55 read precedent); already revoked ⇒ 204 again, since
    ``RevokeCredential`` is idempotent by contract."""
    await services.credentials.revoke.execute(ctx, credential_id=credential_id)
