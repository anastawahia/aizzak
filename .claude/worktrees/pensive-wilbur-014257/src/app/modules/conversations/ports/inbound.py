"""Conversations inbound port (06-domain-models §4, D-12).

The `media` inbound port's precedent (4.7-d-2), applied to the one
conversations operation the platform actually calls today: **starting a
thread**. The caller is the ORCHESTRATOR — `11 §8` puts the D-12 decision
("للـWorkflow **محادثته الخاصة**") in the agents layer, not in any agent, so
this port is imported NOMINALLY there exactly like the `usage` inbound ports
and needs no DIP mirror in ``agent_runtime/deps_ports.py``. No agent reaches
it: an agent that could open threads on its own would make D-12's "one
conversation per workflow run" unenforceable from the one place that knows a
run happened.

**A handle, not the aggregate.** ``StartedConversation`` carries the three
fields a caller can act on; the `Conversation` entity (version, timestamps,
soft-delete state, message count) stays inside the module. Handing the
aggregate out would let the agents layer read — and eventually reason about —
optimistic-lock state it has no business touching.

``kind`` crosses this boundary as a plain ``str``, mirroring
``RequestedMedia.kind``: typing it ``ConversationKind`` would force every
caller to import a module-domain enum, which is exactly the coupling an
inbound port exists to prevent. The string is validated against the enum on
the way IN (``ConversationService.start``), so an invalid kind is a 422 at the
boundary rather than a bad row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class StartedConversation:
    """What a caller gets back after opening a thread."""

    id: Uuid
    agent_key: str
    kind: str


@dataclass(frozen=True, slots=True)
class AppendedMessage:
    """What a caller gets back after writing one turn into a thread.

    A handle again, not the ``Message`` entity — same reasoning as
    ``StartedConversation``, plus the fields the API layer must render
    (`03 §2`'s ``MessageOut``): the caller can display and reference the turn
    without holding a domain object whose soft-delete state it could mutate.
    ``role`` crosses as a plain ``str`` for the same reason ``kind`` does.
    """

    id: Uuid
    conversation_id: Uuid
    role: str
    text: str
    attachments: tuple[str, ...]
    token_count: int | None
    seq: int
    created_at: datetime


class ConversationThreads(Protocol):
    """The agents layer's write access to conversation threads (D-12).

    ``start`` opens a thread for an agent or a workflow run; ``append`` writes
    one turn into an existing one. **6.1-ج-3 added ``append``** — the single
    agent's turn persistence, which is what lets `03 §2`'s non-streaming
    ``AgentInvokeOut`` name a real message instead of a fabricated one. Both
    methods have exactly one caller, the orchestrator, so the port stays as
    wide as its consumer actually uses and no wider.
    """

    async def start(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: str,
        title: str | None = None,
    ) -> StartedConversation: ...

    async def append(
        self,
        ctx: ExecutionContext,
        conversation_id: Uuid,
        *,
        role: str,
        text: str,
        attachments: tuple[str, ...] = (),
        token_count: int | None = None,
    ) -> AppendedMessage: ...
