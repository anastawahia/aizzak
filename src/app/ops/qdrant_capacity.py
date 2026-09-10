"""What 200 tenants actually cost Qdrant -- capacity step 4.4 (``docs/
capacity-plan.md`` §5, Wave 4) and the tool that decides ``ق-3``.

**The decision this exists to settle, and why nothing else could.** ``ق-3``
asks whether a knowledge corpus stays in one collection PER WORKSPACE
(``kn-<workspace_id>``, what ships) or moves to ONE collection partitioned by
an ``is_tenant`` payload index on ``workspace_id`` (what Qdrant's own
multitenancy guidance recommends at these numbers). §4 of the plan refuses to
recommend either "بلا قياس" and names this step as the measurement. So this
module measures, and the report is the step's OUTPUT -- never its premise.

**Four numbers, because the plan names four**: search latency, resident
memory, boot time, and what CREATING a collection does to a search already in
flight. The fourth is the one a single-collection layout would abolish
outright (a new tenant writes points, it creates nothing), so it is the one
that has to be measured rather than reasoned about.

**Every probe is the retrieval path's own shape, not a convenient one.**
``retrieval.py`` fires TWO searches per question -- dense ``limit=30`` and
sparse ``limit=20``, both ``with_vectors=True`` (§3.9's declared price: a
full 384-float vector per hit crosses the wire), both filtered by
``{"workspace_id": ...}`` (DD-04). A probe that asked for ``limit=10`` with
no payload and no vectors would measure a query this platform never issues,
and would flatter both layouts by the same unknown amount. ``_DENSE_K``/
``_SPARSE_K`` below are that shape, and the settings they mirror are named
beside them so a drift shows up as a mismatch rather than as a quiet lie.

**Query vectors come from the CORPUS, not from a random generator.** Each
probe scrolls real points out of the collection under test and perturbs their
vectors (``_perturb``): an HNSW graph is traversed from wherever the query
lands, and a uniformly random 384-dimensional point is near-orthogonal to
every stored vector -- it exits the graph in a handful of hops and measures a
traversal no user ever causes. Perturbation keeps the query near the
manifold, off any exact point. This also keeps the tool free of ``load_seed``
for every measurement: it reads whatever corpus is in front of it, seeded or
real.

⚠️ **COLD is a separate verb because warm numbers are the ones that lie.**
Qdrant memory-maps segments and loads them lazily, so a collection nobody has
queried since the process started pays its first search tens to hundreds of
times over (measured on the shipped layout: first touch p50 **37.6 ms** ·
p95 **233 ms** · max **853 ms** against a warm p50 of 8-10 ms -- 2x on the
smallest tenant, 77x on the largest). With ONE collection every query is warm
after the first; with 200, a tenant that goes quiet falls out and pays again.
``latency`` reports warm cost, ``cold`` reports first touch, and reporting
only the first would make the 200-collection layout look free. The full
tables live in ``docs/design/08-local-runbook.md §4.16``.

**``shadow`` builds the counterfactual from the SAME generator, not a copy.**
``load_seed``'s corpus is deterministic -- ids, vectors and sparse terms all
derive from ``(seed_id, anchor, ordinal)`` -- so the single-collection layout
can be rebuilt point-for-point from the seed instead of scrolled out of the
live instance. That matters for two reasons: a scroll-and-reinsert would
measure the export as much as the layout, and the two instances would have to
be up at the same time (this host has 12 GB and one of them already holds
5.7 GiB, ``د-25``). The shadow is written to a SEPARATE Qdrant given by
``--url``; it refuses to write into a URL that already carries ``kn-``
collections, because "the counterfactual" and "production's data" must never
be the same box.

Usage::

    python -m app.ops.qdrant_capacity survey  [--url U] [--json]
    python -m app.ops.qdrant_capacity latency [--tenants N] [--queries N] [--ef 0,64,128]
    python -m app.ops.qdrant_capacity cold    [--tenants N]
    python -m app.ops.qdrant_capacity load    [--concurrency N] [--seconds S] [--single C]
    python -m app.ops.qdrant_capacity create-impact [--rounds N]
    python -m app.ops.qdrant_capacity boot    [--timeout S]
    python -m app.ops.qdrant_capacity shadow  --url U [--seed-id ID] [--scale F]
    python -m app.ops.qdrant_capacity optimize [--timeout S]     # a repair, not a report
    python -m app.ops.qdrant_capacity quantize --collection C [--off]

``--json`` on every reporting verb, because these numbers are archived beside
the plan and re-read months later (``deploy/load/results/`` precedent).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient, models

from app.framework.settings.settings import QdrantSettings
from app.infrastructure.config.env_settings import load_settings
from app.infrastructure.vector.qdrant_store import (
    HYBRID_PAYLOAD_INDEXES,
    QdrantVectorStore,
    create_qdrant_client,
    drop_collection,
)
from app.modules.knowledge.domain.sparse import Bm25Params
from app.ops.load_seed import (
    CorpusSize,
    TextPool,
    VectorFactory,
    build_plan,
    manifest_path,
    vector_points,
)

_logger = logging.getLogger(__name__)

#: The knowledge prefix (``knowledge_collection``). ``mem-`` collections are
#: provisioned through the narrower ``ensure_collection`` contract and carry
#: no tenant payload index at all, so they are not what ``ق-3`` is about --
#: the same scoping ``app.ops.payload_indexes`` draws, for the same reason.
_KNOWLEDGE_PREFIX = "kn-"

#: The depths ``retrieval.py`` actually asks for, per question:
#: ``search_k = min(default_k * max(search_overfetch, mmr_overfetch),
#: max_search_candidates)`` = ``min(5 * 6, 100)`` and
#: ``sparse_k = min(search_k, max_sparse_candidates)`` = ``min(30, 20)``.
#: Duplicated as literals rather than read from ``Settings`` on purpose: this
#: tool measures the SHIPPED shape, and a deployment that had tuned those
#: knobs would otherwise silently change what the archived numbers mean.
_DENSE_K = 30
_SPARSE_K = 20

#: How far a probe query is moved off the corpus point it was drawn from.
#: Small enough to stay in the same cluster (the query a user's paraphrase
#: produces), large enough that the answer is not the point itself.
_PROBE_SPREAD = 0.15

#: Points scrolled per collection to build probe queries from.
_PROBE_POOL = 8

#: Percentiles every report prints. p95 because that is what ``07 §2``
#: budgets; max because a 200-collection layout's worst case is the whole
#: question.
_P50 = 50.0
_P95 = 95.0

#: How many rows a human-readable survey prints before it defers to
#: ``--json``. 200 collections is a report nobody reads; the ten largest are
#: where every cost in this step concentrates (Zipf skew, ``load_seed``).
_SURVEY_ROWS = 10

#: The three keys ``HYBRID_PAYLOAD_INDEXES`` provisions. A collection with
#: fewer is one `app.ops.payload_indexes` has not swept, and its search cost
#: is not this layout's cost -- it is an unindexed filter's.
_EXPECTED_PAYLOAD_KEYS = 3

#: Qdrant's own name for the named sparse vector every hybrid collection
#: provisions -- ``qdrant_store._SPARSE_NAME``, restated rather than imported
#: because that one is private to the adapter.
_SPARSE_VECTOR_NAME = "text"

_MS = 1000.0
_MIB = 1024.0 * 1024.0


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CollectionFacts:
    """What the SERVER says about one collection (never what a caller
    assumed) -- ``app.ops.payload_indexes``' "assert, then verify" footing."""

    name: str
    points: int
    indexed_vectors: int
    segments: int
    status: str
    tenant_keys: tuple[str, ...]
    indexed_keys: tuple[str, ...]
    quantized: bool


@dataclass(frozen=True, slots=True)
class MemoryFacts:
    """Qdrant's own allocator accounting, from ``GET /metrics``.

    ⚠️ This is NOT what the container's memory limit is enforced against.
    ``memory_resident_bytes`` is jemalloc's view of the process; the cgroup
    additionally charges the page cache holding memory-mapped segments, and
    on this stack that difference was 1.2 GiB (4.57 vs 5.72 GiB). Both belong
    in a capacity report -- the first says what the process allocated, the
    second says what would OOM -- so the reports print this one and name
    ``docker stats`` for the other rather than pretending one covers both.
    """

    resident_bytes: int
    allocated_bytes: int
    metadata_bytes: int


@dataclass(frozen=True, slots=True)
class Timing:
    """One labelled series of milliseconds."""

    label: str
    samples: tuple[float, ...]

    @property
    def count(self) -> int:
        return len(self.samples)

    def percentile(self, q: float) -> float:
        return percentile(self.samples, q)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "count": self.count,
            "p50_ms": round(self.percentile(_P50), 3),
            "p95_ms": round(self.percentile(_P95), 3),
            "max_ms": round(max(self.samples), 3) if self.samples else 0.0,
            "mean_ms": round(statistics.fmean(self.samples), 3) if self.samples else 0.0,
        }


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, and deliberately not an interpolating one.

    An interpolated p95 over 40 samples INVENTS a value between two measured
    ones; every number in this report is meant to be a latency that actually
    happened. Same convention as the load harness's own summary.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(q / 100.0 * len(ordered))))
    return ordered[rank - 1]


# ---------------------------------------------------------------------------
# Reading the server
# ---------------------------------------------------------------------------
async def read_facts(client: AsyncQdrantClient, name: str) -> CollectionFacts:
    info = await client.get_collection(name)
    schema = info.payload_schema or {}
    tenant: list[str] = []
    for key, spec in schema.items():
        params = getattr(spec, "params", None)
        if getattr(params, "is_tenant", False):
            tenant.append(key)
    return CollectionFacts(
        name=name,
        points=info.points_count or 0,
        indexed_vectors=info.indexed_vectors_count or 0,
        segments=info.segments_count or 0,
        # `str()` because the enum's repr is not what an archived JSON file
        # should carry, and the value is only ever compared to itself.
        status=str(getattr(info.status, "value", info.status)),
        tenant_keys=tuple(sorted(tenant)),
        indexed_keys=tuple(sorted(schema)),
        quantized=info.config.quantization_config is not None,
    )


async def survey(
    client: AsyncQdrantClient, *, prefix: str = _KNOWLEDGE_PREFIX
) -> list[CollectionFacts]:
    """Every ``kn-`` collection the server holds, largest first.

    The target set comes from Qdrant and not from Postgres for the reason
    ``app.ops.payload_indexes`` already gives: the question is "what EXISTS",
    and only the vector store knows.
    """
    listing = await client.get_collections()
    names = sorted(c.name for c in listing.collections if c.name.startswith(prefix))
    facts = [await read_facts(client, name) for name in names]
    return sorted(facts, key=lambda f: f.points, reverse=True)


async def read_memory(url: str) -> MemoryFacts:
    """Parse the three allocator gauges out of Qdrant's Prometheus text.

    A hand-rolled parse rather than a client library: three gauges out of a
    document whose format is two fields per line does not justify a
    dependency, and ``prometheus_client``'s parser is not in this project's
    dependency set.
    """
    async with httpx.AsyncClient(timeout=10.0) as http:
        response = await http.get(f"{url.rstrip('/')}/metrics")
        response.raise_for_status()
    values: dict[str, int] = {}
    for line in response.text.splitlines():
        if line.startswith("#"):
            continue
        name, _, raw = line.partition(" ")
        if name.startswith("memory_"):
            try:
                values[name] = int(float(raw))
            except ValueError:  # a gauge that is not a number is not a gauge
                continue
    return MemoryFacts(
        resident_bytes=values.get("memory_resident_bytes", 0),
        allocated_bytes=values.get("memory_allocated_bytes", 0),
        metadata_bytes=values.get("memory_metadata_bytes", 0),
    )


@dataclass(frozen=True, slots=True)
class IndexFacts:
    """How much of the corpus is actually under an HNSW graph.

    ⚠️ **This is the number that decides ``ق-3``, and nothing in the REST
    collection API shows it.** ``indexed_vectors_count`` on ``GET
    /collections/<name>`` counts the sparse index too, so a collection whose
    dense vectors are all brute-forced still reports a large number there
    (measured: 328,840 "indexed" against 170,120 points). The per-SEGMENT
    breakdown under ``GET /telemetry`` is the only place the dense figure
    exists, which is why this tool reads a 2.4 MB telemetry document rather
    than the endpoint that looks like it answers the question.

    The mechanism: Qdrant builds HNSW per SEGMENT, and only for segments
    above ``indexing_threshold`` (20,000 vectors, the shipped default).
    Splitting a tenant across ``default_segment_number`` segments therefore
    divides its corpus by that factor before the threshold is applied -- so
    the smaller the tenant's collection, the further every one of its
    segments sits below the line.
    """

    collections: int
    segments: int
    dense_vectors: int
    dense_indexed: int
    sparse_indexed: int
    deleted_vectors: int
    vectors_bytes: int
    payloads_bytes: int
    collections_with_hnsw: int
    init_ms_total: int
    init_ms_max: int

    @property
    def brute_forced(self) -> int:
        return self.dense_vectors - self.dense_indexed


async def read_index_facts(url: str, *, prefix: str = _KNOWLEDGE_PREFIX) -> IndexFacts:
    """Fetch ``/telemetry`` and fold it. ``details_level=2`` is the cheapest
    level that carries the per-segment breakdown (measured: levels 2, 3 and 4
    return the same 2.4 MB document for this stack)."""
    async with httpx.AsyncClient(timeout=120.0) as http:
        response = await http.get(f"{url.rstrip('/')}/telemetry", params={"details_level": 2})
        response.raise_for_status()
    return fold_telemetry(response.json(), prefix=prefix)


def fold_telemetry(document: dict[str, Any], *, prefix: str = _KNOWLEDGE_PREFIX) -> IndexFacts:
    """The pure half of ``read_index_facts`` -- separated so the fold is
    testable without a server, since the shape it walks is Qdrant's and
    nothing in this repository would notice if a release changed it."""
    collections = [
        collection
        for collection in document["result"]["collections"]["collections"]
        if str(collection.get("id", "")).startswith(prefix)
    ]
    segments = dense = indexed = sparse = deleted = vector_bytes = payload_bytes = 0
    with_hnsw = 0
    inits: list[int] = []
    for collection in collections:
        collection_indexed = 0
        inits.append(int(collection.get("init_time_ms", 0)))
        for shard in collection.get("shards", []):
            for segment in (shard.get("local") or {}).get("segments", []):
                info = segment["info"]
                segments += 1
                deleted += int(info.get("num_deleted_vectors", 0))
                vector_bytes += int(info.get("vectors_size_bytes", 0))
                payload_bytes += int(info.get("payloads_size_bytes", 0))
                data = info.get("vector_data", {})
                dense += int(data.get("", {}).get("num_vectors", 0))
                collection_indexed += int(data.get("", {}).get("num_indexed_vectors", 0))
                sparse += int(data.get(_SPARSE_VECTOR_NAME, {}).get("num_indexed_vectors", 0))
        indexed += collection_indexed
        if collection_indexed:
            with_hnsw += 1
    return IndexFacts(
        collections=len(collections),
        segments=segments,
        dense_vectors=dense,
        dense_indexed=indexed,
        sparse_indexed=sparse,
        deleted_vectors=deleted,
        vectors_bytes=vector_bytes,
        payloads_bytes=payload_bytes,
        collections_with_hnsw=with_hnsw,
        init_ms_total=sum(inits),
        init_ms_max=max(inits, default=0),
    )


async def wait_ready(url: str, *, timeout_s: float, poll_s: float = 0.05) -> float | None:
    """Seconds until ``GET /readyz`` answers 200, or ``None`` on timeout.

    ``/readyz`` and not ``/livez`` -- the compose healthcheck's own choice
    (ت-3): the first means every shard can serve, the second only that the
    process started, and a boot-time number that stopped at "the process
    started" would report 200 collections as free to load.
    """
    started = time.monotonic()
    deadline = started + timeout_s
    async with httpx.AsyncClient(timeout=5.0) as http:
        while time.monotonic() < deadline:
            try:
                response = await http.get(f"{url.rstrip('/')}/readyz")
                if response.status_code == httpx.codes.OK:
                    return time.monotonic() - started
            except httpx.HTTPError:
                pass  # a refused connection IS the "not up yet" answer
            await asyncio.sleep(poll_s)
    return None


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Probe:
    """One query pair drawn from a collection's own contents."""

    collection: str
    workspace_id: str
    dense: list[float]
    sparse: models.SparseVector | None


def _perturb(vector: Sequence[float], rng: random.Random, spread: float) -> list[float]:
    return [value + spread * rng.gauss(0.0, 1.0) for value in vector]


async def build_probes(
    client: AsyncQdrantClient, collection: str, *, count: int, seed: int
) -> list[Probe]:
    """Scroll real points and turn them into near-neighbour queries.

    ``with_vectors=True`` on the scroll is what makes this possible at all
    and is also why the scroll is done ONCE per collection, outside every
    timed region: it is the most expensive call in the file.
    """
    points, _ = await client.scroll(
        collection_name=collection,
        limit=count,
        with_payload=True,
        with_vectors=True,
    )
    rng = random.Random(seed)
    probes: list[Probe] = []
    for point in points:
        vectors = point.vector
        if not isinstance(vectors, dict):
            continue  # a hybrid collection answers with a NAMED vector map
        dense = vectors.get("")
        sparse = vectors.get("text")
        if not isinstance(dense, list):
            continue
        # A dense vector is a list of NUMBERS here; a list of lists is a
        # multivector, which this platform never provisions
        # (`ensure_hybrid_collection` creates one `VectorParams`). Filtering
        # rather than asserting keeps a foreign collection out of the probe
        # set instead of ending the run over it.
        coordinates = [value for value in dense if isinstance(value, int | float)]
        if len(coordinates) != len(dense):
            continue
        payload = point.payload or {}
        workspace_id = str(payload.get("workspace_id", ""))
        probes.append(
            Probe(
                collection=collection,
                workspace_id=workspace_id,
                dense=_perturb([float(v) for v in coordinates], rng, _PROBE_SPREAD),
                sparse=sparse if isinstance(sparse, models.SparseVector) else None,
            )
        )
    return probes


def _tenant_filter(workspace_id: str) -> models.Filter | None:
    """``retrieval.py``'s own filter (DD-04), and ``None`` only when the
    corpus carries no ``workspace_id`` payload to filter on."""
    if not workspace_id:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id))
        ]
    )


async def _timed_dense(
    client: AsyncQdrantClient, probe: Probe, *, ef: int | None, collection: str | None = None
) -> float:
    params = models.SearchParams(hnsw_ef=ef) if ef else None
    started = time.perf_counter()
    await client.query_points(
        collection_name=collection or probe.collection,
        query=probe.dense,
        using=None,
        query_filter=_tenant_filter(probe.workspace_id),
        limit=_DENSE_K,
        with_payload=True,
        with_vectors=True,
        search_params=params,
    )
    return (time.perf_counter() - started) * _MS


async def _timed_sparse(
    client: AsyncQdrantClient, probe: Probe, *, collection: str | None = None
) -> float:
    """The sparse leg takes no ``hnsw_ef``: it is an inverted index, not a
    graph. Timed anyway because ``retrieval.py`` pays for both on every
    question, and a report that showed the dense half alone would understate
    a query by whatever the sparse half costs."""
    if probe.sparse is None:
        return 0.0
    started = time.perf_counter()
    await client.query_points(
        collection_name=collection or probe.collection,
        query=probe.sparse,
        using="text",
        query_filter=_tenant_filter(probe.workspace_id),
        limit=_SPARSE_K,
        with_payload=True,
        with_vectors=True,
    )
    return (time.perf_counter() - started) * _MS


# ---------------------------------------------------------------------------
# Choosing which tenants to probe
# ---------------------------------------------------------------------------
def select_bands(facts: Sequence[CollectionFacts], count: int) -> list[CollectionFacts]:
    """Spread the probe set across the SIZE distribution, never sample it
    uniformly at random.

    The corpus is Zipf-skewed by construction (``load_seed``: the largest
    workspace holds ~17% of it and the smallest ~0.09%), and a uniform sample
    of 200 tenants is 190 small ones -- it would report the cost of an empty
    collection 190 times and miss the tenant whose p99 the platform actually
    has to survive. Bands walk the sorted list from largest to smallest, so
    the report always contains both ends and whatever is between them.
    """
    usable = [fact for fact in facts if fact.points > 0]
    if not usable or count <= 0:
        return []
    if count >= len(usable):
        return list(usable)
    if count == 1:
        return [usable[0]]
    step = (len(usable) - 1) / (count - 1)
    picked: list[CollectionFacts] = []
    for index in range(count):
        candidate = usable[round(index * step)]
        if candidate not in picked:
            picked.append(candidate)
    return picked


def parse_ef(raw: str) -> list[int | None]:
    """``"0,64,128"`` -> ``[None, 64, 128]``.

    ``0`` means "send no ``search_params`` at all" -- the collection's own
    ``hnsw_ef`` default, i.e. exactly what the shipped adapter does today
    (``QdrantVectorStore.search`` passes none). It is the baseline column and
    is spelled as a value rather than implied, so a sweep always carries the
    thing it is being compared against.
    """
    out: list[int | None] = []
    for chunk in raw.split(","):
        text = chunk.strip()
        if not text:
            continue
        value = int(text)
        out.append(None if value <= 0 else value)
    return out


# ---------------------------------------------------------------------------
# The four measurements
# ---------------------------------------------------------------------------
async def measure_latency(
    client: AsyncQdrantClient,
    targets: Sequence[CollectionFacts],
    *,
    queries: int,
    efs: Sequence[int | None],
) -> list[dict[str, Any]]:
    """WARM cost per tenant, per ``hnsw_ef``.

    Every collection is warmed with one discarded query before any timing
    starts -- ``cold`` is the verb that reports the first touch, and leaving
    it inside this average would put a 500 ms outlier in a p95 that is
    supposed to describe the steady state.
    """
    rows: list[dict[str, Any]] = []
    for fact in targets:
        probes = await build_probes(client, fact.name, count=_PROBE_POOL, seed=hash(fact.name))
        if not probes:
            continue
        await _timed_dense(client, probes[0], ef=None)  # warm-up, discarded
        row: dict[str, Any] = {
            "collection": fact.name,
            "points": fact.points,
            "segments": fact.segments,
            "ef": {},
        }
        for ef in efs:
            dense = [
                await _timed_dense(client, probes[i % len(probes)], ef=ef) for i in range(queries)
            ]
            row["ef"]["default" if ef is None else str(ef)] = Timing(
                label=f"dense ef={ef or 'default'}", samples=tuple(dense)
            ).as_dict()
        sparse = [await _timed_sparse(client, probes[i % len(probes)]) for i in range(queries)]
        row["sparse"] = Timing(label="sparse", samples=tuple(sparse)).as_dict()
        rows.append(row)
    return rows


async def measure_load(
    client: AsyncQdrantClient,
    targets: Sequence[CollectionFacts],
    *,
    concurrency: int,
    seconds: float,
    collection: str | None,
    probes_per_target: int = _PROBE_POOL,
) -> dict[str, Any]:
    """The retrieval PAIR (dense + sparse) under concurrency, for a while.

    **Idle latency is not the capacity question.** ``§0`` budgets 40
    queries/second and ``07 §2`` gives the whole RAG lookup 400 ms; a p50 read
    one query at a time says nothing about either, because what a brute-forced
    dense leg spends is CPU proportional to the corpus, and CPU is the thing
    concurrency contends for. This verb is therefore the one whose numbers the
    ``ق-3`` report is actually built on.

    **Tenants are drawn in proportion to their size.** Not uniformly: the
    corpus is Zipf-skewed, and a uniform draw would spend 95% of the run
    inside collections of a few thousand points -- the cheapest possible
    query, reported as the platform's cost. Weighting by point count is the
    assumption that a workspace's query rate tracks its content, which is
    stated here rather than hidden because it is an assumption and a
    different one would move these numbers.

    ``collection`` overrides the target for the single-collection layout:
    every probe keeps its own tenant filter, so the SAME tenant mix is
    replayed against one box instead of two hundred.
    """
    pools: list[tuple[CollectionFacts, list[Probe]]] = []
    for fact in targets:
        probes = await build_probes(
            client, fact.name, count=probes_per_target, seed=hash(fact.name)
        )
        if probes:
            pools.append((fact, probes))
    if not pools:
        raise ValueError("no probe points in any target collection")
    weights = [float(fact.points) for fact, _ in pools]
    # One discarded query per target BEFORE the clock starts. Without it the
    # first pass through the tenant mix pays every collection's first touch
    # (hundreds of milliseconds each, `cold`'s own number) and a 30-second
    # run would report a p95 that is really a mmap fault -- once, at the
    # start, in a report meant to describe the steady state.
    for _, probes in pools:
        await _timed_dense(client, probes[0], ef=None, collection=collection)
        await _timed_sparse(client, probes[0], collection=collection)

    dense_ms: list[float] = []
    sparse_ms: list[float] = []
    pair_ms: list[float] = []
    errors: dict[str, int] = {}
    stop = time.monotonic() + seconds

    async def worker(index: int) -> None:
        rng = random.Random(index)
        while time.monotonic() < stop:
            _, probes = rng.choices(pools, weights=weights, k=1)[0]
            probe = probes[rng.randrange(len(probes))]
            started = time.perf_counter()
            try:
                dense_ms.append(await _timed_dense(client, probe, ef=None, collection=collection))
                sparse_ms.append(await _timed_sparse(client, probe, collection=collection))
            except Exception as exc:
                # A failed query is COUNTED, never fatal, and never timed: a
                # harness that dies on one dropped connection measures
                # nothing, and a harness that folded the failure into the
                # latency series would report the failure as a fast query.
                # `errors` is part of the report because a run that produced
                # any is not a clean measurement -- and because the shipped
                # adapter has no retry, so every one of these is a
                # `common.internal` a user would have seen.
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
                continue
            pair_ms.append((time.perf_counter() - started) * _MS)

    started_at = time.monotonic()
    async with asyncio.TaskGroup() as group:
        for index in range(concurrency):
            group.create_task(worker(index))
    elapsed = time.monotonic() - started_at

    return {
        "layout": collection or f"{len(pools)} collections",
        "concurrency": concurrency,
        "seconds": round(elapsed, 1),
        "pairs": len(pair_ms),
        "pairs_per_second": round(len(pair_ms) / elapsed, 1) if elapsed else 0.0,
        "errors": errors,
        "pair": Timing(label="dense+sparse", samples=tuple(pair_ms)).as_dict(),
        "dense": Timing(label="dense", samples=tuple(dense_ms)).as_dict(),
        "sparse": Timing(label="sparse", samples=tuple(sparse_ms)).as_dict(),
    }


async def measure_cold(
    client: AsyncQdrantClient, targets: Sequence[CollectionFacts], *, warm_queries: int
) -> list[dict[str, Any]]:
    """FIRST-touch cost, which is only meaningful on a freshly started
    process -- run it immediately after ``boot``.

    One query per collection, timed, then ``warm_queries`` more for the
    contrast. The scroll that builds the probe is itself a first touch, so
    the probe pool is built from ONE point and the cold number is the search
    that follows it: what is being measured is the cost of the first SEARCH a
    quiet tenant pays, which is what a 200-collection layout charges every
    time a workspace comes back after an idle spell.
    """
    rows: list[dict[str, Any]] = []
    for fact in targets:
        probes = await build_probes(client, fact.name, count=1, seed=hash(fact.name))
        if not probes:
            continue
        cold_ms = await _timed_dense(client, probes[0], ef=None)
        warm = [await _timed_dense(client, probes[0], ef=None) for _ in range(warm_queries)]
        rows.append(
            {
                "collection": fact.name,
                "points": fact.points,
                "cold_ms": round(cold_ms, 3),
                "warm": Timing(label="warm", samples=tuple(warm)).as_dict(),
                "ratio": round(cold_ms / statistics.fmean(warm), 1) if warm else None,
            }
        )
    return rows


async def measure_create_impact(
    client: AsyncQdrantClient,
    store: QdrantVectorStore,
    hot: CollectionFacts,
    *,
    rounds: int,
    dim: int,
) -> dict[str, Any]:
    """What provisioning a NEW tenant does to a search already in flight.

    A new workspace's first indexed document calls ``ensure_hybrid_collection``
    -- a create plus three ``create_payload_index`` calls with ``wait=True``
    -- on the same process that is answering everyone else's questions. This
    is the cost ``ق-3``'s single-collection option deletes entirely, so it is
    measured rather than argued: a dense query loop runs against the busiest
    collection while collections are created beside it, and the loop's
    latency is reported for the windows before, during and after.

    The created collections are dropped again at the end. They are named with
    a ``kn-`` prefix on purpose (that is what makes them cost what a real
    tenant's collection costs) and with a UUID-shaped random suffix, so a
    crashed run leaves something an operator can identify and drop by hand.
    """
    probes = await build_probes(client, hot.name, count=_PROBE_POOL, seed=hash(hot.name))
    if not probes:
        raise ValueError(f"{hot.name} yielded no probe points")
    await _timed_dense(client, probes[0], ef=None)

    async def loop(samples: list[float], stop: asyncio.Event) -> None:
        index = 0
        while not stop.is_set():
            samples.append(await _timed_dense(client, probes[index % len(probes)], ef=None))
            index += 1

    before: list[float] = []
    during: list[float] = []
    after: list[float] = []
    creates: list[float] = []
    created: list[str] = []

    stop = asyncio.Event()
    task = asyncio.create_task(loop(before, stop))
    await asyncio.sleep(2.0)
    stop.set()
    await task

    stop = asyncio.Event()
    task = asyncio.create_task(loop(during, stop))
    for _ in range(rounds):
        name = f"{_KNOWLEDGE_PREFIX}capacity44-{random.randbytes(8).hex()}"
        started = time.perf_counter()
        await store.ensure_hybrid_collection(name, dim)
        creates.append((time.perf_counter() - started) * _MS)
        created.append(name)
    stop.set()
    await task

    stop = asyncio.Event()
    task = asyncio.create_task(loop(after, stop))
    await asyncio.sleep(2.0)
    stop.set()
    await task

    for name in created:
        await drop_collection(client, name)

    return {
        "hot_collection": hot.name,
        "hot_points": hot.points,
        "collections_created": rounds,
        "create_ms": Timing(label="ensure_hybrid_collection", samples=tuple(creates)).as_dict(),
        "search_before": Timing(label="before", samples=tuple(before)).as_dict(),
        "search_during": Timing(label="during", samples=tuple(during)).as_dict(),
        "search_after": Timing(label="after", samples=tuple(after)).as_dict(),
    }


# ---------------------------------------------------------------------------
# The counterfactual: ONE collection, `is_tenant` on `workspace_id`
# ---------------------------------------------------------------------------
#: The shadow's name. Not ``kn-<uuid>``: it must be impossible for the
#: application to reach it by computing a collection name from a workspace
#: id, and ``knowledge_collection`` always produces a UUID suffix.
SHADOW_COLLECTION = "kn-shadow-single"

#: The shadow build gets its OWN client because production's does not fit it.
#: ``qdrant_store._TIMEOUT_S`` is 5 s -- a deliberate fail-fast for a
#: request-handling coroutine (07-nfr is sub-second) -- and the first full
#: shadow run died on it at 952,500 points, when one 500-point upsert into
#: the single collection stopped fitting inside 5 s. Raising it HERE, in a
#: bulk tool, does not touch the request path: the number that matters for
#: ``ق-3`` is ``batch_ms_max``, and it is reported rather than hidden.
_BULK_TIMEOUT_S = 120

#: Points per upsert. ``load_seed``'s own batch size, for the reason its
#: comment gives -- and holding it identical is what makes the shadow's build
#: time comparable to the seed run that produced the live layout.
_QDRANT_BATCH = 500

#: Qdrant's own default (``optimizer_config.indexing_threshold``), restored
#: after a bulk load. Named here because the shadow turns it OFF to load and
#: a hard-coded 20,000 further down would be a magic number twice.
_INDEXING_THRESHOLD = 20_000


async def _refuse_live_instance(client: AsyncQdrantClient) -> None:
    """The shadow is destructive of the operator's attention and nothing
    else, but it must never land in the instance holding the real corpus:
    one collection of a million foreign points beside 200 real ones would
    change every number the live layout is being measured for.
    """
    listing = await client.get_collections()
    live = [
        c.name
        for c in listing.collections
        if c.name.startswith(_KNOWLEDGE_PREFIX) and c.name != SHADOW_COLLECTION
    ]
    if live:
        raise ValueError(
            f"refusing: this Qdrant already holds {len(live)} `kn-` collection(s) "
            f"(e.g. {live[0]}). Point --url at a SEPARATE instance."
        )


async def build_shadow(
    store: QdrantVectorStore,
    client: AsyncQdrantClient,
    *,
    seed_id: str,
    dimensions: int,
    scale: float,
    text_pool: int,
    progress: bool,
    skip: int = 0,
    optimize_timeout_s: float = 1800.0,
) -> dict[str, Any]:
    """Write ``load_seed``'s corpus into ONE collection, tenant-partitioned.

    **The corpus is REBUILT, never copied.** ``load_seed``'s generators are
    pure functions of ``(seed_id, anchor, ordinal)``, so the same manifest
    produces the same million points here as it did in the live layout --
    point ids included. A scroll-and-reinsert would need both instances up at
    once, which this host cannot afford (``د-25``), and would measure the
    export as much as the layout.

    **``workspace_id`` becomes the tenant key here and ``space`` stops being
    one.** ``HYBRID_PAYLOAD_INDEXES`` marks ``space`` ``is_tenant`` because in
    the shipped layout the collection is ALREADY one workspace and the
    remaining axis worth reordering storage by is the space. In a single
    collection that is upside down: the axis every search filters on first is
    ``workspace_id``, and two competing ``is_tenant`` keys would ask Qdrant to
    order one storage layout by two different things. This is ``ق-3``'s option
    (b) as §4 words it -- "صندوقٌ واحدٌ مقسَّمٌ بـ`is_tenant` على
    `workspace_id`" -- not a variation on it.

    **``skip`` exists because the first full run did not survive its own
    load.** At 952,500 points a single 500-point upsert took long enough to
    trip ``qdrant_store``'s ``_TIMEOUT_S`` (5 s) and the build died with the
    collection loaded but un-indexed. That timeout is production's own
    fail-fast value, so the crash is a MEASUREMENT, not an accident: it is
    recorded in ``batch_ms_max`` below and it belongs to ``ق-3``. Resuming
    costs nothing correctness-wise -- every id is a pure function of
    ``(seed_id, anchor, ordinal)``, so re-writing a partially-applied batch
    rewrites it byte-for-byte -- but a resumed ``load_seconds`` covers only
    the points this run wrote, which is why ``skipped`` is reported beside it.
    """
    manifest = json.loads(manifest_path(seed_id).read_text(encoding="utf-8"))
    size = CorpusSize(**manifest["size"])
    target = size.scaled(scale) if scale != 1.0 else size
    plan = build_plan(
        seed_id=manifest["seed_id"],
        anchor=datetime.fromisoformat(manifest["anchor"]),
        target=target,
        skew=float(manifest["skew"]),
    )
    settings = load_settings()
    pool = TextPool(
        manifest["seed_id"],
        size=text_pool,
        bm25=Bm25Params(
            k1=settings.sparse.bm25_k1,
            b=settings.sparse.bm25_b,
            avg_len=settings.sparse.bm25_avg_len,
        ),
    )
    factory = VectorFactory(manifest["seed_id"], dimensions=dimensions)

    await store.ensure_hybrid_collection(SHADOW_COLLECTION, dimensions)
    # ⚠️ **The bulk recipe is not an optimization here, it is the only way
    # this finishes** -- and the reason is measured rather than assumed. Built
    # the naive way (payload indexes live, `indexing_threshold` at its
    # default), one 500-point upsert into this collection grew from **0.107 s
    # at the start to 0.966 s by the 200,000th point** -- a 9x degradation
    # that projects to hours for the million, against the 591 s the whole
    # 200-collection seed took. The cause is the collection's SIZE and not the
    # tenant index: a controlled trial writing 60,000 points three ways
    # (plain / `is_tenant` / `is_tenant` + no indexing) came out within 7% of
    # itself (42.8 s · 42.9 s · 45.6 s). Qdrant's own bulk-upload guidance is
    # what is followed below: no payload index and no HNSW during the load,
    # both asserted afterwards.
    #
    # That order also happens to be the honest one for `ق-3`: a migration to
    # one collection IS a bulk load followed by a full re-index, and this
    # function's phase timings are that migration's cost.
    for key, _ in HYBRID_PAYLOAD_INDEXES:
        await client.delete_payload_index(collection_name=SHADOW_COLLECTION, field_name=key)
    await client.update_collection(
        collection_name=SHADOW_COLLECTION,
        optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),
    )

    started = time.monotonic()
    written = 0
    seen = 0
    batch_ms: list[float] = []
    batch: list[Any] = []

    async def flush(points: list[Any]) -> None:
        mark = time.monotonic()
        await store.upsert(SHADOW_COLLECTION, points)
        batch_ms.append((time.monotonic() - mark) * 1000.0)

    for workspace in plan.workspaces:
        if workspace.vectors == 0:
            continue
        for point in vector_points(plan, workspace, pool, factory):
            seen += 1
            if seen <= skip:
                continue
            batch.append(point)
            if len(batch) >= _QDRANT_BATCH:
                await flush(batch)
                written += len(batch)
                batch = []
                if progress and written % 50_000 == 0:
                    print(
                        f"  {skip + written:,} / {target.vectors:,}"
                        f"  ({written / (time.monotonic() - started):,.0f}/s"
                        f"  batch p95 {percentile(batch_ms, 95):,.0f}ms)",
                        file=sys.stderr,
                    )
    if batch:
        await flush(batch)
        written += len(batch)
    loaded = time.monotonic() - started

    # **`workspace_id` becomes the tenant key and `space` stops being one**
    # (see the docstring). Asserted here, on a full collection, because that
    # is where a migration would pay for it -- and the wall clock below is
    # what that costs.
    indexing = time.monotonic()
    await store.ensure_payload_index(SHADOW_COLLECTION, "workspace_id", tenant=True)
    await store.ensure_payload_index(SHADOW_COLLECTION, "document_id", tenant=False)
    await store.ensure_payload_index(SHADOW_COLLECTION, "space", tenant=False)
    await client.update_collection(
        collection_name=SHADOW_COLLECTION,
        optimizers_config=models.OptimizersConfigDiff(indexing_threshold=_INDEXING_THRESHOLD),
    )
    green = await _wait_optimized(client, SHADOW_COLLECTION, timeout_s=optimize_timeout_s)
    indexed = time.monotonic() - indexing

    facts = await read_facts(client, SHADOW_COLLECTION)
    return {
        "collection": SHADOW_COLLECTION,
        "seed_id": manifest["seed_id"],
        "scale": scale,
        "written": written,
        "skipped": skip,
        "load_seconds": round(loaded, 1),
        "batch_ms_p50": round(percentile(batch_ms, 50), 1),
        "batch_ms_p95": round(percentile(batch_ms, 95), 1),
        "batch_ms_max": round(max(batch_ms), 1) if batch_ms else 0.0,
        "index_seconds": round(indexed, 1),
        "reached_green": green,
        "server_points": facts.points,
        "segments": facts.segments,
        "tenant_keys": list(facts.tenant_keys),
    }


async def _wait_optimized(client: AsyncQdrantClient, name: str, *, timeout_s: float) -> bool:
    """Block until the collection reports ``green``.

    ``green`` is the only status that means "every segment is indexed as
    configured". ``yellow`` is an optimization in flight and ``grey`` is one
    that is PENDING and will not start until the next update -- and a
    measurement taken in either state is measuring a half-built index.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (await read_facts(client, name)).status == "green":
            return True
        await asyncio.sleep(2.0)
    return False


async def unstick(
    client: AsyncQdrantClient, store: QdrantVectorStore, *, timeout_s: float
) -> list[dict[str, Any]]:
    """Trigger the PENDING optimization on every ``grey`` collection.

    ⚠️ **This is a repair, and the state it repairs is one nothing else in the
    stack will ever leave.** ``grey`` means "optimizations are pending and
    have not started"; Qdrant does not start them on load, it starts them on
    the next UPDATE. A collection that was written once and then went quiet
    therefore keeps whatever segment layout it had when the process last
    stopped -- and if those segments each sit below ``indexing_threshold``,
    its dense vectors have no HNSW graph at all and every search over them is
    a full scan. Measured on this stack: 202 collections, ALL grey, with
    158,720 of 1,006,947 dense vectors indexed (15.8%), surviving restart
    after restart.

    The trigger is the smallest write that exists: ONE point read out of the
    collection and written straight back, byte for byte -- same id, same
    vector, same payload. It is a genuine update as far as the optimizer is
    concerned and a no-op as far as the corpus is concerned. Measured on a
    grey 85,060-point collection: ``grey``/8 segments/0 dense indexed became
    ``green``/2 segments/74,308 dense indexed in **14 seconds**.

    An empty collection is skipped rather than reported as a failure: there is
    no point to write back, and nothing to optimize.
    """
    results: list[dict[str, Any]] = []
    for fact in await survey(client):
        if fact.status == "green" or fact.points == 0:
            continue
        points, _ = await client.scroll(
            collection_name=fact.name, limit=1, with_payload=True, with_vectors=True
        )
        if not points:
            continue
        point = points[0]
        started = time.perf_counter()
        await client.upsert(
            collection_name=fact.name,
            points=[models.PointStruct(id=point.id, vector=point.vector, payload=point.payload)],
            wait=True,
        )
        green = await _wait_optimized(client, fact.name, timeout_s=timeout_s)
        after = await read_facts(client, fact.name)
        results.append(
            {
                "collection": fact.name,
                "points": fact.points,
                "seconds": round(time.perf_counter() - started, 1),
                "status": after.status,
                "segments_before": fact.segments,
                "segments_after": after.segments,
                "indexed_before": fact.indexed_vectors,
                "indexed_after": after.indexed_vectors,
                "green": green,
            }
        )
    return results


async def set_quantization(client: AsyncQdrantClient, name: str, *, enabled: bool) -> None:
    """Scalar (int8) quantization on/off for one collection.

    ``always_ram=True`` is the whole point of the setting: the quantized
    vectors are what searching gets to keep resident, and the originals fall
    back to disk for the rescoring pass. Quantization that lived on disk
    would trade memory for a second read on every query -- the opposite of
    what the plan asks for it ("إن أظهر القياس ضغطَ ذاكرة").
    """
    config: models.ScalarQuantization | None = None
    if enabled:
        config = models.ScalarQuantization(
            scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8, quantile=0.99, always_ram=True
            )
        )
    await client.update_collection(
        collection_name=name,
        quantization_config=config if enabled else models.Disabled.DISABLED,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_survey(facts: Sequence[CollectionFacts], memory: MemoryFacts, index: IndexFacts) -> str:
    points = sum(f.points for f in facts)
    segments = sum(f.segments for f in facts)
    lines = [
        f"collections   {len(facts):>12,}",
        f"points        {points:>12,}",
        f"segments      {segments:>12,}   ({segments / len(facts):.1f} per collection)"
        if facts
        else "",
        f"resident      {memory.resident_bytes / _MIB:>12,.0f} MiB   (jemalloc; the cgroup "
        f"also charges the page cache -- see `docker stats`)",
        f"allocated     {memory.allocated_bytes / _MIB:>12,.0f} MiB",
        f"metadata      {memory.metadata_bytes / _MIB:>12,.0f} MiB"
        f"   ({memory.metadata_bytes / max(1, points):.0f} bytes/point)",
        f"vectors       {index.vectors_bytes / _MIB:>12,.0f} MiB   (segment-reported)",
        f"payloads      {index.payloads_bytes / _MIB:>12,.0f} MiB   (segment-reported)",
        "",
        f"dense under HNSW  {index.dense_indexed:>10,} / {index.dense_vectors:,}"
        f"   ({index.dense_indexed / max(1, index.dense_vectors):.1%}) in "
        f"{index.collections_with_hnsw} of {index.collections} collections",
        f"brute-forced      {index.brute_forced:>10,}   (every segment below `indexing_threshold`)",
        f"deleted vectors   {index.deleted_vectors:>10,}",
        f"quantized         {sum(1 for f in facts if f.quantized):>10,} of {len(facts)} "
        f"collections",
        f"segment init      {index.init_ms_total / _MS:>10,.1f}s total · "
        f"{index.init_ms_max}ms worst collection",
        "",
        f"{'collection':<44}{'points':>10}{'segs':>6}{'status':>8}  tenant keys",
    ]
    for fact in facts[:_SURVEY_ROWS]:
        lines.append(
            f"{fact.name:<44}{fact.points:>10,}{fact.segments:>6}{fact.status:>8}  "
            f"{','.join(fact.tenant_keys) or '-'}"
        )
    if len(facts) > _SURVEY_ROWS:
        lines.append(f"... {len(facts) - _SURVEY_ROWS} more (--json for all)")
    unindexed = [f.name for f in facts if len(f.indexed_keys) < _EXPECTED_PAYLOAD_KEYS]
    if unindexed:
        lines.append("")
        lines.append(
            f"⚠️  {len(unindexed)} collection(s) carry fewer than three payload indexes "
            f"-- `python -m app.ops.payload_indexes run`"
        )
    return "\n".join(line for line in lines if line != "")


def render_latency(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "no collection carried a probe point"
    efs = list(rows[0]["ef"])
    header = f"{'collection':<44}{'points':>10}" + "".join(f"{'ef ' + e:>18}" for e in efs)
    lines = [header + f"{'sparse':>18}", "-" * len(header)]
    for row in rows:
        cells = "".join(
            f"{row['ef'][e]['p50_ms']:>8.1f}/{row['ef'][e]['p95_ms']:<9.1f}" for e in efs
        )
        lines.append(
            f"{row['collection']:<44}{row['points']:>10,}{cells}"
            f"{row['sparse']['p50_ms']:>8.1f}/{row['sparse']['p95_ms']:<9.1f}"
        )
    lines.append("")
    lines.append(
        "cells are p50/p95 milliseconds, dense limit=30 + sparse limit=20, both "
        "`with_vectors=True` and workspace-filtered (the retrieval path's own shape)"
    )
    return "\n".join(lines)


def render_load(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"layout        {report['layout']}",
            f"concurrency   {report['concurrency']}   for {report['seconds']}s",
            f"pairs         {report['pairs']:,}   ({report['pairs_per_second']} query-pairs/s)",
            f"errors        {report['errors'] or 'none'}",
            "",
            f"{'leg':<10}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}",
            *(
                f"{name:<10}{report[key]['p50_ms']:>10.1f}"
                f"{report[key]['p95_ms']:>10.1f}{report[key]['max_ms']:>10.1f}"
                for name, key in (("dense", "dense"), ("sparse", "sparse"), ("pair", "pair"))
            ),
        ]
    )


def render_optimize(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "every collection is already green -- nothing pending"
    lines = [
        f"{row['collection']:<44}{row['points']:>10,}  "
        f"{row['segments_before']}->{row['segments_after']} segs  "
        f"indexed {row['indexed_before']:,}->{row['indexed_after']:,}  "
        f"{row['seconds']:.1f}s  {row['status']}"
        for row in rows
    ]
    gained = sum(row["indexed_after"] - row["indexed_before"] for row in rows)
    lines.append("")
    lines.append(f"{len(rows)} collection(s) triggered · {gained:,} vectors newly indexed")
    return "\n".join(lines)


def render_cold(rows: Sequence[dict[str, Any]]) -> str:
    lines = [f"{'collection':<44}{'points':>10}{'cold ms':>10}{'warm p50':>10}{'ratio':>8}", ""]
    for row in rows:
        lines.append(
            f"{row['collection']:<44}{row['points']:>10,}{row['cold_ms']:>10.1f}"
            f"{row['warm']['p50_ms']:>10.1f}{(row['ratio'] or 0):>7.0f}x"
        )
    colds = [row["cold_ms"] for row in rows]
    if colds:
        lines.append("")
        lines.append(
            f"first touch: p50 {percentile(colds, _P50):.1f} ms · "
            f"p95 {percentile(colds, _P95):.1f} ms · max {max(colds):.1f} ms "
            f"over {len(colds)} collections"
        )
    return "\n".join(lines)


def render_create_impact(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"hot collection      {report['hot_collection']} ({report['hot_points']:,} points)",
            f"collections created {report['collections_created']}",
            f"create call         p50 {report['create_ms']['p50_ms']:.1f} ms · "
            f"p95 {report['create_ms']['p95_ms']:.1f} ms · max {report['create_ms']['max_ms']:.1f}",
            "",
            f"{'window':<10}{'queries':>9}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}",
            *(
                f"{name:<10}{report[key]['count']:>9}{report[key]['p50_ms']:>10.1f}"
                f"{report[key]['p95_ms']:>10.1f}{report[key]['max_ms']:>10.1f}"
                for name, key in (
                    ("before", "search_before"),
                    ("during", "search_during"),
                    ("after", "search_after"),
                )
            ),
        ]
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
async def _survey_report(client: AsyncQdrantClient, url: str, *, as_json: bool) -> str:
    facts = await survey(client)
    memory = await read_memory(url)
    index = await read_index_facts(url)
    if not as_json:
        return render_survey(facts, memory, index)
    return json.dumps(
        {
            "memory": vars(memory),
            "index": {**vars(index), "brute_forced": index.brute_forced},
            "collections": [vars(fact) for fact in facts],
        },
        ensure_ascii=False,
        indent=2,
        default=list,
    )


def _client(args: argparse.Namespace) -> AsyncQdrantClient:
    url = args.url or load_settings().qdrant.url
    return create_qdrant_client(QdrantSettings(url=url))


def _url(args: argparse.Namespace) -> str:
    return str(args.url or load_settings().qdrant.url)


async def _run(args: argparse.Namespace) -> int:  # noqa: PLR0911, PLR0912
    if args.action == "boot":
        # No client at all: the whole point is to time a server that is not
        # answering yet, and building a client would only add a connection
        # nobody uses.
        seconds = await wait_ready(_url(args), timeout_s=args.timeout)
        if seconds is None:
            print(f"NOT READY after {args.timeout:.0f}s", file=sys.stderr)
            return 1
        print(
            json.dumps({"ready_seconds": round(seconds, 2)})
            if args.json
            else f"ready in {seconds:.2f}s"
        )
        return 0

    client = _client(args)
    try:
        if args.action == "survey":
            print(await _survey_report(client, _url(args), as_json=args.json))
            return 0

        if args.action == "latency":
            facts = await survey(client)
            targets = (
                [f for f in facts if f.name == args.collection]
                if args.collection
                else select_bands(facts, args.tenants)
            )
            rows = await measure_latency(
                client, targets, queries=args.queries, efs=parse_ef(args.ef)
            )
            print(json.dumps(rows, indent=2) if args.json else render_latency(rows))
            return 0

        if args.action == "load":
            # The tenant MIX always comes from the collections that exist here
            # (their point counts are the weights). `--single` only changes
            # WHERE the queries are sent; every probe keeps its own
            # `workspace_id` filter either way.
            facts = await survey(client)
            report = await measure_load(
                client,
                select_bands(facts, args.tenants),
                concurrency=args.concurrency,
                seconds=args.seconds,
                collection=args.single or None,
                probes_per_target=args.probes,
            )
            print(json.dumps(report, indent=2) if args.json else render_load(report))
            return 0

        if args.action == "cold":
            facts = await survey(client)
            targets = select_bands(facts, args.tenants)
            rows = await measure_cold(client, targets, warm_queries=args.warm)
            print(json.dumps(rows, indent=2) if args.json else render_cold(rows))
            return 0

        if args.action == "create-impact":
            facts = await survey(client)
            targets = (
                [f for f in facts if f.name == args.collection] if args.collection else facts[:1]
            )
            if not targets:
                print("no collection to run the hot loop against", file=sys.stderr)
                return 2
            report = await measure_create_impact(
                client,
                QdrantVectorStore(client),
                targets[0],
                rounds=args.rounds,
                dim=load_settings().embedding_service.dimensions,
            )
            print(json.dumps(report, indent=2) if args.json else render_create_impact(report))
            return 0

        if args.action == "shadow":
            await _refuse_live_instance(client)
            # Deliberately NOT `create_qdrant_client`: that factory hands out
            # the request path's 5 s fail-fast, and this is the one caller
            # that must not have it (see `_BULK_TIMEOUT_S`). Every other
            # option is held identical to the factory's.
            bulk = AsyncQdrantClient(
                url=_url(args),
                prefer_grpc=False,
                timeout=_BULK_TIMEOUT_S,
                check_compatibility=False,
            )
            report = await build_shadow(
                QdrantVectorStore(bulk),
                bulk,
                seed_id=args.seed_id,
                dimensions=load_settings().embedding_service.dimensions,
                scale=args.scale,
                text_pool=args.text_pool,
                progress=not args.no_progress,
                skip=args.skip,
            )
            print(json.dumps(report, indent=2))
            return 0

        if args.action == "optimize":
            rows = await unstick(client, QdrantVectorStore(client), timeout_s=args.timeout)
            print(json.dumps(rows, indent=2) if args.json else render_optimize(rows))
            return 0

        if args.action == "quantize":
            await set_quantization(client, args.collection, enabled=not args.off)
            print(
                f"{args.collection}: scalar int8 quantization "
                f"{'disabled' if args.off else 'enabled'} -- Qdrant re-optimizes in the "
                f"background; re-run `survey` once `status` is green"
            )
            return 0

        return 2
    except ValueError as exc:  # a refusal, not a fault
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        await client.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.qdrant_capacity",
        description="What 200 tenants cost Qdrant, and the numbers that decide ق-3 "
        "(capacity step 4.4).",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Qdrant base URL (default: QDRANT_URL). Point it at the shadow instance to "
        "measure the single-collection layout.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    survey_p = sub.add_parser("survey", help="inventory + allocator memory")
    survey_p.add_argument("--json", action="store_true")

    latency_p = sub.add_parser("latency", help="WARM search cost per tenant, per hnsw_ef")
    latency_p.add_argument("--tenants", type=int, default=8, help="probe set size (size bands)")
    latency_p.add_argument("--queries", type=int, default=40, help="timed queries per cell")
    latency_p.add_argument(
        "--ef", default="0", help="comma-separated hnsw_ef sweep; 0 = the collection default"
    )
    latency_p.add_argument("--collection", default=None, help="one collection instead of bands")
    latency_p.add_argument("--json", action="store_true")

    load_p = sub.add_parser("load", help="the retrieval PAIR under concurrency -- the §0 shape")
    load_p.add_argument("--concurrency", type=int, default=8)
    load_p.add_argument("--seconds", type=float, default=30.0)
    load_p.add_argument("--tenants", type=int, default=24, help="tenant mix, weighted by size")
    load_p.add_argument(
        "--single",
        default=None,
        help="send every query to ONE collection (the shadow layout) while keeping each "
        "probe's own workspace filter",
    )
    load_p.add_argument(
        "--probes",
        type=int,
        default=_PROBE_POOL,
        help="distinct query pairs drawn per target. On the single-collection layout this is "
        "what supplies the TENANT MIX (each probe carries its own workspace filter), so it "
        "wants to be at least as large as the tenant count used against the live layout.",
    )
    load_p.add_argument("--json", action="store_true")

    cold_p = sub.add_parser("cold", help="FIRST-touch cost -- run right after a restart")
    cold_p.add_argument("--tenants", type=int, default=20)
    cold_p.add_argument("--warm", type=int, default=5, help="warm queries for the contrast")
    cold_p.add_argument("--json", action="store_true")

    create_p = sub.add_parser("create-impact", help="what provisioning a tenant does to a search")
    create_p.add_argument("--rounds", type=int, default=10, help="collections to create")
    create_p.add_argument(
        "--collection", default=None, help="the hot collection (default: the largest)"
    )
    create_p.add_argument("--json", action="store_true")

    boot_p = sub.add_parser(
        "boot", help="seconds until /readyz answers -- start it with the restart"
    )
    boot_p.add_argument("--timeout", type=float, default=300.0)
    boot_p.add_argument("--json", action="store_true")

    shadow_p = sub.add_parser("shadow", help="build ق-3's single-collection counterfactual")
    shadow_p.add_argument("--seed-id", default="dev", help="the load_seed manifest to rebuild")
    shadow_p.add_argument("--scale", type=float, default=1.0, help="fraction of the corpus")
    shadow_p.add_argument("--text-pool", type=int, default=4096, help="load_seed's own default")
    shadow_p.add_argument(
        "--skip",
        type=int,
        default=0,
        help="resume a crashed build: skip the first N points of the corpus (ids are "
        "deterministic, so a partially-written batch is safe to re-write)",
    )
    shadow_p.add_argument("--no-progress", action="store_true")

    opt_p = sub.add_parser(
        "optimize", help="trigger the PENDING optimization on every non-green collection"
    )
    opt_p.add_argument("--timeout", type=float, default=300.0, help="per collection")
    opt_p.add_argument("--json", action="store_true")

    quant_p = sub.add_parser("quantize", help="scalar int8 quantization on one collection")
    quant_p.add_argument("--collection", required=True)
    quant_p.add_argument("--off", action="store_true", help="disable instead of enable")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    # `survey` reads 202 collections one call at a time and `load` issues
    # thousands: at INFO, httpx narrates every one of them and buries the
    # report under its own transport log. The other `app.ops.*` tools do not
    # need this because none of them makes more than a handful of calls.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raise SystemExit(asyncio.run(_run(_build_parser().parse_args())))


if __name__ == "__main__":
    main()
