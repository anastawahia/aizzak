"""Unit tests for memory use-cases over in-memory fake ports.
Pure: the repository, ``EmbeddingProvider`` and ``VectorStore`` are faked."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.pagination import Page
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.event_outbox import OutboxRecord
from app.framework.ports.vector_store import VectorHit, VectorPoint
from app.framework.types import Json
from app.modules.memory.application.use_cases import (
    ForgetMemory,
    IndexMemoryItem,
    RecallRelevant,
    RememberInteraction,
    RememberInteractionService,
)
from app.modules.memory.domain.collections import memory_collection, memory_point_id
from app.modules.memory.domain.entities import MemoryItem
from app.modules.memory.domain.events import MemoryEvent, MemoryForgotten, MemoryStored
from app.modules.memory.domain.value_objects import AgentKey, MemoryKind, VectorRef


class _FakeMemory:
    """In-memory ``MemoryRepository`` — ignores ``ctx`` (no RLS in unit tests)."""

    def __init__(self) -> None:
        self.rows: dict[str, MemoryItem] = {}

    async def add(self, ctx: ExecutionContext, item: MemoryItem) -> None:
        self.rows[item.id] = item

    async def get(self, ctx: ExecutionContext, memory_id: str) -> MemoryItem | None:
        return self.rows.get(memory_id)

    async def list_by_agent(
        self, ctx: ExecutionContext, agent_key: str, *, limit: int, cursor: str | None
    ) -> Page[MemoryItem]:
        items = [row for row in self.rows.values() if row.agent_key.value == agent_key]
        return Page(data=items[:limit], next_cursor=None, limit=limit)

    async def save(self, ctx: ExecutionContext, item: MemoryItem) -> None:
        self.rows[item.id] = item


class _FakeEmbeddings:
    """Fixed single-vector ``EmbeddingProvider`` fake. ``calls`` records
    every ``embed`` call's ``texts`` (5.1-ج's ``IndexMemoryItem`` tests
    below need to assert both that an embed happened at all, and -- for the
    idempotency-guard test -- that a SECOND ``execute`` call does not embed
    again)."""

    provider = "fake"

    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        self.calls.append(list(texts))
        return EmbeddingResult(
            vectors=[list(self._vector) for _ in texts],
            model=model,
            dimensions=len(self._vector),
            tokens=len(texts),
        )

    def dimensions(self, model: str) -> int:
        return len(self._vector)


class _FakeVectors:
    """Preset-hit ``VectorStore`` fake. ``search`` is the ``RecallRelevant``
    precedent; ``upsert``/``ensure_collection`` additionally RECORD what was
    written (5.1-ج's ``IndexMemoryItem`` tests below), keyed by collection so
    a test can assert both which collection got written to and each point's
    own payload."""

    def __init__(self, hits: list[VectorHit] | None = None) -> None:
        self.hits = hits if hits is not None else []
        self.last_call: tuple[str, list[float], int, Json | None] | None = None
        self.points: dict[str, dict[str, VectorPoint]] = {}
        self.ensured: list[tuple[str, int]] = []

    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None:
        self.ensured.append((name, dim))
        self.points.setdefault(name, {})

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        bucket = self.points.setdefault(collection, {})
        for point in points:
            bucket[point.id] = point

    async def search(
        self, collection: str, vector: list[float], k: int, flt: Json | None = None
    ) -> list[VectorHit]:
        self.last_call = (collection, vector, k, flt)
        return self.hits

    async def delete(self, collection: str, ids: Sequence[str]) -> None: ...


def _ctx(workspace_id: str = "w1") -> ExecutionContext:
    return ExecutionContext(
        workspace_id=workspace_id,
        user_id="u1",
        correlation_id="corr",
        roles=frozenset({"member"}),
    )


# --------------------------------------------------------------------------- #
# RememberInteraction                                                          #
# --------------------------------------------------------------------------- #
async def test_remember_creates_pending_item_and_event() -> None:
    memory = _FakeMemory()
    item, events = await RememberInteraction(memory).execute(
        _ctx(), agent_key="rag-agent", content="the user likes tea"
    )
    assert item.vector_ref is None  # pending — a worker indexes it later
    assert item.kind is MemoryKind.SEMANTIC
    assert item.id in memory.rows
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, MemoryStored)
    assert event.memory_id == item.id
    assert event.workspace_id == "w1"
    assert event.agent_key == "rag-agent"


async def test_remember_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        await RememberInteraction(_FakeMemory()).execute(
            _ctx(), agent_key="rag-agent", content="   "
        )


async def test_remember_rejects_invalid_agent_key() -> None:
    with pytest.raises(ValidationError):
        await RememberInteraction(_FakeMemory()).execute(_ctx(), agent_key="", content="hi")


async def test_remember_rejects_invalid_kind() -> None:
    with pytest.raises(ValidationError):
        await RememberInteraction(_FakeMemory()).execute(
            _ctx(), agent_key="rag-agent", content="hi", kind="bogus"
        )


# --------------------------------------------------------------------------- #
# ForgetMemory                                                                 #
# --------------------------------------------------------------------------- #
async def test_forget_emits_event_and_persists() -> None:
    memory = _FakeMemory()
    item, _ = await RememberInteraction(memory).execute(_ctx(), agent_key="rag-agent", content="hi")
    forgotten, events = await ForgetMemory(memory).execute(_ctx(), item.id)
    assert forgotten.deleted_at is not None
    assert len(events) == 1
    assert isinstance(events[0], MemoryForgotten)
    assert memory.rows[item.id].deleted_at is not None


async def test_forget_is_idempotent() -> None:
    memory = _FakeMemory()
    item, _ = await RememberInteraction(memory).execute(_ctx(), agent_key="rag-agent", content="hi")
    forget = ForgetMemory(memory)
    await forget.execute(_ctx(), item.id)
    _, events = await forget.execute(_ctx(), item.id)
    assert events == ()


async def test_forget_missing_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await ForgetMemory(_FakeMemory()).execute(_ctx(), "missing")


# --------------------------------------------------------------------------- #
# RecallRelevant                                                               #
# --------------------------------------------------------------------------- #
async def test_recall_hydrates_and_ranks_hits() -> None:
    memory = _FakeMemory()
    ctx = _ctx()
    live, _ = await RememberInteraction(memory).execute(
        ctx, agent_key="rag-agent", content="likes tea", salience=0.5
    )
    forgotten, _ = await RememberInteraction(memory).execute(
        ctx, agent_key="rag-agent", content="likes coffee"
    )
    await ForgetMemory(memory).execute(ctx, forgotten.id)
    missing_id = new_uuid7()

    hits = [
        VectorHit(id="p1", score=0.9, payload={"memory_id": live.id}),
        VectorHit(id="p2", score=0.8, payload={"memory_id": forgotten.id}),
        VectorHit(id="p3", score=0.7, payload={"memory_id": missing_id}),
    ]
    vectors = _FakeVectors(hits)
    recall = RecallRelevant(_FakeEmbeddings(), vectors, memory)

    results = await recall.execute(
        ctx, agent_key="rag-agent", query="beverages", model="text-embedding-3", api_key="k"
    )

    assert [r.memory_id for r in results] == [live.id]  # forgotten + missing skipped
    assert results[0].score == 0.9
    assert results[0].salience == 0.5
    assert results[0].kind == "semantic"
    # tenant isolation: every search filter carries workspace_id (DD-04)
    assert vectors.last_call is not None
    _, _, _, flt = vectors.last_call
    assert flt is not None
    assert flt["workspace_id"] == ctx.workspace_id
    assert flt["agent_key"] == "rag-agent"


async def test_recall_rejects_k_too_low() -> None:
    with pytest.raises(ValidationError):
        await RecallRelevant(_FakeEmbeddings(), _FakeVectors(), _FakeMemory()).execute(
            _ctx(), agent_key="rag-agent", query="q", model="m", api_key="k", k=0
        )


async def test_recall_rejects_k_too_high() -> None:
    with pytest.raises(ValidationError):
        await RecallRelevant(_FakeEmbeddings(), _FakeVectors(), _FakeMemory()).execute(
            _ctx(), agent_key="rag-agent", query="q", model="m", api_key="k", k=51
        )


async def test_recall_rejects_empty_query() -> None:
    with pytest.raises(ValidationError):
        await RecallRelevant(_FakeEmbeddings(), _FakeVectors(), _FakeMemory()).execute(
            _ctx(), agent_key="rag-agent", query="   ", model="m", api_key="k"
        )


async def test_recall_rejects_invalid_agent_key() -> None:
    with pytest.raises(ValidationError):
        await RecallRelevant(_FakeEmbeddings(), _FakeVectors(), _FakeMemory()).execute(
            _ctx(), agent_key="", query="q", model="m", api_key="k"
        )


# --------------------------------------------------------------------------- #
# memory_collection / memory_point_id (domain/collections.py)                 #
# --------------------------------------------------------------------------- #
def test_memory_collection_name() -> None:
    assert memory_collection("ws-123") == "mem-ws-123"


def test_memory_point_id_is_deterministic() -> None:
    assert memory_point_id("mem-1") == memory_point_id("mem-1")


def test_memory_point_id_differs_by_memory_id() -> None:
    assert memory_point_id("mem-1") != memory_point_id("mem-2")


def test_memory_point_id_is_a_valid_uuid_string() -> None:
    assert uuid.UUID(memory_point_id("mem-1"))  # does not raise


# --------------------------------------------------------------------------- #
# IndexMemoryItem (5.1-ج -- the worker-facing terminal wrapper)               #
# --------------------------------------------------------------------------- #
class _ExplodingEmbeddings:
    """An ``EmbeddingProvider`` whose ``embed`` always raises -- proves
    ``IndexMemoryItem`` does NOT swallow the failure into some fabricated
    terminal state (unlike ``IndexRegisteredDocument``/``RunMediaJob``,
    ``MemoryItem`` has no ``failed`` state to record one in)."""

    provider = "exploding"

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        raise RuntimeError("embedding provider is down")

    def dimensions(self, model: str) -> int:
        return 3


async def test_index_embeds_upserts_and_persists_the_vector_ref() -> None:
    memory = _FakeMemory()
    ctx = _ctx()
    item, _ = await RememberInteraction(memory).execute(
        ctx, agent_key="rag-agent", content="the user likes tea"
    )
    assert item.vector_ref is None
    embeddings = _FakeEmbeddings(vector=[0.4, 0.5, 0.6])
    vectors = _FakeVectors()

    indexed = await IndexMemoryItem(memory, embeddings, vectors).execute(
        ctx, memory_id=item.id, model="text-embedding-3", api_key="k"
    )

    assert embeddings.calls == [["the user likes tea"]]
    assert indexed.vector_ref == VectorRef(
        memory_collection(ctx.workspace_id), memory_point_id(item.id)
    )
    # Persisted, not just returned -- a fresh read-back sees the same ref.
    assert memory.rows[item.id].vector_ref == indexed.vector_ref


async def test_index_upserts_into_the_same_collection_recall_relevant_searches() -> None:
    """``IndexMemoryItem`` and ``RecallRelevant`` must never drift onto two
    different collection names for the same workspace (``memory_collection``
    is the single shared source of truth for both)."""
    memory = _FakeMemory()
    ctx = _ctx(workspace_id="ws-77")
    item, _ = await RememberInteraction(memory).execute(ctx, agent_key="rag-agent", content="hi")
    vectors = _FakeVectors()

    await IndexMemoryItem(memory, _FakeEmbeddings(), vectors).execute(
        ctx, memory_id=item.id, model="m", api_key="k"
    )

    assert list(vectors.points) == [memory_collection("ws-77")]


async def test_index_upserted_point_payload_carries_workspace_agent_and_memory_id() -> None:
    """DD-04 tenant isolation: a payload without ``workspace_id`` could never
    be scoped by a later ``VectorStore.search`` filter."""
    memory = _FakeMemory()
    ctx = _ctx(workspace_id="ws-9")
    item, _ = await RememberInteraction(memory).execute(ctx, agent_key="rag-agent", content="hi")
    vectors = _FakeVectors()

    await IndexMemoryItem(memory, _FakeEmbeddings(), vectors).execute(
        ctx, memory_id=item.id, model="m", api_key="k"
    )

    point = vectors.points[memory_collection("ws-9")][memory_point_id(item.id)]
    assert point.payload == {"workspace_id": "ws-9", "agent_key": "rag-agent", "memory_id": item.id}


async def test_index_is_a_noop_when_the_item_already_has_a_vector_ref() -> None:
    """Mutation-battery target #6: removing this idempotency guard would
    make a second ``execute`` call re-embed -- ``embeddings.calls`` would
    gain a second entry instead of staying at one."""
    memory = _FakeMemory()
    ctx = _ctx()
    item, _ = await RememberInteraction(memory).execute(ctx, agent_key="rag-agent", content="hi")
    embeddings = _FakeEmbeddings()
    vectors = _FakeVectors()
    use_case = IndexMemoryItem(memory, embeddings, vectors)

    first = await use_case.execute(ctx, memory_id=item.id, model="m", api_key="k")
    assert embeddings.calls == [["hi"]]

    second = await use_case.execute(ctx, memory_id=item.id, model="m", api_key="k")

    assert embeddings.calls == [["hi"]]  # still exactly one call -- no re-embed
    assert second.vector_ref == first.vector_ref


async def test_index_raises_not_found_for_a_missing_item() -> None:
    with pytest.raises(NotFoundError):
        await IndexMemoryItem(_FakeMemory(), _FakeEmbeddings(), _FakeVectors()).execute(
            _ctx(), memory_id="missing", model="m", api_key="k"
        )


async def test_index_propagates_an_embedding_failure_rather_than_recording_it() -> None:
    """The honest 5.1 behaviour (module docstring): no terminal ``failed``
    state exists for ``MemoryItem``, so a provider outage RAISES -- the
    worker's engine leaves the Streams entry un-``XACK``\\ ed and it is
    redelivered, rather than this use-case fabricating a "failed" outcome
    the domain has no state for."""
    memory = _FakeMemory()
    ctx = _ctx()
    item, _ = await RememberInteraction(memory).execute(ctx, agent_key="rag-agent", content="hi")

    with pytest.raises(RuntimeError, match="embedding provider is down"):
        await IndexMemoryItem(memory, _ExplodingEmbeddings(), _FakeVectors()).execute(
            ctx, memory_id=item.id, model="m", api_key="k"
        )

    # The failed attempt must not have half-persisted anything.
    assert memory.rows[item.id].vector_ref is None


async def test_run_persists_no_vector_ref_until_finalize() -> None:
    """The 5.2-أ split's load-bearing property (the ``knowledge``/``media``
    twins): ``run`` embeds and upserts but persists NOTHING -- the
    ``vector_ref`` write waits for ``finalize``, which the worker handler
    wraps in one transaction with the DD-09 claim."""
    memory = _FakeMemory()
    ctx = _ctx()
    item, _ = await RememberInteraction(memory).execute(
        ctx, agent_key="rag-agent", content="the user likes tea"
    )
    vectors = _FakeVectors()
    use_case = IndexMemoryItem(memory, _FakeEmbeddings(vector=[0.4, 0.5, 0.6]), vectors)

    attempt = await use_case.run(ctx, memory_id=item.id, model="m", api_key="k")

    assert attempt.vector_ref == VectorRef(
        memory_collection(ctx.workspace_id), memory_point_id(item.id)
    )
    assert not attempt.is_redelivery_noop
    assert list(vectors.points) == [memory_collection(ctx.workspace_id)]  # upserted in run
    assert memory.rows[item.id].vector_ref is None  # NOT persisted yet

    indexed = await use_case.finalize(ctx, attempt)

    assert indexed.vector_ref == attempt.vector_ref
    assert memory.rows[item.id].vector_ref == attempt.vector_ref


async def test_run_short_circuits_an_indexed_item_and_finalize_passes_through() -> None:
    memory = _FakeMemory()
    ctx = _ctx()
    item, _ = await RememberInteraction(memory).execute(ctx, agent_key="rag-agent", content="hi")
    embeddings = _FakeEmbeddings()
    use_case = IndexMemoryItem(memory, embeddings, _FakeVectors())
    await use_case.execute(ctx, memory_id=item.id, model="m", api_key="k")
    assert embeddings.calls == [["hi"]]

    attempt = await use_case.run(ctx, memory_id=item.id, model="m", api_key="k")

    assert attempt.is_redelivery_noop
    assert embeddings.calls == [["hi"]]  # R3: no re-embed

    result = await use_case.finalize(ctx, attempt)

    assert result.vector_ref == memory.rows[item.id].vector_ref


# --------------------------------------------------------------------------- #
# RememberInteractionService -- the Outbox seam (5.1-أ)                       #
# --------------------------------------------------------------------------- #
class _FakeOutbox:
    """Minimal ``EventOutbox`` -- records every append call, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionContext, list[OutboxRecord]]] = []

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        self.calls.append((ctx, list(records)))


class _ExplodingOutbox:
    """An outbox whose append always fails (a dead database, a lost grant)."""

    async def append(self, ctx: ExecutionContext, records: Sequence[OutboxRecord]) -> None:
        raise RuntimeError("outbox is down")


class _FakeUnitOfWork:
    """A no-op transaction boundary -- ``test_unit_of_work.py`` covers the
    real ``TenantSessionFactory.begin`` seam; this only stands in for it."""

    @asynccontextmanager
    async def begin(self, ctx: ExecutionContext) -> AsyncIterator[None]:
        yield


class _FixedEventsRememberInteraction:
    """A fake shaped like ``RememberInteraction`` -- returns PRE-SET events so
    the service's own None-filtering can be exercised independent of the
    real use-case's business rules (which never itself emits a
    ``MemoryForgotten``)."""

    def __init__(self, item: MemoryItem, events: tuple[MemoryEvent, ...]) -> None:
        self._item = item
        self._events = events

    async def execute(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        content: str,
        kind: str = "semantic",
        salience: float = 0.0,
    ) -> tuple[MemoryItem, tuple[MemoryEvent, ...]]:
        return self._item, self._events


async def test_remember_interaction_service_remembers_and_appends_its_event() -> None:
    memory = _FakeMemory()
    ctx = _ctx()
    outbox = _FakeOutbox()
    service = RememberInteractionService(RememberInteraction(memory), outbox, _FakeUnitOfWork())

    item = await service.remember(ctx, agent_key="rag-agent", content="the user likes tea")

    assert item.id in memory.rows
    (call_ctx, records) = outbox.calls[0]
    assert call_ctx is ctx
    assert [r.event_type for r in records] == ["memory.item.stored.v1"]
    assert records[0].aggregate_id == item.id


async def test_remember_interaction_service_outbox_failure_propagates() -> None:
    """Swallowing would leave a stored item with no event -- invisible to the
    Phase-5 memory worker. The failure must roll the aggregate write back
    too."""
    memory = _FakeMemory()
    ctx = _ctx()
    service = RememberInteractionService(
        RememberInteraction(memory), _ExplodingOutbox(), _FakeUnitOfWork()
    )

    with pytest.raises(RuntimeError, match="outbox is down"):
        await service.remember(ctx, agent_key="rag-agent", content="hi")


async def test_remember_interaction_service_drops_none_mapped_events_before_appending() -> None:
    ctx = _ctx()
    item = MemoryItem(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        agent_key=AgentKey("rag-agent"),
        kind=MemoryKind.SEMANTIC,
        content="the user likes tea",
        vector_ref=None,
        salience=0.0,
        created_at=utc_now(),
        deleted_at=None,
    )
    stored = MemoryStored(item.id, ctx.workspace_id, "rag-agent", utc_now())
    forgotten = MemoryForgotten(item.id, utc_now())
    fixed = _FixedEventsRememberInteraction(item, (stored, forgotten))
    outbox = _FakeOutbox()
    service = RememberInteractionService(fixed, outbox, _FakeUnitOfWork())

    await service.remember(ctx, agent_key="rag-agent", content="the user likes tea")

    (_, records) = outbox.calls[0]
    assert [r.event_type for r in records] == ["memory.item.stored.v1"]
