"""Memory use-cases (06-domain-models §5).

Thin application services that coordinate the pure domain over injected
ports. They own identity/time (framework ``new_uuid7`` / ``utc_now``) and
translate domain-rule violations into the shared framework error hierarchy at
this boundary — the domain itself stays framework-free. ``RememberInteraction``
only persists the row: an item starts with ``vector_ref=None`` (pending index)
and a worker embeds + upserts it into Qdrant later, calling back through
``MemoryItem.attach_vector``. ``RecallRelevant`` is the only use-case that
touches ``EmbeddingProvider``/``VectorStore`` directly, and every vector
search filter carries ``workspace_id`` (DD-04 tenant isolation). Domain events
are returned to the caller for later dispatch (event-bus / outbox wiring lands
in Phase 5).

``RememberInteractionService`` (5.1-أ) is the ``MediaRequestService``
precedent applied to ``RememberInteraction``: the first memory caller that
does not drop ``RememberInteraction``'s returned events on the floor,
appending them to the ``EventOutbox`` inside one request-scoped unit of work.

``IndexMemoryItem`` (5.1-ج) is the worker-facing counterpart
``RememberInteraction``'s own docstring already promised ("a worker embeds
the content, upserts it into Qdrant, and calls back through
``MemoryItem.attach_vector``"). 06-domain-models §5 does not literally list
it among memory's use-cases (only ``RememberInteraction``/``RecallRelevant``/
``ForgetMemory``) -- a deliberate, documented deviation: the design doc's own
``VectorRef`` line ("أو NULL ريثما يُفهرَس", "or NULL until indexed") and its
prose ("عملية التخزين قد تُحال إلى العامل عند الحجم", "storage may be
deferred to the worker at scale") already anticipate exactly this step; it
was simply never named. Unlike ``knowledge.IndexRegisteredDocument``/
``media.RunMediaJob`` (the terminal-wrapper precedent this otherwise
follows), ``MemoryItem`` has no terminal ``failed`` state and no
``...Failed`` event to record one in, so this use-case does NOT catch
``Exception`` the way those two do: an embedding/vector-store failure RAISES
here, which is the honest 5.1 behaviour (⇒ the worker's engine leaves the
Streams entry un-``XACK``\\ ed ⇒ redelivered) rather than a fabricated
"failed" outcome the domain has no state for.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.unit_of_work import UnitOfWork
from app.framework.ports.vector_store import VectorPoint, VectorStore
from app.framework.types import Uuid
from app.modules.memory.application.event_mapping import to_outbox_record
from app.modules.memory.domain.collections import memory_collection, memory_point_id
from app.modules.memory.domain.entities import MemoryItem
from app.modules.memory.domain.errors import MemoryError
from app.modules.memory.domain.events import MemoryEvent, MemoryForgotten, MemoryStored
from app.modules.memory.domain.value_objects import AgentKey, MemoryKind, VectorRef
from app.modules.memory.ports.repository import MemoryRepository

# Mirrors Settings.Limits.max_rag_k (07-nfr-slo §4) — kept as a local constant
# so the pure application layer does not depend on the framework Settings model.
_MIN_RECALL_K = 1
_MAX_RECALL_K = 50


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    """A ranked memory item hydrated from a vector-search hit."""

    memory_id: str
    content: str
    kind: str
    salience: float
    score: float


class RememberInteraction:
    """Record a memory item (06 §5).

    Always created pending vector indexing (``vector_ref=None``) until a
    worker embeds the content, upserts it into Qdrant, and calls back through
    ``MemoryItem.attach_vector``.
    """

    def __init__(self, memory: MemoryRepository) -> None:
        self._memory = memory

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        content: str,
        kind: str = "semantic",
        salience: float = 0.0,
    ) -> tuple[MemoryItem, tuple[MemoryEvent, ...]]:
        try:
            key = AgentKey(agent_key)
        except MemoryError as exc:
            raise ValidationError(str(exc)) from exc
        try:
            memory_kind = MemoryKind(kind)
        except ValueError as exc:
            raise ValidationError(f"invalid memory kind: {kind!r}") from exc
        if not content.strip():
            raise ValidationError("memory content must not be empty")

        now = utc_now()
        item = MemoryItem(
            id=new_uuid7(),
            workspace_id=ctx.workspace_id,
            agent_key=key,
            kind=memory_kind,
            content=content,
            vector_ref=None,
            salience=salience,
            created_at=now,
            deleted_at=None,
        )
        await self._memory.add(ctx, item)
        event = MemoryStored(item.id, ctx.workspace_id, key.value, now)
        return item, (event,)


class ForgetMemory:
    """Soft-delete a memory item. Idempotent — re-forgetting emits no event.

    The matching Qdrant point is cleaned up eventually via the emitted event,
    not here (INV-M2).
    """

    def __init__(self, memory: MemoryRepository) -> None:
        self._memory = memory

    async def execute(
        self, ctx: ExecutionContext, memory_id: Uuid
    ) -> tuple[MemoryItem, tuple[MemoryEvent, ...]]:
        item = await self._memory.get(ctx, memory_id)
        if item is None:
            raise NotFoundError("memory item not found")
        already = item.deleted_at is not None
        now = utc_now()
        item.forget(now)
        await self._memory.save(ctx, item)
        events: tuple[MemoryEvent, ...] = () if already else (MemoryForgotten(memory_id, now),)
        return item, events


class RecallRelevant:
    """Embed the query, search the per-workspace Qdrant collection, and
    hydrate hits back into stored memory items (06 §5).

    A hit whose row is missing or soft-deleted is skipped — the vector index
    and the row store are only eventually consistent (INV-M2).
    """

    def __init__(
        self, embeddings: EmbeddingProvider, vectors: VectorStore, memory: MemoryRepository
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._memory = memory

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        query: str,
        model: str,
        api_key: str,
        k: int = 10,
    ) -> list[RecalledMemory]:
        try:
            key = AgentKey(agent_key)
        except MemoryError as exc:
            raise ValidationError(str(exc)) from exc
        if not query.strip():
            raise ValidationError("recall query must not be empty")
        if not (_MIN_RECALL_K <= k <= _MAX_RECALL_K):
            raise ValidationError(f"k must be between {_MIN_RECALL_K} and {_MAX_RECALL_K}")

        result = await self._embeddings.embed([query], model, api_key)
        vector = result.vectors[0]
        # One Qdrant collection per workspace; per-agent isolation (INV-M1) is a
        # payload filter rather than a collection-per-agent scheme.
        collection = memory_collection(ctx.workspace_id)
        flt = {"workspace_id": ctx.workspace_id, "agent_key": key.value}
        hits = await self._vectors.search(collection, vector, k, flt)

        recalled: list[RecalledMemory] = []
        for hit in hits:
            memory_id: Uuid = hit.payload["memory_id"]
            item = await self._memory.get(ctx, memory_id)
            if item is None or item.deleted_at is not None:
                continue
            recalled.append(
                RecalledMemory(
                    memory_id=item.id,
                    content=item.content,
                    kind=item.kind.value,
                    salience=item.salience,
                    score=hit.score,
                )
            )
        return recalled


@dataclass(frozen=True, slots=True)
class MemoryIndexAttempt:
    """The I/O phase's outcome (``IndexMemoryItem.run``), awaiting its
    terminal transaction (``finalize``) — the ``knowledge.IndexAttempt``/
    ``media.GenerationAttempt`` precedent, narrower because memory has no
    failure state to carry (module docstring): ``vector_ref`` set means the
    embed + upsert ran and awaits persistence; ``None`` means the item was
    already indexed — the R3 idempotent-redelivery no-op, for which
    ``finalize`` is a pure pass-through.
    """

    item: MemoryItem
    vector_ref: VectorRef | None

    @property
    def is_redelivery_noop(self) -> bool:
        return self.vector_ref is None


class IndexMemoryItem:
    """Embed a pending ``MemoryItem``'s content and upsert it into the
    per-workspace Qdrant collection, then persist the resulting
    ``VectorRef`` (worker-facing; see the module docstring for why this
    deviates from the ``IndexRegisteredDocument``/``RunMediaJob`` terminal-
    wrapper precedent it otherwise follows).

    **R3 idempotent-redelivery guard:** an item that already has a
    ``vector_ref`` short-circuits -- no re-embed, no re-upsert, no second
    Qdrant round trip. At-least-once delivery (DD-09) means the SAME
    ``memory.item.stored.v1`` event can arrive twice; this guard is what
    makes a second run a true no-op rather than a second (wasted, and --
    absent ``memory_point_id``'s determinism -- potentially duplicating)
    embedding call.

    **R2 -- no DB transaction spans external I/O:** ``self._embeddings.embed``
    and ``self._vectors.upsert`` both run in ``run``, outside any
    transaction; only ``finalize``'s single ``save`` touches Postgres
    afterwards.

    **Split in 5.2-أ into ``run`` (I/O phase) + ``finalize`` (terminal
    phase)** so the worker handler can wrap ``finalize`` (plus the DD-09
    ``processed_events`` claim) in ONE ``uow.begin`` block — the
    ``IndexRegisteredDocument``/``RunMediaJob`` precedent, minus the outbox
    append (indexing an item emits no follow-on wire event, 04 §5).
    ``execute`` composes the two halves with per-call transactions —
    byte-for-byte the pre-split behaviour.
    """

    def __init__(
        self, memory: MemoryRepository, embeddings: EmbeddingProvider, vectors: VectorStore
    ) -> None:
        self._memory = memory
        self._embeddings = embeddings
        self._vectors = vectors

    async def execute(
        self, ctx: ExecutionContext, *, memory_id: Uuid, model: str, api_key: str
    ) -> MemoryItem:
        attempt = await self.run(ctx, memory_id=memory_id, model=model, api_key=api_key)
        return await self.finalize(ctx, attempt)

    async def run(
        self, ctx: ExecutionContext, *, memory_id: Uuid, model: str, api_key: str
    ) -> MemoryIndexAttempt:
        item = await self._memory.get(ctx, memory_id)
        if item is None:
            raise NotFoundError("memory item not found")

        if item.vector_ref is not None:
            # R3 -- already indexed, a redelivered event is a no-op.
            return MemoryIndexAttempt(item=item, vector_ref=None)

        result = await self._embeddings.embed([item.content], model, api_key)
        vector = result.vectors[0]
        dim = self._embeddings.dimensions(model)
        collection = memory_collection(ctx.workspace_id)
        point_id = memory_point_id(item.id)
        await self._vectors.ensure_collection(collection, dim)
        await self._vectors.upsert(
            collection,
            [
                VectorPoint(
                    id=point_id,
                    vector=vector,
                    payload={
                        "workspace_id": ctx.workspace_id,
                        "agent_key": item.agent_key.value,
                        "memory_id": item.id,
                    },
                )
            ],
        )
        return MemoryIndexAttempt(item=item, vector_ref=VectorRef(collection, point_id))

    async def finalize(self, ctx: ExecutionContext, attempt: MemoryIndexAttempt) -> MemoryItem:
        if attempt.vector_ref is None:
            return attempt.item  # Redelivery no-op: nothing ran, nothing to persist.
        attempt.item.attach_vector(attempt.vector_ref)
        await self._memory.save(ctx, attempt.item)
        return attempt.item


class RememberInteractionService:
    """Wraps ``RememberInteraction`` with the Outbox append inside ONE
    request-scoped unit of work (5.1-أ) — the ``MediaRequestService``/
    ``CompleteUploadService`` precedent.

    Not yet bound to an inbound port: memory has no ``ports/inbound.py`` at
    all today, so nothing calls this seam yet — the same deferral as
    ``media.GetJobStatus`` before a live caller existed. The day an
    agent/route needs to record an interaction through the outbox rather
    than the bare use-case, this is what it composes over.

    **Atomic by construction** — see ``CompleteUploadService``'s docstring
    for the full reasoning: the same ``uow.begin(ctx)`` shape, the same
    un-swallowed outbox failure rolling the aggregate write back with it.
    """

    def __init__(
        self, remember_interaction: RememberInteraction, outbox: EventOutbox, uow: UnitOfWork
    ) -> None:
        self._remember_interaction = remember_interaction
        self._outbox = outbox
        self._uow = uow

    async def remember(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        content: str,
        kind: str = "semantic",
        salience: float = 0.0,
    ) -> MemoryItem:
        async with self._uow.begin(ctx):
            item, events = await self._remember_interaction.execute(
                ctx, agent_key=agent_key, content=content, kind=kind, salience=salience
            )
            records = [
                record
                for record in (to_outbox_record(ctx, event) for event in events)
                if record is not None
            ]
            await self._outbox.append(ctx, records)
            return item
