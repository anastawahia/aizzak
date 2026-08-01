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

from dataclasses import dataclass, field

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import (
    Page,
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
)
from app.framework.types import Uuid
from app.modules.conversations.application.use_cases import (
    AppendMessage,
    ConversationService,
    ConversationUseCases,
    GetConversation,
    ListConversationsByAgent,
    ListMessages,
    SoftDeleteConversation,
    StartConversation,
)
from app.modules.conversations.domain.entities import Conversation, Message


@dataclass
class InMemoryConversationRepository:
    """A structural ``ConversationRepository`` over two dicts."""

    rows: dict[str, Conversation] = field(default_factory=dict)
    messages: dict[str, list[Message]] = field(default_factory=dict)

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
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        matches = [
            conversation
            for conversation in self.rows.values()
            if conversation.workspace_id == ctx.workspace_id
            and conversation.agent_key.value == agent_key
            and conversation.deleted_at is None
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
    use_cases: ConversationUseCases
    service: ConversationService


def build_conversations() -> ConversationsStack:
    """The API-side use-cases and the orchestrator-side inbound port, sharing
    one in-memory store — the same single-repository wiring the Composition
    Root builds for production."""
    repository = InMemoryConversationRepository()
    return ConversationsStack(
        repository=repository,
        use_cases=ConversationUseCases(
            start=StartConversation(repository),
            get=GetConversation(repository),
            list_by_agent=ListConversationsByAgent(repository),
            list_messages=ListMessages(repository),
            soft_delete=SoftDeleteConversation(repository),
        ),
        service=ConversationService(StartConversation(repository), AppendMessage(repository)),
    )
