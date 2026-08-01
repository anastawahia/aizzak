"""Conversations DTOs — the wire shapes of `03-api-spec §2` — Phase 6.1-ج-2.

Field-for-field the spec's models. Two notes on what is deliberately NOT here:

* **No ``version``, no ``deleted_at``, no ``message_count``.** The aggregate
  carries them; the wire does not. ``version`` is the optimistic lock, which
  is the repository's business and would invite a client to reason about it;
  a soft-deleted conversation is simply absent from every read route.
* **No rename DTO.** `03 §1` gives Conversations GET·POST·GET·DELETE plus the
  two message routes and no rename, so the module's ``RenameConversation``
  use-case stays unexposed rather than growing a route the contract lacks.

``MessageOut.content`` is ``dict[str, Any]`` (the spec's own type) and is
rendered from the domain's ``MessageContent`` as ``{"text", "attachments"}``
— today the only shape that value object can hold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConversationOut(BaseModel):
    """One conversation thread, as returned by every conversations route."""

    id: str
    agent_key: str
    kind: str
    title: str | None
    created_at: datetime


class ConversationCreateIn(BaseModel):
    """``POST /conversations`` — open a thread under one agent.

    ``kind`` is absent on purpose: `06 §4` reserves ``workflow`` threads for
    runs the orchestrator opens itself (D-12), so a client-created thread is
    always an ``agent`` one and there is nothing to choose.
    """

    agent_key: str = Field(min_length=1)
    title: str | None = None


class MessageOut(BaseModel):
    """One message within a thread."""

    id: str
    role: str
    content: dict[str, Any]
    token_count: int | None
    seq: int
    created_at: datetime


class MessageCreateIn(BaseModel):
    """``POST /conversations/{id}/messages`` — post a turn and run the thread's
    agent on it (`03 §2`: "يشغّل الوكيل ويعيد ردّه").

    ``content`` is passed to the agent verbatim as its request ``input`` AND
    persisted as the user's message, which is why there is no separate
    ``input`` field: one payload, one turn, no way for the two to disagree.
    """

    content: dict[str, Any]
    stream: bool = False
