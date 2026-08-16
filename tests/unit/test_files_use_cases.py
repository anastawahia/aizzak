"""Unit tests for files use-cases over an in-memory fake repository.
Pure: the port is faked, so no infrastructure is exercised."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

import pytest

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
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import Limits, MinioSettings
from app.modules.files.application.use_cases import (
    CompleteUpload,
    CompleteUploadService,
    FilesQueryService,
    FileTransferService,
    ListFiles,
    RegisterUpload,
    RenameFile,
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.files.domain.entities import File
from app.modules.files.domain.events import FileDeleted, FileEvent, FileUploaded
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey

# The space every test registers into unless it is testing the check itself.
_SPACE = "sp-1"


class _FakeFiles:
    """In-memory ``FileRepository``; ``count`` scopes by ``ctx.workspace_id``
    and only counts active (non-deleted) rows."""

    def __init__(self) -> None:
        self.rows: dict[str, File] = {}
        # Every id `save` was asked to write, in order. A dict-backed fake
        # cannot otherwise distinguish "wrote the same row again" from "did
        # not write", and BE-RAG-006's no-op rename turns on that difference:
        # the real `save` bumps `version` and `updated_at` unconditionally.
        self.saved: list[str] = []

    async def get(self, ctx: ExecutionContext, file_id: str) -> File | None:
        row = self.rows.get(file_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, file: File) -> None:
        self.rows[file.id] = file

    async def save(self, ctx: ExecutionContext, file: File) -> None:
        self.rows[file.id] = file
        self.saved.append(file.id)

    async def list(
        self,
        ctx: ExecutionContext,
        *,
        space_id: str | None = None,
        limit: int,
        cursor: str | None = None,
    ) -> Page[File]:
        items = [
            row
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id
            and row.deleted_at is None
            and (space_id is None or row.space_id == space_id)
        ]
        return Page(data=items[:limit], next_cursor=None, limit=limit)

    async def count(self, ctx: ExecutionContext) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.deleted_at is None
        )

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: str) -> int:
        return sum(
            row.size_bytes
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id
            and row.space_id == space_id
            and row.deleted_at is None
        )


@dataclass(frozen=True, slots=True)
class _SpaceRow:
    """``SpaceView``'s shape — what ``ActiveSpaces.get_active`` returns."""

    space_id: str


class _FakeSpaces:
    """In-memory ``files.ports.spaces.ActiveSpaces``: only the ids it was
    constructed with are live, and it records every id it was asked about, so
    a test can prove the check was MADE and not merely satisfied."""

    def __init__(self, *active: str) -> None:
        self.active = set(active)
        self.asked: list[str] = []

    async def get_active(self, ctx: ExecutionContext, space_id: str) -> _SpaceRow | None:
        self.asked.append(space_id)
        return _SpaceRow(space_id) if space_id in self.active else None


def _register(files: _FakeFiles, limits: Limits | None = None) -> RegisterUpload:
    """``RegisterUpload`` over the fakes, with ``_SPACE`` live. Every test that
    registers goes through here so the existence seam is never bypassed — the
    tests that care about a MISSING space build their own ``_FakeSpaces``."""
    return RegisterUpload(files, limits or Limits(), _FakeSpaces(_SPACE))


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def _seed_file(
    files: _FakeFiles,
    ctx: ExecutionContext,
    *,
    deleted_at: datetime | None = None,
    space_id: str | None = _SPACE,
    size_bytes: int = 10,
) -> File:
    """Seed a ready file directly into the fake repo (bypassing the use-case)."""
    now = utc_now()
    file_id = new_uuid7()
    file = File(
        id=file_id,
        workspace_id=ctx.workspace_id,
        space_id=space_id,
        name=FileName("seed.txt"),
        content_type=ContentType("text/plain"),
        size_bytes=size_bytes,
        storage_key=StorageKey.for_file(ctx.workspace_id, file_id),
        checksum=None,
        status=FileStatus.UPLOADED,
        uploaded_by=ctx.user_id,
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        version=1,
    )
    files.rows[file.id] = file
    return file


# --------------------------------------------------------------------------- #
# RegisterUpload                                                               #
# --------------------------------------------------------------------------- #
async def test_register_upload_happy_path() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=1024
    )
    assert file.status is FileStatus.UPLOADED
    assert file.checksum is None
    assert file.storage_key.value.startswith("w1/")
    assert file.id in files.rows


async def test_register_upload_rejects_oversize() -> None:
    files = _FakeFiles()
    with pytest.raises(TooLargeError):
        await _register(files).execute(
            _ctx(),
            space_id=_SPACE,
            name="big.txt",
            content_type="text/plain",
            size_bytes=Limits().max_upload_bytes + 1,
        )


async def test_register_upload_rejects_unsupported_mime() -> None:
    files = _FakeFiles()
    with pytest.raises(UnsupportedTypeError):
        await _register(files).execute(
            _ctx(),
            space_id=_SPACE,
            name="script.exe",
            content_type="application/x-msdownload",
            size_bytes=10,
        )


async def test_register_upload_rejects_negative_size() -> None:
    files = _FakeFiles()
    with pytest.raises(ValidationError):
        await _register(files).execute(
            _ctx(),
            space_id=_SPACE,
            name="report.pdf",
            content_type="application/pdf",
            size_bytes=-1,
        )


async def test_register_upload_rejects_over_workspace_cap() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    _seed_file(files, ctx)
    with pytest.raises(ConflictError):
        await _register(files, Limits(max_files_per_workspace=1)).execute(
            ctx, space_id=_SPACE, name="two.txt", content_type="text/plain", size_bytes=10
        )


# --------------------------------------------------------------------------- #
# RegisterUpload — the space (plan step 6)                                     #
# --------------------------------------------------------------------------- #
async def test_the_registered_file_carries_the_space_it_was_filed_under() -> None:
    """The aggregate has to CARRY it, not merely be asked about it: this is the
    field the adapter's INSERT writes, and the column every later sum, listing
    and cascade reads."""
    files = _FakeFiles()
    ctx = _ctx()

    file = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=1024
    )

    assert file.space_id == _SPACE
    assert files.rows[file.id].space_id == _SPACE


async def test_registering_into_a_space_that_is_not_live_is_a_404_and_writes_nothing() -> None:
    """An id naming no live space must not reach the row. A file filed under
    one would be invisible to every listing and counted by no quota — and
    nothing downstream would ever raise, which is why the refusal has to
    happen here."""
    files = _FakeFiles()
    spaces = _FakeSpaces()  # nothing is live
    ctx = _ctx()

    with pytest.raises(NotFoundError):
        await RegisterUpload(files, Limits(), spaces).execute(
            ctx, space_id="ghost", name="report.pdf", content_type="application/pdf", size_bytes=10
        )

    assert spaces.asked == ["ghost"]
    assert files.rows == {}


async def test_a_registration_without_a_space_never_asks_and_stores_none() -> None:
    """``None`` is a stated absence, not a lookup of a missing space: the media
    worker has none to name until step 7, and asking ``get_active(None)`` would
    be a query with no question in it. The stored value is NULL — the state row
    8-b's ``SET NOT NULL`` exists to find."""
    files = _FakeFiles()
    spaces = _FakeSpaces()
    ctx = _ctx()

    file = await RegisterUpload(files, Limits(), spaces).execute(
        ctx, space_id=None, name="generated.png", content_type="image/png", size_bytes=10
    )

    assert file.space_id is None
    assert spaces.asked == []


async def test_the_space_is_checked_before_the_row_is_minted_not_after() -> None:
    """Order, not outcome: a check that ran after ``add`` would leave the file
    written and then raise, and every value-based assertion above would still
    pass."""
    files = _FakeFiles()
    ctx = _ctx()
    _seed_file(files, ctx)

    with pytest.raises(NotFoundError):
        await RegisterUpload(files, Limits(), _FakeSpaces()).execute(
            ctx, space_id="ghost", name="report.pdf", content_type="application/pdf", size_bytes=10
        )

    # Only the seeded row survives: nothing new was added on the way out.
    assert len(files.rows) == 1


# --------------------------------------------------------------------------- #
# CompleteUpload                                                               #
# --------------------------------------------------------------------------- #
async def test_complete_upload_transitions_to_ready_and_emits_event() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    checksum = "a" * 64
    completed, events = await CompleteUpload(files).execute(
        ctx, file_id=registered.id, checksum=checksum
    )
    assert completed.status is FileStatus.READY
    assert completed.checksum is not None
    assert completed.checksum.value == checksum
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, FileUploaded)
    assert event.file_id == registered.id
    assert event.content_type == "application/pdf"
    assert event.size_bytes == 2048
    assert event.storage_key == registered.storage_key.value
    assert event.checksum == checksum
    # Spaces plan step 8: the row's space travels on the event, because the
    # consumer (`knowledge`) files its document under it and has no other way
    # to learn it. Read off the AGGREGATE, so it is whatever registration
    # actually wrote — not what this call thinks it asked for.
    assert event.space_id == _SPACE


async def test_complete_upload_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await CompleteUpload(_FakeFiles()).execute(_ctx(), file_id="missing", checksum="a" * 64)


async def test_complete_upload_already_ready_raises_conflict() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    complete = CompleteUpload(files)
    await complete.execute(ctx, file_id=registered.id, checksum="a" * 64)
    with pytest.raises(ConflictError):
        await complete.execute(ctx, file_id=registered.id, checksum="b" * 64)


# --------------------------------------------------------------------------- #
# ListFiles                                                                    #
# --------------------------------------------------------------------------- #
async def test_list_files_returns_page() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    _seed_file(files, ctx)
    _seed_file(files, ctx)
    page = await ListFiles(files).execute(ctx, space_id=None)
    assert isinstance(page, Page)
    assert len(page.data) == 2


async def test_list_files_narrows_to_one_space_when_asked() -> None:
    """``ListFiles`` passes the space through to the repository rather than
    filtering after the fact — a page that came back full and was then pruned
    would silently return fewer rows than the limit promised."""
    files = _FakeFiles()
    ctx = _ctx()
    mine = _seed_file(files, ctx, space_id=_SPACE)
    _seed_file(files, ctx, space_id="sp-other")
    _seed_file(files, ctx, space_id=None)

    page = await ListFiles(files).execute(ctx, space_id=_SPACE)

    assert [f.id for f in page.data] == [mine.id]


# --------------------------------------------------------------------------- #
# SoftDeleteFile                                                               #
# --------------------------------------------------------------------------- #
async def test_soft_delete_emits_event_then_idempotent() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)
    soft_delete = SoftDeleteFile(files)
    deleted, events = await soft_delete.execute(ctx, file.id)
    assert deleted.deleted_at is not None
    assert len(events) == 1
    assert isinstance(events[0], FileDeleted)
    _, events_again = await soft_delete.execute(ctx, file.id)
    assert events_again == ()


async def test_soft_delete_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await SoftDeleteFile(_FakeFiles()).execute(_ctx(), "missing")


# --------------------------------------------------------------------------- #
# FilesQueryService                                                            #
# --------------------------------------------------------------------------- #
async def test_files_query_service_returns_none_until_ready() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    query = FilesQueryService(files)
    assert await query.get_readable(ctx, registered.id) is None

    await CompleteUpload(files).execute(ctx, file_id=registered.id, checksum="a" * 64)
    view = await query.get_readable(ctx, registered.id)
    assert view is not None
    assert view.file_id == registered.id
    assert view.status == "ready"
    assert view.content_type == "application/pdf"


async def test_the_view_carries_the_space_the_file_is_in() -> None:
    """Spaces plan step 7 (§3.5): ``conversations`` refuses a pin from another
    space, and this projection is the only way it can learn which space a file
    is in. A view that dropped the column would make that rule compare
    ``None`` against ``None`` and permit everything, with no error anywhere.
    """
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    await CompleteUpload(files).execute(ctx, file_id=registered.id, checksum="a" * 64)

    view = await FilesQueryService(files).get_readable(ctx, registered.id)

    assert view is not None
    assert view.space_id == _SPACE


# --------------------------------------------------------------------------- #
# CompleteUploadService -- the Outbox seam (5.1-أ)                            #
# --------------------------------------------------------------------------- #
class _FakeOutbox:
    """Minimal ``EventOutbox`` -- records every append call, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionContext, list[OutboxRecord]]] = []

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        self.calls.append((ctx, list(records)))


class _ExplodingOutbox:
    """An outbox whose append always fails (a dead database, a lost grant)."""

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        raise RuntimeError("outbox is down")


class _FakeUnitOfWork:
    """A no-op transaction boundary -- ``test_unit_of_work.py`` covers the
    real ``TenantSessionFactory.begin`` seam; this only stands in for it."""

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        yield


class _FixedEventsCompleteUpload:
    """A fake shaped like ``CompleteUpload`` -- returns PRE-SET events so the
    service's own None-filtering can be exercised independent of the real
    use-case's business rules (which never itself emits a ``FileDeleted``)."""

    def __init__(self, file: File, events: tuple[FileEvent, ...]) -> None:
        self._file = file
        self._events = events

    async def execute(
        self, ctx: ExecutionContext, *, file_id: str, checksum: str
    ) -> tuple[File, tuple[FileEvent, ...]]:
        return self._file, self._events


async def test_complete_upload_service_completes_and_appends_its_event() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    outbox = _FakeOutbox()
    service = CompleteUploadService(CompleteUpload(files), outbox, _FakeUnitOfWork())

    completed = await service.complete(ctx, file_id=registered.id, checksum="a" * 64)

    assert completed.status is FileStatus.READY
    (call_ctx, records) = outbox.calls[0]
    assert call_ctx is ctx
    assert [r.event_type for r in records] == ["files.file.uploaded.v1"]
    assert records[0].aggregate_id == completed.id


async def test_complete_upload_service_outbox_failure_propagates() -> None:
    """Swallowing would leave a ``ready`` file with no event -- invisible to
    ``knowledge``. The failure must roll the aggregate write back too."""
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    service = CompleteUploadService(CompleteUpload(files), _ExplodingOutbox(), _FakeUnitOfWork())

    with pytest.raises(RuntimeError, match="outbox is down"):
        await service.complete(ctx, file_id=registered.id, checksum="a" * 64)


async def test_complete_upload_service_drops_none_mapped_events_before_appending() -> None:
    ctx = _ctx()
    file_id = new_uuid7()
    file = File(
        id=file_id,
        workspace_id=ctx.workspace_id,
        space_id=_SPACE,
        name=FileName("report.pdf"),
        content_type=ContentType("application/pdf"),
        size_bytes=10,
        storage_key=StorageKey.for_file(ctx.workspace_id, file_id),
        checksum=None,
        status=FileStatus.READY,
        uploaded_by=ctx.user_id,
        created_at=utc_now(),
        updated_at=utc_now(),
        deleted_at=None,
        version=1,
    )
    uploaded = FileUploaded(
        file.id,
        ctx.workspace_id,
        _SPACE,
        "application/pdf",
        10,
        file.storage_key.value,
        "sig",
        utc_now(),
    )
    deleted = FileDeleted(file.id, ctx.workspace_id, utc_now())
    fixed = _FixedEventsCompleteUpload(file, (uploaded, deleted))
    outbox = _FakeOutbox()
    service = CompleteUploadService(fixed, outbox, _FakeUnitOfWork())

    await service.complete(ctx, file_id=file.id, checksum="a" * 64)

    (_, records) = outbox.calls[0]
    assert [r.event_type for r in records] == ["files.file.uploaded.v1"]


# --------------------------------------------------------------------------- #
# Optional checksum (6.1-هـ-2 — 03 §2: FileCompleteIn.checksum: str | None)    #
# --------------------------------------------------------------------------- #
async def test_complete_upload_without_checksum_is_ready_with_none_recorded() -> None:
    """A client that cannot hash its upload may still complete it: `ready`
    with `checksum=None` on the aggregate AND on the event — an honest record,
    never an invented digest."""
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )

    completed, events = await CompleteUpload(files).execute(
        ctx, file_id=registered.id, checksum=None
    )

    assert completed.status is FileStatus.READY
    assert completed.checksum is None
    assert isinstance(events[0], FileUploaded)
    assert events[0].checksum is None


async def test_complete_upload_present_checksum_must_still_be_valid() -> None:
    """Optional does not mean unvalidated: a PRESENT but malformed checksum is
    still a 422, not silently dropped."""
    files = _FakeFiles()
    ctx = _ctx()
    registered = await _register(files).execute(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )

    with pytest.raises(ValidationError):
        await CompleteUpload(files).execute(ctx, file_id=registered.id, checksum="not-a-sha")


# --------------------------------------------------------------------------- #
# FileTransferService (6.1-هـ-2 — the presigned faces)                         #
# --------------------------------------------------------------------------- #
class _FakeStorage:
    """Minimal ``StorageProvider`` for the presign faces — records calls and
    returns URLs that encode their inputs, so delegation is provable."""

    def __init__(self) -> None:
        self.presigned_puts: list[tuple[str, int, str]] = []
        self.presigned_gets: list[tuple[str, int]] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise AssertionError("not exercised")

    async def get(self, key: str) -> bytes:
        raise AssertionError("not exercised")

    async def delete(self, key: str) -> None:
        raise AssertionError("not exercised")

    async def presign_get(self, key: str, ttl_s: int) -> str:
        self.presigned_gets.append((key, ttl_s))
        return f"https://get/{key}"

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        self.presigned_puts.append((key, ttl_s, content_type))
        return f"https://put/{key}"


def _transfers(
    files: _FakeFiles, storage: _FakeStorage, *, minio: MinioSettings | None = None
) -> FileTransferService:
    """Wired from a real ``MinioSettings`` (3.79), so the TTLs asserted below
    are the ones the production boot produces rather than literals a test
    chose."""
    minio = minio or MinioSettings()
    return FileTransferService(
        _register(files),
        files,
        storage,
        put_ttl_s=minio.presign_put_ttl_s,
        get_ttl_s=minio.presign_get_ttl_s,
    )


async def test_register_returns_a_presigned_put_for_the_minted_storage_key() -> None:
    files = _FakeFiles()
    storage = _FakeStorage()
    ctx = _ctx()

    registered = await _transfers(files, storage).register(
        ctx, space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )

    key = registered.file.storage_key.value
    assert storage.presigned_puts == [(key, 900, "application/pdf")]
    assert registered.upload_url == f"https://put/{key}"
    assert registered.expires_in == 900
    # The limit enforcement still lives in RegisterUpload underneath.
    assert registered.file.status is FileStatus.UPLOADED


async def test_configured_ttls_reach_the_signer_and_the_expires_in_it_reports() -> None:
    """3.79: the lifetimes are configuration now, so a non-default value must
    reach BOTH the presign call and the ``expires_in`` the client is told —
    reporting 900 while signing 1800 (or the reverse) is the one way this
    field can lie, and it is unobservable until a URL dies early."""
    files = _FakeFiles()
    storage = _FakeStorage()
    minio = MinioSettings(presign_put_ttl_s=1800, presign_get_ttl_s=60)
    transfers = _transfers(files, storage, minio=minio)

    registered = await transfers.register(
        _ctx(), space_id=_SPACE, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    registered.file.status = FileStatus.READY
    read = await transfers.describe(registered.file)

    key = registered.file.storage_key.value
    assert storage.presigned_puts == [(key, 1800, "application/pdf")]
    assert registered.expires_in == 1800
    assert storage.presigned_gets == [(key, 60)]
    assert read.download_url == f"https://get/{key}"


async def test_register_limit_violations_pass_through_unwrapped() -> None:
    files = _FakeFiles()
    storage = _FakeStorage()

    with pytest.raises(TooLargeError):
        await _transfers(files, storage).register(
            _ctx(),
            space_id=_SPACE,
            name="big.pdf",
            content_type="application/pdf",
            size_bytes=10**9,
        )
    assert storage.presigned_puts == []  # nothing presigned for a refused slot


async def test_get_of_missing_and_of_deleted_both_read_as_not_found() -> None:
    """The §3.55 read precedent: a read's only truthful answer for a deleted
    resource is "gone" — the 409-shaped answer belongs to writes."""
    files = _FakeFiles()
    ctx = _ctx()
    deleted = _seed_file(files, ctx, deleted_at=utc_now())

    with pytest.raises(NotFoundError):
        await _transfers(files, _FakeStorage()).get(ctx, new_uuid7())
    with pytest.raises(NotFoundError):
        await _transfers(files, _FakeStorage()).get(ctx, deleted.id)


async def test_get_before_ready_carries_no_download_url() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)  # status: uploaded
    storage = _FakeStorage()

    read = await _transfers(files, storage).get(ctx, file.id)

    assert read.file.id == file.id
    assert read.download_url is None
    assert storage.presigned_gets == []  # never presign a half-uploaded file


async def test_get_of_a_ready_file_carries_a_presigned_get() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)
    file.status = FileStatus.READY
    storage = _FakeStorage()

    read = await _transfers(files, storage).get(ctx, file.id)

    key = file.storage_key.value
    assert storage.presigned_gets == [(key, 300)]
    assert read.download_url == f"https://get/{key}"


async def test_list_presigns_only_the_ready_rows() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    pending = _seed_file(files, ctx)
    ready = _seed_file(files, ctx)
    ready.status = FileStatus.READY
    storage = _FakeStorage()

    page = await _transfers(files, storage).list(ctx, space_id=None, limit=10)

    by_id = {row.file.id: row for row in page.data}
    assert set(by_id) == {pending.id, ready.id}
    assert by_id[pending.id].download_url is None
    assert by_id[ready.id].download_url == f"https://get/{ready.storage_key.value}"
    assert storage.presigned_gets == [(ready.storage_key.value, 300)]


# --------------------------------------------------------------------------- #
# SoftDeleteFileService (6.1-هـ-2 — the atomic delete face)                    #
# --------------------------------------------------------------------------- #
async def test_soft_delete_service_deletes_and_appends_nothing_today() -> None:
    """`FileDeleted` is internal-only (04 §5) so the mapper yields `None` and
    the append is provably EMPTY — but it must still happen inside the unit of
    work, so the day the event is promoted nothing at the API layer moves."""
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)
    outbox = _FakeOutbox()
    service = SoftDeleteFileService(SoftDeleteFile(files), outbox, _FakeUnitOfWork())

    result = await service.delete(ctx, file.id)

    assert result.deleted_at is not None
    assert outbox.calls == [(ctx, [])]  # append ran, with the None filtered out


async def test_soft_delete_service_missing_file_is_not_found() -> None:
    service = SoftDeleteFileService(SoftDeleteFile(_FakeFiles()), _FakeOutbox(), _FakeUnitOfWork())
    with pytest.raises(NotFoundError):
        await service.delete(_ctx(), new_uuid7())


# --------------------------------------------------------------------------- #
# RenameFile (BE-RAG-006 — the one mutable field a file has)                   #
# --------------------------------------------------------------------------- #
async def test_rename_persists_the_new_name() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)  # `seed.txt`

    renamed = await RenameFile(files).execute(ctx, file.id, name="notes.txt")

    assert renamed.name.value == "notes.txt"
    assert files.rows[file.id].name.value == "notes.txt"
    assert files.saved == [file.id]


async def test_rename_inherits_the_extension_the_client_left_off() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)

    renamed = await RenameFile(files).execute(ctx, file.id, name="notes")

    assert renamed.name.value == "notes.txt"


async def test_renaming_to_the_current_name_never_writes() -> None:
    """A no-op must not bump `version`/`updated_at` — but it is still a
    success, so the file comes back rather than an error."""
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)

    renamed = await RenameFile(files).execute(ctx, file.id, name="seed.txt")

    assert renamed.name.value == "seed.txt"
    assert files.saved == []


async def test_rename_to_a_different_extension_is_a_422_not_a_409() -> None:
    """The extension policy rejects INPUT; it says nothing about the file's
    state, so it must not arrive as a conflict."""
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)

    with pytest.raises(ValidationError):
        await RenameFile(files).execute(ctx, file.id, name="notes.exe")
    assert files.rows[file.id].name.value == "seed.txt"
    assert files.saved == []


async def test_rename_with_an_empty_name_is_rejected() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx)

    with pytest.raises(ValidationError):
        await RenameFile(files).execute(ctx, file.id, name="   ")


async def test_rename_of_a_deleted_file_is_a_conflict() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    file = _seed_file(files, ctx, deleted_at=utc_now())

    with pytest.raises(ConflictError):
        await RenameFile(files).execute(ctx, file.id, name="late.txt")


async def test_rename_of_an_unknown_file_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        await RenameFile(_FakeFiles()).execute(_ctx(), new_uuid7(), name="x.txt")


async def test_rename_cannot_reach_another_tenants_file() -> None:
    files = _FakeFiles()
    owner = _ctx("w1")
    file = _seed_file(files, owner)

    with pytest.raises(NotFoundError):
        await RenameFile(files).execute(_ctx("w2"), file.id, name="stolen.txt")
    assert files.rows[file.id].name.value == "seed.txt"
