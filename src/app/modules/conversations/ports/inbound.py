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
        space_id: Uuid | None,
        agent_key: str,
        kind: str,
        title: str | None = None,
    ) -> StartedConversation:
        """Open a thread inside one space (spaces plan step 7).

        ``space_id`` crosses as a plain ``Uuid`` string — an opaque id, like
        ``kind`` and ``role``, so no caller has to import anything of this
        module's or of ``spaces``'. It is REQUIRED and nullable with no
        default: the orchestrator has no space to name until the invoke
        contract carries one (step 12), and a default would have hidden that
        rather than stating it at the call.

        An id that names no live space is a 404 from ``StartConversation``,
        raised before the thread exists.
        """
        ...

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

    async def routed_model(self, ctx: ExecutionContext, conversation_id: Uuid) -> str | None:
        """This thread's pinned D-16 routing key, or ``None`` when unpinned.

        The port's first READ, and it earns the widening: without it the pin
        (BE-RAG-003) would be a column the orchestrator writes nothing to and
        reads nothing from — a stored preference the platform ignores, which is
        worse than no preference at all.

        A plain ``str`` crosses, like ``kind`` and ``role`` above: the value is
        a configuration key, not a domain concept, and typing it would force
        the agents layer to import something from inside this module.

        Missing or soft-deleted ⇒ ``None``, never an error. The caller reads
        this before the write that reports either condition properly, and a
        read-ahead that raised would take that reporting over.
        """
        ...

    async def pinned_files(self, ctx: ExecutionContext, conversation_id: Uuid) -> tuple[Uuid, ...]:
        """The file ids this thread's retrieval is scoped to, or ``()``.

        The port's second READ, and it earns it the way ``routed_model`` did:
        without it the pin (BE-RAG-005) would be a table the orchestrator
        writes nothing to and reads nothing from — a stored preference the
        platform ignores, which is worse than no preference at all.

        **Empty is not "retrieve nothing".** ``()`` means the thread is
        unscoped and searches the whole workspace corpus, which is what every
        thread did before the table existed. The orchestrator passes this
        straight through to the knowledge seam, which reads it the same way.

        Plain ``Uuid`` strings cross, and FILE ids rather than document ids:
        what a caller pinned is a file, and translating "file ⇒ document" is
        the knowledge module's own business (`02 §2`). Returning documents here
        would make the agents layer aware that documents exist at all.

        Missing or soft-deleted ⇒ ``()``, never an error, for ``routed_model``'s
        reason: this is a read-ahead in front of the write that does the real
        reporting.
        """
        ...
