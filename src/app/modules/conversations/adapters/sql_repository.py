"""SQL adapter for ``ConversationRepository`` (02-port-contracts §2;
01-data-model §2.4; ``migrations/versions/conversations/0001_conversations.py``).

Declares its own local Core ``Table``\\ s against a module-local ``MetaData``
(R9, 12-module-authoring-guide §3) — this module never imports another
module or ``app.infrastructure`` (import-linter contracts 4/6); the engine,
sessionmaker and RLS machinery it needs are built in
``infrastructure/persistence/`` and handed in by the Composition Root as a
plain callable, so this adapter never even imports ``app.infrastructure``.

Two-layer tenant isolation (DD-04) as in the ``media`` precedent: Layer 1
(RLS GUC) is set by the injected ``tenant_session`` provider before this
adapter's code runs; Layer 2 (``WHERE workspace_id = :ws``) is applied
explicitly in every method below, on both ``conversations`` and ``messages``.

``message_count`` (02-port-contracts §2, 06-domain-models §4) is NOT a
persisted column — ``get``/``list_by_agent`` hydrate it as a correlated
``COALESCE(MAX(seq), 0)`` scalar subquery over the conversation's own
messages, counting soft-deleted ones too (their ``seq`` is never reused,
INV-CV3). ``get`` returns a soft-deleted conversation as-is (no
``deleted_at`` filter): it is the DOMAIN's ``_guard_not_deleted`` that
rejects further mutation on it, not the adapter (``AppendMessage``/
``RenameConversation`` both ``get`` first and let the aggregate raise).

``list_messages`` reads that same child table back in ``seq`` order and
DOES filter ``deleted_at IS NULL``: a soft-deleted message is excluded from
retrieval while its ``seq`` stays reserved (INV-CV3), so a page may show a
gap — that is the invariant working, not a defect. Its keyset runs on
``seq`` rather than ``id`` (the ``list_by_agent`` choice): ``seq`` IS the
conversation's canonical order, it is unique per conversation, and
``uq_msg_seq(conversation_id, seq)`` makes both the filter and the ordering
index-backed. Its codec is ``encode_seq_cursor``/``decode_seq_cursor``
(``framework/pagination.py``, 6.3-أ) — a SECOND, typed pair beside the ``id``
one, so the keyset a cursor belongs to is visible in the call rather than
hidden behind a stringified int.

``get_message``/``save_message`` (BE-RAG-004) are that child's write path.
``get_message`` filters on the CONVERSATION id as well as the message id, so
the route's ownership check is a SQL predicate rather than a comparison a
caller must remember; it does NOT filter ``deleted_at``, so a second delete
finds the row and no-ops instead of 404-ing. ``save_message`` writes
``deleted_at`` and nothing else -- `06 §4` makes a message immutable except
for its soft-delete -- and deliberately does not touch the parent's
``version``: see the method for why bumping it would only manufacture
conflicts.

``append_message`` writes the new message row and bumps the parent
conversation's ``version`` in the SAME transaction (one ``tenant_session``
call): the concurrency guard for INV-CV1 (gap-free, non-duplicated ``seq``)
is the schema's own ``UNIQUE(conversation_id, seq)``, not a version
precondition on the conversation UPDATE -- two racers computing the same
``seq`` off a stale in-memory ``message_count`` collide there (``23505`` ->
``ConflictError``), and because both statements share one transaction, a
losing racer's message insert and its (never committed) conversation
version bump roll back together.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime

from sqlalchemy import (
    Column,
    ColumnElement,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    delete,
    func,
    insert,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ConflictError
from app.framework.pagination import (
    Page,
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
)
from app.framework.types import Json
from app.framework.types import Uuid as UuidStr
from app.modules.conversations.domain.entities import Conversation, Message, PinnedFile
from app.modules.conversations.domain.value_objects import (
    AgentKey,
    ConversationKind,
    MessageContent,
    MessageRole,
)

_metadata = MetaData()

# UUIDv7 identifiers round-trip as plain `str` (`as_uuid=False`, matching
# `app.framework.types.Uuid`); timestamps are always timezone-aware.
_uuid_col = Uuid(as_uuid=False)
_timestamptz = DateTime(timezone=True)

conversations = Table(
    "conversations",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("agent_key", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("title", Text, nullable=True),
    Column("model_route", Text, nullable=True),
    Column("created_by", _uuid_col, nullable=True),
    Column("created_at", _timestamptz, nullable=False),
    Column("updated_at", _timestamptz, nullable=False),
    Column("deleted_at", _timestamptz, nullable=True),
    Column("version", Integer, nullable=False),
    schema="conversations",
)

messages = Table(
    "messages",
    _metadata,
    Column("id", _uuid_col, primary_key=True),
    Column("conversation_id", _uuid_col, nullable=False),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("role", Text, nullable=False),
    Column("content", JSONB, nullable=False),
    Column("token_count", Integer, nullable=True),
    Column("seq", Integer, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    Column("deleted_at", _timestamptz, nullable=True),
    schema="conversations",
)

conversation_files = Table(
    "conversation_files",
    _metadata,
    Column("conversation_id", _uuid_col, primary_key=True),
    Column("file_id", _uuid_col, primary_key=True),
    Column("workspace_id", _uuid_col, nullable=False),
    Column("created_at", _timestamptz, nullable=False),
    schema="conversations",
)

# A request-scoped session-provider seam (structurally satisfies whatever
# ``TenantSessionFactory.__call__`` returns): the adapter depends only on this
# narrow shape, never on ``infrastructure.persistence`` directly.
TenantSessionProvider = Callable[[ExecutionContext], AbstractAsyncContextManager[AsyncSession]]


class SqlConversationRepository:
    """SQL ``ConversationRepository`` adapter (structural Protocol match — no
    inheritance, per this codebase's Protocol-based ports).

    Each method opens its own tenant-scoped transaction (one round trip per
    call, media precedent) — except ``append_message``, whose INSERT
    (message) + UPDATE (conversation version bump) share ONE transaction, so
    a losing racer's message insert and its conversation update either both
    commit or both roll back together.
    """

    def __init__(self, tenant_session: TenantSessionProvider) -> None:
        self._tenant_session = tenant_session

    async def get(self, ctx: ExecutionContext, conversation_id: UuidStr) -> Conversation | None:
        stmt = select(conversations, _message_count_column()).where(
            conversations.c.id == conversation_id,
            conversations.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_conversation(row)

    async def add(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id):
        # a forged/mismatched conversation.workspace_id is then rejected by
        # the RLS WITH CHECK clause rather than silently persisted under
        # ctx's tenant (media precedent).
        stmt = insert(conversations).values(
            id=conversation.id,
            workspace_id=conversation.workspace_id,
            agent_key=conversation.agent_key.value,
            kind=conversation.kind.value,
            title=conversation.title,
            model_route=conversation.model_route,
            created_by=conversation.created_by,
            created_at=conversation.created_at,
            updated_at=conversation.updated_at,
            deleted_at=conversation.deleted_at,
            version=conversation.version,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc

    async def save(self, ctx: ExecutionContext, conversation: Conversation) -> None:
        # Optimistic lock: only a row still at `conversation.version` is
        # updated. Used for rename / model pin / soft-delete
        # (02-port-contracts §2) -- ALL mutable fields are written regardless
        # of which mutation ran, mirroring `SqlWorkspaceRepository.save`
        # (workspace precedent). Writing the full set is what keeps a new
        # mutation from needing a new statement, and what makes forgetting to
        # add a column here a test failure rather than a silent no-op.
        stmt = (
            update(conversations)
            .where(
                conversations.c.id == conversation.id,
                conversations.c.workspace_id == ctx.workspace_id,
                conversations.c.version == conversation.version,
            )
            .values(
                title=conversation.title,
                model_route=conversation.model_route,
                deleted_at=conversation.deleted_at,
                version=conversations.c.version + 1,
            )
            .returning(conversations.c.version, conversations.c.updated_at)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(
                f"conversation {conversation.id} was modified concurrently (stale version)"
            )
        # `version`/`updated_at` are repository-owned (02-port-contracts §2)
        # -- write the fresh values back onto the aggregate, media precedent.
        conversation.version, conversation.updated_at = row

    async def list_by_agent(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[Conversation]:
        # Non-deleted only (`ListConversationsByAgent`'s own docstring) --
        # backed by the partial index `ix_conv_ws_agent` (01 §2.4).
        conditions = [
            conversations.c.workspace_id == ctx.workspace_id,
            conversations.c.agent_key == agent_key,
            conversations.c.deleted_at.is_(None),
        ]
        # NEWEST FIRST (`framework/pagination`, 6.3-ب): the most recently
        # opened thread is the one a client wants at the top of the list.
        # The message TRANSCRIPT below is the deliberate exception — it reads
        # forward by `seq`, because a conversation is a narrative.
        if cursor is not None:
            conditions.append(conversations.c.id < decode_id_cursor(cursor))
        stmt = (
            select(conversations, _message_count_column())
            .where(*conditions)
            .order_by(conversations.c.id.desc())
            .limit(limit + 1)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate(rows, limit, _hydrate_conversation, next_cursor_of=_id_cursor_of)

    async def append_message(self, ctx: ExecutionContext, message: Message) -> None:
        # The aggregate's OWN workspace_id is written (not ctx.workspace_id)
        # -- same forged-write guard as `add` (media precedent).
        insert_stmt = insert(messages).values(
            id=message.id,
            conversation_id=message.conversation_id,
            workspace_id=message.workspace_id,
            role=message.role.value,
            content=_content_to_json(message.content),
            token_count=message.token_count,
            seq=message.seq,
            created_at=message.created_at,
            deleted_at=message.deleted_at,
        )
        # Unconditional bump (no WHERE version=:expected): the concurrency
        # guard for INV-CV1 is `UNIQUE(conversation_id, seq)` on the INSERT
        # above, not a version precondition here (see class docstring).
        update_stmt = (
            update(conversations)
            .where(
                conversations.c.id == message.conversation_id,
                conversations.c.workspace_id == ctx.workspace_id,
            )
            .values(version=conversations.c.version + 1)
            .returning(conversations.c.version)
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(insert_stmt)
                row = (await session.execute(update_stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(
                f"conversation {message.conversation_id} was not found while appending a message"
            )

    async def get_message(
        self, ctx: ExecutionContext, conversation_id: UuidStr, message_id: UuidStr
    ) -> Message | None:
        # Both ids are in the WHERE clause, so a message paired with the wrong
        # thread is absent rather than reachable -- the route's ownership check
        # is this predicate, not a comparison the use-case has to remember.
        # NO `deleted_at IS NULL` filter, unlike `list_messages`: an
        # already-deleted row must still come back so the soft-delete stays
        # idempotent (the DOMAIN's `Message.soft_delete` no-ops on it), exactly
        # as `get` hands back a soft-deleted conversation.
        stmt = select(messages).where(
            messages.c.id == message_id,
            messages.c.conversation_id == conversation_id,
            messages.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return None if row is None else _hydrate_message(row)

    async def save_message(self, ctx: ExecutionContext, message: Message) -> None:
        # `deleted_at` ONLY: `06 §4` makes a message immutable except for its
        # soft-delete, so writing the full column set (the `save` above) would
        # let a mutation this table does not have re-persist content or `seq`.
        #
        # No optimistic lock and no parent version bump. There is no `version`
        # on this table, and the mutation is idempotent-by-value -- two racers
        # both write a timestamp onto a row that ends up deleted either way.
        # Bumping `conversations.version` would be worse than useless: nothing
        # on the parent row changes (`message_count` is MAX(seq), which a
        # soft-delete deliberately does not move, INV-CV3), so it would only
        # make a concurrent rename fail with a conflict it did not have.
        stmt = (
            update(messages)
            .where(
                messages.c.id == message.id,
                messages.c.workspace_id == ctx.workspace_id,
            )
            .values(deleted_at=message.deleted_at)
            .returning(messages.c.id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"message {message.id} disappeared before it could be deleted")

    async def list_messages(
        self, ctx: ExecutionContext, conversation_id: UuidStr, *, limit: int, cursor: str | None
    ) -> Page[Message]:
        # Soft-deleted messages are excluded from retrieval (INV-CV3); their
        # `seq` stays reserved, so a page can legitimately show a gap.
        conditions = [
            messages.c.conversation_id == conversation_id,
            messages.c.workspace_id == ctx.workspace_id,
            messages.c.deleted_at.is_(None),
        ]
        if cursor is not None:
            conditions.append(messages.c.seq > decode_seq_cursor(cursor))
        stmt = select(messages).where(*conditions).order_by(messages.c.seq).limit(limit + 1)
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return _paginate(rows, limit, _hydrate_message, next_cursor_of=_seq_cursor_of)

    async def list_files(self, ctx: ExecutionContext, conversation_id: UuidStr) -> list[PinnedFile]:
        # Ordered by `created_at` so the UI shows pins in the order they were
        # made; `file_id` breaks the tie, because two pins made inside the same
        # transaction share a timestamp and an unordered list would reshuffle
        # itself between reads.
        stmt = (
            select(conversation_files)
            .where(
                conversation_files.c.conversation_id == conversation_id,
                conversation_files.c.workspace_id == ctx.workspace_id,
            )
            .order_by(conversation_files.c.created_at, conversation_files.c.file_id)
        )
        try:
            async with self._tenant_session(ctx) as session:
                rows = (await session.execute(stmt)).mappings().all()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        return [_hydrate_pinned_file(row) for row in rows]

    async def pin_file(
        self, ctx: ExecutionContext, conversation_id: UuidStr, file_id: UuidStr, now: datetime
    ) -> PinnedFile:
        # INSERT ... SELECT, not INSERT ... VALUES, and that is Layer 2 (DD-04)
        # applied to the PARENT rather than only to the row being written.
        # `fk_convfile_conv` cannot do it: Postgres runs a foreign-key check as
        # a system trigger with RLS bypassed, so the FK happily finds a
        # conversation this tenant cannot see, and the policy's `WITH CHECK`
        # passes because the new row carries the CALLER's `workspace_id`. The
        # result would be a pin in tenant A's partition naming tenant B's
        # thread -- invisible to B, and unreachable through the use-case (which
        # loads the conversation first and 404s), but a row that should not
        # exist. Selecting the parent under the tenant filter means the INSERT
        # simply produces no row instead.
        source = (
            select(
                literal(conversation_id, _uuid_col),
                literal(file_id, _uuid_col),
                literal(ctx.workspace_id, _uuid_col),
                literal(now, _timestamptz),
            )
            .select_from(conversations)
            .where(
                conversations.c.id == conversation_id,
                conversations.c.workspace_id == ctx.workspace_id,
            )
        )
        # `ON CONFLICT DO NOTHING` + `RETURNING` returns NOTHING when the row
        # already existed, which is the whole reason for the second SELECT
        # below rather than a `DO UPDATE`: re-pinning must hand back the
        # ORIGINAL `created_at`, and `DO UPDATE SET created_at = now` would
        # silently rewrite it -- turning an idempotent call into a mutation
        # that reorders the caller's list.
        stmt = (
            pg_insert(conversation_files)
            .from_select(
                ["conversation_id", "file_id", "workspace_id", "created_at"],
                source,
            )
            .on_conflict_do_nothing(index_elements=["conversation_id", "file_id"])
            .returning(*conversation_files.c)
        )
        existing = select(conversation_files).where(
            conversation_files.c.conversation_id == conversation_id,
            conversation_files.c.file_id == file_id,
            conversation_files.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                row = (await session.execute(stmt)).mappings().first()
                if row is None:
                    # Two ways to get here, and the SELECT tells them apart:
                    # the pin already existed (return it -- idempotency), or
                    # the parent was not visible and the INSERT selected no
                    # source row at all (nothing to return -- raise).
                    row = (await session.execute(existing)).mappings().first()
        except DBAPIError as exc:
            raise _translate(exc) from exc
        if row is None:
            raise ConflictError(f"conversation {conversation_id} is not available to pin files to")
        return _hydrate_pinned_file(row)

    async def unpin_file(
        self, ctx: ExecutionContext, conversation_id: UuidStr, file_id: UuidStr
    ) -> None:
        # No `RETURNING` check and no error on zero rows: "this file is not
        # pinned" is the state the caller asked for, and a pin that was never
        # there is indistinguishable from one another request just removed.
        stmt = delete(conversation_files).where(
            conversation_files.c.conversation_id == conversation_id,
            conversation_files.c.file_id == file_id,
            conversation_files.c.workspace_id == ctx.workspace_id,
        )
        try:
            async with self._tenant_session(ctx) as session:
                await session.execute(stmt)
        except DBAPIError as exc:
            raise _translate(exc) from exc


def _message_count_column() -> ColumnElement[int]:
    """A correlated scalar subquery: ``COALESCE(MAX(seq), 0)`` over a
    conversation's own messages (including soft-deleted ones -- their ``seq``
    is never reused, INV-CV3), labeled to match ``Conversation.message_count``
    (not a persisted column, 01-data-model §2.4)."""
    return (
        select(func.coalesce(func.max(messages.c.seq), 0))
        .where(
            messages.c.conversation_id == conversations.c.id,
            messages.c.workspace_id == conversations.c.workspace_id,
        )
        .scalar_subquery()
        .label("message_count")
    )


def _content_to_json(content: MessageContent) -> Json:
    return {"text": content.text, "attachments": list(content.attachments)}


def _id_cursor_of(row: RowMapping) -> str:
    return encode_id_cursor(str(row["id"]))


def _seq_cursor_of(row: RowMapping) -> str:
    return encode_seq_cursor(row["seq"])


def _paginate[T](
    rows: Sequence[RowMapping],
    limit: int,
    hydrate: Callable[[RowMapping], T],
    *,
    next_cursor_of: Callable[[RowMapping], str],
) -> Page[T]:
    # The KEYSET is named at each call site rather than passed as a column
    # string: this module is the one place where two keysets meet, and the
    # minter is what has to agree with the `WHERE` predicate above it.
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = next_cursor_of(page_rows[-1]) if has_more and page_rows else None
    return Page(data=[hydrate(row) for row in page_rows], next_cursor=next_cursor, limit=limit)


def _hydrate_conversation(row: RowMapping) -> Conversation:
    return Conversation(
        id=row["id"],
        workspace_id=row["workspace_id"],
        agent_key=AgentKey(row["agent_key"]),
        kind=ConversationKind(row["kind"]),
        title=row["title"],
        model_route=row["model_route"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
        version=row["version"],
        message_count=row["message_count"],
    )


def _hydrate_message(row: RowMapping) -> Message:
    # `content` round-trips through the exact shape `_content_to_json` writes,
    # so the value object is rebuilt (and re-validated) rather than trusted.
    content: Json = row["content"]
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        workspace_id=row["workspace_id"],
        role=MessageRole(row["role"]),
        content=MessageContent(text=content["text"], attachments=tuple(content["attachments"])),
        token_count=row["token_count"],
        seq=row["seq"],
        created_at=row["created_at"],
        deleted_at=row["deleted_at"],
    )


def _hydrate_pinned_file(row: RowMapping) -> PinnedFile:
    return PinnedFile(
        conversation_id=row["conversation_id"],
        file_id=row["file_id"],
        workspace_id=row["workspace_id"],
        created_at=row["created_at"],
    )


def _translate(exc: DBAPIError) -> AppError:
    """Map a driver-level failure onto the shared framework error hierarchy
    (03-api-spec §4) -- ``sqlalchemy``/``asyncpg`` exception types never
    escape this adapter (R6 media precedent).

    ``23505`` (``unique_violation``) -- lost a uniqueness race: a duplicate
    ``id`` on ``add``, or (the common case) ``uq_msg_seq`` on
    ``append_message`` -- two racers computing the same ``seq`` off a stale
    in-memory ``message_count`` -- ``ConflictError`` (409, ``common.conflict``).
    ``42501`` (``insufficient_privilege``) -- the RLS ``WITH CHECK`` clause
    rejected the write (e.g. a forged cross-tenant ``workspace_id`` on
    ``add``/``append_message``) -- an internal/500-class error
    (``common.internal``): a well-behaved caller can never trigger this, so
    it is not a normal 4xx. Anything else is an unexpected database failure,
    folded into the same 500-class error rather than leaking the driver
    exception.
    """
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "23505":
        return ConflictError("conversation write lost a uniqueness race")
    if sqlstate == "42501":
        return AppError("conversation write rejected by row-level security", code="common.internal")
    return AppError(
        "unexpected database error while persisting a conversation", code="common.internal"
    )
