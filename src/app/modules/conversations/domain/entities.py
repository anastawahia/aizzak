"""Conversations aggregates (pure — 06-domain-models §4).

``Conversation`` is the aggregate root, threaded per ``(workspace, agent_key)``
(or per workflow run when ``kind=workflow``); ``Message`` is its append-only
child entity. Behaviour lives on the aggregate; mutations touch only state +
``updated_at``. The optimistic ``version`` is advanced by the repository on
``save``/``append_message`` (02-port-contracts §2). Identifiers are UUIDv7
text (``str``); timestamps are timezone-aware UTC (DD-03).

Since the spaces plan's step 7 a ``Conversation`` also carries the
``space_id`` it was opened in — an opaque ownership axis INSIDE the tenant
(``docs/spaces-backend-plan.md`` §3.2), never a second security boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    # The owning space (`docs/spaces-backend-plan.md` step 7). NOT a second
    # security boundary -- the workspace stays the only one, and RLS stays on
    # `workspace_id` alone (§3.2); this is an ownership axis, filtered in the
    # query. Opaque here: the module stores an id whose meaning it does not
    # know (`ports/spaces.py` proves it names something real), and decision 1
    # gives the id its consequence -- a thread retrieves from ITS space's
    # files, which is what §3.5's pin rule enforces at the boundary.
    #
    # `| None` mirrors the column, which stays NULLable until plan row 8-b.
    # NOT defaulted, deliberately, and for `File.space_id`'s reason: a default
    # would make a writer that FORGOT its space indistinguishable from one
    # that decided it has none. Step 12 paid both of the debtors this comment
    # used to name: `POST /conversations` takes `space_id` in its body, and
    # the orchestrator's agent/workflow threads take it from
    # `AgentInvokeIn`/`WorkflowRunIn` and refuse to open without one. What is
    # left is `WorkerMediaGenerator` (§7), and row 8-b is where it is settled.
    #
    # There is no mutator for it, matching `File`: decision 3 forbids moving
    # a file between spaces, and moving a THREAD would be worse -- it would
    # silently re-point the whole retrieval scope its messages were answered
    # from. `save` leaves the column out of its UPDATE, so this is unwritable
    # rather than merely undocumented.
    space_id: str | None
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
    # A ROUTING KEY from the D-16 table, never a raw model name (01 §2.4).
    # ``None`` = not pinned ⇒ the thread resolves by agent key, which is what
    # every thread did before the column existed. Defaulted, and the ONLY
    # defaulted field here, because that is the semantics of the rows that
    # predate it: a construction site that says nothing means "unpinned", and
    # forcing every one of them to spell it out would add noise without adding
    # a decision. The domain does not validate the key — valid keys are
    # whatever the operator configured today, which is not a fact the domain
    # can hold (``PinConversationModel`` checks it against ``ModelCatalog``).
    model_route: str | None = None
    # ب-9 (خطة السيناريوهات §7, gap ف-1أ) — the file names this thread's LAST
    # turn asked the user to choose between, in the order they were shown.
    # `()` = nothing pending, which is what every turn that asked no question
    # means and what every row predating the column means.
    #
    # The SECOND defaulted field here, and it shares `model_route`'s reason
    # exactly: a construction site that says nothing means "this thread is not
    # waiting on an answer", which is true of almost every one of them, and
    # forcing all of them to spell it out would add noise without adding a
    # decision. (`space_id` above is undefaulted for the opposite reason: a
    # forgotten space is indistinguishable from a decided absence, and the
    # consequence is a thread filed nowhere. A forgotten pending list is
    # simply a thread with no question outstanding.)
    #
    # NAMES and not document ids, and the domain is where that has to be
    # stated because it is the domain that makes the value meaningful: what
    # the user was shown is names, the answer is about what was shown, and
    # «الثاني» is only readable against the list that was actually displayed.
    # Ids would make the ordinal answerable only by re-deriving a display
    # order from somewhere else. This module never resolves them — it holds
    # them, and the module that offered them reads them back.
    #
    # Not validated, deliberately. These are strings another module minted and
    # this one only remembers; a rule here about what a "valid" candidate name
    # is would be this module inventing an opinion about a vocabulary it does
    # not own.
    pending_clarification: tuple[str, ...] = ()

    def rename(self, title: str | None, now: datetime) -> None:
        """Change the display title. Rejected once the conversation is deleted."""
        self._guard_not_deleted()
        self.title = title
        self.updated_at = now

    def pin_model_route(self, route: str | None, now: datetime) -> None:
        """Pin (or, with ``None``, unpin) the model route for this thread.

        Rejected once the conversation is deleted, for the same reason
        ``rename`` is: a deleted thread refuses WRITES rather than denying its
        own existence, and re-routing one would be a write to something the
        reads have already stopped returning.
        """
        self._guard_not_deleted()
        self.model_route = route
        self.updated_at = now

    def expect_clarification(self, options: Sequence[str], now: datetime) -> None:
        """Record (or, with an empty sequence, forget) the file names this
        thread has just asked the user to choose between (ب-9).

        **Setting and clearing are the same call, and that is the decision.**
        The pending intent lives for exactly one turn: the next turn reads it,
        acts on it or does not, and then writes whatever THAT turn left
        outstanding — which is nothing, almost always. A separate ``clear``
        would make the erasure an extra step a caller could omit, and an
        intent that survives two turns reads a brand-new question as an answer
        to a forgotten one. Making "what is outstanding now" the only thing
        anyone can write is what keeps that impossible rather than merely
        discouraged.

        Copied into a tuple, not aliased: the caller's list must not stay a
        live handle on the aggregate's state.

        Rejected once the conversation is deleted, for ``pin_model_route``'s
        reason: a deleted thread refuses writes rather than denying its own
        existence.
        """
        self._guard_not_deleted()
        self.pending_clarification = tuple(options)
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


@dataclass(frozen=True, slots=True)
class PinnedFile:
    """One file in a thread's retrieval scope (BE-RAG-005, 01 §2.4).

    ``frozen``, unlike the two above, because it has no lifecycle: a pin is
    created and dropped, never edited. It is a value object rather than an
    entity for the same reason — its identity IS ``(conversation_id,
    file_id)``, which is also its primary key, so there is no surrogate id and
    nothing to mutate.

    ``file_id`` is a reference into another module's table, not something this
    module owns or can validate. Whether it names a real, readable file — and,
    since the spaces plan's step 7, whether that file lives in this thread's
    own space (§3.5) — is checked once at the boundary
    (``PinConversationFile`` against the ``FilesQuery`` seam) and deliberately
    not re-checked on read: a file deleted after it was pinned leaves the pin
    standing, and retrieval finds nothing for it — the same outcome as a file
    that was never indexed. There is no ``space_id`` on the pin itself for the
    same reason: it would be a copy of a fact that already has one owner (the
    file's row), and copies of a fact go stale.
    """

    conversation_id: str
    file_id: str
    workspace_id: str
    created_at: datetime
