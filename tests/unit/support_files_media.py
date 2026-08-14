"""In-memory files/media wiring shared by the API router tests (6.1-هـ-3).

Not a ``test_*`` module, so pytest never collects it — the
``support_conversations`` precedent: every router test file has to construct
``ApiServices``, and copying a files repository fake five times is how five
copies drift apart.

The fakes are faithful on what the routes depend on: the files repository
scopes by workspace and its ``list`` excludes soft-deleted rows; the storage
fake returns URLs that ENCODE their inputs so a response's ``upload_url``/
``download_url`` proves which key and TTL were signed; the outbox records
appends so a test can assert the media POST left its ``MediaRequested`` row.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import Limits, MinioSettings
from app.modules.files.application.use_cases import (
    CompleteUpload,
    CompleteUploadService,
    FileTransferService,
    FileUseCases,
    RegisterUpload,
    RenameFile,
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.files.domain.entities import File
from app.modules.media.application.use_cases import (
    GetJobStatus,
    MediaRequestService,
    MediaUseCases,
    RequestMedia,
)
from app.modules.media.domain.entities import MediaJob


@dataclass
class InMemoryFileRepository:
    """A structural ``FileRepository`` over one dict."""

    rows: dict[str, File] = field(default_factory=dict)
    # Every id `save` was called with, in order. A dict-backed fake cannot show
    # the difference between "wrote the same row again" and "did not write",
    # and BE-RAG-006's no-op rename turns on exactly that difference (a `save`
    # bumps `version`/`updated_at` in the real adapter).
    saved: list[str] = field(default_factory=list)

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
        self, ctx: ExecutionContext, *, limit: int, cursor: str | None = None
    ) -> Page[File]:
        # Keysets on `id` like the SQL adapter (6.3-أ), NEWEST FIRST like it
        # too (6.3-ب), through the REAL codec: a fake that always answered
        # `next_cursor=None` left the router's `next_cursor=page.next_cursor`
        # line unpinned, and left every malformed-cursor path unreachable
        # from a unit test.
        items = sorted(
            (
                row
                for row in self.rows.values()
                if row.workspace_id == ctx.workspace_id and row.deleted_at is None
            ),
            key=lambda row: row.id,
            reverse=True,
        )
        if cursor is not None:
            after = decode_id_cursor(cursor)
            items = [row for row in items if row.id < after]
        page, has_more = items[:limit], len(items) > limit
        next_cursor = encode_id_cursor(page[-1].id) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)

    async def count(self, ctx: ExecutionContext) -> int:
        return sum(
            1
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.deleted_at is None
        )


@dataclass
class InMemoryMediaJobRepository:
    """A structural ``MediaJobRepository`` over one dict."""

    rows: dict[str, MediaJob] = field(default_factory=dict)

    async def add(self, ctx: ExecutionContext, job: MediaJob) -> None:
        self.rows[job.id] = job

    async def get(self, ctx: ExecutionContext, job_id: str) -> MediaJob | None:
        row = self.rows.get(job_id)
        if row is None or row.workspace_id != ctx.workspace_id:
            return None
        return row

    async def save(self, ctx: ExecutionContext, job: MediaJob) -> None:
        self.rows[job.id] = job


class RecordingStorage:
    """A ``StorageProvider`` whose URLs encode their inputs."""

    def __init__(self) -> None:
        self.presigned_puts: list[tuple[str, int, str]] = []
        self.presigned_gets: list[tuple[str, int]] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise AssertionError("not exercised by the routers")

    async def get(self, key: str) -> bytes:
        raise AssertionError("not exercised by the routers")

    async def delete(self, key: str) -> None:
        raise AssertionError("not exercised by the routers")

    async def presign_get(self, key: str, ttl_s: int) -> str:
        self.presigned_gets.append((key, ttl_s))
        return f"https://get/{key}"

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        self.presigned_puts.append((key, ttl_s, content_type))
        return f"https://put/{key}"


class RecordingOutbox:
    """Minimal ``EventOutbox`` — records every append call, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionContext, list[OutboxRecord]]] = []

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        self.calls.append((ctx, list(records)))


class NoopUnitOfWork:
    """A no-op transaction boundary — the seam tests own the real one."""

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        yield


@dataclass(frozen=True, slots=True)
class FilesMediaStack:
    """Both bundles plus the fakes a test asserts against."""

    files: FileUseCases
    media: MediaUseCases
    file_repository: InMemoryFileRepository
    media_repository: InMemoryMediaJobRepository
    storage: RecordingStorage
    outbox: RecordingOutbox


def build_files_media(
    limits: Limits | None = None, *, minio: MinioSettings | None = None
) -> FilesMediaStack:
    """One repository per module behind every face, one storage handle-alike,
    one outbox — the same single-instance wiring the Composition Root builds.

    The presign lifetimes come from a real ``MinioSettings`` (3.79) for the
    same reason ``limits`` does: a hand-picked literal here would let a caller
    assert a TTL the production wiring never produces."""
    limits = limits or Limits()
    minio = minio or MinioSettings()
    files_repo = InMemoryFileRepository()
    media_repo = InMemoryMediaJobRepository()
    storage = RecordingStorage()
    outbox = RecordingOutbox()
    uow = NoopUnitOfWork()
    return FilesMediaStack(
        files=FileUseCases(
            transfers=FileTransferService(
                RegisterUpload(files_repo, limits),
                files_repo,
                storage,
                put_ttl_s=minio.presign_put_ttl_s,
                get_ttl_s=minio.presign_get_ttl_s,
            ),
            complete=CompleteUploadService(CompleteUpload(files_repo), outbox, uow),
            rename=RenameFile(files_repo),
            delete=SoftDeleteFileService(SoftDeleteFile(files_repo), outbox, uow),
        ),
        media=MediaUseCases(
            requests=MediaRequestService(RequestMedia(media_repo, limits), outbox, uow),
            get_status=GetJobStatus(media_repo),
        ),
        file_repository=files_repo,
        media_repository=media_repo,
        storage=storage,
        outbox=outbox,
    )
