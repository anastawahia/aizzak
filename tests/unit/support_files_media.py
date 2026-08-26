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

import unicodedata
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.di.file_deletion import DeleteFileService
from app.framework.di.file_replacement import ReplaceNamesakesService
from app.framework.di.space_quota import SpaceQuotaService
from app.framework.pagination import Page, decode_id_cursor, encode_id_cursor
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.settings.settings import Limits, MinioSettings
from app.modules.files.application.use_cases import (
    CompleteUpload,
    CompleteUploadService,
    FileTransferService,
    FileUseCases,
    FindNamesakes,
    RegisteredUpload,
    RegisterUpload,
    RenameFile,
    SoftDeleteFile,
    SoftDeleteFileService,
    SummariseSpaces,
)
from app.modules.files.domain.entities import File
from app.modules.files.ports.repository import SpaceFileTotals
from app.modules.media.application.use_cases import (
    GetJobStatus,
    MediaRequestService,
    MediaUseCases,
    RequestMedia,
)
from app.modules.media.domain.entities import MediaJob


def _name_key(name: str) -> str:
    """``lower(normalize(name, NFC))`` in Python — the one expression س-29
    rule 1's "same name" is defined by (``files/0003_file_name_lookup.py``)."""
    return unicodedata.normalize("NFC", name).casefold()


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
        self,
        ctx: ExecutionContext,
        *,
        space_id: str | None = None,
        limit: int,
        cursor: str | None = None,
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
                if row.workspace_id == ctx.workspace_id
                and row.deleted_at is None
                and (space_id is None or row.space_id == space_id)
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

    async def bytes_in_space(self, ctx: ExecutionContext, space_id: str) -> int:
        return sum(
            row.size_bytes
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id
            and row.space_id == space_id
            and row.deleted_at is None
        )

    async def totals_by_space(
        self, ctx: ExecutionContext, space_ids: Sequence[str]
    ) -> dict[str, SpaceFileTotals]:
        # ABSENT, not zero, for a space that owns nothing -- the real adapter's
        # `GROUP BY` returns only groups that exist, and a fake that filled in
        # zeros would let the router's `.get(id, _EMPTY)` default go untested.
        wanted = set(space_ids)
        totals: dict[str, SpaceFileTotals] = {}
        for row in self.rows.values():
            if (
                row.workspace_id != ctx.workspace_id
                or row.space_id not in wanted
                or row.deleted_at is not None
            ):
                continue
            # `space_id` is narrowed by the membership test above, and mypy
            # cannot see that through `in`.
            assert row.space_id is not None
            seen = totals.get(row.space_id)
            totals[row.space_id] = SpaceFileTotals(
                bytes_used=(seen.bytes_used if seen else 0) + row.size_bytes,
                file_count=(seen.file_count if seen else 0) + 1,
            )
        return totals

    async def ready_names(self, ctx: ExecutionContext, file_ids: Sequence[str]) -> dict[str, str]:
        # ABSENT, not `""`, for anything that is not READY -- the same
        # `File.is_ready` rule `get_readable` applies to one file, which is
        # what lets `knowledge` keep skipping the documents it already
        # skipped (branch review §2). Another tenant's ids are absent too.
        wanted = set(file_ids)
        return {
            row.id: row.name.value
            for row in self.rows.values()
            if row.id in wanted and row.workspace_id == ctx.workspace_id and row.is_ready
        }

    def _in_space(self, ctx: ExecutionContext, space_id: str) -> list[File]:
        # NO `deleted_at` filter, unlike `bytes_in_space` right above: a
        # soft-deleted file has given its bytes back but still owns its object
        # (spaces plan step 11). The fake keeps that difference because it is
        # the whole reason the port has two methods.
        return [
            row
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id and row.space_id == space_id
        ]

    async def live_namesakes(self, ctx: ExecutionContext, file: File) -> list[str]:
        # Faithful to `SqlFileRepository.live_namesakes` clause for clause,
        # because س-29 rule 1 is a RULE and not a query: a fake that matched
        # on raw equality would let every router test pass while the product
        # missed "Report.pdf" vs "report.pdf" and every Arabic name typed on a
        # second keyboard. `normalize` then `casefold` is the same order as
        # SQL's `lower(normalize(name, NFC))`.
        return sorted(
            row.id
            for row in self.rows.values()
            if row.workspace_id == ctx.workspace_id
            and row.space_id == file.space_id
            and _name_key(row.name.value) == _name_key(file.name.value)
            and row.deleted_at is None
            and (row.created_at, row.id) < (file.created_at, file.id)
        )

    async def storage_keys_in_space(self, ctx: ExecutionContext, space_id: str) -> list[str]:
        return [row.storage_key.value for row in self._in_space(ctx, space_id)]

    async def purge_space(self, ctx: ExecutionContext, space_id: str) -> int:
        doomed = self._in_space(ctx, space_id)
        for row in doomed:
            del self.rows[row.id]
        return len(doomed)


@dataclass(frozen=True, slots=True)
class SpaceRow:
    """``SpaceView``'s shape — what ``ActiveSpaces.get_active`` hands back."""

    space_id: str


@dataclass
class InMemorySpaces:
    """A structural ``files.ports.spaces.ActiveSpaces``.

    Deliberately a SET of live ids rather than a store of spaces: the seam
    asks one yes/no question, and a fake that modelled names, deletion and
    versions would be re-implementing the module ``files`` is not allowed to
    know about.
    """

    active: set[str] = field(default_factory=set)

    async def get_active(self, ctx: ExecutionContext, space_id: str) -> SpaceRow | None:
        return SpaceRow(space_id) if space_id in self.active else None

    async def lock(self, ctx: ExecutionContext, space_id: str) -> bool:
        # `SpaceQuotaService`'s `SpaceLock`, on the same set. A dict-backed
        # fake has no rows to lock and no transaction to hold one in, so what
        # it can be faithful about is the ANSWER: `True` exactly when there
        # was a live space to lock. The serialisation itself is proved on a
        # real database (`tests/integration/test_space_quota_live.py`), which
        # is the only place it can be.
        return space_id in self.active


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
        # Every key removed, in order (spaces plan step 11). Recorded rather
        # than refused since the cascade became a caller: "which objects went,
        # and before or after their rows" is the one thing `PurgeSpaceFiles`
        # can get wrong without any count changing.
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        raise AssertionError("not exercised by the routers")

    async def get(self, key: str) -> bytes:
        raise AssertionError("not exercised by the routers")

    async def delete(self, key: str) -> None:
        self.deleted.append(key)

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


class RecordingFileKnowledgePurge:
    """The file cascade's knowledge half, recorded rather than run.

    This suite has no in-memory ``knowledge`` stack and needs none: what the
    files ROUTER owes is that ``DELETE /files/{id}`` reaches the CASCADE at all
    — the defect being repaired is that it reached only the soft delete — and
    the purge's own behaviour is pinned where it lives
    (``test_file_deletion.py``).
    """

    def __init__(self, documents: int = 0) -> None:
        self._documents = documents
        self.calls: list[str] = []

    async def execute(self, ctx: ExecutionContext, file_id: str) -> int:
        self.calls.append(file_id)
        return self._documents


@dataclass(frozen=True, slots=True)
class FilesMediaStack:
    """Both bundles plus the fakes a test asserts against."""

    files: FileUseCases
    media: MediaUseCases
    file_repository: InMemoryFileRepository
    spaces: SpaceGate
    media_repository: InMemoryMediaJobRepository
    storage: RecordingStorage
    outbox: RecordingOutbox
    # `spaces-backend-plan.md` step 12 — what `POST /api/v1/files` now
    # registers through, so a router test exercises the same quota gate
    # production does instead of the bare registrar it used to reach.
    space_quota: SpaceQuotaService[RegisteredUpload]
    # The file cascade (`framework/di/file_deletion.py`) — what `DELETE
    # /api/v1/files/{id}` goes through since the delete started reaching the
    # index. On the stack rather than built per test file for `space_quota`'s
    # reason: a router test that wired the bare soft delete instead would pass
    # while asserting the exact behaviour the cascade exists to prevent.
    file_deletion: DeleteFileService
    # س-29 rule 1 — what `POST /files/{id}/complete` and `PATCH /files/{id}`
    # go through now that a name already held in the space is a replacement.
    # On the stack for `file_deletion`'s reason: a router test that wired the
    # bundle's bare `complete` would pass while asserting exactly the state
    # the cascade exists to prevent — two files, one name, both indexed.
    file_replacement: ReplaceNamesakesService[File]
    knowledge_purge: RecordingFileKnowledgePurge


class SpaceGate(Protocol):
    """The two questions this stack asks about a space: is it live
    (``ActiveSpaces``, for ``RegisterUpload``) and may I hold it still
    (``SpaceLock``, for the quota). ``InMemorySpaces`` answers both, and so
    does ``support_spaces.InMemorySpaceRepository`` — which is what lets a
    router test create a space through ``POST /spaces`` and then upload into
    it."""

    async def get_active(self, ctx: ExecutionContext, space_id: str) -> object | None: ...

    async def lock(self, ctx: ExecutionContext, space_id: str) -> bool: ...


def build_files_media(
    limits: Limits | None = None,
    *,
    minio: MinioSettings | None = None,
    spaces: SpaceGate | None = None,
) -> FilesMediaStack:
    """One repository per module behind every face, one storage handle-alike,
    one outbox — the same single-instance wiring the Composition Root builds.

    The presign lifetimes come from a real ``MinioSettings`` (3.79) for the
    same reason ``limits`` does: a hand-picked literal here would let a caller
    assert a TTL the production wiring never produces."""
    limits = limits or Limits()
    minio = minio or MinioSettings()
    files_repo = InMemoryFileRepository()
    spaces = spaces if spaces is not None else InMemorySpaces()
    media_repo = InMemoryMediaJobRepository()
    storage = RecordingStorage()
    outbox = RecordingOutbox()
    uow = NoopUnitOfWork()
    transfers = FileTransferService(
        RegisterUpload(files_repo, limits, spaces),
        files_repo,
        storage,
        put_ttl_s=minio.presign_put_ttl_s,
        get_ttl_s=minio.presign_get_ttl_s,
    )
    # ONE `SoftDeleteFileService` behind the bundle AND the cascade — the
    # Composition Root's own `mark` rule (`_build_space_services`): two
    # instances would let a change to its idempotence land in one and miss the
    # other.
    file_delete = SoftDeleteFileService(SoftDeleteFile(files_repo), outbox, uow)
    knowledge_purge = RecordingFileKnowledgePurge()
    # The same single-instance rule `file_delete` above follows, one step out:
    # the cascade wraps the bundle's OWN `complete`/`rename`/`namesakes`.
    file_complete = CompleteUploadService(CompleteUpload(files_repo), outbox, uow)
    file_rename = RenameFile(files_repo)
    namesakes = FindNamesakes(files_repo)
    file_deletion = DeleteFileService(file_delete, knowledge=knowledge_purge)
    return FilesMediaStack(
        files=FileUseCases(
            transfers=transfers,
            complete=file_complete,
            rename=file_rename,
            delete=file_delete,
            namesakes=namesakes,
            space_totals=SummariseSpaces(files_repo),
        ),
        media=MediaUseCases(
            requests=MediaRequestService(RequestMedia(media_repo, limits), outbox, uow),
            get_status=GetJobStatus(media_repo),
        ),
        file_repository=files_repo,
        spaces=spaces,
        media_repository=media_repo,
        storage=storage,
        outbox=outbox,
        # The REAL coordination service over the same three collaborators the
        # bundle above holds, so a `POST /files` in a router test takes the
        # lock, sums the space and only then registers — the production path.
        # `NoopUnitOfWork` is what it cannot be faithful about, and that is
        # the one thing the live test owns.
        space_quota=SpaceQuotaService(transfers, spaces, files_repo, uow, limits),
        file_deletion=file_deletion,
        file_replacement=ReplaceNamesakesService(
            file_complete, file_rename, namesakes=namesakes, erase=file_deletion
        ),
        knowledge_purge=knowledge_purge,
    )
