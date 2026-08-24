"""Files use-cases (06-domain-models §6).

Thin application services that coordinate the pure domain over an injected
``FileRepository``. They own identity/time (framework ``new_uuid7`` /
``utc_now``) and enforce the *configurable* business limits (07-nfr-slo §4) —
whitelist, max size, per-workspace cap — that the domain value objects
deliberately leave unchecked, translating both domain-rule and limit
violations into the shared framework error hierarchy at this boundary.

``RegisterUpload`` returns no event: the client still has to upload bytes to
``storage_key`` and call ``CompleteUpload``. The global ``FileUploaded`` event
fires only on completion (status -> ready), so ``knowledge`` never indexes a
half-uploaded file.

``CompleteUploadService`` (5.1-أ) is the ``MediaRequestService`` precedent
applied to ``CompleteUpload``: the first files caller that does not drop
``CompleteUpload``'s returned events on the floor, appending them to the
``EventOutbox`` inside one request-scoped unit of work.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    ConflictError,
    NotFoundError,
    TooLargeError,
    UnsupportedTypeError,
    ValidationError,
)
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.storage_provider import StorageProvider
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.settings.settings import Limits
from app.framework.types import Uuid
from app.modules.files.application.event_mapping import to_outbox_record
from app.modules.files.domain.entities import File
from app.modules.files.domain.errors import FileError, FileStateError
from app.modules.files.domain.events import FileDeleted, FileEvent, FileUploaded
from app.modules.files.domain.value_objects import (
    ContentType,
    FileName,
    FileStatus,
    Sha256,
    StorageKey,
)
from app.modules.files.ports.inbound import FileView
from app.modules.files.ports.repository import FileRepository, SpaceFileTotals
from app.modules.files.ports.spaces import ActiveSpaces


class RegisterUpload:
    """Register a new upload slot: validates shape + the configured limits and
    mints a ``File`` in ``uploaded`` status. The caller uploads bytes to
    ``storage_key`` out of band, then calls ``CompleteUpload``.

    **``space_id`` is proven before it is stored** (spaces plan step 6). An id
    that names no live space would file the row under an axis that does not
    exist: invisible to every listing and uncounted by every quota, with no
    error anywhere. So a non-``None`` value is checked against the injected
    ``ActiveSpaces`` seam (``ports/spaces.py``) and a 404 is the answer for
    unknown, foreign-tenant and deleted alike.

    On the quota path (``framework/di/space_quota.py``) this check runs a
    second time inside a transaction that already holds the space's row lock,
    and that redundancy is accepted rather than removed: the lock is the
    authority on CONCURRENCY, this is the authority on EXISTENCE, and this
    use-case is also reached directly — by the media worker and by the
    router — where no lock was ever taken. One extra indexed primary-key read
    inside an open transaction is a cheap price for a check that does not
    depend on which caller arrived.

    ``space_id`` is required-but-nullable, with no default. ``None`` means "no
    space", which is a real state until plan row 8-b makes the column
    ``NOT NULL``, but it has to be TYPED at the call site: a default would let
    a writer drop into it silently, and the whole point of the transitional
    window is that the writers still owing a space are visible.
    """

    def __init__(self, files: FileRepository, limits: Limits, spaces: ActiveSpaces) -> None:
        self._files = files
        self._limits = limits
        self._spaces = spaces

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid | None,
        name: str,
        content_type: str,
        size_bytes: int,
    ) -> File:
        try:
            file_name = FileName(name)
            media_type = ContentType(content_type)
        except FileError as exc:
            raise ValidationError(str(exc)) from exc

        if media_type.value not in self._limits.allowed_mime:
            raise UnsupportedTypeError(
                f"unsupported content type: {media_type.value!r}",
                code="files.unsupported_type",
            )
        if size_bytes < 0:
            raise ValidationError("size_bytes must not be negative")
        if size_bytes > self._limits.max_upload_bytes:
            raise TooLargeError(
                f"file exceeds the {self._limits.max_upload_bytes} byte limit",
                code="files.too_large",
            )
        if space_id is not None and await self._spaces.get_active(ctx, space_id) is None:
            raise NotFoundError("space not found")
        if await self._files.count(ctx) >= self._limits.max_files_per_workspace:
            raise ConflictError(
                f"workspace has reached the {self._limits.max_files_per_workspace} file limit",
                code="files.too_many",
            )

        now = utc_now()
        file_id = new_uuid7()
        file = File(
            id=file_id,
            workspace_id=ctx.workspace_id,
            space_id=space_id,
            name=file_name,
            content_type=media_type,
            size_bytes=size_bytes,
            storage_key=StorageKey.for_file(ctx.workspace_id, file_id),
            checksum=None,
            status=FileStatus.UPLOADED,
            uploaded_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            version=1,
        )
        await self._files.add(ctx, file)
        return file


class CompleteUpload:
    """Mark a registered upload ``ready`` once its bytes have landed in
    storage, recording the checksum. Emits the global ``FileUploaded`` event
    (INV-F2) — the only point at which ``knowledge`` may index the file."""

    def __init__(self, files: FileRepository) -> None:
        self._files = files

    async def execute(
        self, ctx: ExecutionContext, *, file_id: Uuid, checksum: str | None
    ) -> tuple[File, tuple[FileEvent, ...]]:
        file = await self._files.get(ctx, file_id)
        if file is None:
            raise NotFoundError("file not found")
        # Optional on the wire (03 §2: `FileCompleteIn.checksum: str | None`),
        # so optional here: a PRESENT checksum must be a valid sha256, an
        # absent one completes honestly with `checksum=None` (6.1-هـ-2 —
        # the domain's `complete` widened in the same step).
        sha: Sha256 | None = None
        if checksum is not None:
            try:
                sha = Sha256(checksum)
            except FileError as exc:
                raise ValidationError(str(exc)) from exc

        now = utc_now()
        try:
            file.complete(sha, now)
        except FileStateError as exc:
            raise ConflictError(str(exc)) from exc
        await self._files.save(ctx, file)

        event = FileUploaded(
            file.id,
            ctx.workspace_id,
            # The row's space, read back off the aggregate rather than taken
            # from the request (spaces plan step 8): the file was filed under
            # it at registration, `save` cannot move it, and `knowledge` files
            # its document under whatever this says.
            file.space_id,
            file.content_type.value,
            file.size_bytes,
            file.storage_key.value,
            None if sha is None else sha.value,
            now,
        )
        return file, (event,)


class ListFiles:
    """List active files via keyset pagination on ``id`` — one space's when
    ``space_id`` is given, the whole workspace's when it is ``None`` (the
    port's docstring says why that stays sayable, and why it must be said)."""

    def __init__(self, files: FileRepository) -> None:
        self._files = files

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[File]:
        return await self._files.list(ctx, space_id=space_id, limit=limit, cursor=cursor)


class SummariseSpaces:
    """``bytes_used``/``file_count`` for a page of spaces — the files half of
    what ``GET /api/v1/spaces`` publishes (``docs/spaces-backend-plan.md``
    §3.7, step 12).

    **Why this is a files use-case and not a spaces one.** ``ListSpaces`` says
    it: these are sums over ``files.files``, and a spaces module that could
    compute them would be a spaces module that reads another module's table.
    The numbers are answered here, by their owner, and the spaces ROUTER —
    the one place allowed to hold both modules' faces — puts them side by
    side.

    It takes opaque ids and returns a mapping keyed by them. This module still
    does not know what a space is; it knows its rows carry one, and that a
    caller may hand it several at once.
    """

    def __init__(self, files: FileRepository) -> None:
        self._files = files

    async def execute(
        self, ctx: ExecutionContext, space_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, SpaceFileTotals]:
        return await self._files.totals_by_space(ctx, space_ids)


class RenameFile:
    """Rename a file (BE-RAG-006). The extension policy is the aggregate's
    (``File.rename``, INV-F4); this service only owns the boundary — the raw
    string becomes a ``FileName`` here, and the domain's two error kinds are
    translated into the framework's 422/409.

    **No event, and none is missing.** ``knowledge.documents`` keys on
    ``file_id`` and stores no name, and the Qdrant point payload carries none
    either (``knowledge/application/indexing.py``), so nothing downstream can
    consume a ``FileRenamed`` — publishing one would be the "صفٌّ في الكتالوج
    وعدٌ بالإصدار" mistake in event form: a promise with no consumer and no way
    to tell whether it is being honoured. It follows that a rename costs no
    re-index and cannot change an answer.

    **A no-op rename does not write.** ``File.rename`` returns early when the
    resulting name is the one it already has; the check below turns that into
    a skipped ``save``, because ``save`` bumps ``version`` and ``updated_at``
    unconditionally — a stored "modified at" that moves when nothing was
    modified is a false record, and the version churn would invalidate other
    holders of the aggregate for no reason. The caller still gets the file:
    "the name is already that" is a successful outcome of "make the name
    that", not a failure.
    """

    def __init__(self, files: FileRepository) -> None:
        self._files = files

    async def execute(self, ctx: ExecutionContext, file_id: Uuid, *, name: str) -> File:
        file = await self._files.get(ctx, file_id)
        if file is None:
            raise NotFoundError("file not found")
        try:
            new_name = FileName(name)
        except FileError as exc:
            raise ValidationError(str(exc)) from exc
        before = file.name.value
        try:
            file.rename(new_name, utc_now())
        except FileStateError as exc:
            raise ConflictError(str(exc)) from exc
        except FileError as exc:
            # `InvalidFileInput` — the extension policy, or a name pushed past
            # 255 characters by inheriting the current extension.
            raise ValidationError(str(exc)) from exc
        if file.name.value != before:
            await self._files.save(ctx, file)
        return file


class SoftDeleteFile:
    """Soft-delete a file. Idempotent — re-deleting emits no new event."""

    def __init__(self, files: FileRepository) -> None:
        self._files = files

    async def execute(
        self, ctx: ExecutionContext, file_id: Uuid
    ) -> tuple[File, tuple[FileEvent, ...]]:
        file = await self._files.get(ctx, file_id)
        if file is None:
            raise NotFoundError("file not found")
        already_deleted = file.deleted_at is not None
        file.soft_delete(utc_now())
        await self._files.save(ctx, file)
        events: tuple[FileEvent, ...] = (
            () if already_deleted else (FileDeleted(file.id, ctx.workspace_id, file.updated_at),)
        )
        return file, events


class PurgeSpaceFiles:
    """Destroy one space's files — objects first, then rows (steps 5 and 6 of
    ``docs/spaces-backend-plan.md`` §3.6, step 11).

    **Not in ``FileUseCases``, and that is the point.** The bundle is what the
    API layer consumes, and no request deletes a space's files: the only caller
    is the composition-root ``DeleteSpaceService``, which runs this between the
    knowledge purge and the conversations one. A face reachable from a router
    would be a way to empty a space without deleting it.

    **Objects before rows.** A row still names its object, so a failure between
    the two leaves a file whose bytes are gone — recoverable, because the next
    run reads the same rows and asks MinIO to delete keys that are already
    deleted, which is a no-op (204). The reverse order loses the keys forever:
    nothing else in the platform knows them, and the objects would then sit in
    the bucket until the whole workspace is purged.

    Deleting every object one call at a time is deliberate, not a placeholder.
    A bulk ``delete_keys`` would be faster and §3.6 says why it is refused: a
    PORT that can delete many keys at once becomes injectable into every
    module, and the whole point of this file is that only the cascade may do
    this.
    """

    def __init__(self, files: FileRepository, storage: StorageProvider) -> None:
        self._files = files
        self._storage = storage

    async def execute(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        for key in await self._files.storage_keys_in_space(ctx, space_id):
            await self._storage.delete(key)
        return await self._files.purge_space(ctx, space_id)


class FilesQueryService:
    """Implements the ``FilesQuery`` inbound port (02 §2) over the repository —
    both faces: the single readable view, and the bulk name read a corpus walk
    needs (branch review §2)."""

    def __init__(self, files: FileRepository) -> None:
        self._files = files

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> FileView | None:
        file = await self._files.get(ctx, file_id)
        if file is None or not file.is_ready:
            return None
        return FileView(
            file_id=file.id,
            space_id=file.space_id,
            name=file.name.value,
            content_type=file.content_type.value,
            size_bytes=file.size_bytes,
            storage_key=file.storage_key.value,
            status=file.status.value,
        )

    async def names_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, str]:
        # A pass-through, deliberately: the READY rule this port promises is
        # `File.is_ready`, and the repository states it in SQL rather than
        # hydrating a page of aggregates to ask each one (adapter comment).
        # Re-filtering here would be a SECOND home for the rule and the place
        # the two could drift.
        return await self._files.ready_names(ctx, file_ids)


class CompleteUploadService:
    """Wraps ``CompleteUpload`` with the Outbox append inside ONE
    request-scoped unit of work (5.1-أ) — the ``MediaRequestService``
    precedent (``modules/media/application/use_cases.py``).

    Since 6.1-هـ-2 it ships inside ``FileUseCases`` — the bundle the Phase-6
    files router (هـ-3) completes an upload through; ``ports/inbound.py``
    still declares only ``FilesQuery`` because the API consumes the module's
    use-cases directly (`10 §3`), not through a new inbound port.

    **Atomic by construction.** ``uow.begin(ctx)`` opens ONE transaction for
    the whole call: ``CompleteUpload``'s aggregate write (through its
    injected ``FileRepository``) and this append both resolve their session
    through the SAME ``TenantSessionFactory``, so the append's ``__call__``
    joins the aggregate write's transaction instead of opening its own (04
    §3.1's "لا فقد" guarantee). An outbox failure is deliberately NOT
    swallowed — it propagates out of the ``async with`` block, rolling the
    aggregate write back too: a ``File`` marked ``ready`` with no
    ``FileUploaded`` event would be invisible to ``knowledge``, exactly the
    failure atomicity exists to prevent.
    """

    def __init__(
        self, complete_upload: CompleteUpload, outbox: EventOutbox, uow: UnitOfWork
    ) -> None:
        self._complete_upload = complete_upload
        self._outbox = outbox
        self._uow = uow

    async def complete(self, ctx: ExecutionContext, *, file_id: Uuid, checksum: str | None) -> File:
        async with self._uow.begin(ctx):
            file, events = await self._complete_upload.execute(
                ctx, file_id=file_id, checksum=checksum
            )
            records = [
                record
                for record in (to_outbox_record(ctx, event) for event in events)
                if record is not None
            ]
            await self._outbox.append(ctx, records)
            return file


class SoftDeleteFileService:
    """Wraps ``SoftDeleteFile`` with the Outbox append inside ONE
    request-scoped unit of work — the ``CompleteUploadService`` shape, and the
    exact "future ``SoftDeleteFile``-backed service" ``event_mapping``'s
    docstring anticipated.

    Today the append is provably empty: ``FileDeleted`` is internal-only (04
    §5 gives it no promotion asterisk), so ``to_outbox_record`` maps it to
    ``None`` and the filter drops it. The service exists anyway, on purpose —
    the day 04 §5 promotes ``FileDeleted`` to the wire, the delete path is
    already atomic and nothing at the API layer moves; the alternative (the
    router dropping the use-case's returned events on the floor) is exactly
    the 4.7-d-1 debt shape this platform spent a phase repaying.

    **This is the MARK, not the whole deletion** — and the distinction is the
    repair of a live defect rather than a note about layering. Emptying the
    file's index is the second half, and it belongs to no module: ``DELETE
    /api/v1/files/{id}`` reaches this service THROUGH
    ``framework/di/file_deletion.py``'s ``DeleteFileService``, which calls it
    first and then purges the corpus ``knowledge`` built from the file. A
    caller that holds this service directly performs half a deletion — the row
    goes, the chunks and Qdrant points stay searchable — which is exactly the
    state the router was in before the cascade existed. The API bundle still
    carries it because the cascade is what calls it; no route may.
    """

    def __init__(self, delete: SoftDeleteFile, outbox: EventOutbox, uow: UnitOfWork) -> None:
        self._delete = delete
        self._outbox = outbox
        self._uow = uow

    async def delete(self, ctx: ExecutionContext, file_id: Uuid) -> File:
        async with self._uow.begin(ctx):
            file, events = await self._delete.execute(ctx, file_id)
            records = [
                record
                for record in (to_outbox_record(ctx, event) for event in events)
                if record is not None
            ]
            await self._outbox.append(ctx, records)
            return file


@dataclass(frozen=True, slots=True)
class RegisteredUpload:
    """``RegisterUpload``'s aggregate plus the presigned PUT the client
    uploads to — the application-level shape under 03 §2 ``FileRegisterOut``."""

    file: File
    upload_url: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class ReadFile:
    """A file plus its presigned GET — ``None`` unless the file is ``ready``
    (03 §2 ``FileOut.download_url``: "presigned GET عند ready")."""

    file: File
    download_url: str | None


class FileTransferService:
    """The presigned-URL faces of the module (6.1-هـ-2): register with an
    upload URL, read single/list with download URLs — everything 03 §2's
    ``FileRegisterOut``/``FileOut`` need beyond the bare aggregate.

    Composes ``RegisterUpload`` (all the 07 §4 limit enforcement stays there)
    with the ``StorageProvider`` port; URL minting lives HERE and not in the
    router because presigning is a port call, and `10 §3` keeps the API layer
    to "DTO validation + call a use-case". In production the injected storage
    is the Composition Root's ``StorageHandle`` (§3.59): live once the startup
    lifespan ran, a clean ``common.internal`` if it did not.

    ``get`` answers 404 for a missing AND a soft-deleted file alike — the
    §3.55 read precedent (a read's only truthful answer for a deleted resource
    is "gone"; the 409-shaped answer belongs to writes, which the domain guard
    already gives). ``list`` presigns only the ``ready`` rows: presigning is
    local HMAC work in the MinIO adapter, but the port cannot promise that, so
    the page size (07 §4, ≤50) is the accepted per-request ceiling on presign
    calls — recorded rather than silently assumed.

    The two lifetimes are INJECTED (3.79), not module constants: they are
    operator-tunable configuration (``MINIO_PRESIGN_PUT_TTL_S`` /
    ``MINIO_PRESIGN_GET_TTL_S``, 05 §2), and the bounds that make an unsignable
    value impossible are enforced once at boot by ``MinioSettings``. Passed as
    two plain ints rather than the ``MinioSettings`` object so this application
    service still cannot see an endpoint, a bucket or a TLS flag — none of
    which are its business — and ``expires_in`` stays what was actually signed
    by construction, since one value feeds both the presign call and the DTO.
    """

    def __init__(
        self,
        register: RegisterUpload,
        files: FileRepository,
        storage: StorageProvider,
        *,
        put_ttl_s: int,
        get_ttl_s: int,
    ) -> None:
        self._register = register
        self._files = files
        self._storage = storage
        self._put_ttl_s = put_ttl_s
        self._get_ttl_s = get_ttl_s

    async def register(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid | None,
        name: str,
        content_type: str,
        size_bytes: int,
    ) -> RegisteredUpload:
        file = await self._register.execute(
            ctx,
            space_id=space_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
        )
        upload_url = await self._storage.presign_put(
            file.storage_key.value, self._put_ttl_s, file.content_type.value
        )
        return RegisteredUpload(file=file, upload_url=upload_url, expires_in=self._put_ttl_s)

    async def get(self, ctx: ExecutionContext, file_id: Uuid) -> ReadFile:
        file = await self._files.get(ctx, file_id)
        if file is None or file.deleted_at is not None:
            raise NotFoundError("file not found")
        return await self.describe(file)

    async def list(
        self,
        ctx: ExecutionContext,
        *,
        space_id: Uuid | None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> Page[ReadFile]:
        page = await self._files.list(ctx, space_id=space_id, limit=limit, cursor=cursor)
        rows = [await self.describe(file) for file in page.data]
        return Page(data=rows, next_cursor=page.next_cursor, limit=page.limit)

    async def describe(self, file: File) -> ReadFile:
        """An ALREADY-LOADED aggregate → its wire-ready ``ReadFile``. Public
        (6.1-هـ-3) for the caller that holds a fresh ``File`` from another
        use-case — the complete route renders the file ``CompleteUploadService``
        just returned, and re-reading it for a URL would be a second
        transaction recovering data the caller already holds (the ``submit``
        argument, applied to reads)."""
        if not file.is_ready:
            return ReadFile(file=file, download_url=None)
        url = await self._storage.presign_get(file.storage_key.value, self._get_ttl_s)
        return ReadFile(file=file, download_url=url)


@dataclass(frozen=True, slots=True)
class FileUseCases:
    """The bundle the API layer consumes (the ``ConversationUseCases``
    precedent: the module packages its own faces so ``ApiServices`` grows ONE
    field in 6.1-هـ-3). ``transfers`` covers register/get/list (the presigned
    faces); ``complete`` and ``delete`` are the two atomic write services.

    ``rename`` is deliberately the bare use-case and not a ``…Service``
    wrapper: the wrappers exist to append events inside one unit of work, and
    a rename produces none (``RenameFile``'s docstring says why). Wrapping it
    anyway would ship the ceremony of atomicity around a single statement that
    has nothing to be atomic with."""

    transfers: FileTransferService
    complete: CompleteUploadService
    rename: RenameFile
    delete: SoftDeleteFileService
    # `spaces-backend-plan.md` step 12 — the files half of `GET /api/v1/spaces`
    # (§3.7). It is on THIS bundle, not on a spaces one, because the numbers
    # are sums over this module's table; the spaces router holds both bundles
    # and is the only place the two halves meet.
    space_totals: SummariseSpaces
