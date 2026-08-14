"""Unit tests for conversations use-cases over in-memory fake repositories.
Pure: the ports are faked, so no infrastructure is exercised."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.providers.catalog import ModelChoice
from app.modules.conversations.application.use_cases import (
    MAX_PINNED_FILES,
    AppendMessage,
    ListConversationFiles,
    ListConversationsByAgent,
    ListMessages,
    PinConversationFile,
    PinConversationModel,
    RenameConversation,
    SoftDeleteConversation,
    SoftDeleteMessage,
    StartConversation,
    UnpinConversationFile,
)
from app.modules.conversations.domain.entities import Conversation, Message, PinnedFile
from app.modules.conversations.domain.events import (
    ConversationDeleted,
    ConversationRenamed,
    ConversationStarted,
    MessageAppended,
)
from app.modules.conversations.domain.value_objects import AgentKey, ConversationKind
from tests.unit.support_conversations import StubModelCatalog


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
        # Message ids handed to `save_message`, in order — the delete's
        # idempotent path is defined by still reaching the repository, which
        # is not observable from the returned entity alone.
        self.saved_message_ids: list[str] = []
        self.pins: dict[str, list[PinnedFile]] = {}

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

    async def get_message(
        self, ctx: ExecutionContext, conversation_id: str, message_id: str
    ) -> Message | None:
        # Both ids, and NO soft-delete filter — the two properties the SQL
        # adapter's predicate has and the delete's behaviour rests on.
        for message in self.messages.get(conversation_id, []):
            if message.id == message_id:
                return message
        return None

    async def save_message(self, ctx: ExecutionContext, message: Message) -> None:
        self.saved_message_ids.append(message.id)

    async def list_files(self, ctx: ExecutionContext, conversation_id: str) -> list[PinnedFile]:
        return list(self.pins.get(conversation_id, []))

    async def pin_file(
        self, ctx: ExecutionContext, conversation_id: str, file_id: str, now: datetime
    ) -> PinnedFile:
        # The UPSERT's observable contract: an existing pin comes back
        # UNCHANGED, so a repeat cannot rewrite `created_at`.
        stored = self.pins.setdefault(conversation_id, [])
        for pin in stored:
            if pin.file_id == file_id:
                return pin
        pin = PinnedFile(
            conversation_id=conversation_id,
            file_id=file_id,
            workspace_id=ctx.workspace_id,
            created_at=now,
        )
        stored.append(pin)
        return pin

    async def unpin_file(self, ctx: ExecutionContext, conversation_id: str, file_id: str) -> None:
        stored = self.pins.get(conversation_id, [])
        self.pins[conversation_id] = [pin for pin in stored if pin.file_id != file_id]

    async def list_messages(
        self, ctx: ExecutionContext, conversation_id: str, *, limit: int, cursor: str | None
    ) -> Page[Message]:
        # Soft-deleted messages are excluded from retrieval (INV-CV3); the
        # keyset itself is the SQL adapter's job, tested live in
        # `tests/integration/test_conversations_repository_rls.py`.
        self.list_messages_calls.append((conversation_id, limit, cursor))
        visible = [m for m in self.messages.get(conversation_id, []) if m.deleted_at is None]
        return Page(data=visible[:limit], next_cursor=None, limit=limit)


@dataclass(frozen=True, slots=True)
class _ReadableFile:
    file_id: str


@dataclass
class _FakeReadableFiles:
    """A ``ReadableFiles`` seam: anything outside ``ready`` is unreadable, and
    the use-case may not care WHY (``ports/files.py``)."""

    ready: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

    async def get_readable(self, ctx: ExecutionContext, file_id: str) -> _ReadableFile | None:
        self.calls.append(file_id)
        return _ReadableFile(file_id) if file_id in self.ready else None


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


# --------------------------------------------------------------------------- #
# PinConversationModel (BE-RAG-003)                                           #
# --------------------------------------------------------------------------- #
async def test_pin_stores_a_configured_route_and_bumps_updated_at() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    before = conversation.updated_at
    pinned, events = await PinConversationModel(conversations, StubModelCatalog()).execute(
        _ctx(), conversation.id, "rag_agent"
    )
    assert pinned.model_route == "rag_agent"
    assert pinned.updated_at >= before
    # No event: 04 §5 gives this module no stream, so inventing a catalog
    # entry nothing subscribes to would be contract surface with nothing behind
    # it.
    assert events == ()


async def test_pin_to_null_unpins_without_consulting_the_catalog() -> None:
    """Unpinning must always be possible — including from a route the operator
    has since retired, which a catalogue check would make unremovable."""

    class _RefusingCatalog:
        async def list_llm_models(self, ctx: ExecutionContext) -> list[ModelChoice]:
            raise AssertionError("unpinning must not consult the catalogue")

    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    conversation.model_route = "retired-route"
    unpinned, _ = await PinConversationModel(conversations, _RefusingCatalog()).execute(
        _ctx(), conversation.id, None
    )
    assert unpinned.model_route is None


async def test_pinning_an_unconfigured_route_is_422_naming_what_is_configured() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    with pytest.raises(ValidationError) as exc_info:
        await PinConversationModel(conversations, StubModelCatalog()).execute(
            _ctx(), conversation.id, "gpt-4o"
        )
    # The message names the real keys: the caller is choosing from a table it
    # cannot see, so a bare rejection would leave it guessing.
    assert "gpt-4o" in str(exc_info.value)
    assert "rag_agent" in str(exc_info.value)
    assert conversation.model_route is None


async def test_a_blank_route_is_rejected_separately_from_an_unknown_one() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    with pytest.raises(ValidationError) as exc_info:
        await PinConversationModel(conversations, StubModelCatalog()).execute(
            _ctx(), conversation.id, "   "
        )
    assert "non-empty" in str(exc_info.value)


async def test_pinning_an_unknown_conversation_is_404() -> None:
    with pytest.raises(NotFoundError):
        await PinConversationModel(_FakeConversations(), StubModelCatalog()).execute(
            _ctx(), "missing", "default"
        )


async def test_pinning_a_soft_deleted_conversation_is_a_conflict() -> None:
    """A deleted thread refuses the WRITE rather than denying it exists — the
    same asymmetry rename documents."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations, deleted_at=utc_now())
    with pytest.raises(ConflictError):
        await PinConversationModel(conversations, StubModelCatalog()).execute(
            _ctx(), conversation.id, "default"
        )


async def test_an_unavailable_route_is_still_pinnable() -> None:
    """Availability is a property of the caller and the moment, not of the
    route. Refusing here would make add-a-key-later impossible."""

    class _NoKeys:
        async def list_llm_models(self, ctx: ExecutionContext) -> list[ModelChoice]:
            return [
                ModelChoice(
                    capability="cloud", provider="openai", model="gpt-test", available=False
                )
            ]

    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    pinned, _ = await PinConversationModel(conversations, _NoKeys()).execute(
        _ctx(), conversation.id, "cloud"
    )
    assert pinned.model_route == "cloud"


# --------------------------------------------------------------------------- #
# SoftDeleteMessage (BE-RAG-004)                                              #
# --------------------------------------------------------------------------- #
async def test_soft_delete_message_stamps_deleted_at_and_persists() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    append = AppendMessage(conversations)
    message, _ = await append.execute(_ctx(), conversation.id, role="user", text="one")

    deleted, events = await SoftDeleteMessage(conversations).execute(
        _ctx(), conversation.id, message.id
    )

    assert deleted.deleted_at is not None
    assert conversations.saved_message_ids == [message.id]
    # No event, for PinConversationModel's reason: 04 §5 gives this module no
    # stream, so there is nothing subscribed to lose.
    assert events == ()


async def test_soft_delete_message_leaves_the_seq_counter_alone() -> None:
    """INV-CV3: a deleted turn keeps its ``seq``, so the next append does not
    reuse it and the transcript shows a gap instead of renumbering."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    append = AppendMessage(conversations)
    first, _ = await append.execute(_ctx(), conversation.id, role="user", text="one")

    await SoftDeleteMessage(conversations).execute(_ctx(), conversation.id, first.id)
    second, _ = await append.execute(_ctx(), conversation.id, role="assistant", text="two")

    assert (first.seq, second.seq) == (1, 2)
    page = await ListMessages(conversations).execute(_ctx(), conversation.id, limit=5)
    assert [m.id for m in page.data] == [second.id]


async def test_soft_delete_message_is_idempotent_and_still_writes() -> None:
    """A second delete is a success, not a 404 — and it still reaches the
    repository, so the answer never depends on state the caller cannot see."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    message, _ = await AppendMessage(conversations).execute(
        _ctx(), conversation.id, role="user", text="one"
    )
    delete = SoftDeleteMessage(conversations)
    first, _ = await delete.execute(_ctx(), conversation.id, message.id)
    stamped = first.deleted_at

    again, _ = await delete.execute(_ctx(), conversation.id, message.id)

    assert again.deleted_at == stamped  # the original timestamp is not overwritten
    assert conversations.saved_message_ids == [message.id, message.id]


async def test_soft_delete_message_of_another_thread_is_404() -> None:
    """Ownership is the repository's (conversation_id, message_id) predicate,
    so a mismatch is never noticed after something has been mutated."""
    conversations = _FakeConversations()
    owner = await _seed_conversation(conversations)
    other = await _seed_conversation(conversations)
    message, _ = await AppendMessage(conversations).execute(
        _ctx(), owner.id, role="user", text="one"
    )

    with pytest.raises(NotFoundError):
        await SoftDeleteMessage(conversations).execute(_ctx(), other.id, message.id)

    assert message.deleted_at is None
    assert conversations.saved_message_ids == []


async def test_soft_delete_message_unknown_message_is_404() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    with pytest.raises(NotFoundError):
        await SoftDeleteMessage(conversations).execute(_ctx(), conversation.id, "missing")


async def test_soft_delete_message_unknown_conversation_is_404() -> None:
    with pytest.raises(NotFoundError):
        await SoftDeleteMessage(_FakeConversations()).execute(_ctx(), "missing", "m1")


async def test_soft_delete_message_on_a_deleted_thread_is_a_conflict() -> None:
    """409 rather than 404 — the WRITE is refused, the thread is not denied.
    Reaching for the message first would have collapsed both into "no such
    message"."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    message, _ = await AppendMessage(conversations).execute(
        _ctx(), conversation.id, role="user", text="one"
    )
    conversation.soft_delete(utc_now())

    with pytest.raises(ConflictError):
        await SoftDeleteMessage(conversations).execute(_ctx(), conversation.id, message.id)

    assert message.deleted_at is None


# --------------------------------------------------------------------------- #
# BE-RAG-005 — the thread's retrieval scope                                    #
# --------------------------------------------------------------------------- #
async def test_pinning_a_file_stores_it_and_reports_it_back() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    files = _FakeReadableFiles(ready={"f1"})

    pin = await PinConversationFile(conversations, files).execute(_ctx(), conversation.id, "f1")

    assert pin.file_id == "f1"
    assert pin.conversation_id == conversation.id
    assert pin.workspace_id == "w1"
    listed = await ListConversationFiles(conversations).execute(_ctx(), conversation.id)
    assert [p.file_id for p in listed] == ["f1"]


async def test_pinning_checks_the_file_against_the_files_module_not_this_table() -> None:
    """An unreadable file is a 422 BEFORE anything is stored: a pin retrieval
    can never match would leave the thread answering from less than the UI
    shows as pinned."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    files = _FakeReadableFiles(ready=set())

    with pytest.raises(ValidationError):
        await PinConversationFile(conversations, files).execute(_ctx(), conversation.id, "ghost")

    assert files.calls == ["ghost"]
    assert conversations.pins.get(conversation.id, []) == []


async def test_pinning_the_same_file_twice_keeps_the_original_timestamp() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    use_case = PinConversationFile(conversations, _FakeReadableFiles(ready={"f1"}))

    first = await use_case.execute(_ctx(), conversation.id, "f1")
    second = await use_case.execute(_ctx(), conversation.id, "f1")

    assert second.created_at == first.created_at
    assert len(conversations.pins[conversation.id]) == 1


async def test_pinning_stops_at_the_scope_bound_but_a_repeat_still_passes() -> None:
    """The ceiling counts NEW pins only — re-pinning something already in the
    scope adds nothing, so refusing it would make a retry fail where the
    original call succeeded."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    ids = [f"f{n}" for n in range(MAX_PINNED_FILES)]
    files = _FakeReadableFiles(ready={*ids, "one-too-many"})
    use_case = PinConversationFile(conversations, files)
    for file_id in ids:
        await use_case.execute(_ctx(), conversation.id, file_id)

    with pytest.raises(ValidationError):
        await use_case.execute(_ctx(), conversation.id, "one-too-many")

    repeat = await use_case.execute(_ctx(), conversation.id, ids[0])
    assert repeat.file_id == ids[0]


async def test_pinning_on_a_deleted_thread_is_a_conflict_and_an_unknown_one_a_404() -> None:
    conversations = _FakeConversations()
    deleted = await _seed_conversation(conversations, deleted_at=utc_now())
    use_case = PinConversationFile(conversations, _FakeReadableFiles(ready={"f1"}))

    with pytest.raises(ConflictError):
        await use_case.execute(_ctx(), deleted.id, "f1")
    with pytest.raises(NotFoundError):
        await use_case.execute(_ctx(), "missing", "f1")


async def test_unpinning_is_idempotent_and_never_consults_the_files_module() -> None:
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    files = _FakeReadableFiles(ready={"f1"})
    await PinConversationFile(conversations, files).execute(_ctx(), conversation.id, "f1")
    unpin = UnpinConversationFile(conversations)

    await unpin.execute(_ctx(), conversation.id, "f1")
    await unpin.execute(_ctx(), conversation.id, "f1")

    assert conversations.pins[conversation.id] == []
    # Only the pin call reached the seam: un-pinning a file that has since
    # been deleted must still work.
    assert files.calls == ["f1"]


async def test_unpinning_on_a_deleted_thread_is_a_conflict() -> None:
    """A write is a write: that it removes rather than adds does not make it a
    read, so it answers like the rename and the pin, not like the listing."""
    conversations = _FakeConversations()
    deleted = await _seed_conversation(conversations, deleted_at=utc_now())

    with pytest.raises(ConflictError):
        await UnpinConversationFile(conversations).execute(_ctx(), deleted.id, "f1")


async def test_a_deleted_thread_still_lists_its_scope() -> None:
    """Reads in this module see through a soft delete, and a client that can
    still open the transcript must be able to see what it answered from."""
    conversations = _FakeConversations()
    conversation = await _seed_conversation(conversations)
    await PinConversationFile(conversations, _FakeReadableFiles(ready={"f1"})).execute(
        _ctx(), conversation.id, "f1"
    )
    conversation.soft_delete(utc_now())

    listed = await ListConversationFiles(conversations).execute(_ctx(), conversation.id)

    assert [p.file_id for p in listed] == ["f1"]


async def test_listing_the_scope_of_an_unknown_thread_is_a_404() -> None:
    with pytest.raises(NotFoundError):
        await ListConversationFiles(_FakeConversations()).execute(_ctx(), "missing")
