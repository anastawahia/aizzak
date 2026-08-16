"""Live-Postgres proof of the pin's space rule (``docs/spaces-backend-plan.md``
§3.5, step 7; 09-testing-strategy §3).

The unit file next door proves the RULE over a stubbed seam: given a file in
one space and a thread in another, the pin is a 409. What it cannot prove is
that the two spaces the rule compares are the ones the database actually
holds — the stub decides both, so a projection that dropped ``space_id`` on
the way out of ``files`` (``FilesQueryService`` handing back ``None`` for
every file) would leave every unit assertion green while the rule compared
``None`` with ``None`` and permitted everything.

So this file wires the real thing, exactly as the Composition Root does: one
``SqlFileRepository`` behind ``FilesQueryService``, one
``SqlConversationRepository``, and ``SpacesQueryService`` bound to the
``ActiveSpaces`` seam each module declares for itself. Nothing is seeded by
SQL — the file is registered and completed through its own use-cases, and the
thread is opened through ``StartConversation``, so what the rule reads is what
the platform would have written.
"""

from __future__ import annotations

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.settings.settings import Limits
from app.infrastructure.persistence.rls import TenantSessionFactory
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.application.use_cases import (
    PinConversationFile,
    StartConversation,
)
from app.modules.files.adapters.sql_repository import SqlFileRepository
from app.modules.files.application.use_cases import (
    CompleteUpload,
    FilesQueryService,
    RegisterUpload,
)
from app.modules.spaces.adapters.sql_repository import SqlSpaceRepository
from app.modules.spaces.application.use_cases import SpacesQueryService
from app.modules.spaces.domain.entities import Space
from app.modules.spaces.domain.value_objects import SpaceName

pytestmark = [pytest.mark.live_db]


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


async def _ready_file(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str | None
) -> str:
    """A file in ``ready`` state (INV-F2), through the module's own path.

    ``get_readable`` answers only for a ready file, so a registration alone
    would be refused for a reason that has nothing to do with spaces — and a
    test that cannot tell those two refusals apart proves neither.
    """
    files = SqlFileRepository(tenant_session)
    register = RegisterUpload(
        files, Limits(), SpacesQueryService(SqlSpaceRepository(tenant_session))
    )
    file = await register.execute(
        ctx,
        space_id=space_id,
        name="paper.pdf",
        content_type="application/pdf",
        size_bytes=11,
    )
    await CompleteUpload(files).execute(ctx, file_id=file.id, checksum=None)
    return file.id


def _pin(tenant_session: TenantSessionFactory) -> PinConversationFile:
    """``PinConversationFile`` over the production binding: the ``files``
    module's own ``FilesQuery`` behind the ``ReadableFiles`` seam."""
    return PinConversationFile(
        SqlConversationRepository(tenant_session),
        FilesQueryService(SqlFileRepository(tenant_session)),
    )


async def _thread(
    tenant_session: TenantSessionFactory, ctx: ExecutionContext, space_id: str | None
) -> str:
    start = StartConversation(
        SqlConversationRepository(tenant_session),
        SpacesQueryService(SqlSpaceRepository(tenant_session)),
    )
    conversation, _events = await start.execute(ctx, space_id=space_id, agent_key="rag-agent")
    return conversation.id


async def test_a_file_pins_into_a_thread_of_its_own_space(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """The permitted case, proven first: without it, every assertion below
    could be satisfied by a rule that refuses everything."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    file_id = await _ready_file(tenant_session, ctx, space.id)
    conversation_id = await _thread(tenant_session, ctx, space.id)

    pin = await _pin(tenant_session).execute(ctx, conversation_id, file_id)

    assert pin.file_id == file_id
    assert pin.conversation_id == conversation_id


async def test_a_file_from_another_space_is_refused_and_no_pin_row_is_written(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """§3.5, on the real path. Both spaces come from the database — the
    thread's from ``conversations.conversations``, the file's projected out of
    ``files.files`` by ``FilesQueryService`` — so a projection that dropped
    the column would turn this refusal into a stored pin.
    """
    ws = new_uuid7()
    ctx = _ctx(ws)
    research, drafts = _space(ws, "Research"), _space(ws, "Drafts")
    await repo_spaces.add(ctx, research)
    await repo_spaces.add(ctx, drafts)
    file_id = await _ready_file(tenant_session, ctx, research.id)
    conversation_id = await _thread(tenant_session, ctx, drafts.id)
    conversations = SqlConversationRepository(tenant_session)

    with pytest.raises(ConflictError) as exc_info:
        await _pin(tenant_session).execute(ctx, conversation_id, file_id)

    assert exc_info.value.code == "spaces.cross_space_pin"
    # Nothing landed: the refusal is a decision, not a cleanup.
    assert await conversations.list_files(ctx, conversation_id) == []


async def test_an_unreadable_file_is_still_the_422_and_not_the_space_error(
    tenant_session: TenantSessionFactory, repo_spaces: SqlSpaceRepository
) -> None:
    """The two refusals stay distinguishable on the live path: a registered
    but never-completed file is unreadable (INV-F2) and answers 422, even
    though it sits in the SAME space as the thread — so a client is never told
    "another space" about a file that simply has not landed yet.
    """
    ws = new_uuid7()
    ctx = _ctx(ws)
    space = _space(ws, "Research")
    await repo_spaces.add(ctx, space)
    files = SqlFileRepository(tenant_session)
    register = RegisterUpload(
        files, Limits(), SpacesQueryService(SqlSpaceRepository(tenant_session))
    )
    pending = await register.execute(
        ctx, space_id=space.id, name="half.pdf", content_type="application/pdf", size_bytes=3
    )
    conversation_id = await _thread(tenant_session, ctx, space.id)

    with pytest.raises(ValidationError):
        await _pin(tenant_session).execute(ctx, conversation_id, pending.id)


async def test_a_thread_cannot_be_opened_in_a_space_that_is_not_live(
    tenant_session: TenantSessionFactory,
    repo_spaces: SqlSpaceRepository,
    repo_conversations: SqlConversationRepository,
) -> None:
    """``StartConversation``'s own check, live: unknown and soft-deleted are
    one 404, and neither leaves a thread behind — a thread filed under an id
    that names nothing would be invisible to every space listing and scoped
    (decision 1) to the files of a space nobody can name.
    """
    ws = new_uuid7()
    ctx = _ctx(ws)
    deleted = _space(ws, "Archived")
    await repo_spaces.add(ctx, deleted)
    deleted.soft_delete(utc_now())
    await repo_spaces.save(ctx, deleted)

    for space_id in (new_uuid7(), deleted.id):
        with pytest.raises(NotFoundError):
            await _thread(tenant_session, ctx, space_id)

    page = await repo_conversations.list_by_agent(
        ctx, "rag-agent", space_id=None, limit=10, cursor=None
    )
    assert page.data == []
