"""Conversations domain events (pure — 06-domain-models §4, 04-event-catalog §5).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code). Dispatch to the event bus / outbox happens
in the application and infrastructure layers, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ConversationStarted:
    """A new conversation thread was started for an agent (or workflow)."""

    conversation_id: str
    workspace_id: str
    agent_key: str
    kind: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MessageAppended:
    """A message was appended to a conversation."""

    message_id: str
    conversation_id: str
    role: str
    seq: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationRenamed:
    """A conversation's title was changed."""

    conversation_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationDeleted:
    """A conversation was soft-deleted."""

    conversation_id: str
    workspace_id: str
    occurred_at: datetime


ConversationEvent = (
    ConversationStarted | MessageAppended | ConversationRenamed | ConversationDeleted
)
