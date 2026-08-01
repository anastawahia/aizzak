"""Unit tests for conversations use-cases over in-memory fake repositories.
Pure: the ports are faked, so no infrastructure is exercised."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    ListConversationsByAgent,
    ListMessages,
    RenameConversation,
    SoftDeleteConversation,
    StartConversation,
)
from app.modules.conversations.domain.entities import Conversation, Message
from app.modules.conversations.domain.events import (
    ConversationDeleted,
    ConversationRenamed,
    ConversationStarted,
    MessageAppended,
)
from app.modules.conversations.domain.value_objects import AgentKey, ConversationKind


class _FakeConversations:
    """In-memory ``ConversationRepository`` — ignores ``ctx`` (no RLS in unit tests).

    ``get`` returns the exact stored object (no copy), so in-place aggregate
    mutations — e.g. ``message_count``/``updated_at`` bumped by
    ``append_message`` — are visible on the next ``get`` without an explicit
    ``save``, mirroring how a real session's identity map behaves.
    """

    def __init__(self) -> None:
        self.rows: dict[str, Conversation] = {}
        self.messages: dict[str, list[Message]] = {}
        # (conversation_id, limit, cursor) per `list_messages` call — lets a
        # test assert the paging arguments are forwarded, not re-derived.
        self.list_messages_calls: list[tuple[str, int, str | None]] = []

    async def get(self, ctx: ExecutionContext, conversation_id: str) -> Conversation | None:
        return self.rows.get(conversation_id)

    async def add(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation

    async def save(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation

    async def list_by_agent(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        matches = [
            c for c in self.rows.values() if c.agent_key.value == agent_key and c.deleted_at is None
        ]
        return Page(data=matches[:limit], next_cursor=None, limit=limit)

    async def append_message(self, ctx: ExecutionContext, message: Message) -> None:
        self.messages.setdefault(message.conversation_id, []).append(message)

    async def list_messages(
        self, ctx: ExecutionContext, conversation_id: str, *, limit: int, cursor: str | None
    ) -> Page[Message]:
        # Soft-deleted messages are excluded from retrieval (INV-CV3); the
        # keyset itself is the SQL adapter's job, tested live in
        # `tests/integration/test_conversations_repository_rls.py`.
        self.list_messages_calls.append((conversation_id, limit, cursor))
        visible = [m for m in self.messages.get(conversation_id, []) if m.deleted_at is None]
        return Page(data=visible[:limit], next_cursor=None, limit=limit)


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


async def _seed_conversation(
    conversations: _FakeConversations,
    *,
    agent_key: str = "rag-agent",
    deleted_at: datetime | None = None,
    message_count: int = 0,
) -> Conversation:
    now = utc_now()
    conversation = Conversation(
        id=new_uuid7(),
        workspace_id="w1",
        agent_key=AgentKey(agent_key),
        kind=ConversationKind.AGENT,
        title=None,
        created_by="u1",
        created_at=now,
        updated_at=now,
        deleted_at=deleted_at,
        version=1,
        message_count=message_count,
    )
    await conversations.add(_ctx(conversation.workspace_id), conversation)
    return conversation


# --------------------------------------------------------------------------- #
# StartConversation                                                            #
# --------------------------------------------------------------------------- #
async def test_start_conversation_creates_and_emits_event() -> None:
    conversations = _FakeConversations()
    conversation, events = await StartConversation(conversations).execute(
        _ctx(), agent_key="RAG-Agent", title="First chat"
    )
    assert conversation.agent_key.value == "rag-agent"
    assert conversation.kind is ConversationKind.AGENT
    assert conversation.title == "First chat"
    assert conversation.created_by == "u1"
    assert conversation.version == 1
    assert conversation.message_count == 0
    assert conversations.rows[conversation.id] is conversation
    assert len(events) == 1
    started = events[0]
    assert isinstance(started, ConversationStarted)
    assert started.conversation_id == conversation.id
    assert started.agent_key == "rag-agent"
    assert started.kind == "agent"


async def test_start_conversation_rejects_invalid_agent_key() -> None:
    with pytest.raises(ValidationError):
        await StartConversation(_FakeConversations()).execute(_ctx(), agent_key="")


# --------------------------------------------------------------------------- #
# AppendMessage                                                                #
# --------------------------------------------------------------------------- #
async def test_append_message_assigns_ascending_seq_across_calls() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    append = AppendMessage(conversations)

    first, first_events = await append.execute(_ctx(), conversation.id, role="user", text="hello")
    second, second_events = await append.execute(
        _ctx(), conversation.id, role="assistant", text="hi there"
    )

    assert first.seq == 1
    assert second.seq == 2
    assert conversations.rows[conversation.id].message_count == 2
    assert len(first_events) == 1
    assert len(second_events) == 1
    assert isinstance(first_events[0], MessageAppended)
    assert second_events[0].seq == 2
    assert conversations.messages[conversation.id] == [first, second]


async def test_append_message_missing_conversation_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await AppendMessage(_FakeConversations()).execute(_ctx(), "missing", role="user", text="hi")


async def test_append_message_on_deleted_conversation_raises_conflict() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations, deleted_at=utc_now())
    with pytest.raises(ConflictError):
        await AppendMessage(conversations).execute(_ctx(), conversation.id, role="user", text="hi")


async def test_append_message_rejects_invalid_role() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    with pytest.raises(ValidationError):
        await AppendMessage(conversations).execute(_ctx(), conversation.id, role="bogus", text="hi")


# --------------------------------------------------------------------------- #
# ListConversationsByAgent                                                     #
# --------------------------------------------------------------------------- #
async def test_list_conversations_by_agent_returns_page() -> None:
    conversations = _FakeConversations()
    await _seed_conversation(conversations, agent_key="rag-agent")
    await _seed_conversation(conversations, agent_key="rag-agent")
    await _seed_conversation(conversations, agent_key="other-agent")

    page = await ListConversationsByAgent(conversations).execute(_ctx(), "RAG-Agent", limit=10)

    assert isinstance(page, Page)
    assert len(page.data) == 2
    assert all(c.agent_key.value == "rag-agent" for c in page.data)
    assert page.limit == 10


async def test_list_conversations_by_agent_rejects_invalid_agent_key() -> None:
    with pytest.raises(ValidationError):
        await ListConversationsByAgent(_FakeConversations()).execute(_ctx(), "", limit=10)


# --------------------------------------------------------------------------- #
# ListMessages                                                                 #
# --------------------------------------------------------------------------- #
async def test_list_messages_returns_thread_and_forwards_paging_arguments() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    append = AppendMessage(conversations)
    await append.execute(_ctx(), conversation.id, role="user", text="one")
    await append.execute(_ctx(), conversation.id, role="assistant", text="two")

    page = await ListMessages(conversations).execute(
        _ctx(), conversation.id, limit=5, cursor="opaque"
    )

    assert [m.seq for m in page.data] == [1, 2]
    assert [m.content.text for m in page.data] == ["one", "two"]
    assert conversations.list_messages_calls == [(conversation.id, 5, "opaque")]


async def test_list_messages_excludes_soft_deleted_messages() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    append = AppendMessage(conversations)
    first, _ = await append.execute(_ctx(), conversation.id, role="user", text="one")
    await append.execute(_ctx(), conversation.id, role="assistant", text="two")
    first.soft_delete(utc_now())

    page = await ListMessages(conversations).execute(_ctx(), conversation.id, limit=5)

    # seq 1 is gone but never reused — the surviving message keeps seq 2.
    assert [m.seq for m in page.data] == [2]


async def test_list_messages_missing_conversation_raises_not_found() -> None:
    conversations = _FakeConversations()
    with pytest.raises(NotFoundError):
        await ListMessages(conversations).execute(_ctx(), "missing", limit=5)
    assert conversations.list_messages_calls == []


async def test_list_messages_on_deleted_conversation_raises_not_found() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations, deleted_at=utc_now())
    with pytest.raises(NotFoundError):
        await ListMessages(conversations).execute(_ctx(), conversation.id, limit=5)
    assert conversations.list_messages_calls == []


# --------------------------------------------------------------------------- #
# RenameConversation                                                           #
# --------------------------------------------------------------------------- #
async def test_rename_conversation_updates_title() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    updated, events = await RenameConversation(conversations).execute(
        _ctx(), conversation.id, "New title"
    )
    assert updated.title == "New title"
    assert conversations.rows[conversation.id].title == "New title"
    assert len(events) == 1
    assert isinstance(events[0], ConversationRenamed)


async def test_rename_conversation_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await RenameConversation(_FakeConversations()).execute(_ctx(), "missing", "X")


async def test_rename_conversation_on_deleted_raises_conflict() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations, deleted_at=utc_now())
    with pytest.raises(ConflictError):
        await RenameConversation(conversations).execute(_ctx(), conversation.id, "X")


# --------------------------------------------------------------------------- #
# SoftDeleteConversation                                                       #
# --------------------------------------------------------------------------- #
async def test_soft_delete_conversation_emits_event_and_persists() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    deleted, events = await SoftDeleteConversation(conversations).execute(_ctx(), conversation.id)
    assert deleted.deleted_at is not None
    assert len(events) == 1
    assert isinstance(events[0], ConversationDeleted)
    assert conversations.rows[conversation.id].deleted_at is not None


async def test_soft_delete_conversation_is_idempotent_no_event() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations, deleted_at=utc_now())
    _, events = await SoftDeleteConversation(conversations).execute(_ctx(), conversation.id)
    assert events == ()
