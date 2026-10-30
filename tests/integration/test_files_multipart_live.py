"""Live proof of the resumable multipart upload, end to end (PLT-01, INC0-AC-2).

Real ``files.files`` (RLS, ``app_rw``), the real ``SqlFileRepository``, the real
``SpaceQuotaService`` and ``MultipartUploadService`` over a real
``MinioStorage``, and real signed part URLs ``PUT`` by an unauthenticated
client. The unit suites prove the SEQUENCE against fakes; what only a live
pair can prove is that the pieces agree with each other: the upload id the
adapter returns is the one the column stores and the repository hydrates, the
sizes ``ListParts`` reports are the ones ``check_parts`` expects, the object
``CompleteMultipartUpload`` produces has exactly the declared size, and the
transaction that marks the file ``ready`` writes exactly one outbox row.

Needs both ``live_db`` and ``live_minio`` (Postgres and MinIO).
"""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.request

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.file_deletion import DeleteFileService
from app.framework.di.file_replacement import ReplaceNamesakesService
from app.framework.di.space_quota import SpaceQuotaService
from app.framework.errors import ConflictError, NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.quota_lock import AdvisoryQuotaLock
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.infrastructure.storage.minio_storage import MinioStorage
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    CompleteUploadService,
    FindNamesakes,
    MultipartRegistrar,
    MultipartUploadService,
    RegisterUpload,
    RenameFile,
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.files.domain.value_objects import FileStatus
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db, pytest.mark.live_minio]

_MIB = 1024 * 1024
# The smallest part S3 accepts, so a three-part file costs 10 MiB + a tail.
_LIMITS = Limits(multipart_part_bytes=5 * _MIB)
_SIZE = 2 * 5 * _MIB + 7  # parts of 5 MiB, 5 MiB and 7 bytes


class _NoIndex:
    """The knowledge half of the delete cascade: this suite has no index."""

    async def execute(self, ctx: ExecutionContext, file_id: str) -> int:
        return 0


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _space(workspace_id: str) -> Space:
    at = utc_now()
    return Space(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName("Plans"),
        created_by=new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
    )


def _put(url: str, data: bytes) -> int:
    request = urllib.request.Request(url, data=data, method="PUT")
    try:
        with urllib.request.urlopen(request) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


class _Stack:
    def __init__(
        self,
        tenant_session: TenantSessionFactory,
        spaces: SqlSpaceRepository,
        storage: MinioStorage,
    ) -> None:
        self.files = SqlFileRepository(tenant_session)
        self.storage = storage
        register = RegisterUpload(
            self.files, _LIMITS, SpacesQueryService(spaces), multipart=storage
        )
        self.multipart = MultipartUploadService(self.files, storage, put_ttl_s=300)
        self.quota = SpaceQuotaService(
            MultipartRegistrar(register),
            spaces,
            self.files,
            tenant_session,
            _LIMITS,
            AdvisoryQuotaLock(tenant_session),
        )
        self.complete = CompleteUploadService(
            CompleteUpload(self.files),
            SqlEventOutbox(tenant_session),
            tenant_session,
            assembler=self.multipart,
        )
        mark = SoftDeleteFileService(
            SoftDeleteFile(self.files), SqlEventOutbox(tenant_session), tenant_session
        )
        self.replacement = ReplaceNamesakesService(
            self.complete,
            RenameFile(self.files),
            namesakes=FindNamesakes(self.files),
            erase=DeleteFileService(mark, knowledge=_NoIndex()),
        )

    async def upload_part(
        self, ctx: ExecutionContext, file_id: str, number: int, data: bytes
    ) -> None:
        signed = await self.multipart.sign_parts(ctx, file_id, [number])
        assert await asyncio.to_thread(_put, signed.parts[0].upload_url, data) == 200


@pytest.fixture
def stack(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    minio_storage: MinioStorage,
) -> _Stack:
    return _Stack(tenant_session, repo_spaces, minio_storage)


async def _outbox_rows(owner_dsn: str, file_id: str) -> int:
    engine = create_async_engine(owner_dsn)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM platform.outbox WHERE aggregate_id = :id"),
                {"id": file_id},
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


async def _register(
    stack: _Stack, ctx: ExecutionContext, space_id: str, name: str = "plan.pdf", size: int = _SIZE
) -> str:
    registered = await stack.quota.register(
        ctx, space_id=space_id, name=name, content_type="application/pdf", size_bytes=size
    )
    return registered.file.id


async def test_register_resume_and_complete_a_multipart_file(
    stack: _Stack,
    repo_spaces: SqlSpaceRepository,
    live_db: LiveDbDsns,
    tenant_session: TenantSessionFactory,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    older = await _register(stack, ctx, space.id, size=10)
    older_row = await stack.files.get(ctx, older)
    assert older_row is not None and older_row.multipart is not None
    # An older namesake that is already complete (assembled from one tiny part).
    await stack.upload_part(ctx, older, 1, b"x" * 10)
    await stack.complete.complete(ctx, file_id=older, checksum=None)

    registered = await stack.quota.register(
        ctx, space_id=space.id, name="plan.pdf", content_type="application/pdf", size_bytes=_SIZE
    )
    file_id = registered.file.id
    assert (registered.part_bytes, registered.part_count) == (5 * _MIB, 3)
    stored = await stack.files.get(ctx, file_id)
    # The upload id and part size survive the round trip through the columns.
    assert stored is not None and stored.multipart == registered.file.multipart

    # One part arrives, then the client "crashes": progress reports exactly it.
    await stack.upload_part(ctx, file_id, 1, b"a" * (5 * _MIB))
    progress = await stack.multipart.progress(ctx, file_id)
    assert [(p.part_number, p.size_bytes) for p in progress.uploaded] == [(1, 5 * _MIB)]

    # Completing now is refused and changes nothing.
    with pytest.raises(ConflictError) as incomplete:
        await stack.complete.complete(ctx, file_id=file_id, checksum=None)
    assert incomplete.value.code == "files.upload_incomplete"
    still = await stack.files.get(ctx, file_id)
    assert still is not None and still.status is FileStatus.UPLOADED
    assert await _outbox_rows(live_db.owner, file_id) == 0

    # Resume: upload what is missing, then complete through the replacement.
    await stack.upload_part(ctx, file_id, 2, b"b" * (5 * _MIB))
    await stack.upload_part(ctx, file_id, 3, b"c" * 7)
    done = await stack.replacement.complete(ctx, file_id=file_id, checksum=None)

    assert done.status is FileStatus.READY
    assert await stack.storage.object_size(done.storage_key.value) == _SIZE
    assert await _outbox_rows(live_db.owner, file_id) == 1
    gone = await stack.files.get(ctx, older)
    assert gone is not None and gone.deleted_at is not None  # the older namesake

    # A second completion is a state conflict, not a second event.
    with pytest.raises(ConflictError):
        await stack.complete.complete(ctx, file_id=file_id, checksum=None)
    assert await _outbox_rows(live_db.owner, file_id) == 1


async def test_another_workspace_can_neither_read_sign_nor_complete_the_upload(
    stack: _Stack, repo_spaces: SqlSpaceRepository
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    file_id = await _register(stack, ctx, space.id)
    stranger = _ctx(new_uuid7())

    with pytest.raises(NotFoundError):
        await stack.multipart.progress(stranger, file_id)
    with pytest.raises(NotFoundError):
        await stack.multipart.sign_parts(stranger, file_id, [1])
    # `assemble` answers silently for a file it cannot see (CompleteUpload then
    # raises the 404), so the proof is the completion as a whole.
    with pytest.raises(NotFoundError):
        await stack.complete.complete(stranger, file_id=file_id, checksum=None)


async def test_a_swept_upload_is_reported_expired(
    stack: _Stack, repo_spaces: SqlSpaceRepository
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws)
    await repo_spaces.add(ctx, space)
    file_id = await _register(stack, ctx, space.id)
    stored = await stack.files.get(ctx, file_id)
    assert stored is not None and stored.multipart is not None
    # Stand in for MinIO's stale-upload sweep: abort the upload out of band.
    await asyncio.to_thread(
        stack.storage._client._abort_multipart_upload,
        stack.storage._bucket,
        stored.storage_key.value,
        stored.multipart.upload_id,
    )

    with pytest.raises(ConflictError) as progress:
        await stack.multipart.progress(ctx, file_id)
    with pytest.raises(ConflictError) as complete:
        await stack.complete.complete(ctx, file_id=file_id, checksum=None)

    assert progress.value.code == "files.upload_expired"
    assert complete.value.code == "files.upload_expired"
