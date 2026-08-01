"""Agents resource DTOs (03-api-spec §2) — Phase 6.1-b.

The wire shapes for ``/api/v1/agents``: ``AgentOut`` (a registry manifest as the
client sees it) and ``AgentInvokeIn`` (one invocation's request body). Pydantic
v2, ``snake_case`` on the wire, exactly the field sets 03 §2 fixes.

**``AgentInvokeOut`` is real as of 6.1-ج-3.** It was withheld in 6.1-b
because it names three things the single-agent path did not produce then — an
owning conversation, a persisted message, and a ``prompt``/``completion``
split — and fabricating them would have put untrue data on the wire. The
orchestrator now opens the thread, writes both turns, and reports the split
from its own meter, so every field below is read from something that actually
happened.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.api.v1.dto.conversations import MessageOut


class AgentOut(BaseModel):
    """One registered agent's manifest as the API exposes it (03 §2).

    ``capabilities``/``required_permissions`` are the manifest's frozensets
    rendered as ordered lists (the router sorts them, so the wire order is
    stable across boots rather than set-iteration-dependent).
    """

    key: str
    name: str
    version: str
    description: str
    capabilities: list[str]
    required_permissions: list[str]


class AgentInvokeIn(BaseModel):
    """The ``POST /agents/{key}/invoke`` request body (03 §2).

    ``conversation_id`` is optional — an invocation may continue an existing
    thread, and one that names none gets a fresh thread opened for it (the
    reply's ``conversation_id`` is not optional). ``stream`` selects SSE vs.
    the aggregated reply.
    """

    conversation_id: str | None = None
    input: dict[str, Any]
    stream: bool = False


class Usage(BaseModel):
    """One turn's token split (03 §2). Measured when the provider reported
    both counters, estimated otherwise — the orchestrator's meter owns that
    distinction and records it on the usage ledger, not on this DTO."""

    prompt_tokens: int
    completion_tokens: int


class AgentInvokeOut(BaseModel):
    """The ``stream=false`` reply (03 §2): which thread the turn landed in, the
    assistant message as persisted, and what it consumed."""

    conversation_id: str
    message: MessageOut
    usage: Usage
