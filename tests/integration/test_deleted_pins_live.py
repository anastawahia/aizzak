"""Live-Postgres proof that a pin on a deleted file is hidden (gap th-7;
``docs/delivery/data-search-deleted-pins/design.md`` §5).

The unit suites prove the rule over stubbed seams: given a files seam that says
"not live", the pin drops out of the list, the turn scope and the bound. What
they cannot prove is that the real seam says it. ``names_for_files`` is
``ready_names`` under RLS, and the claim that a deleted file is absent from it
rests on SQL (``status = 'ready' AND deleted_at IS NULL``) and on the real
delete cascade having stamped ``deleted_at`` on the very row the read looks at.

So this file wires the real thing exactly as the Composition Root does: one
``SqlFileRepository`` behind ONE ``FilesQueryService`` that both
``PinConversationFile`` and ``ListConversationFiles`` ask, one
``SqlConversationRepository`` behind ``ConversationService``, and the real
``DeleteFileService`` (Qdrant is a recording fake, ``test_file_cascade_live.py``'s
reason: it has its own live suite and this one owes the SQL). Files are
registered and completed through their own use-cases and threads opened through
``StartConversation``; only the pin on an id no file ever had is inserted
directly, because the product cannot write it.

Local copies of the small helpers are deliberate -- the two reference live files
do the same, and a test file importing another's helpers couples their fates.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.electrical_purge import ElectricalFilePurge
from app.framework.di.file_deletion import DeleteFileService
from app.framework.errors import NotFoundError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import DatabaseSettings, Limits
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.persistence.outbox import SqlEventOutbox
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    ConversationService,
    ExpectClarification,
    GetConversation,
    ListConversationFiles,
    PinConversationFile,
    StartConversation,
)
from app.modules.electrical.drawings.adapters.sql_repository import (
    SqlDocumentRepository as SqlDrawingDocumentRepository,
)
from app.modules.electrical.drawings.adapters.sql_repository import (
    SqlJobRepository as SqlDrawingJobRepository,
)
from app.modules.electrical.drawings.application.purge import PurgeFileDocuments
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    FilesQueryService,
    RegisterUpload,
    SoftDeleteFile,
    SoftDeleteFileService,
)
from app.modules.knowledge.adapters.sql_repository import SqlDocumentRepository
from app.modules.knowledge.application.use_cases import PurgeFileKnowledge
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


class _NoopVectors:
    """The one ``HybridVectorStore`` method the cascade calls, doing nothing."""

    async def delete(self, collection: str, ids: Sequence[str]) -> None:
        return None

    async def delete_everywhere(self, name: str, ids: Sequence[str]) -> None:
        return None


class _NoopUnitOfWork:
    def begin(self, ctx: ExecutionContext) -> _NoopUnitOfWork:
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _NoopOutbox:
    async def append(self, ctx: ExecutionContext, records: Sequence[object]) -> None:
        assert list(records) == []


def _ctx(workspace_id: str) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _space(workspace_id: str, name: str) -> Space:
    at = utc_now()
    return Space(
        id=new_uuid7(),
        workspace_id=workspace_id,
        name=SpaceName(name),
        created_by=new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
    )


def _service(tenant_session: TenantSessionFactory) -> DeleteFileService:
    """The production wiring of the Composition Root, minus Qdrant."""
    return DeleteFileService(
        SoftDeleteFileService(
            SoftDeleteFile(SqlFileRepository(tenant_session)),
            _NoopOutbox(),
            _NoopUnitOfWork(),
        ),
        knowledge=PurgeFileKnowledge(SqlDocumentRepository(tenant_session), _NoopVectors()),  # type: ignore[arg-type]
        electrical=ElectricalFilePurge(
            drawings=PurgeFileDocuments(
                SqlDrawingDocumentRepository(tenant_session),
                SqlDrawingJobRepository(tenant_session),
                SqlEventOutbox(tenant_session),
                tenant_session,
            )
        ),
    )


async def _ready_file(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str, name: str
) -> str:
    files = SqlFileRepository(tenant_session)
    register = RegisterUpload(
        files, Limits(), SpacesQueryService(SqlSpaceRepository(tenant_session))
    )
    file = await register.execute(
        ctx, space_id=space_id, name=name, content_type="application/pdf", size_bytes=11
    )
    await CompleteUpload(files).execute(ctx, file_id=file.id, checksum=None)
    return file.id


class _Stack:
    """The conversations module over ONE repository and ONE files seam."""

    def __init__(self, tenant_session: TenantSessionFactory) -> None:
        self.repository = SqlConversationRepository(tenant_session)
        self.files = FilesQueryService(SqlFileRepository(tenant_session))
        self.pin = PinConversationFile(self.repository, self.files)
        self.list_files = ListConversationFiles(self.repository, self.files)
        self.service = ConversationService(
            StartConversation(
                self.repository, SpacesQueryService(SqlSpaceRepository(tenant_session))
            ),
            AppendMessage(self.repository),
            GetConversation(self.repository),
            self.list_files,
            ExpectClarification(self.repository),
        )
        self._spaces = SpacesQueryService(SqlSpaceRepository(tenant_session))

    async def thread(self, ctx: ExecutionContext, space_id: str) -> str:
        start = StartConversation(self.repository, self._spaces)
        conversation, _events = await start.execute(ctx, space_id=space_id, agent_key="rag-agent")
        return conversation.id

    async def listed(self, ctx: ExecutionContext, conversation_id: str) -> list[str]:
        return [p.file_id for p in await self.list_files.execute(ctx, conversation_id)]


async def _seed(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    *names: str,
) -> tuple[str, ExecutionContext, _Stack, str, list[str]]:
    """A workspace with one space, one thread and ``names`` pinned, in order."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    stack = _Stack(tenant_session)
    conversation_id = await stack.thread(ctx, space.id)
    file_ids = []
    for name in names:
        file_id = await _ready_file(tenant_session, ctx, space.id, name)
        await stack.pin.execute(ctx, conversation_id, file_id)
        file_ids.append(file_id)
    return ws, ctx, stack, conversation_id, file_ids


async def _owner_rows(live_db: LiveDbDsns, ws: str, conversation_id: str) -> list[str]:
    """The thread's ``conversation_files`` rows, as the table owner (still under
    FORCE RLS, hence the GUC): the only way to tell "hidden" from "deleted"."""
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})
            result = await conn.execute(
                text(
                    "SELECT file_id::text FROM conversations.conversation_files "
                    "WHERE conversation_id = :cid ORDER BY created_at, file_id"
                ),
                {"cid": conversation_id},
            )
            return [row[0] for row in result.all()]
    finally:
        await engine.dispose()


async def _insert_pin_as_owner(
    live_db: LiveDbDsns, ws: str, conversation_id: str, file_id: str
) -> None:
    engine = create_engine(DatabaseSettings(url=live_db.owner), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws})
            await conn.execute(
                text(
                    "INSERT INTO conversations.conversation_files "
                    "(conversation_id, file_id, workspace_id) VALUES (:cid, :fid, :ws)"
                ),
                {"cid": conversation_id, "fid": file_id, "ws": ws},
            )
    finally:
        await engine.dispose()


async def test_a_row_naming_no_file_at_all_is_not_listed(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    live_db: LiveDbDsns,
) -> None:
    """AC-3.2 (and AC-1.1's live half): an id no file ever had is not live."""
    ws, ctx, stack, conversation_id, (live,) = await _seed(tenant_session, repo_spaces, "b.pdf")
    await _insert_pin_as_owner(live_db, ws, conversation_id, new_uuid7())

    assert await stack.listed(ctx, conversation_id) == [live]
    assert len(await _owner_rows(live_db, ws, conversation_id)) == 2


async def test_deleting_the_file_twice_keeps_the_list_at_the_rest(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """AC-3.4: the cascade is idempotent, so is its effect on the list."""
    _ws, ctx, stack, conversation_id, (doomed, kept) = await _seed(
        tenant_session, repo_spaces, "a.pdf", "b.pdf"
    )
    assert await stack.listed(ctx, conversation_id) == [doomed, kept]
    service = _service(tenant_session)

    await service.delete(ctx, doomed)
    assert await stack.listed(ctx, conversation_id) == [kept]
    await service.delete(ctx, doomed)
    assert await stack.listed(ctx, conversation_id) == [kept]


async def test_the_cascade_leaves_the_pin_row_and_the_list_hides_it(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    live_db: LiveDbDsns,
) -> None:
    """AC-3.7: no cleanup is added to the cascade; the row stays, the read hides it."""
    ws, ctx, stack, conversation_id, (doomed, kept) = await _seed(
        tenant_session, repo_spaces, "a.pdf", "b.pdf"
    )

    await _service(tenant_session).delete(ctx, doomed)

    assert sorted(await _owner_rows(live_db, ws, conversation_id)) == sorted([doomed, kept])
    assert await stack.listed(ctx, conversation_id) == [kept]
    assert await stack.service.pinned_files(ctx, conversation_id) == (kept,)


async def test_a_pin_on_a_file_this_tenant_deleted_is_gone_and_another_tenant_sees_nothing(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """AC-5.3: the deleted pin is gone for its own tenant, and another tenant
    can neither read W1's live files through the seam nor list W1's thread."""
    _ws, ctx, stack, conversation_id, (doomed, kept) = await _seed(
        tenant_session, repo_spaces, "a.pdf", "b.pdf"
    )
    await _service(tenant_session).delete(ctx, doomed)
    other = _ctx(new_uuid7())

    assert await stack.listed(ctx, conversation_id) == [kept]
    assert doomed not in await stack.files.names_for_files(ctx, [doomed, kept])
    assert dict(await stack.files.names_for_files(other, [kept])) == {}
    with pytest.raises(NotFoundError):
        await stack.list_files.execute(other, conversation_id)


async def test_no_live_pin_left_reads_as_an_unscoped_thread(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """AC-1.2 and AC-2.2 on the real path, at the conversations end: once every
    pinned file is deleted the scope the orchestrator reads is ``()`` --
    unscoped, the thread's whole space -- and the list is empty."""
    _ws, ctx, stack, conversation_id, file_ids = await _seed(
        tenant_session, repo_spaces, "a.pdf", "b.pdf"
    )
    assert await stack.service.pinned_files(ctx, conversation_id) == tuple(file_ids)
    service = _service(tenant_session)

    for file_id in file_ids:
        await service.delete(ctx, file_id)

    assert await stack.listed(ctx, conversation_id) == []
    assert await stack.service.pinned_files(ctx, conversation_id) == ()
