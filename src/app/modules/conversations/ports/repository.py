"""Conversations persistence port (02-port-contracts §2, 06-domain-models §4).

Outbound repository for the ``Conversation`` aggregate and its ``Message``
child entity. Every tenant-scoped method takes ``ExecutionContext`` first so
the SQL adapter can apply the RLS guard (``SET LOCAL app.workspace_id``) and
the ``WHERE workspace_id`` filter (DD-04).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.conversations.domain.entities import Conversation, Message, PinnedFile


class ConversationRepository(Protocol):
    """Tenant-scoped persistence for conversations and their messages.

    ``save`` uses an optimistic lock on ``version`` (used for rename /
    soft-delete) — a stale write surfaces as a conflict at the adapter.
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
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[Conversation]: ...

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
