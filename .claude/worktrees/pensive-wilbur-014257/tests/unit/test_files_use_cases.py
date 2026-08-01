"""Unit tests for files use-cases over an in-memory fake repository.
Pure: the port is faked, so no infrastructure is exercised."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
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
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.files.domain.entities import File
from app.modules.files.domain.events import FileDeleted, FileEvent, FileUploaded
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey


class _FakeFiles:
    """In-memory ``FileRepository``; ``count`` scopes by ``ctx.workspace_id``
    and only counts active (non-deleted) rows."""

    def __init__(self) -> None:
        self.rows: dict[str, File] = {}

    async def get(self, ctx: ExecutionContext, file_id: str) -> File | None:
        row = self.rows.get(file_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, file: File) -> None:
        self.rows[file.id] = file

    async def save(self, ctx: ExecutionContext, file: File) -> None:
        self.rows[file.id] = file

    async def list(
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[File]:
        items = [
            row
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.deleted_at is None
        ]
        return Page(data=items[:limit], next_cursor=None, limit=limit)

    async def count(self, ctx: ExecutionContext) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.deleted_at is None
        )


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


def _seed_file(
    files: _FakeFiles, ctx: ExecutionContext, *, deleted_at: datetime | None = None
) -> File:
    """Seed a ready file directly into the fake repo (bypassing the use-case)."""
    now = utc_now()
    file_id = new_uuid7()
    file = File(
        id=file_id,
        workspace_id=ctx.workspace_id,
        name=FileName("seed.txt"),
        content_type=ContentType("text/plain"),
        size_bytes=10,
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
    file = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=1024
    )
    assert file.status is FileStatus.UPLOADED
    assert file.checksum is None
    assert file.storage_key.value.startswith("w1/")
    assert file.id in files.rows


async def test_register_upload_rejects_oversize() -> None:
    files = _FakeFiles()
    with pytest.raises(TooLargeError):
        await RegisterUpload(files, Limits()).execute(
            _ctx(),
            name="big.txt",
            content_type="text/plain",
            size_bytes=Limits().max_upload_bytes + 1,
        )


async def test_register_upload_rejects_unsupported_mime() -> None:
    files = _FakeFiles()
    with pytest.raises(UnsupportedTypeError):
        await RegisterUpload(files, Limits()).execute(
            _ctx(), name="script.exe", content_type="application/x-msdownload", size_bytes=10
        )


async def test_register_upload_rejects_negative_size() -> None:
    files = _FakeFiles()
    with pytest.raises(ValidationError):
        await RegisterUpload(files, Limits()).execute(
            _ctx(), name="report.pdf", content_type="application/pdf", size_bytes=-1
        )


async def test_register_upload_rejects_over_workspace_cap() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    _seed_file(files, ctx)
    with pytest.raises(ConflictError):
        await RegisterUpload(files, Limits(max_files_per_workspace=1)).execute(
            ctx, name="two.txt", content_type="text/plain", size_bytes=10
        )


# --------------------------------------------------------------------------- #
# CompleteUpload                                                               #
# --------------------------------------------------------------------------- #
async def test_complete_upload_transitions_to_ready_and_emits_event() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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


async def test_complete_upload_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await CompleteUpload(_FakeFiles()).execute(_ctx(), file_id="missing", checksum="a" * 64)


async def test_complete_upload_already_ready_raises_conflict() -> None:
    files = _FakeFiles()
    ctx = _ctx()
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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
    page = await ListFiles(files).execute(ctx)
    assert isinstance(page, Page)
    assert len(page.data) == 2


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
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
    )
    query = FilesQueryService(files)
    assert await query.get_readable(ctx, registered.id) is None

    await CompleteUpload(files).execute(ctx, file_id=registered.id, checksum="a" * 64)
    view = await query.get_readable(ctx, registered.id)
    assert view is not None
    assert view.file_id == registered.id
    assert view.status == "ready"
    assert view.content_type == "application/pdf"


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
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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
        file.id, ctx.workspace_id, "application/pdf", 10, file.storage_key.value, "sig", utc_now()
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
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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
    registered = await RegisterUpload(files, Limits()).execute(
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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
        RegisterUpload(files, Limits()),
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
        ctx, name="report.pdf", content_type="application/pdf", size_bytes=2048
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
        _ctx(), name="report.pdf", content_type="application/pdf", size_bytes=2048
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
            _ctx(), name="big.pdf", content_type="application/pdf", size_bytes=10**9
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

    page = await _transfers(files, storage).list(ctx, limit=10)

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
