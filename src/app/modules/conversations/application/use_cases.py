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
from app.framework.providers.catalog import ModelCatalog
from app.framework.types import Uuid
from app.modules.conversations.domain.entities import Conversation, Message, PinnedFile
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
from app.modules.conversations.ports.files import ReadableFiles
from app.modules.conversations.ports.inbound import AppendedMessage, StartedConversation
from app.modules.conversations.ports.repository import ConversationRepository

# How many files one thread may pin into its retrieval scope. See
# ``PinConversationFile`` for why this is a scope bound rather than a quota,
# and why it is checked without a lock.
MAX_PINNED_FILES = 50


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


class PinConversationModel:
    """Pin (or unpin) which configured model route answers one thread.

    **The route is validated against the LIVE catalogue, not a stored list.**
    D-16 keeps the provider/model choice in configuration, so the set of valid
    keys is whatever the operator has routed at this moment — no column, enum
    or CHECK constraint can hold it. Checking here means an unknown key is a
    422 naming what is actually available, instead of a row that resolves to
    nothing and fails as a provider error on the thread's next turn.

    ``None`` unpins, and unpinning is deliberately NOT validated: a client must
    always be able to undo a pin, including one whose route the operator has
    since retired — otherwise a thread could be stranded on a key it can no
    longer name.

    An unavailable route (configured, but no credential resolves for this
    caller) is accepted. Availability is a property of the caller and the
    moment, not of the route: refusing the pin would make "add a key later"
    impossible, and the resolution path already answers the missing key with
    the credentials module's own error when the turn actually runs.

    No event. `04 §5` lists this module's events as internal and in-memory with
    no stream behind them, and inventing a catalog entry for a state change
    nothing subscribes to would be contract surface with nothing under it —
    the same reasoning ``ConversationService`` records for dropping the others.
    """

    def __init__(self, conversations: ConversationRepository, catalog: ModelCatalog) -> None:
        self._conversations = conversations
        self._catalog = catalog

    async def execute(
        self, ctx: ExecutionContext, conversation_id: Uuid, route: str | None
    ) -> tuple[Conversation, tuple[ConversationEvent, ...]]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        if route is not None:
            await self._require_routable(ctx, route)
        try:
            conversation.pin_model_route(route, utc_now())
        except ConversationDeletedError as exc:
            raise ConflictError(str(exc)) from exc
        await self._conversations.save(ctx, conversation)
        return conversation, ()

    async def _require_routable(self, ctx: ExecutionContext, route: str) -> None:
        """422 unless the key names a configured route.

        The blank check is separate from the lookup so the message says what is
        wrong: an empty string is a caller bug, while a non-empty unknown key
        is a stale choice, and the caller can act on the difference.
        """
        if not route.strip():
            raise ValidationError("model_route must be a non-empty string, or null to unpin")
        known = [choice.capability for choice in await self._catalog.list_llm_models(ctx)]
        if route not in known:
            raise ValidationError(f"unknown model route {route!r} (configured: {sorted(known)})")


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


class SoftDeleteMessage:
    """Soft-delete one turn of a thread (BE-RAG-004). Idempotent.

    **The conversation is loaded first, and not only to find the message.** It
    is what makes the two failures distinguishable: an unknown thread is a 404
    from here, while a soft-deleted one is the 409 every other WRITE in this
    module answers with (``RenameConversation``, ``PinConversationModel``) —
    the thread is refusing the write, not denying that it exists. Reaching
    straight for the message would have collapsed both into "no such message".

    The ownership check is the repository's ``(conversation_id, message_id)``
    predicate, not a comparison performed here: a message quoted against the
    wrong thread is simply not found, so there is no path on which a mismatch
    is noticed *after* something has already been mutated.

    A second delete returns the message unchanged — ``Message.soft_delete``
    no-ops on an already-deleted row — so a client retrying a lost 204 gets a
    204, matching ``SoftDeleteConversation``. The repository is still called
    on that path: skipping the write would make the use-case's answer depend
    on state the caller cannot see, and the UPDATE is a no-op by value anyway.

    No event, for ``PinConversationModel``'s reason: `04 §5` keeps this
    module's events internal and in-memory, so there is nothing subscribed to
    lose.
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(
        self, ctx: ExecutionContext, conversation_id: Uuid, message_id: Uuid
    ) -> tuple[Message, tuple[ConversationEvent, ...]]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        if conversation.deleted_at is not None:
            raise ConflictError("conversation is deleted")
        message = await self._conversations.get_message(ctx, conversation_id, message_id)
        if message is None:
            raise NotFoundError("message not found")
        message.soft_delete(utc_now())
        await self._conversations.save_message(ctx, message)
        return message, ()


class ListConversationFiles:
    """The files this thread's retrieval is pinned to (BE-RAG-005).

    A soft-deleted thread still answers here, unlike every WRITE in this
    module: reads in this module are permitted on a deleted thread
    (``GetConversation``, ``ListMessages``), and a client that can still open
    the transcript must be able to see what it was answering from.
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, ctx: ExecutionContext, conversation_id: Uuid) -> list[PinnedFile]:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        return await self._conversations.list_files(ctx, conversation_id)


class PinConversationFile:
    """Pin one workspace file into this thread's retrieval scope (BE-RAG-005).

    **The file is checked before the pin is stored, and it is checked against
    the ``files`` module rather than against this table.** A pin whose file
    does not exist — or is quarantined, or half-uploaded — is not a harmless
    dangling row: it is a scope entry retrieval can never match, so the thread
    would answer from *less* than the caller asked for while the UI showed the
    file as pinned. Refusing it as a 422 makes the failure visible at the
    moment the caller can still fix it. ``PinConversationModel``'s check
    against the live routing table is the same idea against a different
    authority.

    The check is deliberately NOT repeated on read. A file deleted after it was
    pinned leaves the pin standing (there is no cross-schema FK to cascade,
    01 §2.4), and retrieval finds nothing for it — the same outcome as a file
    that was never indexed. Re-validating on every read would turn another
    module's deletion into this module's error, on a path the caller did not
    ask to write.

    Idempotent by primary key, not by read-then-write: the repository upserts
    and returns the stored row, so a double-click yields 201 with the ORIGINAL
    ``created_at`` instead of a second pin or a 409.

    ``MAX_PINNED_FILES`` is a scope bound, not a quota. Retrieval fuses a
    bounded number of chunks (``RetrieveContext``'s ``_MAX_K`` is 50), so a
    scope far wider than that stops narrowing anything while making the
    ``document_id`` filter — and the page this endpoint returns unpaginated —
    grow without limit. The check is a race-tolerant pre-check: two concurrent
    pins can both pass it and leave the set one over, which is accepted here
    because the alternative (locking the thread to add a pin) buys a bound
    nothing depends on being exact.

    No event, for ``PinConversationModel``'s reason (`04 §5`).
    """

    def __init__(self, conversations: ConversationRepository, files: ReadableFiles) -> None:
        self._conversations = conversations
        self._files = files

    async def execute(
        self, ctx: ExecutionContext, conversation_id: Uuid, file_id: Uuid
    ) -> PinnedFile:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        if conversation.deleted_at is not None:
            raise ConflictError("conversation is deleted")
        if not file_id.strip():
            raise ValidationError("file_id must be a non-empty string")
        pinned = await self._conversations.list_files(ctx, conversation_id)
        already = any(pin.file_id == file_id for pin in pinned)
        if not already and len(pinned) >= MAX_PINNED_FILES:
            raise ValidationError(
                f"a conversation may pin at most {MAX_PINNED_FILES} files (currently {len(pinned)})"
            )
        if await self._files.get_readable(ctx, file_id) is None:
            raise ValidationError(f"file {file_id!r} does not exist or is not readable")
        return await self._conversations.pin_file(ctx, conversation_id, file_id, utc_now())


class UnpinConversationFile:
    """Drop one pin, widening the thread's retrieval scope back out.

    Idempotent, and idempotent all the way down: the repository deletes without
    checking that anything was there, so unpinning twice is one 204 and then
    another. A missing pin is the state the caller asked for.

    A soft-deleted thread is a 409, not a 404 — this is a WRITE, and it answers
    like the other writes here. That it removes rather than adds does not make
    it a read.
    """

    def __init__(self, conversations: ConversationRepository) -> None:
        self._conversations = conversations

    async def execute(self, ctx: ExecutionContext, conversation_id: Uuid, file_id: Uuid) -> None:
        conversation = await self._conversations.get(ctx, conversation_id)
        if conversation is None:
            raise NotFoundError("conversation not found")
        if conversation.deleted_at is not None:
            raise ConflictError("conversation is deleted")
        await self._conversations.unpin_file(ctx, conversation_id, file_id)


@dataclass(frozen=True, slots=True)
class ConversationUseCases:
    """The conversations use-cases the API layer drives (6.1-ج-2).

    `10-code-standards §3` makes the API layer thin — "DTO validation,
    authorization, **call a Use-Case**, map to a response" — and the layering
    contract allows ``app.api → app.modules``, so the router calls these
    directly rather than through a second inbound port invented for it. This
    bundle exists only so the Composition Root hands ``ApiServices`` ONE field
    instead of six, mirroring how ``OrchestratorDependencies`` groups the
    orchestrator's own collaborators.

    ``rename`` joined the bundle when `03 §1` grew ``PATCH /conversations/{id}``
    — the route came first and the wiring followed it, which is the only order
    that keeps the contract the source of truth rather than a description of
    whatever happens to be reachable.
    """

    start: StartConversation
    get: GetConversation
    list_by_agent: ListConversationsByAgent
    list_messages: ListMessages
    rename: RenameConversation
    # `PUT /conversations/{id}/model` (BE-RAG-003). A SEPARATE face from
    # `rename`, matching the separate route: folding the pin into the rename
    # DTO would have made `title` and `model_route` share one required-present
    # body, so a client changing either would have had to restate the other.
    pin_model: PinConversationModel
    soft_delete: SoftDeleteConversation
    # `DELETE /conversations/{id}/messages/{message_id}` (BE-RAG-004). Named
    # for what it deletes rather than sharing `soft_delete`: the two take
    # different arguments and answer differently on a deleted thread, so one
    # field for both would have hidden a branch inside the bundle.
    soft_delete_message: SoftDeleteMessage
    # `…/files` (BE-RAG-005) — three fields, not one "files" facade, for the
    # reason `soft_delete_message` is separate: they differ in permission
    # (read vs write), in what a soft-deleted thread does (answers vs 409),
    # and in their arguments, so a single field would have hidden three
    # branches behind one name.
    list_files: ListConversationFiles
    pin_file: PinConversationFile
    unpin_file: UnpinConversationFile


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

    def __init__(
        self,
        start: StartConversation,
        append: AppendMessage,
        get: GetConversation,
        list_files: ListConversationFiles,
    ) -> None:
        self._start = start
        self._append = append
        self._get = get
        self._list_files = list_files

    async def routed_model(self, ctx: ExecutionContext, conversation_id: Uuid) -> str | None:
        """The thread's pinned route, or ``None`` when it is not pinned.

        A str, not the aggregate — the port's handle discipline: the caller
        needs the routing key and has no business holding version or
        soft-delete state.

        A thread that is missing OR soft-deleted answers ``None`` rather than
        raising. The caller reads this ahead of the write that would fail on
        it anyway, and letting a read-ahead own the failure would move where an
        unknown thread (404) and a deleted one (409) are reported — two
        different answers this method cannot tell apart, and both already
        correct where they are raised today.
        """
        try:
            conversation = await self._get.execute(ctx, conversation_id)
        except NotFoundError:
            return None
        return conversation.model_route

    async def pinned_files(self, ctx: ExecutionContext, conversation_id: Uuid) -> tuple[Uuid, ...]:
        """The thread's retrieval scope as bare file ids, or ``()``.

        ``PinnedFile`` carries ``workspace_id`` and ``created_at`` that the
        caller has no use for and no business holding — the same handle
        discipline ``routed_model`` follows, so what crosses is the tuple of
        ids and nothing else.

        Missing thread ⇒ ``()``, for ``routed_model``'s reason. A soft-deleted
        one answers with its real pins rather than ``()``: ``ListConversationFiles``
        reads through a deleted thread deliberately, and quietly emptying the
        scope here would change what a thread retrieves from as a side effect
        of its deletion, on the one path where nothing should change at all.
        """
        try:
            pinned = await self._list_files.execute(ctx, conversation_id)
        except NotFoundError:
            return ()
        return tuple(pin.file_id for pin in pinned)

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
