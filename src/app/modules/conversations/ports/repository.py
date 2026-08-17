"""Conversations persistence port (02-port-contracts §2, 06-domain-models §4).

Outbound repository for the ``Conversation`` aggregate and its ``Message``
child entity. Every tenant-scoped method takes ``ExecutionContext`` first so
the SQL adapter can apply the RLS guard (``SET LOCAL app.workspace_id``) and
the ``WHERE workspace_id`` filter (DD-04).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.conversations.domain.entities import Conversation, Message, PinnedFile


class ConversationRepository(Protocol):
    """Tenant-scoped persistence for conversations and their messages.

    ``save`` uses an optimistic lock on ``version`` (used for rename /
    soft-delete) — a stale write surfaces as a conflict at the adapter. It
    persists every MUTABLE field, which since the spaces plan's step 7 makes
    one omission load-bearing: ``add`` writes the aggregate's ``space_id`` and
    ``save`` deliberately does not, so a thread cannot be moved between spaces
    (the ``files`` decision 3 applied to the axis's other owner) — and moving
    one would re-point the retrieval scope its past answers came from.
    ``append_message`` persists the message and bumps ``Conversation.version``
    under that same optimistic lock, with ``UNIQUE(conversation_id, seq)`` at
    the schema level guaranteeing a gap-free, non-duplicated ``seq`` (INV-CV1).

    ``list_messages`` (6.1-ج-1) is the read side of that append-only child:
    one conversation's messages in ``seq`` order, soft-deleted ones excluded
    (INV-CV3). It is an ADDITION to the port sketch in `02 §2` — which lists
    only the four methods the modelling phase needed — made for the endpoint
    the API spec has always carried (`03 §1`, ``listMessages``) and which had
    no persistence method behind it, exactly as ``save`` was added for
    rename/soft-delete.

    ``get_message``/``save_message`` (BE-RAG-004) are the write side of that
    same child. ``get_message`` takes the CONVERSATION id as well as the
    message id — a message is a child entity, so "which thread" is part of its
    identity here, and a pair that does not match is simply absent rather than
    a row the caller may act on. ``save_message`` writes ``deleted_at`` and
    nothing else, because that is the only field ``Message`` can change
    (`06 §4`: append-only, "immutable except for soft-delete"); a general
    message ``save`` would be a door onto an append-only table.

    ``list_files``/``pin_file``/``unpin_file`` (BE-RAG-005) are the thread's
    retrieval scope. All three are unpaginated and take no cursor: the set is
    small and bounded by ``MAX_PINNED_FILES``, and a cursor would be a page
    protocol over something that never has a second page. ``pin_file`` is an
    UPSERT that returns the stored row, so re-pinning yields the ORIGINAL
    ``created_at`` rather than a fresh one — idempotency comes from the
    composite primary key, not from a read-then-write the second caller could
    interleave with. ``unpin_file`` returns nothing and treats a missing pin as
    success, for the same reason a soft delete does: the caller asked for a
    state, and the state already holds.
    """

    async def get(self, ctx: ExecutionContext, conversation_id: Uuid) -> Conversation | None: ...

    async def add(self, ctx: ExecutionContext, conversation: Conversation) -> None: ...

    async def save(self, ctx: ExecutionContext, conversation: Conversation) -> None: ...

    async def list_by_agent(
        self,
        ctx: ExecutionContext,
        agent_key: str,
        *,
        space_id: Uuid | None,
        limit: int,
        cursor: str | None,
    ) -> Page[Conversation]:
        """One agent's active threads, newest first.

        ``space_id`` narrows the page to one space's threads; ``None`` returns
        the workspace's. ``?space_id=`` became mandatory on ``GET
        /conversations`` at step 12, so the router always names one now.
        ``None`` still means "all spaces", NOT "threads with no space" — a
        distinction the adapter enforces by adding a condition rather than by
        comparing against ``NULL``.

        It is a REQUIRED keyword with no default, matching
        ``FileRepository.list``: "all spaces" is then a decision written at
        the call site rather than one a caller falls into by omission.
        """
        ...

    async def counts_by_space(
        self, ctx: ExecutionContext, space_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, int]:
        """How many ACTIVE threads each of ``space_ids`` holds — the third
        number ``GET /api/v1/spaces`` publishes (§3.7, step 12).

        Plural for the reason ``FileRepository.totals_by_space`` is plural: it
        serves a page of spaces, and one query per row would be twenty round
        trips for one column.

        Soft-deleted threads are excluded, matching ``list_by_agent``: the
        count beside a space's name must be the number of threads a user can
        actually open from it, not the number of rows the table happens to
        hold.

        A space with no threads is ABSENT from the mapping rather than present
        with ``0`` — ``GROUP BY`` returns the groups that exist, and this
        module cannot vouch that an id it never matched names a real space.
        An empty ``space_ids`` returns an empty mapping without a query.
        """
        ...

    async def append_message(self, ctx: ExecutionContext, message: Message) -> None: ...

    async def list_messages(
        self, ctx: ExecutionContext, conversation_id: Uuid, *, limit: int, cursor: str | None
    ) -> Page[Message]: ...

    async def get_message(
        self, ctx: ExecutionContext, conversation_id: Uuid, message_id: Uuid
    ) -> Message | None: ...

    async def save_message(self, ctx: ExecutionContext, message: Message) -> None: ...

    async def list_files(
        self, ctx: ExecutionContext, conversation_id: Uuid
    ) -> list[PinnedFile]: ...

    async def pin_file(
        self, ctx: ExecutionContext, conversation_id: Uuid, file_id: Uuid, now: datetime
    ) -> PinnedFile: ...

    async def unpin_file(
        self, ctx: ExecutionContext, conversation_id: Uuid, file_id: Uuid
    ) -> None: ...

    async def purge_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        """HARD-delete one space's threads with their messages and pins;
        returns how many CONVERSATIONS went (``docs/spaces-backend-plan.md``
        §3.6 step 7, the last step of the cascade).

        The module's only hard delete. Everything else here soft-deletes,
        because a thread is a record a user may want back; a space's deletion
        is the one act that says they do not (decision 2 — cascade, and no
        undo).

        Soft-deleted threads and messages go too: ``deleted_at`` marks a row
        the user stopped wanting, and this deletes the space that owned it.
        Filtering them out would leave the tombstones of a space that no longer
        exists, invisible to every listing and impossible to reach.

        ``conversation_files`` has no ``space_id`` of its own — a pin is a
        narrowing INSIDE a thread (``conversations/0004_conversation_space``),
        so its space is its thread's — and it is deleted through that thread,
        which is also the order ``fk_msg_conv`` forces on the messages.

        Deleting nothing is a no-op: the cascade must be re-runnable (§3.6).
        """
        ...
