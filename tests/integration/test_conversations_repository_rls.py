"""Live-Postgres tests for ``SqlConversationRepository`` + RLS
(09-testing-strategy §3).

Runs against the real local Compose PostgreSQL 16 (see
``tests/integration/conftest.py``); auto-skips via ``live_db`` when
unreachable. Builders mirror ``tests/unit/test_conversations_use_cases.py``,
except every id is a *real* ``new_uuid7()`` string rather than an arbitrary
label -- the underlying Postgres columns are actually typed ``uuid``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import encode_id_cursor
from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.persistence.database import create_engine
from app.modules.conversations.adapters.sql_repository import SqlConversationRepository
from app.modules.conversations.domain.entities import Conversation
from app.modules.conversations.domain.errors import ConversationDeletedError
from app.modules.conversations.domain.value_objects import (
    AgentKey,
    ConversationKind,
    MessageContent,
    MessageRole,
)
from tests.integration.conftest import LiveDbDsns

pytestmark = [pytest.mark.live_db]


# --------------------------------------------------------------------------- #
# Shared test helpers                                                         #
# --------------------------------------------------------------------------- #
def _ctx(workspace_id: str, *, user_id: str | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id=user_id or new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def _conversation(
    *,
    workspace_id: str,
    conversation_id: str | None = None,
    agent_key: str = "rag-agent",
    kind: ConversationKind = ConversationKind.AGENT,
    title: str | None = None,
    created_by: str | None = None,
    now: datetime | None = None,
) -> Conversation:
    at = now or utc_now()
    return Conversation(
        id=conversation_id or new_uuid7(),
        workspace_id=workspace_id,
        agent_key=AgentKey(agent_key),
        kind=kind,
        title=title,
        created_by=created_by or new_uuid7(),
        created_at=at,
        updated_at=at,
        deleted_at=None,
        version=1,
        message_count=0,
    )


async def _fetch_conversation_row_as_owner(
    owner_dsn: str, workspace_id: str, conversation_id: str
) -> RowMapping | None:
    """Bypass the repository entirely: read the raw row as the table owner,
    for assertions that must be independent of ``repo.get``'s own
    bookkeeping. Still goes through RLS -- ``0001_conversations.py`` uses
    ``FORCE ROW LEVEL SECURITY``, so even the owner is policy-subject --
    hence setting the GUC first, on the same connection/transaction, before
    reading.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text("SELECT * FROM conversations.conversations WHERE id = :id"),
                {"id": conversation_id},
            )
            return result.mappings().first()
    finally:
        await engine.dispose()


async def _fetch_message_rows_as_owner(
    owner_dsn: str, workspace_id: str, conversation_id: str
) -> list[RowMapping]:
    """Bypass the repository: read the conversation's raw message rows, in
    ``seq`` order, as the table owner (same FORCE RLS caveat as above)."""
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            result = await conn.execute(
                text(
                    "SELECT * FROM conversations.messages WHERE conversation_id = :cid ORDER BY seq"
                ),
                {"cid": conversation_id},
            )
            return list(result.mappings().all())
    finally:
        await engine.dispose()


async def _soft_delete_message_as_owner(owner_dsn: str, workspace_id: str, message_id: str) -> None:
    """Stamp one message's ``deleted_at`` directly, as the table owner.

    ``Message.soft_delete`` is a domain operation with no persistence method
    behind it yet (the port exposes append + list, `02 §2`), so the row is
    stamped here rather than pretending a repository call exists. Same FORCE
    RLS caveat as the readers above — hence setting the GUC first, on the same
    connection/transaction.
    """
    engine = create_engine(DatabaseSettings(url=owner_dsn), poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
            )
            await conn.execute(
                text("UPDATE conversations.messages SET deleted_at = now() WHERE id = :id"),
                {"id": message_id},
            )
    finally:
        await engine.dispose()


# --------------------------------------------------------------------------- #
# (1) round-trip add/get, hydrated message_count == 0                        #
# --------------------------------------------------------------------------- #
async def test_add_then_get_round_trips_with_hydrated_message_count_zero(
    repo_conversations: SqlConversationRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws, agent_key="RAG-Agent", title="First chat")

    await repo_conversations.add(ctx, conversation)
    fetched = await repo_conversations.get(ctx, conversation.id)

    assert fetched is not None
    assert fetched.id == conversation.id
    assert fetched.workspace_id == ws
    assert fetched.agent_key.value == "rag-agent"
    assert fetched.kind is ConversationKind.AGENT
    assert fetched.title == "First chat"
    assert fetched.created_by == conversation.created_by
    assert fetched.created_at == conversation.created_at
    assert fetched.updated_at == conversation.updated_at
    assert fetched.deleted_at is None
    assert fetched.version == 1
    assert fetched.message_count == 0


# --------------------------------------------------------------------------- #
# (2) missing -> None                                                        #
# --------------------------------------------------------------------------- #
async def test_get_missing_conversation_returns_none(
    repo_conversations: SqlConversationRepository,
) -> None:
    assert await repo_conversations.get(_ctx(new_uuid7()), new_uuid7()) is None


# --------------------------------------------------------------------------- #
# (3) append 3 messages -> gap-free seq, persisted rows match (owner truth)  #
# --------------------------------------------------------------------------- #
async def test_append_three_messages_assigns_gap_free_seq_and_persists_rows(
    repo_conversations: SqlConversationRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)

    now = utc_now()
    m1 = conversation.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="hi"), None, now
    )
    await repo_conversations.append_message(ctx, m1)
    m2 = conversation.append_message(
        new_uuid7(), MessageRole.ASSISTANT, MessageContent(text="hello"), 5, now
    )
    await repo_conversations.append_message(ctx, m2)
    m3 = conversation.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="thanks"), None, now
    )
    await repo_conversations.append_message(ctx, m3)

    assert [m1.seq, m2.seq, m3.seq] == [1, 2, 3]

    rows = await _fetch_message_rows_as_owner(live_db.owner, ws, conversation.id)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    # raw asyncpg rows decode `uuid` columns to `uuid.UUID`, not `str` (unlike
    # the adapter's `Uuid(as_uuid=False)`-typed Core columns) -- str() first.
    assert [str(r["id"]) for r in rows] == [m1.id, m2.id, m3.id]
    assert rows[0]["role"] == "user"
    assert rows[0]["content"]["text"] == "hi"
    assert rows[1]["role"] == "assistant"
    assert rows[1]["token_count"] == 5
    assert rows[2]["content"]["text"] == "thanks"

    # each append bumps the parent conversation's version once (1 + 3 = 4).
    conv_row = await _fetch_conversation_row_as_owner(live_db.owner, ws, conversation.id)
    assert conv_row is not None
    assert conv_row["version"] == 4


# --------------------------------------------------------------------------- #
# (4) message round-trip incl. attachments + content                        #
# --------------------------------------------------------------------------- #
async def test_message_round_trip_includes_attachments_and_content(
    repo_conversations: SqlConversationRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)

    file_ref = new_uuid7()
    now = utc_now()
    content = MessageContent(text="see attached", attachments=(file_ref,))
    message = conversation.append_message(new_uuid7(), MessageRole.USER, content, 42, now)
    await repo_conversations.append_message(ctx, message)

    rows = await _fetch_message_rows_as_owner(live_db.owner, ws, conversation.id)
    assert len(rows) == 1
    row = rows[0]
    # raw asyncpg rows decode `uuid` columns to `uuid.UUID`, not `str` -- str() first.
    assert str(row["id"]) == message.id
    assert str(row["conversation_id"]) == conversation.id
    assert str(row["workspace_id"]) == ws
    assert row["role"] == "user"
    assert row["content"] == {"text": "see attached", "attachments": [file_ref]}
    assert row["token_count"] == 42
    assert row["seq"] == 1
    assert row["deleted_at"] is None


# --------------------------------------------------------------------------- #
# (5) re-get hydrates message_count -> next append continues the sequence   #
# --------------------------------------------------------------------------- #
async def test_reget_hydrates_message_count_and_next_append_continues_seq(
    repo_conversations: SqlConversationRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)

    now = utc_now()
    for text_ in ("a", "b", "c"):
        msg = conversation.append_message(
            new_uuid7(), MessageRole.USER, MessageContent(text=text_), None, now
        )
        await repo_conversations.append_message(ctx, msg)

    refetched = await repo_conversations.get(ctx, conversation.id)
    assert refetched is not None
    assert refetched.message_count == 3

    next_message = refetched.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="d"), None, utc_now()
    )
    assert next_message.seq == 4
    await repo_conversations.append_message(ctx, next_message)


# --------------------------------------------------------------------------- #
# (6) CONCURRENT append: second raises ConflictError, exactly one seq row   #
# --------------------------------------------------------------------------- #
async def test_concurrent_append_second_raises_conflict_and_only_one_row_at_that_seq(
    repo_conversations: SqlConversationRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)
    now = utc_now()
    seed = conversation.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="seed"), None, now
    )
    await repo_conversations.append_message(ctx, seed)  # seq=1, version 1 -> 2

    reader_a = await repo_conversations.get(ctx, conversation.id)
    reader_b = await repo_conversations.get(ctx, conversation.id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.message_count == reader_b.message_count == 1

    msg_a = reader_a.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="race-a"), None, utc_now()
    )
    msg_b = reader_b.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="race-b"), None, utc_now()
    )
    assert msg_a.seq == msg_b.seq == 2

    await repo_conversations.append_message(ctx, msg_a)  # wins: seq=2, version 2 -> 3
    with pytest.raises(ConflictError):
        await repo_conversations.append_message(ctx, msg_b)  # loses: uq_msg_seq collision

    rows = await _fetch_message_rows_as_owner(live_db.owner, ws, conversation.id)
    seq_two_rows = [r for r in rows if r["seq"] == 2]
    assert len(seq_two_rows) == 1
    assert str(seq_two_rows[0]["id"]) == msg_a.id

    # no partial write: msg_b's insert AND its (would-be) version bump were
    # rolled back together (one tenant_session transaction) -- version only
    # reflects the initial add (1) + seed append (+1) + msg_a append (+1).
    conv_row = await _fetch_conversation_row_as_owner(live_db.owner, ws, conversation.id)
    assert conv_row is not None
    assert conv_row["version"] == 3


# --------------------------------------------------------------------------- #
# (7) save() rename: version/updated_at write-back, owner-verified          #
# --------------------------------------------------------------------------- #
async def test_save_rename_advances_version_and_writes_back(
    repo_conversations: SqlConversationRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)
    created_at = conversation.created_at

    conversation.rename("Renamed", utc_now())
    await repo_conversations.save(ctx, conversation)

    assert conversation.version == 2
    assert conversation.updated_at >= created_at
    row = await _fetch_conversation_row_as_owner(live_db.owner, ws, conversation.id)
    assert row is not None
    assert row["title"] == "Renamed"
    assert row["version"] == 2

    fetched = await repo_conversations.get(ctx, conversation.id)
    assert fetched is not None
    assert fetched.title == "Renamed"
    assert fetched.version == 2


# --------------------------------------------------------------------------- #
# (8) optimistic conflict on stale rename                                    #
# --------------------------------------------------------------------------- #
async def test_save_with_stale_version_raises_conflict_and_leaves_row_unchanged(
    repo_conversations: SqlConversationRepository, live_db: LiveDbDsns
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)

    reader_a = await repo_conversations.get(ctx, conversation.id)
    reader_b = await repo_conversations.get(ctx, conversation.id)
    assert reader_a is not None
    assert reader_b is not None
    assert reader_a.version == reader_b.version == 1

    reader_a.rename("First", utc_now())
    await repo_conversations.save(ctx, reader_a)
    assert reader_a.version == 2

    reader_b.rename("Second", utc_now())  # reader_b is still stuck at version==1
    with pytest.raises(ConflictError):
        await repo_conversations.save(ctx, reader_b)

    row = await _fetch_conversation_row_as_owner(live_db.owner, ws, conversation.id)
    assert row is not None
    assert row["version"] == 2
    assert row["title"] == "First"


# --------------------------------------------------------------------------- #
# (9) soft-deleted conversation stays readable; domain append guard raises  #
# --------------------------------------------------------------------------- #
async def test_soft_deleted_conversation_still_readable_and_domain_append_guard_raises(
    repo_conversations: SqlConversationRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)

    conversation.soft_delete(utc_now())
    await repo_conversations.save(ctx, conversation)

    fetched = await repo_conversations.get(ctx, conversation.id)
    assert fetched is not None  # `get` does NOT filter deleted_at
    assert fetched.deleted_at is not None

    with pytest.raises(ConversationDeletedError):
        fetched.append_message(
            new_uuid7(), MessageRole.USER, MessageContent(text="too late"), None, utc_now()
        )


# --------------------------------------------------------------------------- #
# (10) list_by_agent: tenant + agent scoped, excludes soft-deleted          #
# --------------------------------------------------------------------------- #
async def test_list_by_agent_scopes_by_tenant_and_agent_and_excludes_deleted(
    repo_conversations: SqlConversationRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    keep = _conversation(workspace_id=ws_a, agent_key="rag-agent")
    await repo_conversations.add(ctx_a, keep)
    deleted = _conversation(workspace_id=ws_a, agent_key="rag-agent")
    await repo_conversations.add(ctx_a, deleted)
    deleted.soft_delete(utc_now())
    await repo_conversations.save(ctx_a, deleted)
    other_agent = _conversation(workspace_id=ws_a, agent_key="other-agent")
    await repo_conversations.add(ctx_a, other_agent)
    cross_tenant = _conversation(workspace_id=ws_b, agent_key="rag-agent")
    await repo_conversations.add(_ctx(ws_b), cross_tenant)

    page = await repo_conversations.list_by_agent(ctx_a, "rag-agent", limit=10, cursor=None)

    assert [c.id for c in page.data] == [keep.id]
    assert page.next_cursor is None


# --------------------------------------------------------------------------- #
# (10b) list_messages: seq order, soft-deleted excluded, tenant-scoped       #
# --------------------------------------------------------------------------- #
async def test_list_messages_returns_seq_order_excludes_deleted_and_is_tenant_scoped(
    repo_conversations: SqlConversationRepository, live_db: LiveDbDsns
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    ctx_a = _ctx(ws_a)
    conversation = _conversation(workspace_id=ws_a)
    await repo_conversations.add(ctx_a, conversation)

    now = utc_now()
    appended = []
    for role, body in ((MessageRole.USER, "one"), (MessageRole.ASSISTANT, "two")):
        message = conversation.append_message(
            new_uuid7(), role, MessageContent(text=body), None, now
        )
        await repo_conversations.append_message(ctx_a, message)
        appended.append(message)
    doomed = conversation.append_message(
        new_uuid7(), MessageRole.USER, MessageContent(text="gone"), None, now
    )
    await repo_conversations.append_message(ctx_a, doomed)
    await _soft_delete_message_as_owner(live_db.owner, ws_a, doomed.id)

    page = await repo_conversations.list_messages(ctx_a, conversation.id, limit=10, cursor=None)

    assert [m.seq for m in page.data] == [1, 2]  # seq 3 excluded, never reused
    assert [m.id for m in page.data] == [m.id for m in appended]
    assert [m.content.text for m in page.data] == ["one", "two"]
    assert page.data[1].role is MessageRole.ASSISTANT
    assert page.next_cursor is None

    # another tenant asking for the same conversation id sees nothing.
    other = await repo_conversations.list_messages(
        _ctx(ws_b), conversation.id, limit=10, cursor=None
    )
    assert other.data == []


# --------------------------------------------------------------------------- #
# (10c) list_messages: keyset paging over `seq`                              #
# --------------------------------------------------------------------------- #
async def test_list_messages_pages_by_seq_cursor(
    repo_conversations: SqlConversationRepository,
) -> None:
    ws = new_uuid7()
    ctx = _ctx(ws)
    conversation = _conversation(workspace_id=ws)
    await repo_conversations.add(ctx, conversation)
    now = utc_now()
    for body in ("a", "b", "c"):
        message = conversation.append_message(
            new_uuid7(), MessageRole.USER, MessageContent(text=body), None, now
        )
        await repo_conversations.append_message(ctx, message)

    first = await repo_conversations.list_messages(ctx, conversation.id, limit=2, cursor=None)
    assert [m.seq for m in first.data] == [1, 2]
    assert first.next_cursor is not None

    second = await repo_conversations.list_messages(
        ctx, conversation.id, limit=2, cursor=first.next_cursor
    )
    assert [m.seq for m in second.data] == [3]
    assert second.next_cursor is None  # last page


# --------------------------------------------------------------------------- #
# (10d) list_messages: a cursor that is not a seq -> common.invalid_cursor   #
# --------------------------------------------------------------------------- #
async def test_list_messages_rejects_a_cursor_that_does_not_carry_a_seq(
    repo_conversations: SqlConversationRepository,
) -> None:
    # Well-formed base64 (so the envelope opens) carrying a UUID — e.g. a
    # cursor minted by `list_by_agent` — must be a 422, not an int() 500.
    foreign_cursor = encode_id_cursor(new_uuid7())
    with pytest.raises(ValidationError) as exc_info:
        await repo_conversations.list_messages(
            _ctx(new_uuid7()), new_uuid7(), limit=10, cursor=foreign_cursor
        )
    assert exc_info.value.code == "common.invalid_cursor"


# --------------------------------------------------------------------------- #
# (11) RLS: no context set at all -> zero rows, no exception                #
# --------------------------------------------------------------------------- #
async def test_no_tenant_context_returns_zero_rows(
    repo_conversations: SqlConversationRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws = new_uuid7()
    await repo_conversations.add(_ctx(ws), _conversation(workspace_id=ws))

    async with sessionmaker_app() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM conversations.conversations"))
        ).scalar_one()

    assert count == 0


# --------------------------------------------------------------------------- #
# (12) empty-string GUC discriminator -- what the NULLIF fix buys           #
# --------------------------------------------------------------------------- #
async def test_empty_string_guc_yields_zero_rows_not_an_exception(
    repo_conversations: SqlConversationRepository,
    sessionmaker_app: async_sessionmaker[AsyncSession],
) -> None:
    ws_a = new_uuid7()
    await repo_conversations.add(_ctx(ws_a), _conversation(workspace_id=ws_a))

    async with sessionmaker_app() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', '', true)"))
        count = (
            await session.execute(text("SELECT count(*) FROM conversations.conversations"))
        ).scalar_one()
        assert count == 0

        await session.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": ws_a}
        )
        count = (
            await session.execute(text("SELECT count(*) FROM conversations.conversations"))
        ).scalar_one()
        assert count == 1


# --------------------------------------------------------------------------- #
# (13) RLS isolation between two tenants                                     #
# --------------------------------------------------------------------------- #
async def test_two_tenant_isolation(repo_conversations: SqlConversationRepository) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    conv_a = _conversation(workspace_id=ws_a)
    await repo_conversations.add(_ctx(ws_a), conv_a)

    assert await repo_conversations.get(_ctx(ws_b), conv_a.id) is None
    assert await repo_conversations.get(_ctx(ws_a), conv_a.id) is not None

    conv_b = _conversation(workspace_id=ws_b)
    await repo_conversations.add(_ctx(ws_b), conv_b)

    assert await repo_conversations.get(_ctx(ws_a), conv_b.id) is None
    assert await repo_conversations.get(_ctx(ws_b), conv_b.id) is not None


# --------------------------------------------------------------------------- #
# (14) forged cross-tenant write -> RLS WITH CHECK rejects it               #
# --------------------------------------------------------------------------- #
async def test_add_with_forged_workspace_id_is_rejected_by_rls_with_check(
    repo_conversations: SqlConversationRepository,
) -> None:
    ws_a, ws_b = new_uuid7(), new_uuid7()
    forged_conversation = _conversation(workspace_id=ws_b)

    with pytest.raises(AppError) as exc_info:
        await repo_conversations.add(_ctx(ws_a), forged_conversation)

    assert exc_info.value.status == 500
    assert exc_info.value.code == "common.internal"
    assert not isinstance(exc_info.value, ConflictError)
    assert await repo_conversations.get(_ctx(ws_b), forged_conversation.id) is None


# --------------------------------------------------------------------------- #
# (10e) list_by_agent: newest-first keyset paging (6.3-ب)                    #
# --------------------------------------------------------------------------- #
async def test_list_by_agent_pages_newest_first(
    repo_conversations: SqlConversationRepository,
) -> None:
    """The thread list reads newest first, while the TRANSCRIPT above reads
    forward by ``seq`` — the one deliberate exception to the direction rule
    (a conversation is a narrative; a thread list is an inbox)."""
    ws = new_uuid7()
    ctx = _ctx(ws)
    created = []
    for _ in range(3):
        conversation = _conversation(workspace_id=ws, agent_key="rag-agent")
        await repo_conversations.add(ctx, conversation)
        created.append(conversation)
    expected = [c.id for c in reversed(created)]  # UUIDv7 monotonic ⇒ reversed insertion

    first = await repo_conversations.list_by_agent(ctx, "rag-agent", limit=2, cursor=None)
    assert [c.id for c in first.data] == expected[:2]
    assert first.next_cursor is not None

    second = await repo_conversations.list_by_agent(
        ctx, "rag-agent", limit=2, cursor=first.next_cursor
    )
    assert [c.id for c in second.data] == expected[2:]
    assert second.next_cursor is None
