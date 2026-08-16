"""The Files router — ``/api/v1/files`` (03-api-spec §1 · FR-100) — Phase
6.1-هـ-3.

Six routes, each a thin delegate over the ``FileUseCases`` bundle (§3.60):

* ``POST /files`` — register an upload slot (201 + presigned PUT);
* ``POST /files/{id}/complete`` — mark the uploaded bytes ready (200);
* ``GET /files`` — the workspace's active files, paginated (API-04 envelope);
* ``GET /files/{id}`` — one file, bare;
* ``PATCH /files/{id}`` — rename (200; the extension is immutable, INV-F4);
* ``DELETE /files/{id}`` — soft-delete (204, idempotent).

Everything interesting happens BELOW this file, which is the point: presign
TTLs, the ready-only download URL, the optional checksum, the atomic
complete/delete — all live in the module's application services, so this
router is `10 §3`'s "DTO validation + call a use-case" and nothing more. The
error faces come from the use-cases' own raised codes (``files.too_large``/413,
``files.unsupported_type``/415, ``files.too_many``/409, ``common.not_found``/
404) through the app-level RFC 9457 handlers.

**A present-but-not-ready file is a body, not a problem** (the §3.58
status-is-a-field logic): ``GET /files/{id}`` on a half-uploaded file answers
200 with ``status: "uploaded"`` and ``download_url: null`` — refusing it would
turn a truthful state into an error about a resource that plainly exists.

**``Idempotency-Key`` on the register route (3.79).** 03 §0 gives the heavy
creates the header and ``openapi.yaml`` declares it on ``registerFile``; from
6.1-هـ-3 until 3.79 it was accepted and silently dropped, which on a create
that consumes a workspace's file quota meant a client believing its retry was
safe when it was not. It is now backed by the Postgres ledger
(``api/v1/idempotency.py``): same key + same body replays the first
``FileRegisterOut`` — the same file id and the same presigned URL — instead of
burning a second slot; same key + a different body is a 409. Absent header ⇒
byte-for-byte the previous behaviour, including no database round trip.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.files import (
    FileCompleteIn,
    FileOut,
    FileRegisterIn,
    FileRegisterOut,
    FileRenameIn,
)
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.api.v1.idempotency import IdempotencyKey, idempotent
from app.modules.access.domain.value_objects import Permission
from app.modules.files.application.use_cases import ReadFile

# Belt and braces, knowingly (the §3.56 precedent): every route below builds
# `ctx`, whose chain authenticates anyway. The router-level gate keeps the
# guarantee structural for any future route that doesn't need a context.
router = APIRouter(prefix="/files", tags=["files"], dependencies=[Depends(current_principal)])


def _to_file_out(read: ReadFile) -> FileOut:
    file = read.file
    return FileOut(
        id=file.id,
        name=file.name.value,
        content_type=file.content_type.value,
        size_bytes=file.size_bytes,
        status=file.status.value,
        download_url=read.download_url,
        created_at=file.created_at,
    )


@router.post("", status_code=201, dependencies=[Depends(require(Permission.FILES_WRITE))])
async def register_file(
    body: FileRegisterIn,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> FileRegisterOut:
    """Register an upload slot (201): the client PUTs the bytes to
    ``upload_url`` out of band, then calls ``/complete``. Limit violations
    (07 §4) surface as the use-case's own 413/415/409 codes.

    Replaying the stored ``FileRegisterOut`` gives back a presigned URL minted
    at the FIRST attempt, so its remaining lifetime is shorter than
    ``expires_in`` claims. That is the correct trade: re-minting would mean a
    second storage key and a second quota slot, which is the duplicate this
    header exists to prevent — and the client that is retrying still holds the
    original URL anyway.
    """

    async def _register() -> FileRegisterOut:
        registered = await services.files.transfers.register(
            ctx,
            # `space_id` is not on `FileRegisterIn` yet: the wire contract
            # (§3.7) and the switch to `services.space_quota` are step 12/13,
            # and a router that guessed a space would be inventing ownership
            # the client never stated. Typed as `None` rather than defaulted so
            # this route shows up as one of the writers still owing a space.
            space_id=None,
            name=body.name,
            content_type=body.content_type,
            size_bytes=body.size_bytes,
        )
        return FileRegisterOut(
            file_id=registered.file.id,
            upload_url=registered.upload_url,
            expires_in=registered.expires_in,
        )

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /files",
        key=idempotency_key,
        body=body,
        model=FileRegisterOut,
        run=_register,
    )


@router.post("/{file_id}/complete", dependencies=[Depends(require(Permission.FILES_WRITE))])
async def complete_file(
    file_id: str, services: Services, ctx: Context, body: FileCompleteIn | None = None
) -> FileOut:
    """Mark the uploaded bytes ready (INV-F2 — the moment ``knowledge`` may
    index the file). The BODY is optional (openapi.yaml: ``required: false``):
    an absent body and an absent checksum mean the same honest completion.
    The response renders the file ``CompleteUploadService`` just returned —
    ``describe`` presigns its download URL without a second read."""
    checksum = None if body is None else body.checksum
    file = await services.files.complete.complete(ctx, file_id=file_id, checksum=checksum)
    return _to_file_out(await services.files.transfers.describe(file))


@router.get("", dependencies=[Depends(require(Permission.FILES_READ))])
async def list_files(
    services: Services, ctx: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> Page[FileOut]:
    """The workspace's active files (API-04 envelope), ready rows carrying
    their presigned GET.

    Still the WHOLE workspace: ``?space_id=`` becomes a mandatory query
    parameter in step 12 (§3.7), and until it exists on the wire the honest
    filter is "none" — narrowing to a space the client did not name would
    hide files from a listing that promises all of them."""
    page = await services.files.transfers.list(ctx, space_id=None, limit=limit, cursor=cursor)
    return Page(
        data=[_to_file_out(read) for read in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.get("/{file_id}", dependencies=[Depends(require(Permission.FILES_READ))])
async def get_file(file_id: str, services: Services, ctx: Context) -> FileOut:
    """One file, bare. Unknown, another tenant's, or soft-deleted ⇒ 404
    (the §3.55 read precedent); not-ready ⇒ 200 with its status."""
    read = await services.files.transfers.get(ctx, file_id)
    return _to_file_out(read)


@router.patch("/{file_id}", dependencies=[Depends(require(Permission.FILES_WRITE))])
async def rename_file(
    file_id: str, body: FileRenameIn, services: Services, ctx: Context
) -> FileOut:
    """Rename a file (BE-RAG-006) — the only field of it that may change.

    ``files:write``, the same permission that registered it: renaming destroys
    nothing, so requiring ``files:delete`` would leave a member able to upload
    a file but unable to correct its name. The extension policy (INV-F4)
    surfaces as the use-case's 422; a soft-deleted file as its 409. The
    response is the full ``FileOut`` — the client replaces the row it holds
    rather than patching a name into it — and ``describe`` presigns the
    download URL from the aggregate the use-case just returned, without a
    second read (the complete route's reasoning)."""
    file = await services.files.rename.execute(ctx, file_id, name=body.name)
    return _to_file_out(await services.files.transfers.describe(file))


@router.delete(
    "/{file_id}", status_code=204, dependencies=[Depends(require(Permission.FILES_DELETE))]
)
async def delete_file(file_id: str, services: Services, ctx: Context) -> None:
    """Soft-delete (204, no body). Idempotent by the use-case's contract —
    the declared ``status_code`` produces the empty 204 (the conversations
    router's reasoning: one source for one status)."""
    await services.files.delete.delete(ctx, file_id)
