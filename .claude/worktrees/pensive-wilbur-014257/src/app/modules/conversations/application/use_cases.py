"""Conversations use-cases (06-domain-models §4).

Thin application services that coordinate the pure domain over an injected
``ConversationRepository``. They own identity/time (framework ``new_uuid7`` /
``utc_now``) and translate domain-rule violations into the shared framework
error hierarchy at this boundary — the domain itself stays framework-free.
Domain events are returned to the caller for later dispatch (event-bus /
outbox wiring lands in Phase 5); nothing here performs I/O beyond the
injected repository.

``ConversationService`` at the bottom is the inbound-port façade (4.7-e-1) —
the ``MediaRequestService`` precedent, with one deliberate difference: it
DROPS the returned events instead of writing them to the Outbox, and that is
contract-correct rather than debt. `04 §5` marks only the starred modules
(`files`·`knowledge`·`media`·`memory`) as promoted to global streams;
``ConversationStarted``/``MessageAppended`` are listed there as **internal,
in-memory events that never cross a stream**, so there is no consumer to lose
and no outbox row to write. Compare `media`, where dropping the event WAS the
bug 4.7-d-2 fixed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.conversations.domain.entities import Conversation, Message
from app.modules.conversations.domain.errors import ConversationDeletedError, ConversationError
from app.modules.conversations.domain.events import (
    ConversationDeleted,
    ConversationEvent,
    ConversationRenamed,
    ConversationStarted,
    MessageAppended,
)
from app.modules.conversations.domain.value_objects import (
    AgentKey,
    ConversationKind,
    MessageContent,
    MessageRole,
)
from app.modules.conversations.ports.inbound import AppendedMessage, StartedConversation
from app.modules.conversations.ports.repository import ConversationRepository


class StartConversation:
    """Start a new conversation thread for an agent (or workflow)."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: ConversationKind = ConversationKind.AGENT,
        title: str | None = None,
    ) -> tuple[Conversation, tuple[ConversationEvent, ...]]:
        try:
            key = AgentKey(agent_key)
        except ConversationError as exc:
            raise ValidationError(str(exc)) from exc

        now = utc_now()
        conversation = Conversation(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            agent_key=key,
            kind=kind,
            title=title,
            created_by=ctx.user_id,
            created_at=now,
            updated_at=now,
            deleted_at=None,
            version=1,
            message_count=0,
        )
        await self._conversations.add(ctx, conversation)
        event = ConversationStarted(conversation.id, ctx.workspace_id, key.value, kind.value, now)
        return conversation, (event,)


class AppendMessage:
    """Append a message to an existing, non-deleted conversation (INV-CV1/CV3)."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
    ) -> tuple[Message, tuple[ConversationEvent, ...]]:
        try:
            role_enum = MessageRole(role)
        except ValueError as exc:
            raise ValidationError(f"invalid message role: {role!r}") from exc
        try:
            content = MessageContent(text=text, attachments=attachments)
        except ConversationError as exc:
            raise ValidationError(str(exc)) from exc

        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")

        now = utc_now()
        try:
            message = conversation.append_message(new_uuid7(), role_enum, content, token_count, now)
        except ConversationDeletedError as exc:
            raise ConflictError(str(exc)) from exc

        await self._conversations.append_message(ctx, message)
        event = MessageAppended(message.id, conversation.id, role_enum.value, message.seq, now)
        return message, (event,)


class ListConversationsByAgent:
    """List a workspace's (non-deleted) conversations threaded under one agent."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None = None
    ) -> Page[Conversation]:
        try:
            key = AgentKey(agent_key)
        except ConversationError as exc:
            raise ValidationError(str(exc)) from exc
        return await self._conversations.list_by_agent(ctx, key.value, limit=limit, cursor=cursor)


class GetConversation:
    """Load one conversation for READING.

    A soft-deleted thread is absent here for the same reason it is absent from
    ``ListMessages``/``list_by_agent``: the caller deleted it, so the truthful
    answer is 404. Writes keep the domain's 409 (see ``ListMessages``).
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, ctx: ExecutionContext, conversation_id: Uuid) -> Conversation:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError("conversation not found")
        return conversation


class ListMessages:
    """List one conversation's messages in ``seq`` order (INV-CV1).

    **Why it reads the conversation first.** Handing the repository an id it
    has never seen returns an empty page, which on the wire is
    indistinguishable from "this thread has no messages yet" — and, worse,
    identical for another tenant's conversation id. Loading the aggregate
    turns both into an honest 404.

    A soft-deleted thread is treated as ABSENT here, matching how
    ``list_by_agent`` already hides it. The asymmetry with ``AppendMessage``/
    ``RenameConversation`` — which surface the domain's deleted-guard as a 409
    — is deliberate: for a WRITE the caller is told the thread exists but can
    no longer change (``ConversationDeletedError``), while for a READ the only
    truthful answer about a deleted thread is that it is gone.
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> Page[Message]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None or conversation.deleted_at is not None:
            raise NotFoundError("conversation not found")
        return await self._conversations.list_messages(
            ctx, conversation_id, limit=limit, cursor=cursor
        )


class RenameConversation:
    """Rename a conversation's title (an owner/admin/member action; authorization
    is the caller's)."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self, ctx: ExecutionContext, conversation_id: Uuid, new_title: str | None
    ) -> tuple[Conversation, tuple[ConversationEvent, ...]]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        try:
            conversation.rename(new_title, utc_now())
        except ConversationDeletedError as exc:
            raise ConflictError(str(exc)) from exc
        await self._conversations.save(ctx, conversation)
        event = ConversationRenamed(conversation.id, conversation.updated_at)
        return conversation, (event,)


class SoftDeleteConversation:
    """Soft-delete a conversation. Idempotent — deleting twice emits no new event."""

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self, ctx: ExecutionContext, conversation_id: Uuid
    ) -> tuple[Conversation, tuple[ConversationEvent, ...]]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        already_deleted = conversation.deleted_at is not None
        conversation.soft_delete(utc_now())
        await self._conversations.save(ctx, conversation)
        if already_deleted:
            return conversation, ()
        event = ConversationDeleted(
            conversation.id, conversation.workspace_id, conversation.updated_at
        )
        return conversation, (event,)


@dataclass(frozen=True, slots=True)
class ConversationUseCases:
    """The conversations use-cases the API layer drives (6.1-ج-2).

    `10-code-standards §3` makes the API layer thin — "DTO validation,
    authorization, **call a Use-Case**, map to a response" — and the layering
    contract allows ``app.api → app.modules``, so the router calls these
    directly rather than through a second inbound port invented for it. This
    bundle exists only so the Composition Root hands ``ApiServices`` ONE field
    instead of five, mirroring how ``OrchestratorDependencies`` groups the
    orchestrator's own collaborators.

    ``rename`` is deliberately absent: `03 §1` gives Conversations
    GET·POST·GET·DELETE·GET/POST-messages and no rename route, so wiring
    ``RenameConversation`` here would expose a capability the API contract
    does not have.
    """

    start: StartConversation
    get: GetConversation
    list_by_agent: ListConversationsByAgent
    list_messages: ListMessages
    soft_delete: SoftDeleteConversation


class ConversationService:
    """The ``ConversationThreads`` inbound port over ``StartConversation`` +
    ``AppendMessage`` (4.7-e-1; ``append`` added 6.1-ج-3).

    Its whole job is to keep the module's vocabulary inside the module: the
    caller passes ``kind``/``role`` as strings, this translates them to the
    domain enums, and what comes back are the ``StartedConversation`` /
    ``AppendedMessage`` handles rather than the aggregate.

    **The invalid-kind branch is the reason this is not a one-liner.**
    ``ConversationKind(kind)`` raises a bare ``ValueError`` — a 500 at the API
    edge, for what is plainly a caller mistake. Converting it here makes it a
    422, matching how ``StartConversation`` already treats a malformed
    ``agent_key``: translation to the framework hierarchy happens at the
    application boundary, never in the domain. (``AppendMessage`` already
    translates an invalid ``role`` itself, so ``append`` adds no branch of its
    own — it maps the returned entity onto the port's handle.)
    """

    def __init__(self, start: StartConversation, append: AppendMessage) -> None:
        self._start = start
        self._append = append

    async def append(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
    ) -> AppendedMessage:
        message, _events = await self._append.execute(
            ctx,
            conversation_id,
            role=role,
            text=text,
            attachments=attachments,
            token_count=token_count,
        )
        return AppendedMessage(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role.value,
            text=message.content.text,
            attachments=message.content.attachments,
            token_count=message.token_count,
            seq=message.seq,
            created_at=message.created_at,
        )

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: str,
        title: str | None = None,
    ) -> StartedConversation:
        try:
            kind_enum = ConversationKind(kind)
        except ValueError as exc:
            raise ValidationError(f"invalid conversation kind: {kind!r}") from exc
        conversation, _events = await self._start.execute(
            ctx, agent_key=agent_key, kind=kind_enum, title=title
        )
        return StartedConversation(
            id=conversation.id,
            agent_key=conversation.agent_key.value,
            kind=conversation.kind.value,
        )
