"""Conversations persistence port (02-port-contracts §2, 06-domain-models §4).

Outbound repository for the ``Conversation`` aggregate and its ``Message``
child entity. Every tenant-scoped method takes ``ExecutionContext`` first so
the SQL adapter can apply the RLS guard (``SET LOCAL app.workspace_id``) and
the ``WHERE workspace_id`` filter (DD-04).
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.conversations.domain.entities import Conversation, Message


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
