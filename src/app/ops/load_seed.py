"""Realistic-corpus seeder for the load harness -- condition (3) of capacity
step 0.1 (``docs/capacity-plan.md`` §5, Wave 0) and the blocker ``د-2``.

**What was missing.** ``deploy/load/`` has been able to run since 0.1's
scaffolding landed, and every run it produced was stamped ``"valid": false``
by ``lib/config.js``'s own floor check, because the third of the plan's three
conditions was never met: «**بذرةُ بياناتٍ بحجمٍ واقعيّ** — مليونُ رسالةٍ ·
100 ألف ملفٍّ · مليونُ متّجهٍ موزّعةٌ على 200 مساحة عمل، تُولَّد بأداةٍ
تحترم RLS. استعلامٌ على جدولٍ فارغٍ يقيس الفهرسَ لا المنصّة.» Against the
empty database this repository ships, ``GET /files`` returns in the time it
takes to plan the statement, ``/knowledge/search`` asks Qdrant a question
about a collection that does not exist, and every p95 in the archive is the
p95 of an index scan over zero rows. This module is the tool that sentence
demands, and ``0.5``'s baseline cannot be measured until it has been run.

**"A tool that respects RLS" is not a style note -- it forbids the fast
path.** PostgreSQL refuses ``COPY FROM`` outright on a table whose row-level
security applies to the copying role::

    ERROR:  COPY FROM not supported with row-level security
    HINT:  Use INSERT statements instead.

(measured on this cluster as ``app_rw``, 2026-09-03). So the bulk load is
batched multi-row ``INSERT`` under ``SET LOCAL app.workspace_id`` -- the same
Layer-1 guard every request-path transaction sets (``infrastructure/
persistence/rls.py``, DD-04) -- and the seeder is slower than a ``COPY``
loader by construction. That is the correct trade: a corpus written *around*
RLS could contain rows no tenant can reach, or rows reachable from the wrong
tenant, and a load run against it would measure a database the application
can never produce. The tool refuses to run as a superuser or a
``BYPASSRLS`` role for the same reason (``_refuse_privileged_role``): the
guarantee is only worth what the connecting role cannot do.

``workspace.workspaces`` is the one table written with no tenant GUC, and it
is not an exception to the above -- that table carries no RLS at all
(verified live: ``relrowsecurity = false``), because it *is* the tenant root.
A policy keyed on ``workspace_id = current_setting('app.workspace_id')``
cannot gate the row that defines the value.

**Determinism, and why the ids are UUIDv7-shaped rather than ``uuid5``.**
Every id is derived from ``(seed id, kind, ordinal)`` through ``blake2b``, so
a second run of the same seed id produces the same corpus and every INSERT is
``ON CONFLICT DO NOTHING`` -- the tool is idempotent and resumable after an
interrupted run, which matters when the full corpus takes tens of minutes.
But a plain ``uuid5`` would scatter the primary keys randomly across their
B-trees, and DD-02 mints UUIDv7 precisely so that production's inserts land
at the right-hand edge of the index. A corpus keyed randomly would carry
index bloat and a buffer-hit ratio production never has, and 0.5's baseline
would inherit both. ``_seeded_uuid7`` therefore builds the RFC 9562 v7 layout
by hand: the 48-bit millisecond field comes from the row's own simulated
timestamp, and only the 74 random bits come from the digest. Deterministic
*and* time-ordered.

The simulated history is anchored at ``--as-of`` (default: today, 00:00 UTC)
and runs back ``HISTORY_DAYS``. The anchor is part of the id derivation, so
re-running tomorrow with the same seed id does not half-overwrite yesterday's
corpus -- it writes a second, disjoint one. Name seed ids by date
(``dev-2026-09-03``) and this never comes up.

**Skew is deliberate.** Content is allocated across workspaces by a Zipf
weight (``--skew``, default 1.0), not evenly. 200 tenants each holding
exactly 5,000 messages is not a realistic 1M-message corpus: it is one
tenant's query cost measured 200 times. Real tenant size spans orders of
magnitude, and it is the largest tenant that finds the missing index and
produces the p99 the baseline has to report. At the default skew the largest
workspace holds ~17% of the corpus and the smallest ~0.09%.

**What is synthetic, stated plainly -- this corpus is sized, not real.**

* **Embeddings are generated, not computed.** Asking the embedding service
  for a million vectors would take longer than the load run it prepares and
  would measure ``ح-4``, not seed for it. Vectors are drawn around a handful
  of per-workspace centroids so the HNSW graph has genuine cluster structure
  to build (uniformly random 384-dimensional points are near-orthogonal, and
  a graph over them degenerates -- the *opposite* of a realistic index), but
  no vector here means anything. Retrieval QUALITY cannot be measured on
  this corpus; retrieval COST can, which is what Wave 0 asks for.
* **Chunk text is drawn from a pool** of ``--text-pool`` distinct paragraphs
  over a Zipf-sampled synthetic vocabulary. The BM25 term vector is then
  computed ONCE per distinct text by the application's own
  ``build_document_terms`` -- the same function ``knowledge`` indexes with,
  so the stored sparse vectors have the shape the hybrid search reads -- and
  reused. A pool rather than a million unique texts because tokenising a
  million paragraphs costs minutes and buys nothing: what a capacity run
  measures is row count, index size and TOAST pressure, not prose.
* **No object bytes.** ``files.files`` rows are written; nothing is uploaded
  to MinIO. The load harness's ``index`` scenario uploads its own file, and
  100,000 objects of real content would be a storage benchmark, which is a
  different question from ``ح-6``. A scenario that downloads a seeded file
  would get a 404 from storage -- none does today (``deploy/load/README.md``
  §5), and this is the sentence to revisit if one is ever added.
* **No parent chunks, no summaries, no outbox rows.** The seeder writes the
  tables the harness's read paths touch. ``knowledge.parent_chunks`` is left
  empty and ``chunks.parent_id`` NULL, which is the same shape every document
  indexed before the parent-chunk work already has.

**Connect DIRECTLY to ``postgres:5432``, not through PgBouncer** --
``app.ops.slow_queries``'s reason, doubled. The pooler's ``MAX_CLIENT_CONN``
is bottleneck ``ح-3`` and the thing 0.5 is meant to measure; a seeder that
holds one of its slots for half an hour while writing two million rows is
perturbing the instrument it is preparing. It also has no business being
transaction-pooled: ``SET LOCAL`` per transaction is exactly the pattern
PgBouncer's transaction mode is safe for, but the write volume would evict
every request-path connection from the pool for the duration.

``DATABASE_URL`` for THIS process must be ``app_rw``'s OWN DSN pointed at
``postgres:5432`` (one role per process, never one role wearing another's
hat -- ``provision.py``'s convention). From the host, that is the mapped
port; from a container on ``aizzak_default``, the service name::

    # host (ports from .env)
    export DATABASE_URL="postgresql+asyncpg://app_rw:$APP_RW_PASSWORD@127.0.0.1:${HOST_PORT_POSTGRES:-15432}/aizzak"
    export QDRANT_URL="http://127.0.0.1:${HOST_PORT_QDRANT:-16333}"

Usage::

    python -m app.ops.load_seed plan   [--seed-id ID] [--scale F] [--skew S]
    python -m app.ops.load_seed run    [--seed-id ID] [--scale F]
                                       [--only postgres|qdrant]
                                       [--include-workspace UUID ...]
    python -m app.ops.load_seed status [--seed-id ID] [--export] [--json]
    python -m app.ops.load_seed purge  --seed-id ID --yes

``run`` writes a manifest to ``deploy/load/seeds/<seed-id>.json`` recording
exactly what it wrote; ``status --export`` renders that manifest as the
``LOAD_SEED_*`` environment block ``deploy/load/run.sh`` archives into every
result file. The manifest is the *declaration* condition (3) asks for -- it
is why a run can say ``"realistic_seed": true`` and be checked afterwards,
instead of an operator remembering a number.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import sys
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.ports.vector_store import SparseVector, VectorPoint
from app.framework.settings.settings import DatabaseSettings, Settings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine
from app.infrastructure.vector.qdrant_store import (
    QdrantVectorStore,
    create_qdrant_client,
    drop_collection,
)
from app.modules.knowledge.domain.collections import chunk_point_id, knowledge_collection
from app.modules.knowledge.domain.sparse import Bm25Params, SparseTerms, build_document_terms

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CorpusSize:
    """The four numbers ``deploy/load/lib/config.js`` compares against its
    ``SEED_FLOOR``, in the same units and under the same names."""

    workspaces: int
    messages: int
    files: int
    vectors: int

    def scaled(self, factor: float) -> CorpusSize:
        """A proportionally smaller corpus, floored at one of everything.

        ``--scale`` exists for one purpose: proving the tool end to end
        against the live stack in a minute instead of an hour. A scaled
        corpus is below the floor by construction and can never make a run a
        baseline -- ``lib/config.js`` decides that from the declared numbers,
        which is why the manifest records the scaled truth and not the
        target."""
        return CorpusSize(
            workspaces=max(1, round(self.workspaces * factor)),
            messages=max(1, round(self.messages * factor)),
            files=max(1, round(self.files * factor)),
            vectors=max(1, round(self.vectors * factor)),
        )


#: ``docs/capacity-plan.md`` §5 step 0.1, condition (3), and the same literal
#: ``deploy/load/lib/config.js``'s ``SEED_FLOOR`` holds. Two copies in two
#: languages is a drift risk, and ``tests/unit/test_load_seed.py`` compares
#: them character by character rather than trusting the coincidence.
FLOOR = CorpusSize(workspaces=200, messages=1_000_000, files=100_000, vectors=1_000_000)

#: Thread length. 1M messages over 20-message threads is 50,000 conversations
#: -- the ratio matters more than either number: `list_messages` reads one
#: thread, so a corpus of a million single-message threads would make every
#: read cheap and every conversation-list read absurd.
MESSAGES_PER_CONVERSATION = 20

#: How far back the simulated history runs. Long enough that `created_at`
#: ranges and the `ix_conv_ws_agent` partial index have something to
#: discriminate on; short enough that every row is inside any retention
#: window `app.ops.retention` would sweep on.
HISTORY_DAYS = 90

#: Spaces per workspace. The spaces work made `space_id` an ownership axis on
#: files, conversations and documents, and Qdrant's `space` payload index is
#: declared `is_tenant=True` -- a corpus with one space per workspace would
#: leave that partition trivially satisfiable and measure nothing about it.
SPACES_PER_WORKSPACE = 2

#: Dense-vector cluster centres per workspace -- see the module docstring on
#: why the vectors are clustered rather than uniform.
CENTROIDS_PER_WORKSPACE = 8

#: How far a point wanders from its centroid. At 0.35 the clusters are
#: distinguishable without being degenerate: cosine similarity within a
#: cluster lands around 0.9 and across clusters around 0.0, which is roughly
#: what a real embedding corpus of related documents looks like.
_CENTROID_SPREAD = 0.35

#: Distinct perturbation vectors reused across points. 512 x 8 centroids x
#: 200 workspaces is 819,200 distinct dense vectors before a single one
#: repeats, which is more than the corpus has points per collection.
_NOISE_POOL = 512

#: Rows per INSERT statement. Large enough that the round trip is amortised,
#: small enough that one failed batch is a small rollback and the progress
#: line moves often enough to be believable.
_PG_BATCH = 1_000

#: Points per Qdrant upsert. Smaller than the Postgres batch because each
#: point carries a 384-float vector AND its chunk text: at 2,000 the request
#: body passes 10 MB and the client's own serialisation becomes the cost.
_QDRANT_BATCH = 256

#: How often the stderr progress line redraws. Half a second is fast
#: enough to look alive and slow enough that a 50,000-rows-per-second
#: batch loop is not spending its time on `write(2)`.
_PROGRESS_INTERVAL_S = 0.5

#: How many workspaces `plan` prints in full before eliding the middle --
#: enough to see the top of the skew and the bottom of it.
_PLAN_HEAD = 5
_PLAN_TAIL = 2

#: Target length of one generated chunk, in characters -- the middle of what
#: the real chunker emits for prose.
_CHUNK_CHARS = 600

#: Agent keys content is spread across. Slugs, because `AgentKey` validates
#: `^[a-z0-9][a-z0-9_-]*$` and a corpus that could not have been written
#: through the domain is not a corpus of this platform.
_AGENT_KEYS = ("assistant", "researcher", "analyst", "writer")

_ROLES = ("user", "assistant")

_CONTENT_TYPES = (
    "application/pdf",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
)

#: The synthetic vocabulary's building blocks. Terms are formed by
#: concatenating two of these, giving 128x128 = 16,384 distinct terms whose
#: letter statistics are plausible in both scripts -- the tokeniser
#: `build_document_terms` uses is multilingual, and a corpus of `w0001`
#: tokens would exercise a different code path in it than the platform's own
#: Arabic-first content does.
_SYLLABLES_AR = (
    "مست",
    "كتا",
    "بيان",
    "تقر",
    "مشر",
    "عمل",
    "نظا",
    "قرا",
    "دعم",
    "خدم",
    "منص",
    "وحد",
    "سجل",
    "طلب",
    "فهر",
    "بحث",
)
_SYLLABLES_EN = (
    "data",
    "report",
    "proj",
    "sys",
    "req",
    "serv",
    "plat",
    "unit",
    "log",
    "index",
    "search",
    "note",
    "plan",
    "team",
    "case",
    "draft",
)

#: Where `run` records what it wrote. Not committed -- a manifest describes
#: one machine's database, exactly like `deploy/load/results/`.
MANIFEST_DIR = Path("deploy/load/seeds")


@dataclass(frozen=True, slots=True)
class WorkspacePlan:
    """One tenant's share of the corpus, decided before a row is written.

    Deciding the whole allocation up front is what makes ``plan`` a real dry
    run: it prints the same numbers ``run`` will write, from the same code,
    so an operator can see the skew and the totals before committing half an
    hour to them."""

    ordinal: int
    workspace_id: str
    user_id: str
    space_ids: tuple[str, ...]
    messages: int
    files: int
    vectors: int
    pre_existing: bool

    @property
    def conversations(self) -> int:
        return _ceil_div(self.messages, MESSAGES_PER_CONVERSATION)

    @property
    def documents(self) -> int:
        """One document per file. The knowledge module's own shape -- a
        document IS the indexed form of a file (``documents.file_id``) -- so
        a workspace with no files can hold no vectors, which is why the
        vector allocation is derived from the file allocation rather than
        drawn independently."""
        return self.files


@dataclass(frozen=True, slots=True)
class SeedPlan:
    """The whole corpus: what to write, where, and under which identity."""

    seed_id: str
    anchor: datetime
    skew: float
    target: CorpusSize
    workspaces: tuple[WorkspacePlan, ...]

    @property
    def actual(self) -> CorpusSize:
        """What the allocation actually sums to. Equal to ``target`` by
        construction (largest-remainder allocation is exact) -- reported
        separately anyway, because the manifest must state what was written
        and not what was asked for."""
        return CorpusSize(
            workspaces=len(self.workspaces),
            messages=sum(w.messages for w in self.workspaces),
            files=sum(w.files for w in self.workspaces),
            vectors=sum(w.vectors for w in self.workspaces),
        )


# ---------------------------------------------------------------------------
# Deterministic identity
# ---------------------------------------------------------------------------


def _digest(*parts: object) -> bytes:
    """A 16-byte blake2b over the parts, joined by a separator that cannot
    occur in a UUID or a seed id. ``"\\x00"`` rather than ``":"`` so
    ``("a:b", "c")`` and ``("a", "b:c")`` cannot collide."""
    key = "\x00".join(str(part) for part in parts)
    return hashlib.blake2b(key.encode("utf-8"), digest_size=16).digest()


def _seeded_uuid7(seed_id: str, anchor: datetime, kind: str, ordinal: int, at: datetime) -> str:
    """A deterministic, RFC 9562 v7 identifier for one seeded row.

    The layout is built by hand rather than through ``uuid6.uuid7`` because
    that function draws its random bits from the OS and its timestamp from
    the clock -- neither of which a reproducible corpus can use. See the
    module docstring for why the v7 SHAPE (not merely uniqueness) is
    load-bearing here.
    """
    raw = bytearray(_digest(seed_id, anchor.isoformat(), kind, ordinal))
    milliseconds = int(at.timestamp() * 1000)
    raw[0:6] = milliseconds.to_bytes(6, "big")
    raw[6] = 0x70 | (raw[6] & 0x0F)  # version 7
    raw[8] = 0x80 | (raw[8] & 0x3F)  # RFC 4122 variant
    hexed = raw.hex()
    return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


def _unit(seed_id: str, *parts: object) -> float:
    """A deterministic float in ``[0, 1)`` for the given key -- the seeder's
    only source of "randomness", so that nothing about a corpus depends on
    process state, iteration order or the clock."""
    return int.from_bytes(_digest(seed_id, *parts)[:8], "big") / 2**64


def _moment(seed_id: str, anchor: datetime, *parts: object) -> datetime:
    """A deterministic instant inside the simulated history window."""
    return anchor - timedelta(seconds=_unit(seed_id, "when", *parts) * HISTORY_DAYS * 86_400)


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def zipf_weights(count: int, exponent: float) -> list[float]:
    """Tenant-size weights, normalised to sum to 1.

    ``exponent = 0`` is the uniform corpus (every tenant identical), 1.0 the
    classic Zipf law. See the module docstring on why uniform is the wrong
    default: it measures one tenant 200 times.
    """
    raw = [1.0 / (rank**exponent) for rank in range(1, count + 1)]
    total = sum(raw)
    return [value / total for value in raw]


def allocate(total: int, weights: Sequence[float]) -> list[int]:
    """Split ``total`` across ``weights`` by largest remainder.

    Exact by construction: the floors are handed out first and the leftover
    units go to the largest fractional parts, so the result always sums to
    ``total``. A naive ``round(total * weight)`` loses or invents rows, and
    the manifest would then declare a number the database does not hold --
    the one thing condition (3) exists to prevent.
    """
    if not weights:
        return []
    exact = [total * weight for weight in weights]
    floors = [int(value) for value in exact]
    remainder = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: exact[i] - floors[i], reverse=True)
    for index in order[:remainder]:
        floors[index] += 1
    return floors


def build_plan(
    *,
    seed_id: str,
    anchor: datetime,
    target: CorpusSize,
    skew: float,
    include: Sequence[str] = (),
) -> SeedPlan:
    """Decide the whole corpus without touching a database.

    ``include`` names workspaces that ALREADY exist -- the ones behind the
    real Firebase tokens in ``deploy/load/tokens.json``. They are seeded with
    content but their ``workspace``/``users`` rows are left alone, and they
    take the LARGEST shares (they are placed first, and the weights descend),
    because those are the tenants the load run will actually query. A corpus
    whose bulk sits in workspaces no VU ever authenticates as is a corpus the
    harness cannot see.
    """
    count = max(target.workspaces, len(include))
    weights = zipf_weights(count, skew)
    messages = allocate(target.messages, weights)
    files = allocate(target.files, weights)
    # Vectors follow the FILE allocation, not the raw weights: a document is
    # the indexed form of a file, so a workspace that got no files must get
    # no vectors. Deriving them independently produces chunks whose
    # `document_id` names a document that was never planned.
    file_total = sum(files)
    vectors = (
        allocate(target.vectors, [count / file_total for count in files])
        if file_total
        else [0] * count
    )

    plans: list[WorkspacePlan] = []
    for ordinal in range(count):
        pre_existing = ordinal < len(include)
        workspace_id = (
            include[ordinal]
            if pre_existing
            else _seeded_uuid7(
                seed_id, anchor, "workspace", ordinal, _moment(seed_id, anchor, "ws", ordinal)
            )
        )
        plans.append(
            WorkspacePlan(
                ordinal=ordinal,
                workspace_id=workspace_id,
                user_id=_seeded_uuid7(
                    seed_id, anchor, "user", ordinal, _moment(seed_id, anchor, "ws", ordinal)
                ),
                space_ids=tuple(
                    _seeded_uuid7(
                        seed_id,
                        anchor,
                        "space",
                        ordinal * SPACES_PER_WORKSPACE + slot,
                        _moment(seed_id, anchor, "ws", ordinal),
                    )
                    for slot in range(SPACES_PER_WORKSPACE)
                ),
                messages=messages[ordinal],
                files=files[ordinal],
                vectors=vectors[ordinal],
                pre_existing=pre_existing,
            )
        )
    return SeedPlan(
        seed_id=seed_id,
        anchor=anchor,
        skew=skew,
        target=target,
        workspaces=tuple(plans),
    )


# ---------------------------------------------------------------------------
# Synthetic content
# ---------------------------------------------------------------------------


class TextPool:
    """A fixed set of distinct paragraphs, each with its BM25 term vector
    computed once.

    The vocabulary is Zipf-sampled, so a handful of terms appear in almost
    every text and the tail appears in one -- which is what gives Qdrant's
    IDF modifier something to weight. A uniform vocabulary would make every
    term equally informative and the sparse leg of the hybrid search
    meaningless.
    """

    def __init__(self, seed_id: str, *, size: int, bm25: Bm25Params) -> None:
        self._vocabulary = tuple(
            f"{arabic}{english}" for arabic in _SYLLABLES_AR for english in _SYLLABLES_EN
        ) + tuple(f"{a}{b}" for a in _SYLLABLES_EN for b in _SYLLABLES_AR)
        # Zipf over the vocabulary, precomputed as a cumulative table so
        # picking a term is one binary search rather than a fresh reduction.
        weights = zipf_weights(len(self._vocabulary), 1.0)
        cumulative: list[float] = []
        running = 0.0
        for weight in weights:
            running += weight
            cumulative.append(running)
        self._cumulative = cumulative
        self._texts: tuple[str, ...] = tuple(self._compose(seed_id, index) for index in range(size))
        self._terms: tuple[SparseTerms, ...] = tuple(
            build_document_terms(body, bm25) for body in self._texts
        )

    def _pick(self, value: float) -> str:
        low, high = 0, len(self._cumulative) - 1
        while low < high:
            middle = (low + high) // 2
            if self._cumulative[middle] < value:
                low = middle + 1
            else:
                high = middle
        return self._vocabulary[low]

    def _compose(self, seed_id: str, index: int) -> str:
        words: list[str] = []
        length = 0
        position = 0
        while length < _CHUNK_CHARS:
            word = self._pick(_unit(seed_id, "text", index, position))
            words.append(word)
            length += len(word) + 1
            position += 1
        return " ".join(words)

    def __len__(self) -> int:
        return len(self._texts)

    def text(self, index: int) -> str:
        return self._texts[index % len(self._texts)]

    def terms(self, index: int) -> SparseTerms:
        return self._terms[index % len(self._terms)]


class VectorFactory:
    """Clustered dense vectors, deterministic and cheap.

    One centroid set per workspace, one shared pool of perturbations. A
    point's vector is ``centroid + spread * noise``, which costs a single
    384-element comprehension per point -- the difference between a corpus
    that takes twenty minutes and one that takes two hours, since there is no
    numpy in this project's dependency set (``pyproject.toml``) and a million
    fresh Gaussian draws in pure CPython is not free.

    Not normalised: the collections are created with cosine distance, and
    Qdrant normalises internally for that metric.
    """

    def __init__(self, seed_id: str, *, dimensions: int) -> None:
        self._seed_id = seed_id
        self._dimensions = dimensions
        self._noise: tuple[tuple[float, ...], ...] = tuple(
            self._draw("noise", index) for index in range(_NOISE_POOL)
        )
        self._centroids: dict[str, tuple[tuple[float, ...], ...]] = {}

    def _draw(self, kind: str, index: int) -> tuple[float, ...]:
        # Box-Muller off the deterministic uniform stream: a Gaussian
        # coordinate distribution, because that is what a normalised
        # embedding's coordinates look like and a uniform one would put every
        # point on the surface of a cube.
        values: list[float] = []
        while len(values) < self._dimensions:
            first = max(_unit(self._seed_id, kind, index, len(values)), 1e-12)
            second = _unit(self._seed_id, kind, index, len(values), "b")
            radius = math.sqrt(-2.0 * math.log(first))
            values.append(radius * math.cos(2.0 * math.pi * second))
            if len(values) < self._dimensions:
                values.append(radius * math.sin(2.0 * math.pi * second))
        return tuple(values)

    def _centroids_for(self, workspace_id: str) -> tuple[tuple[float, ...], ...]:
        cached = self._centroids.get(workspace_id)
        if cached is None:
            cached = tuple(
                self._draw(f"centroid:{workspace_id}", index)
                for index in range(CENTROIDS_PER_WORKSPACE)
            )
            # One workspace at a time: 200 x 8 x 384 floats held forever is
            # 4.9M floats of ballast for a value used once per tenant.
            self._centroids = {workspace_id: cached}
        return cached

    def vector(self, workspace_id: str, ordinal: int) -> list[float]:
        centroids = self._centroids_for(workspace_id)
        centroid = centroids[ordinal % CENTROIDS_PER_WORKSPACE]
        noise = self._noise[(ordinal // CENTROIDS_PER_WORKSPACE) % _NOISE_POOL]
        return [c + _CENTROID_SPREAD * n for c, n in zip(centroid, noise, strict=True)]


# ---------------------------------------------------------------------------
# Row generation
# ---------------------------------------------------------------------------


def _batched(rows: Iterator[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def conversation_rows(plan: SeedPlan, workspace: WorkspacePlan) -> Iterator[dict[str, Any]]:
    for index in range(workspace.conversations):
        at = _moment(plan.seed_id, plan.anchor, "conv", workspace.ordinal, index)
        yield {
            "id": _seeded_uuid7(plan.seed_id, plan.anchor, f"conv:{workspace.ordinal}", index, at),
            "workspace_id": workspace.workspace_id,
            "agent_key": _AGENT_KEYS[index % len(_AGENT_KEYS)],
            "title": f"seed thread {index}",
            "created_by": workspace.user_id,
            "created_at": at,
            "space_id": workspace.space_ids[index % len(workspace.space_ids)],
        }


def message_rows(
    plan: SeedPlan, workspace: WorkspacePlan, pool: TextPool
) -> Iterator[dict[str, Any]]:
    """Messages, laid out thread by thread.

    ``seq`` starts at 1 and is gap-free within a thread, because that is what
    ``Conversation.append_message`` produces (INV-CV1) and what
    ``UNIQUE(conversation_id, seq)`` enforces -- a 0-based corpus would still
    load and would still be wrong.
    """
    remaining = workspace.messages
    for index in range(workspace.conversations):
        conversation_at = _moment(plan.seed_id, plan.anchor, "conv", workspace.ordinal, index)
        conversation_id = _seeded_uuid7(
            plan.seed_id, plan.anchor, f"conv:{workspace.ordinal}", index, conversation_at
        )
        length = min(MESSAGES_PER_CONVERSATION, remaining)
        for seq in range(1, length + 1):
            # Messages advance forward from the thread's own creation, so a
            # thread reads in `created_at` order exactly as `seq` order --
            # the invariant every conversation listing assumes.
            at = conversation_at + timedelta(minutes=seq)
            body = pool.text(workspace.ordinal * 7919 + index * 31 + seq)
            yield {
                "id": _seeded_uuid7(
                    plan.seed_id,
                    plan.anchor,
                    f"msg:{workspace.ordinal}:{index}",
                    seq,
                    at,
                ),
                "conversation_id": conversation_id,
                "workspace_id": workspace.workspace_id,
                "role": _ROLES[seq % len(_ROLES)],
                "content": json.dumps({"text": body[:400], "attachments": []}, ensure_ascii=False),
                "token_count": len(body) // 4,
                "seq": seq,
                "created_at": at,
            }
        remaining -= length
        if remaining <= 0:
            return


def file_rows(plan: SeedPlan, workspace: WorkspacePlan) -> Iterator[dict[str, Any]]:
    for index in range(workspace.files):
        at = _moment(plan.seed_id, plan.anchor, "file", workspace.ordinal, index)
        file_id = _seeded_uuid7(plan.seed_id, plan.anchor, f"file:{workspace.ordinal}", index, at)
        yield {
            "id": file_id,
            "workspace_id": workspace.workspace_id,
            "name": f"seed-{workspace.ordinal:04d}-{index:06d}.pdf",
            "content_type": _CONTENT_TYPES[index % len(_CONTENT_TYPES)],
            # A plausible size distribution rather than a constant: `ح-6`'s
            # budget is per megabyte, and a corpus of identical sizes cannot
            # show a per-size effect.
            "size_bytes": 16_384 + int(_unit(plan.seed_id, "size", workspace.ordinal, index) * 4e6),
            # The key MinIO would hold, in the layout `purge.py` deletes by
            # (`<workspace_id>/` prefix, INV-F1) -- even though no object is
            # written, so the rows stay consistent with the storage contract.
            "storage_key": f"{workspace.workspace_id}/{file_id}",
            "status": "ready",
            "uploaded_by": workspace.user_id,
            "created_at": at,
            "space_id": workspace.space_ids[index % len(workspace.space_ids)],
        }


def _document_chunk_counts(workspace: WorkspacePlan) -> list[int]:
    """How many chunks each of this workspace's documents holds.

    Spread by the same largest-remainder rule as everything else, so the
    totals are exact; the spread itself is even, because chunk count tracks
    document LENGTH and the file-size skew already carries that variation
    where it matters (Postgres row width, not vector count)."""
    if workspace.documents == 0:
        return []
    return allocate(workspace.vectors, [1.0 / workspace.documents] * workspace.documents)


def document_rows(plan: SeedPlan, workspace: WorkspacePlan) -> Iterator[dict[str, Any]]:
    counts = _document_chunk_counts(workspace)
    for index in range(workspace.documents):
        at = _moment(plan.seed_id, plan.anchor, "file", workspace.ordinal, index)
        file_id = _seeded_uuid7(plan.seed_id, plan.anchor, f"file:{workspace.ordinal}", index, at)
        chunks = counts[index]
        yield {
            "id": _seeded_uuid7(plan.seed_id, plan.anchor, f"doc:{workspace.ordinal}", index, at),
            "workspace_id": workspace.workspace_id,
            "file_id": file_id,
            # `indexed` and not `pending`: a pending document is one the
            # worker still owes work on, and a corpus of a hundred thousand
            # of those would have every `ix_doc_ws_status` read return the
            # whole table.
            "status": "indexed",
            "chunk_count": chunks,
            "text_chunks": chunks,
            "created_at": at,
            "space_id": workspace.space_ids[index % len(workspace.space_ids)],
        }


def chunk_rows(
    plan: SeedPlan, workspace: WorkspacePlan, pool: TextPool
) -> Iterator[dict[str, Any]]:
    collection = knowledge_collection(workspace.workspace_id)
    counts = _document_chunk_counts(workspace)
    for index in range(workspace.documents):
        at = _moment(plan.seed_id, plan.anchor, "file", workspace.ordinal, index)
        document_id = _seeded_uuid7(
            plan.seed_id, plan.anchor, f"doc:{workspace.ordinal}", index, at
        )
        for seq in range(counts[index]):
            yield {
                "id": _seeded_uuid7(
                    plan.seed_id, plan.anchor, f"chunk:{workspace.ordinal}:{index}", seq, at
                ),
                "document_id": document_id,
                "workspace_id": workspace.workspace_id,
                "seq": seq,
                "text": pool.text(workspace.ordinal * 104_729 + index * 97 + seq),
                "token_count": _CHUNK_CHARS // 4,
                "collection": collection,
                # The SAME derivation `knowledge` indexes with, so a
                # re-index of a seeded document would upsert the point it
                # already has instead of duplicating it.
                "point_id": chunk_point_id(document_id, seq),
                "created_at": at,
            }


def vector_points(
    plan: SeedPlan, workspace: WorkspacePlan, pool: TextPool, factory: VectorFactory
) -> Iterator[VectorPoint]:
    """The Qdrant side of the same chunks, payload for payload.

    Built from the same derivations as ``chunk_rows`` rather than from its
    output: the two must agree, and the way to guarantee that is to compute
    both from the seed, not to pass rows between them."""
    counts = _document_chunk_counts(workspace)
    ordinal = 0
    for index in range(workspace.documents):
        at = _moment(plan.seed_id, plan.anchor, "file", workspace.ordinal, index)
        document_id = _seeded_uuid7(
            plan.seed_id, plan.anchor, f"doc:{workspace.ordinal}", index, at
        )
        space_id = workspace.space_ids[index % len(workspace.space_ids)]
        for seq in range(counts[index]):
            point_id = chunk_point_id(document_id, seq)
            body_index = workspace.ordinal * 104_729 + index * 97 + seq
            terms = pool.terms(body_index)
            yield VectorPoint(
                id=point_id,
                vector=factory.vector(workspace.workspace_id, ordinal),
                # The key set `knowledge/application/indexing.py::_payload`
                # writes. `space` is present (never null) for the same reason
                # it is there: an absent key matches no `MatchValue`, and a
                # null one would be a third meaning.
                payload={
                    "workspace_id": workspace.workspace_id,
                    "document_id": document_id,
                    "chunk_id": point_id,
                    "seq": seq,
                    "text": pool.text(body_index),
                    "kind": "text",
                    "space": space_id,
                },
                sparse=SparseVector(indices=list(terms.indices), values=list(terms.values)),
            )
            ordinal += 1


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_INSERT_WORKSPACE = """
INSERT INTO workspace.workspaces (id, owner_user_id, name, status, created_at, updated_at)
VALUES (:id, :owner_user_id, :name, 'active', :created_at, :created_at)
ON CONFLICT DO NOTHING
"""

_INSERT_USER = """
INSERT INTO workspace.users
    (id, workspace_id, firebase_uid, email, display_name, status, created_at, updated_at)
VALUES (:id, :workspace_id, :firebase_uid, :email, :display_name, 'active', :created_at,
        :created_at)
ON CONFLICT DO NOTHING
"""

_INSERT_SPACE = """
INSERT INTO spaces.spaces (id, workspace_id, name, created_by, created_at, updated_at)
VALUES (:id, :workspace_id, :name, :created_by, :created_at, :created_at)
ON CONFLICT DO NOTHING
"""

_INSERT_CONVERSATION = """
INSERT INTO conversations.conversations
    (id, workspace_id, agent_key, kind, title, created_by, created_at, updated_at, space_id)
VALUES (:id, :workspace_id, :agent_key, 'agent', :title, :created_by, :created_at, :created_at,
        :space_id)
ON CONFLICT DO NOTHING
"""

_INSERT_MESSAGE = """
INSERT INTO conversations.messages
    (id, conversation_id, workspace_id, role, content, token_count, seq, created_at)
VALUES (:id, :conversation_id, :workspace_id, :role, CAST(:content AS jsonb), :token_count, :seq,
        :created_at)
ON CONFLICT DO NOTHING
"""

_INSERT_FILE = """
INSERT INTO files.files
    (id, workspace_id, name, content_type, size_bytes, storage_key, status, uploaded_by,
     created_at, updated_at, space_id)
VALUES (:id, :workspace_id, :name, :content_type, :size_bytes, :storage_key, :status,
        :uploaded_by, :created_at, :created_at, :space_id)
ON CONFLICT DO NOTHING
"""

_INSERT_DOCUMENT = """
INSERT INTO knowledge.documents
    (id, workspace_id, file_id, status, chunk_count, text_chunks, created_at, updated_at, space_id)
VALUES (:id, :workspace_id, :file_id, :status, :chunk_count, :text_chunks, :created_at,
        :created_at, :space_id)
ON CONFLICT DO NOTHING
"""

_INSERT_CHUNK = """
INSERT INTO knowledge.chunks
    (id, document_id, workspace_id, seq, text, token_count, collection, point_id, created_at)
VALUES (:id, :document_id, :workspace_id, :seq, :text, :token_count, :collection,
        CAST(:point_id AS uuid), :created_at)
ON CONFLICT DO NOTHING
"""


@asynccontextmanager
async def tenant_transaction(conn: AsyncConnection, workspace_id: str) -> AsyncIterator[None]:
    """One transaction whose FIRST statement is the tenant GUC.

    Re-set per transaction and never once per connection, because
    ``set_config(..., is_local => true)`` is transaction-scoped -- which is
    the property that makes it safe under PgBouncer and the reason the
    request path sets it on every unit of work (``rls.py``). A seeder that
    set it once and then committed a thousand batches would be writing the
    second batch onward with no tenant context at all, and RLS would reject
    every row: the failure would be loud, but only after the first commit."""
    async with conn.begin():
        await conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": workspace_id}
        )
        yield


async def _refuse_privileged_role(conn: AsyncConnection) -> None:
    """Refuse to run as a role RLS does not apply to.

    The plan asks for a tool that RESPECTS row-level security. A superuser or
    a ``BYPASSRLS`` role writes rows the policies never saw, so the corpus
    could contain rows no tenant can read back -- and the load run would
    measure a database shape the application cannot produce. Checked once,
    before the first row.
    """
    # In its OWN transaction: SQLAlchemy autobegins on the first `execute`,
    # and a connection left holding an implicit transaction refuses the
    # explicit `begin()` every tenant batch below opens.
    async with conn.begin():
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    if bool(row.rolsuper) or bool(row.rolbypassrls):
        raise SystemExit(
            "load_seed refused: this connection's role is a superuser or holds BYPASSRLS, so "
            "row-level security would not apply to a single row it writes. Capacity step 0.1's "
            "condition (3) asks for a corpus generated by a tool that RESPECTS RLS -- point "
            "DATABASE_URL at app_rw's own DSN (see this module's docstring) and run it again."
        )


async def _require_included_workspaces_exist(conn: AsyncConnection, plan: SeedPlan) -> None:
    """A ``--include-workspace`` id must name a workspace that already exists.

    Nothing else would catch a typo. The RLS policies compare the row's
    ``workspace_id`` to the GUC and never ask whether that tenant is real, and
    only ``workspace.users`` has an FK to the root -- which this path
    deliberately does not write for an included tenant. So a mistyped id
    produces a full, valid-looking corpus belonging to a workspace no one can
    ever authenticate into, discovered hours later as an empty load run.
    ``workspace.workspaces`` carries no RLS, so this is one plain read.
    """
    included = [w.workspace_id for w in plan.workspaces if w.pre_existing]
    if not included:
        return
    async with conn.begin():
        rows = await conn.execute(
            text("SELECT id::text FROM workspace.workspaces WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": included},
        )
    missing = sorted(set(included) - {str(row[0]) for row in rows})
    if missing:
        raise SystemExit(
            "--include-workspace names workspaces that do not exist: "
            + ", ".join(missing)
            + ". Seeding content into them would produce a corpus no token can reach. "
            "These ids come from the tenants behind deploy/load/tokens.json -- log in "
            "once so JIT provisioning creates them, then read the id back."
        )


class Progress:
    """A one-line stderr counter. stdout stays a document (``--json``)."""

    def __init__(self, label: str, total: int, *, enabled: bool) -> None:
        self._label = label
        self._total = total
        self._enabled = enabled and total > 0
        self._done = 0
        self._started = time.monotonic()
        self._last = 0.0

    def advance(self, count: int) -> None:
        self._done += count
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._last < _PROGRESS_INTERVAL_S and self._done < self._total:
            return
        self._last = now
        elapsed = max(now - self._started, 1e-6)
        percent = 100.0 * self._done / self._total
        print(
            f"\r{self._label:<12} {self._done:>10,}/{self._total:<10,} {percent:5.1f}%  "
            f"{self._done / elapsed:>9,.0f}/s",
            end="",
            file=sys.stderr,
            flush=True,
        )

    def close(self) -> None:
        if self._enabled:
            print(file=sys.stderr)


async def seed_postgres(
    engine: AsyncEngine, plan: SeedPlan, pool: TextPool, *, progress: bool
) -> None:
    """Write every Postgres row of the corpus, tenant by tenant."""
    actual = plan.actual
    counters = {
        "conversations": Progress(
            "threads", sum(w.conversations for w in plan.workspaces), enabled=progress
        ),
        "messages": Progress("messages", actual.messages, enabled=progress),
        "files": Progress("files", actual.files, enabled=progress),
        "documents": Progress(
            "documents", sum(w.documents for w in plan.workspaces), enabled=progress
        ),
        "chunks": Progress("chunks", actual.vectors, enabled=progress),
    }
    async with engine.connect() as conn:
        await _refuse_privileged_role(conn)
        await _require_included_workspaces_exist(conn, plan)
        for workspace in plan.workspaces:
            await _seed_workspace_identity(conn, plan, workspace)
            for label, statement, rows in (
                ("conversations", _INSERT_CONVERSATION, conversation_rows(plan, workspace)),
                ("messages", _INSERT_MESSAGE, message_rows(plan, workspace, pool)),
                ("files", _INSERT_FILE, file_rows(plan, workspace)),
                ("documents", _INSERT_DOCUMENT, document_rows(plan, workspace)),
                ("chunks", _INSERT_CHUNK, chunk_rows(plan, workspace, pool)),
            ):
                for batch in _batched(rows, _PG_BATCH):
                    async with tenant_transaction(conn, workspace.workspace_id):
                        await conn.execute(text(statement), batch)
                    counters[label].advance(len(batch))
    for counter in counters.values():
        counter.close()


async def _seed_workspace_identity(
    conn: AsyncConnection, plan: SeedPlan, workspace: WorkspacePlan
) -> None:
    """The tenant root, its owner and its spaces.

    ``workspace.workspaces`` is written OUTSIDE any tenant context -- it
    carries no RLS (module docstring) -- and everything below it inside one.
    A ``--include-workspace`` tenant already has the first two and gets only
    its spaces, so the tool never rewrites a real user's account row.
    """
    at = _moment(plan.seed_id, plan.anchor, "ws", workspace.ordinal)
    if not workspace.pre_existing:
        async with conn.begin():
            await conn.execute(
                text(_INSERT_WORKSPACE),
                {
                    "id": workspace.workspace_id,
                    "owner_user_id": workspace.user_id,
                    "name": f"load-seed {plan.seed_id} #{workspace.ordinal:04d}"[:80],
                    "created_at": at,
                },
            )
        async with tenant_transaction(conn, workspace.workspace_id):
            await conn.execute(
                text(_INSERT_USER),
                {
                    "id": workspace.user_id,
                    "workspace_id": workspace.workspace_id,
                    # `.invalid` is reserved by RFC 2606 and can never be a
                    # real Firebase account: a seeded identity must be
                    # impossible to confuse with one that can log in.
                    "firebase_uid": f"seed:{plan.seed_id}:{workspace.ordinal:06d}",
                    "email": f"seed-{workspace.ordinal:06d}@load.invalid",
                    "display_name": f"Load Seed {workspace.ordinal:04d}",
                    "created_at": at,
                },
            )
    async with tenant_transaction(conn, workspace.workspace_id):
        await conn.execute(
            text(_INSERT_SPACE),
            [
                {
                    "id": space_id,
                    "workspace_id": workspace.workspace_id,
                    "name": f"seed-space-{slot}",
                    "created_by": workspace.user_id,
                    "created_at": at,
                }
                for slot, space_id in enumerate(workspace.space_ids)
            ],
        )


async def seed_qdrant(
    store: QdrantVectorStore,
    plan: SeedPlan,
    pool: TextPool,
    factory: VectorFactory,
    *,
    dimensions: int,
    progress: bool,
) -> None:
    """Fill each tenant's ``kn-<workspace_id>`` collection.

    Through the application's OWN ``QdrantVectorStore``, and specifically
    through ``ensure_hybrid_collection`` -- not a hand-rolled client call.
    The hybrid collection is what carries the named sparse vector and the
    three payload indexes (``HYBRID_PAYLOAD_INDEXES``); a seeder that created
    plain collections would produce a Qdrant whose search path is not the
    platform's, and the RAG scenario's p95 would be measuring the wrong
    index.
    """
    counter = Progress("vectors", plan.actual.vectors, enabled=progress)
    for workspace in plan.workspaces:
        if workspace.vectors == 0:
            continue
        collection = knowledge_collection(workspace.workspace_id)
        await store.ensure_hybrid_collection(collection, dimensions)
        batch: list[VectorPoint] = []
        for point in vector_points(plan, workspace, pool, factory):
            batch.append(point)
            if len(batch) >= _QDRANT_BATCH:
                await store.upsert(collection, batch)
                counter.advance(len(batch))
                batch = []
        if batch:
            await store.upsert(collection, batch)
            counter.advance(len(batch))
    counter.close()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def manifest_path(seed_id: str) -> Path:
    return MANIFEST_DIR / f"{seed_id}.json"


def manifest_document(plan: SeedPlan, *, wrote: Sequence[str]) -> dict[str, Any]:
    """What ``run`` archives, and what ``status --export`` reads back.

    Records the ACTUAL allocation and not the target, and names the stores it
    actually touched: a run stopped after ``--only postgres`` has a corpus in
    Postgres and none in Qdrant, and a manifest that claimed a million
    vectors would turn condition (3) from a check into a rubber stamp.
    """
    actual = plan.actual
    return {
        "seed_id": plan.seed_id,
        "anchor": plan.anchor.isoformat(),
        "skew": plan.skew,
        "stores": list(wrote),
        "size": asdict(actual),
        "floor": asdict(FLOOR),
        "meets_floor": meets_floor(actual) and set(wrote) == {"postgres", "qdrant"},
        "shape": {
            "messages_per_conversation": MESSAGES_PER_CONVERSATION,
            "spaces_per_workspace": SPACES_PER_WORKSPACE,
            "history_days": HISTORY_DAYS,
            "conversations": sum(w.conversations for w in plan.workspaces),
            "documents": sum(w.documents for w in plan.workspaces),
        },
        # The largest tenants, which are the ones a token pool should
        # authenticate as -- see `build_plan` on why `--include-workspace`
        # takes the front of the list.
        "largest_workspaces": [
            {
                "workspace_id": w.workspace_id,
                "space_ids": list(w.space_ids),
                "messages": w.messages,
                "files": w.files,
                "vectors": w.vectors,
                "pre_existing": w.pre_existing,
            }
            for w in plan.workspaces[:10]
        ],
    }


def meets_floor(size: CorpusSize) -> bool:
    """The same predicate ``deploy/load/lib/config.js::seedIsRealistic``
    applies to the declared numbers -- restated here so the tool can tell an
    operator the answer before the harness does."""
    return (
        size.workspaces >= FLOOR.workspaces
        and size.messages >= FLOOR.messages
        and size.files >= FLOOR.files
        and size.vectors >= FLOOR.vectors
    )


def export_block(document: dict[str, Any]) -> str:
    """The manifest as the environment ``deploy/load/run.sh`` reads."""
    size = document["size"]
    return "\n".join(
        (
            f"export LOAD_SEED_ID={document['seed_id']}",
            f"export LOAD_SEED_MESSAGES={size['messages']}",
            f"export LOAD_SEED_FILES={size['files']}",
            f"export LOAD_SEED_VECTORS={size['vectors']}",
            f"export LOAD_SEED_WORKSPACES={size['workspaces']}",
        )
    )


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------

_PURGE_STATEMENTS = (
    ("knowledge.chunks", "DELETE FROM knowledge.chunks WHERE workspace_id = CAST(:ws AS uuid)"),
    (
        "knowledge.documents",
        "DELETE FROM knowledge.documents WHERE workspace_id = CAST(:ws AS uuid)",
    ),
    ("files.files", "DELETE FROM files.files WHERE workspace_id = CAST(:ws AS uuid)"),
    (
        "conversations.messages",
        "DELETE FROM conversations.messages WHERE workspace_id = CAST(:ws AS uuid)",
    ),
    (
        "conversations.conversations",
        "DELETE FROM conversations.conversations WHERE workspace_id = CAST(:ws AS uuid)",
    ),
    ("spaces.spaces", "DELETE FROM spaces.spaces WHERE workspace_id = CAST(:ws AS uuid)"),
    ("workspace.users", "DELETE FROM workspace.users WHERE workspace_id = CAST(:ws AS uuid)"),
)


async def purge(
    engine: AsyncEngine, client: AsyncQdrantClient, plan: SeedPlan, *, progress: bool
) -> dict[str, int]:
    """Remove exactly what this seed wrote, and nothing else.

    Scoped by the seed's OWN derived workspace ids, so a purge cannot reach a
    tenant the tool did not create -- and ``--include-workspace`` tenants are
    skipped entirely: their content came from this tool, but their identity
    did not, and a purge that deleted a real account's rows would be a very
    expensive surprise. Their content is left in place deliberately; delete
    it by re-running the seed under a fresh id or by hand.
    """
    removed: dict[str, int] = {}
    async with engine.connect() as conn:
        await _refuse_privileged_role(conn)
        counter = Progress("purge", len(plan.workspaces), enabled=progress)
        for workspace in plan.workspaces:
            if workspace.pre_existing:
                counter.advance(1)
                continue
            async with tenant_transaction(conn, workspace.workspace_id):
                for label, statement in _PURGE_STATEMENTS:
                    result = await conn.execute(text(statement), {"ws": workspace.workspace_id})
                    removed[label] = removed.get(label, 0) + result.rowcount
            # The tenant root last, and outside the tenant transaction: it
            # carries no RLS, and dropping it before its children would leave
            # `fk_user_ws` refusing the delete.
            async with conn.begin():
                result = await conn.execute(
                    text("DELETE FROM workspace.workspaces WHERE id = CAST(:ws AS uuid)"),
                    {"ws": workspace.workspace_id},
                )
                removed["workspace.workspaces"] = (
                    removed.get("workspace.workspaces", 0) + result.rowcount
                )
            await drop_collection(client, knowledge_collection(workspace.workspace_id))
            counter.advance(1)
        counter.close()
    return removed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_plan(plan: SeedPlan) -> str:
    actual = plan.actual
    lines = [
        f"seed id      : {plan.seed_id}",
        f"anchor       : {plan.anchor.isoformat()}  (history {HISTORY_DAYS}d back)",
        f"skew         : {plan.skew}",
        f"workspaces   : {actual.workspaces:,}"
        f"   ({sum(1 for w in plan.workspaces if w.pre_existing)} pre-existing)",
        f"messages     : {actual.messages:,}"
        f"   in {sum(w.conversations for w in plan.workspaces):,} threads",
        f"files        : {actual.files:,}",
        f"vectors      : {actual.vectors:,}"
        f"   in {sum(w.documents for w in plan.workspaces):,} documents",
        f"meets floor  : {'yes' if meets_floor(actual) else 'NO -- runs stay valid:false'}",
        "",
        f"{'#':>4}  {'workspace_id':<38} {'messages':>10} {'files':>8} {'vectors':>10}",
    ]
    head = plan.workspaces[:_PLAN_HEAD]
    tail = plan.workspaces[-_PLAN_TAIL:] if len(plan.workspaces) > _PLAN_HEAD + _PLAN_TAIL else ()
    for workspace in head:
        lines.append(
            f"{workspace.ordinal:>4}  {workspace.workspace_id:<38} {workspace.messages:>10,} "
            f"{workspace.files:>8,} {workspace.vectors:>10,}"
        )
    if tail:
        lines.append(f"{'...':>4}")
        for workspace in tail:
            lines.append(
                f"{workspace.ordinal:>4}  {workspace.workspace_id:<38} {workspace.messages:>10,} "
                f"{workspace.files:>8,} {workspace.vectors:>10,}"
            )
    return "\n".join(lines)


def _plan_from_args(args: argparse.Namespace) -> SeedPlan:
    anchor = (
        datetime.fromisoformat(args.as_of).replace(tzinfo=UTC)
        if args.as_of
        else datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    target = FLOOR.scaled(args.scale)
    if args.workspaces is not None:
        target = CorpusSize(
            workspaces=args.workspaces,
            messages=target.messages,
            files=target.files,
            vectors=target.vectors,
        )
    return build_plan(
        seed_id=args.seed_id,
        anchor=anchor,
        target=target,
        skew=args.skew,
        include=tuple(args.include_workspace or ()),
    )


def _show_status(args: argparse.Namespace) -> int:
    """Read back what a previous ``run`` on THIS machine wrote.

    Off the manifest, never off the database, and that is not laziness: a
    global ``count(*)`` is exactly what RLS forbids the seeding role from
    asking (a tenant-scoped role can count one tenant's rows, not the
    corpus's), and counting 200 tenants one at a time on a million-row table
    with no ``workspace_id`` index on ``conversations.messages`` is minutes
    of sequential scans to re-derive a number the writer already knew. The
    manifest is the declaration condition (3) asks for; the check that it
    matches reality is the load run itself.
    """
    path = manifest_path(args.seed_id)
    if not path.exists():
        raise SystemExit(
            f"no manifest at {path}: this seed has never been run on this machine. "
            "`python -m app.ops.load_seed run` writes it."
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    if args.export:
        print(export_block(document))
    elif args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(document["size"], indent=2))
        print(f"meets floor  : {'yes' if document['meets_floor'] else 'no'}")
        print(f"stores       : {', '.join(document['stores'])}")
    return 0


async def _write_corpus(
    args: argparse.Namespace,
    settings: Settings,
    plan: SeedPlan,
    engine: AsyncEngine,
    store: QdrantVectorStore,
) -> int:
    started = time.monotonic()
    dimensions = settings.embedding_service.dimensions
    bm25 = Bm25Params(
        k1=settings.sparse.bm25_k1,
        b=settings.sparse.bm25_b,
        avg_len=settings.sparse.bm25_avg_len,
    )
    pool = TextPool(args.seed_id, size=args.text_pool, bm25=bm25)
    wrote: list[str] = []
    if args.only in (None, "postgres"):
        await seed_postgres(engine, plan, pool, progress=not args.no_progress)
        wrote.append("postgres")
    if args.only in (None, "qdrant"):
        await seed_qdrant(
            store,
            plan,
            pool,
            VectorFactory(args.seed_id, dimensions=dimensions),
            dimensions=dimensions,
            progress=not args.no_progress,
        )
        wrote.append("qdrant")

    document = manifest_document(plan, wrote=wrote)
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path(args.seed_id).write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    elapsed = time.monotonic() - started
    _logger.info(
        "ops.load_seed.written",
        extra={
            "seed_id": args.seed_id,
            "stores": wrote,
            "workspaces": plan.actual.workspaces,
            "seconds": round(elapsed, 1),
        },
    )
    print(f"seeded in {elapsed:,.1f}s -- manifest: {manifest_path(args.seed_id)}")
    print(export_block(document))
    if not document["meets_floor"]:
        print(_BELOW_FLOOR_NOTE, file=sys.stderr)
    return 0


#: Printed whenever the corpus that was just written is smaller than the
#: floor -- a `--scale` smoke run, a `--only` half, a `--workspaces`
#: override. The run it prepares is still a real measurement; it is only
#: never a BASELINE, and the difference has to be said out loud at the moment
#: the operator could still mistake one for the other.
_BELOW_FLOOR_NOTE = (
    "⚠️  this corpus is BELOW the floor condition (3) sets "
    f"({FLOOR.messages:,} messages · {FLOOR.files:,} files · {FLOOR.vectors:,} vectors · "
    f'{FLOOR.workspaces} workspaces), so every run against it archives "valid": false. '
    "Real, but not a baseline."
)


async def _act(args: argparse.Namespace) -> int:
    # `status` reads a file and nothing else -- before `load_settings()`, so
    # it answers on a machine with no database credentials configured at all.
    if args.action == "status":
        return _show_status(args)

    settings = load_settings()
    plan = _plan_from_args(args)
    if args.action == "plan":
        print(_render_plan(plan))
        return 0

    engine: AsyncEngine = create_engine(
        DatabaseSettings(url=settings.database.url), poolclass=NullPool
    )
    client = create_qdrant_client(settings.qdrant)
    try:
        if args.action == "purge":
            removed = await purge(engine, client, plan, progress=not args.no_progress)
            manifest_path(args.seed_id).unlink(missing_ok=True)
            print(json.dumps(removed, indent=2))
            _logger.info("ops.load_seed.purged", extra={"seed_id": args.seed_id})
            return 0
        return await _write_corpus(args, settings, plan, engine, QdrantVectorStore(client))
    finally:
        await engine.dispose()
        await client.close()


def _add_shape_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--seed-id",
        default="dev",
        help="names this corpus; it is what LOAD_SEED_ID declares and what every derived id "
        "hashes from. Date it (dev-2026-09-03) -- see the module docstring on the anchor.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="fraction of the floor to generate (default 1.0 = the full corpus). Anything below "
        "1.0 is a smoke run and can never be a baseline.",
    )
    parser.add_argument(
        "--workspaces",
        type=int,
        default=None,
        help="override the tenant count alone, leaving the content totals as --scale set them",
    )
    parser.add_argument(
        "--skew",
        type=float,
        default=1.0,
        help="Zipf exponent for tenant size (default 1.0; 0 = every tenant identical, which is "
        "not what a real corpus looks like -- module docstring)",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="ISO date/datetime the simulated 90-day history ends at (default: today 00:00 UTC). "
        "Part of the id derivation, so two anchors are two disjoint corpora.",
    )
    parser.add_argument(
        "--include-workspace",
        action="append",
        metavar="UUID",
        help="seed CONTENT into a workspace that already exists -- the tenants behind the real "
        "Firebase tokens in deploy/load/tokens.json. Repeatable. Their account rows are never "
        "written and `purge` never touches them.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.load_seed",
        description="Generate the realistic corpus capacity step 0.1 condition (3) requires, "
        "through RLS (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    plan_parser = sub.add_parser("plan", help="print the allocation without writing anything")
    _add_shape_arguments(plan_parser)

    run_parser = sub.add_parser("run", help="write the corpus (idempotent; safe to re-run)")
    _add_shape_arguments(run_parser)
    run_parser.add_argument(
        "--only",
        choices=("postgres", "qdrant"),
        default=None,
        help="write one store only. The manifest records which, and a partial corpus never "
        "reports meets_floor.",
    )
    run_parser.add_argument(
        "--text-pool",
        type=int,
        default=4096,
        help="distinct generated paragraphs to draw chunk text from (default 4096)",
    )
    run_parser.add_argument(
        "--no-progress", action="store_true", help="suppress the stderr progress line"
    )

    status_parser = sub.add_parser(
        "status", help="read back the manifest this machine's seed run wrote"
    )
    status_parser.add_argument("--seed-id", default="dev")
    status_parser.add_argument(
        "--export",
        action="store_true",
        help="print the LOAD_SEED_* block for deploy/load/run.sh",
    )
    status_parser.add_argument("--json", action="store_true", help="print the whole manifest")

    purge_parser = sub.add_parser(
        "purge", help="delete every workspace THIS seed created, and its vectors"
    )
    _add_shape_arguments(purge_parser)
    purge_parser.add_argument(
        "--yes", action="store_true", help="required: the deletion is immediate and has no undo"
    )
    purge_parser.add_argument("--no-progress", action="store_true")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    # `qdrant-client` speaks through `httpx`, which logs one INFO line PER
    # REQUEST -- four thousand of them for the full corpus, interleaved with
    # (and destroying) the progress line this tool writes to the same stream.
    # Warnings still come through, so a failing upsert is not silenced.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _build_parser().parse_args()
    if args.action == "purge" and not args.yes:
        raise SystemExit(
            "purge refused: pass --yes. This deletes every workspace, user, space, thread, "
            "message, file, document and chunk the named seed created, plus each tenant's "
            "Qdrant collection. Re-seeding costs the same wall clock it cost the first time."
        )
    raise SystemExit(asyncio.run(_act(args)))


if __name__ == "__main__":
    main()
