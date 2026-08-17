"""In-memory conversations wiring shared by the API router tests (6.1-ج).

Not a ``test_*`` module, so pytest never collects it. It exists because three
test files need the SAME conversations stack — the app-shell tests (which only
have to construct ``ApiServices``), the Conversations router tests, and the
Agents router tests (whose non-streaming invoke now persists a turn) — and
copying a repository fake three times is how three copies drift apart.

The fake repository is deliberately faithful on the two things the routes
actually depend on: ``message_count`` is ``max(seq)`` including soft-deleted
messages (INV-CV3, so ``seq`` is never reused), and ``list_messages`` filters
soft-deleted rows and keysets on ``seq`` exactly as the SQL adapter does. The
cursor is the adapter's own text codec, so a cursor produced here is decoded
by the same rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import (
    Page,
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
)
from app.framework.providers.catalog import ModelCatalog, ModelChoice
from app.framework.types import Uuid
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    ConversationService,
    ConversationUseCases,
    CountConversationsBySpace,
    GetConversation,
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
from app.modules.conversations.ports.files import ReadableFile


@dataclass
class InMemoryConversationRepository:
    """A structural ``ConversationRepository`` over two dicts."""

    rows: dict[str, Conversation] = field(default_factory=dict)
    messages: dict[str, list[Message]] = field(default_factory=dict)
    pins: dict[str, list[PinnedFile]] = field(default_factory=dict)

    async def get(self, ctx: ExecutionContext, conversation_id: Uuid) -> Conversation | None:
        conversation = self.rows.get(conversation_id)
        if conversation is None or conversation.workspace_id != ctx.workspace_id:
            return None
        # Rehydrate `message_count` the way the adapter does — COALESCE(MAX(seq))
        # over ALL rows, soft-deleted included.
        stored = self.messages.get(conversation_id, [])
        conversation.message_count = max((m.seq for m in stored), default=0)
        return conversation

    async def add(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        self.rows[conversation.id] = conversation

    async def save(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        conversation.version += 1
        self.rows[conversation.id] = conversation

    async def list_by_agent(
        self,
        ctx: ExecutionContext,
        agent_key: str,
        *,
        space_id: Uuid | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Conversation]:
        matches = [
            conversation
            for conversation in self.rows.values()
            if conversation.workspace_id == ctx.workspace_id
            and conversation.agent_key.value == agent_key
            and conversation.deleted_at is None
            # `None` narrows nothing (every space), exactly as the adapter's
            # extra condition does — never a match against a NULL space.
            and (space_id is None or conversation.space_id == space_id)
        ]
        # Newest first, like the SQL adapter (6.3-ب).
        matches.sort(key=lambda conversation: conversation.id, reverse=True)
        if cursor is not None:
            after = decode_id_cursor(cursor)
            matches = [conversation for conversation in matches if conversation.id < after]
        window = matches[: limit + 1]
        has_more = len(window) > limit
        page = window[:limit]
        next_cursor = encode_id_cursor(page[-1].id) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)

    async def append_message(self, ctx: ExecutionContext, message: Message) -> None:
        self.messages.setdefault(message.conversation_id, []).append(message)

    async def get_message(
        self, ctx: ExecutionContext, conversation_id: Uuid, message_id: Uuid
    ) -> Message | None:
        # Matches on BOTH ids and does NOT hide a soft-deleted row — the two
        # properties the delete route's behaviour rests on, exactly as the SQL
        # adapter's predicate has them.
        for message in self.messages.get(conversation_id, []):
            if message.id == message_id and message.workspace_id == ctx.workspace_id:
                return message
        return None

    async def save_message(self, ctx: ExecutionContext, message: Message) -> None:
        # The store already holds the very object the use-case mutated, so
        # there is nothing to write back; the method exists because the port
        # declares it, and asserting it was reached is what a caller tests.
        return None

    async def list_files(self, ctx: ExecutionContext, conversation_id: Uuid) -> list[PinnedFile]:
        return [
            pin
            for pin in self.pins.get(conversation_id, [])
            if pin.workspace_id == ctx.workspace_id
        ]

    async def pin_file(
        self, ctx: ExecutionContext, conversation_id: Uuid, file_id: Uuid, now: datetime
    ) -> PinnedFile:
        # The upsert's observable contract, not its SQL: an existing pin is
        # returned UNCHANGED (original `created_at`), which is what makes the
        # route idempotent rather than merely non-duplicating.
        stored = self.pins.setdefault(conversation_id, [])
        for pin in stored:
            if pin.file_id == file_id and pin.workspace_id == ctx.workspace_id:
                return pin
        pin = PinnedFile(
            conversation_id=conversation_id,
            file_id=file_id,
            workspace_id=ctx.workspace_id,
            created_at=now,
        )
        stored.append(pin)
        return pin

    async def unpin_file(self, ctx: ExecutionContext, conversation_id: Uuid, file_id: Uuid) -> None:
        stored = self.pins.get(conversation_id, [])
        self.pins[conversation_id] = [
            pin
            for pin in stored
            if not (pin.file_id == file_id and pin.workspace_id == ctx.workspace_id)
        ]

    async def counts_by_space(
        self, ctx: ExecutionContext, space_ids: Sequence[Uuid]
    ) -> dict[Uuid, int]:
        # Active threads only, and ABSENT rather than zero for a space that
        # holds none -- the two rules the real `GROUP BY` follows, kept here
        # because the router's zero-default depends on the second one.
        wanted = set(space_ids)
        counts: dict[Uuid, int] = {}
        for conversation in self.rows.values():
            if (
                conversation.workspace_id != ctx.workspace_id
                or conversation.space_id not in wanted
                or conversation.deleted_at is not None
            ):
                continue
            assert conversation.space_id is not None
            counts[conversation.space_id] = counts.get(conversation.space_id, 0) + 1
        return counts

    async def purge_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        # Threads, messages and pins together, soft-deleted rows included
        # (spaces plan step 11): the space is gone, so a tombstone under it is
        # a row nothing can reach.
        doomed = [
            conversation.id
            for conversation in self.rows.values()
            if conversation.workspace_id == ctx.workspace_id and conversation.space_id == space_id
        ]
        for conversation_id in doomed:
            del self.rows[conversation_id]
            self.messages.pop(conversation_id, None)
            self.pins.pop(conversation_id, None)
        return len(doomed)

    async def list_messages(
        self, ctx: ExecutionContext, conversation_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[Message]:
        visible = sorted(
            (m for m in self.messages.get(conversation_id, []) if m.deleted_at is None),
            key=lambda m: m.seq,
        )
        if cursor is not None:
            after = decode_seq_cursor(cursor)
            visible = [m for m in visible if m.seq > after]
        window = visible[: limit + 1]
        has_more = len(window) > limit
        page = window[:limit]
        next_cursor = encode_seq_cursor(page[-1].seq) if has_more and page else None
        return Page(data=page, next_cursor=next_cursor, limit=limit)


@dataclass(frozen=True, slots=True)
class ConversationsStack:
    """Everything the two faces of the module need, over ONE repository."""

    repository: InMemoryConversationRepository
    files: StubReadableFiles
    spaces: StubActiveSpaces
    use_cases: ConversationUseCases
    service: ConversationService


class StubModelCatalog:
    """A ``ModelCatalog`` over a fixed route list — enough for the pin's
    validation, which only ever reads ``capability``.

    Defaults to the two keys the router suites pin against. A suite that needs
    a different table builds its own; a suite that needs none still gets a
    catalogue rather than ``None``, because "no routes configured" and "no
    catalogue wired" must stay distinguishable in tests too.
    """

    def __init__(self, capabilities: tuple[str, ...] = ("default", "rag_agent")) -> None:
        self.capabilities = capabilities

    async def list_llm_models(self, ctx: ExecutionContext) -> list[ModelChoice]:
        return [
            ModelChoice(capability=key, provider="stub", model=f"{key}-model", available=True)
            for key in self.capabilities
        ]


@dataclass(frozen=True, slots=True)
class _StubActiveSpace:
    space_id: str


@dataclass
class StubActiveSpaces:
    """An ``ActiveSpaces`` seam over the set of ids that are live.

    Empty by default, which is the honest default for the router suites: they
    open threads with ``space_id=None``, and a stub that answered "yes" to
    every id would let a suite pass a space this workspace never had.
    """

    live: set[str] = field(default_factory=set)

    async def get_active(self, ctx: ExecutionContext, space_id: Uuid) -> _StubActiveSpace | None:
        return _StubActiveSpace(space_id=space_id) if space_id in self.live else None


@dataclass(frozen=True, slots=True)
class _StubReadableFile:
    file_id: str
    space_id: str | None


@dataclass
class StubReadableFiles:
    """A ``ReadableFiles`` seam over a set of ids that are "ready".

    Everything NOT in ``ready`` answers ``None`` — which is the single answer
    the seam gives for unknown, deleted, quarantined and still-uploading
    alike (see ``ports/files.py``), so a suite that wants any of those adds
    nothing here and simply pins an id it never registered.

    ``spaces`` says which space a ready file is in; an id absent from it is a
    file with NO space, which is the state every row has until plan row 8-b
    and the one the router suites (which start their threads spaceless) need.
    """

    ready: set[str] = field(default_factory=set)
    spaces: dict[str, str] = field(default_factory=dict)

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> ReadableFile | None:
        if file_id not in self.ready:
            return None
        return _StubReadableFile(file_id=file_id, space_id=self.spaces.get(file_id))


def build_conversations(
    catalog: ModelCatalog | None = None,
    files: StubReadableFiles | None = None,
    spaces: StubActiveSpaces | None = None,
) -> ConversationsStack:
    """The API-side use-cases and the orchestrator-side inbound port, sharing
    one in-memory store — the same single-repository wiring the Composition
    Root builds for production."""
    repository = InMemoryConversationRepository()
    readable = files if files is not None else StubReadableFiles()
    live_spaces = spaces if spaces is not None else StubActiveSpaces()
    # ONE instance behind both faces, exactly as `_build_conversations` wires
    # it: a suite that pins through the API must see that pin from the
    # orchestrator's `pinned_files`.
    list_files = ListConversationFiles(repository)
    return ConversationsStack(
        repository=repository,
        files=readable,
        spaces=live_spaces,
        use_cases=ConversationUseCases(
            start=StartConversation(repository, live_spaces),
            get=GetConversation(repository),
            list_by_agent=ListConversationsByAgent(repository),
            list_messages=ListMessages(repository),
            rename=RenameConversation(repository),
            pin_model=PinConversationModel(repository, catalog or StubModelCatalog()),
            soft_delete=SoftDeleteConversation(repository),
            soft_delete_message=SoftDeleteMessage(repository),
            list_files=list_files,
            pin_file=PinConversationFile(repository, readable),
            unpin_file=UnpinConversationFile(repository),
            space_counts=CountConversationsBySpace(repository),
        ),
        service=ConversationService(
            StartConversation(repository, live_spaces),
            AppendMessage(repository),
            GetConversation(repository),
            list_files,
        ),
    )
