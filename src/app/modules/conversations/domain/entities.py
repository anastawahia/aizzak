"""Conversations aggregates (pure — 06-domain-models §4).

``Conversation`` is the aggregate root, threaded per ``(workspace, agent_key)``
(or per workflow run when ``kind=workflow``); ``Message`` is its append-only
child entity. Behaviour lives on the aggregate; mutations touch only state +
``updated_at``. The optimistic ``version`` is advanced by the repository on
``save``/``append_message`` (02-port-contracts §2). Identifiers are UUIDv7
text (``str``); timestamps are timezone-aware UTC (DD-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.conversations.domain.errors import ConversationDeletedError
from app.modules.conversations.domain.value_objects import (
    AgentKey,
    ConversationKind,
    MessageContent,
    MessageRole,
)


@dataclass(slots=True)
class Conversation:
    """The conversation thread for one agent (or workflow) within a workspace."""

    id: str
    workspace_id: str
    agent_key: AgentKey
    kind: ConversationKind
    title: str | None
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: int
    # NOT a persisted column (01-data-model §2.4 has no such field on
    # conversations.conversations): the SQL adapter hydrates this on load as
    # ``COALESCE(MAX(seq), 0)`` over the conversation's messages. It is kept on
    # the aggregate — rather than recomputed per call — so ``append_message``
    # can assign the next ``seq`` in-memory, which is what keeps INV-CV1
    # (gap-free, strictly ascending ``seq``) a domain-owned invariant instead
    # of a database-only constraint. Soft-deleted messages still count toward
    # it: a soft-deleted message is excluded from retrieval but its ``seq`` is
    # never reused, so the counter never has to "go backwards" (INV-CV3).
    message_count: int

    def rename(self, title: str | None, now: datetime) -> None:
        """Change the display title. Rejected once the conversation is deleted."""
        self._guard_not_deleted()
        self.title = title
        self.updated_at = now

    def soft_delete(self, now: datetime) -> None:
        """Soft-delete the conversation. Idempotent: a second call is a no-op."""
        if self.deleted_at is not None:
            return
        self.deleted_at = now
        self.updated_at = now

    def append_message(
        self,
        message_id: str,
        role: MessageRole,
        content: MessageContent,
        token_count: int | None,
        now: datetime,
    ) -> Message:
        """Append a new message, assigning the next gap-free ``seq`` (INV-CV1).

        Rejected once the conversation is soft-deleted (INV-CV3).
        """
        self._guard_not_deleted()
        self.message_count += 1
        seq = self.message_count
        self.updated_at = now
        return Message(
            id=message_id,
            conversation_id=self.id,
            workspace_id=self.workspace_id,
            role=role,
            content=content,
            token_count=token_count,
            seq=seq,
            created_at=now,
            deleted_at=None,
        )

    def _guard_not_deleted(self) -> None:
        if self.deleted_at is not None:
            raise ConversationDeletedError("conversation is deleted")


@dataclass(slots=True)
class Message:
    """A single message within a conversation, immutable except for soft-delete."""

    id: str
    conversation_id: str
    workspace_id: str
    role: MessageRole
    content: MessageContent
    token_count: int | None
    seq: int
    created_at: datetime
    deleted_at: datetime | None

    def soft_delete(self, now: datetime) -> None:
        """Soft-delete the message. Idempotent: a second call is a no-op."""
        if self.deleted_at is not None:
            return
        self.deleted_at = now
