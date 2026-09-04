"""``EXPLAIN (ANALYZE, BUFFERS)`` over the request path's own statements, run
under a tenant's RLS context -- capacity step 2.4 (``docs/capacity-plan.md``
§5, Wave 2).

**What was missing, and why 0.4 could not supply it.** Step 2.4 says: take the
most expensive queries and re-read them with ``EXPLAIN (ANALYZE, BUFFERS)``
*with the RLS context set*, "because the plan under ``SET LOCAL
app.workspace_id`` is not the plan without it -- and that is a trap that gets
measured, not assumed". ``app.ops.slow_queries`` (step 0.4) says the same thing
from the other side, in its own Pitfall 3: its rows are *normalised* text with
constants replaced by ``$1``, so one row aggregates every workspace's execution
and **cannot be executed**, let alone executed under one tenant's predicate.
The ranking and the plan are two different instruments; 0.4 built the first and
named this module as the second.

**Why the catalogue is written from the ADAPTERS and not read off
``pg_stat_statements``.** Two independent reasons, and the second is the one
that decided it:

1. A ranking describes the load that has *happened*. On this repository today
   that is the test suite (``د-9``: there is no real Firebase token pool, so
   no authenticated load run has ever reached these statements). Ranking that
   traffic and calling the top five "the hot path" would be measurement
   theatre -- the report currently ranks a ``SELECT ... FOR UPDATE``
   contention test first, by a factor of three.
2. Even with a load run, a normalised statement is a *string*, not a query.
   ``EXPLAIN ANALYZE`` needs real parameters, under a real tenant, and the
   only place that knows which predicate a request path issues is the adapter
   that issues it. So the catalogue below mirrors ``modules/*/adapters/
   sql_repository.py`` line for line, each entry naming the method it mirrors
   -- and ``tests/unit/test_ops_explain_hot_paths.py`` reads those files and
   fails when a named method disappears, so the mirror cannot rot silently.

A ranking still has a job here, and it is the one 0.4 describes: once a load
run exists, ``slow_queries top`` says which of these statements to look at
*first*, and whether the catalogue is missing one. It orders the list; it does
not produce it.

**Why this connects as ``app_rw`` and not as the owner -- the plan under the
request-path role is the only plan that matters.** Two reasons, both already
measured in this repository rather than inferred:

* ``FORCE ROW LEVEL SECURITY`` is on for every tenant table (01-data-model §3),
  so the policy's qual is part of the plan. It shows up in every output below
  as the ``One-Time Filter`` on ``current_setting('app.workspace_id')`` -- and
  a plan taken by a role the policy does not apply to simply does not have it.
* ``migrations/versions/files/0003_file_name_lookup.py`` recorded the sharper
  consequence: ``lower``/``normalize`` are ``IMMUTABLE`` but **not**
  ``LEAKPROOF``, and the planner may not run a non-leakproof function before a
  row-security qual. Under the policy an expression index over them can never
  be a search key; without the policy the same index matches. A plan taken as
  a bypassing role would therefore show an index scan the request path can
  never get -- a green report for a query that is slow in production.

Consequently this tool **refuses to run as a superuser or a ``BYPASSRLS``
role** (``load_seed``'s ``_refuse_privileged_role``, same words for the same
reason: the guarantee is only worth what the connecting role cannot do).

**Why "no ``Seq Scan``" is the WEAKER half of 2.4's acceptance criterion, and
what this tool checks instead.** The criterion is written as «لا ``Seq Scan``
على جدولٍ مستأجرٍ في المسار الساخن». That is necessary and it is not
sufficient, and the step's own measured entry point is the proof: the defect
Wave 0's seed probe found was an **Index** Scan that read 17,012 rows to return
20. A guard that looks only for ``Seq Scan`` passes that plan without a word.
So every statement is judged on **read amplification** -- how many rows the
scan nodes touched per row the query returned -- and a ``Seq Scan`` on a tenant
table is reported as its own, separate, always-failing verdict on top.

**Amplification means different things to different queries, so each entry
declares its KIND.** Judging a ``count(*)`` by rows-read-per-row-returned would
flag every aggregate in the catalogue forever (an aggregate returns one row by
definition), and judging a page by heap access would miss the very defect this
step exists to find:

* ``page``      -- returns at most one page. Judged on ``rows_read /
                   rows_returned``: the index either carries the predicate and
                   the ordering, or the server reads the tenant's whole set and
                   sorts it for twenty rows.
* ``aggregate`` -- reads a set by design and returns one row. Rows read is not
                   the defect; **touching the heap** for them is. An aggregate
                   over columns an index already carries is an ``Index Only
                   Scan``; a ``Bitmap Heap Scan`` under it means the index does
                   not cover the query, and the difference is a factor of ten
                   in buffers on a path that holds a row lock.
* ``point``     -- resolves one row by key. Anything but a handful of rows read
                   means the key is not indexed.

**``EXPLAIN ANALYZE`` EXECUTES the statement.** That is the point -- estimates
are what this step exists to stop trusting -- and it is also why every entry in
the catalogue is a ``SELECT``, checked here (``_refuse_writes``) rather than
left to reviewer discipline: an ``EXPLAIN ANALYZE`` of a ``DELETE`` deletes.

**Connect DIRECTLY to ``postgres:5432``, not through PgBouncer** --
``slow_queries``' reason: the pooler's ``MAX_CLIENT_CONN`` is bottleneck
``ح-3``, and a measuring tool that occupies one of the client slots it is
measuring perturbs the measurement.

``DATABASE_URL`` for THIS process must be ``app_rw``'s OWN DSN pointed at
``postgres:5432`` (one role per process, never one role wearing another's hat
-- ``provision.py``'s convention)::

    export DATABASE_URL="postgresql+asyncpg://app_rw:$APP_RW_PASSWORD@127.0.0.1:${HOST_PORT_POSTGRES:-15432}/aizzak"

**Which tenant is measured, and why it is never chosen by a query.** The worst
plan on this schema belongs to the LARGEST tenant, and ``app_rw`` under RLS
cannot see across tenants to find it -- by design. Rather than hand this
process a second, wider role to answer one question, the workspace comes from
``--workspace-id`` or, failing that, from the seed manifest
``deploy/load/seeds/<seed-id>.json`` that ``app.ops.load_seed`` already writes
(its ``largest_workspaces`` block exists for exactly this). Every OTHER
parameter -- a conversation, a document, a page of file ids, a page of chunk
point ids -- is then discovered *inside* that tenant, as ``app_rw``, under the
GUC. That is not only role-clean: it means the tool can only ever measure rows
the request path itself could reach.

Usage::

    python -m app.ops.explain_hot_paths run [--workspace-id UUID] [--seed-id ID]
                                            [--only NAME ...] [--json]
    python -m app.ops.explain_hot_paths list

``run`` exits non-zero when any statement scores a failing verdict, so it is
usable as a gate and not only as a report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.pool import NullPool

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config import load_settings
from app.infrastructure.persistence.database import create_engine
from app.ops.load_seed import MANIFEST_DIR

_logger = logging.getLogger(__name__)

#: The page a client actually asks for. ``api/v1/dto/pagination.py`` bounds
#: ``limit`` at ``le=100`` and every router defaults it to 20; the adapters
#: then ask for ``limit + 1`` to learn whether a next page exists. Measuring
#: the DEFAULT rather than the ceiling is deliberate -- it is the page that
#: runs on every screen, and a plan that reads the tenant's whole set reads it
#: whatever the limit says.
PAGE_LIMIT = 20

#: ``k`` for a retrieval, at its ceiling (``api/v1/dto/knowledge.py``:
#: ``le=50``). Unlike the page above, the ceiling is the honest number here:
#: the parent-widening lookup receives one id per retrieved chunk, and a
#: caller asking for the maximum is asking for the worst case of a query that
#: sits on the synchronous RAG path (``ح-5``/``ح-11``).
RETRIEVAL_K = 50

#: Schemas whose tables carry ``workspace_id`` and an RLS policy. A ``Seq
#: Scan`` on one of these is 2.4's acceptance criterion failing; a ``Seq Scan``
#: on ``pg_catalog`` (which every plan does, while planning) is not.
TENANT_SCHEMAS = frozenset(
    {
        "conversations",
        "files",
        "knowledge",
        "media",
        "memory",
        "spaces",
        "usage",
        "access",
        "credentials",
        "integrations",
    }
)

#: A ``page`` query may read this many rows per row it returns before the
#: index is judged not to carry the predicate. Chosen from the two measured
#: shapes and not from taste: a healthy page on this schema reads its own
#: rows plus whatever the backwards primary-key walk skips (5-35x measured
#: across the catalogue), and the defect shape reads the tenant's entire set
#: (851x for ``ids_for_files``, 83,396x for ``parent_texts_for_chunk_ids``).
#: There are two orders of magnitude between them, so the threshold does not
#: need to be delicate -- it needs to be somewhere in the gap and stated.
PAGE_AMPLIFICATION_MAX = 50

#: Below this many rows read, no amplification verdict is passed at all. A
#: query that read 60 rows to return one is not a capacity problem however bad
#: the ratio looks, and a threshold without a floor turns every small
#: correlated subquery into a finding.
AMPLIFICATION_FLOOR_ROWS = 500

#: A ``point`` lookup resolves one row by key. The allowance is not 1: the
#: correlated ``message_count`` subquery on a conversation legitimately reads
#: that thread's messages, and a soft-deleted row can be skipped.
POINT_ROWS_MAX = 200

#: An ``aggregate`` may fetch this fraction of its rows from the heap before
#: the index is judged not to cover it. An ``Index Only Scan`` still visits
#: the heap for rows whose page the visibility map does not mark all-visible,
#: so a small fraction is normal on a table written recently; ``sum`` over a
#: column the index does not carry visits every one of them.
AGGREGATE_HEAP_FRACTION_MAX = 0.10

Kind = Literal["page", "aggregate", "point"]

Verdict = Literal["ok", "amplified", "uncovered", "seq-scan", "small-scan", "skipped"]

#: Verdicts that make ``run`` exit non-zero.
FAILING_VERDICTS: frozenset[str] = frozenset({"seq-scan", "amplified", "uncovered"})


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HotPath:
    """One statement the request path issues, as the request path issues it.

    ``sql`` is written out rather than built through SQLAlchemy on purpose.
    The adapter builds its statement from an ``ExecutionContext`` and a
    hydration path this process has no business constructing, and what 2.4
    needs is the *predicate and the ordering* -- which are the part a human
    has to be able to read next to the plan. ``source`` names the method it
    mirrors so the two can be diffed by a person, and by a test.
    """

    name: str
    kind: Kind
    #: ``schema.table`` the statement is judged against -- the one a ``Seq
    #: Scan`` would be a finding on.
    table: str
    #: ``module/adapters/file.py::method`` -- checked to exist by the unit
    #: tests, so a renamed adapter method fails a gate instead of leaving this
    #: catalogue quietly describing a method nobody calls.
    source: str
    #: What breaks if this statement is slow: the scenario in ``§0`` and the
    #: bottleneck in ``§2`` it belongs to.
    backs: str
    sql: str
    #: Probe names this statement needs bound (see ``TenantProbes``). A probe
    #: that comes back empty skips the statement rather than measuring a
    #: predicate no row matches.
    needs: tuple[str, ...] = ()


CATALOGUE: tuple[HotPath, ...] = (
    HotPath(
        name="files.list",
        kind="page",
        table="files.files",
        source="modules/files/adapters/sql_repository.py::SqlFileRepository.list",
        backs="§0 `browse` -- the file list every screen opens with (ح-9)",
        needs=("workspace_id",),
        sql="""
        SELECT * FROM files.files
        WHERE workspace_id = :workspace_id AND deleted_at IS NULL
        ORDER BY id DESC LIMIT :page
        """,
    ),
    HotPath(
        name="files.count",
        kind="aggregate",
        table="files.files",
        source="modules/files/adapters/sql_repository.py::SqlFileRepository.count",
        backs="the per-workspace file cap (`max_files_per_workspace`, 07 §4) -- every upload",
        needs=("workspace_id",),
        sql="""
        SELECT count(*) FROM files.files
        WHERE workspace_id = :workspace_id AND deleted_at IS NULL
        """,
    ),
    HotPath(
        name="files.bytes_in_space",
        kind="aggregate",
        table="files.files",
        source="modules/files/adapters/sql_repository.py::SqlFileRepository.bytes_in_space",
        backs="the space quota (`framework/di/space_quota.py`) -- runs on EVERY upload "
        "registration, inside the transaction holding `SELECT ... FOR UPDATE` on the space row, "
        "so its duration is lock-hold time",
        needs=("workspace_id", "space_id"),
        sql="""
        SELECT coalesce(sum(size_bytes), 0)::bigint FROM files.files
        WHERE workspace_id = :workspace_id AND space_id = :space_id AND deleted_at IS NULL
        """,
    ),
    HotPath(
        name="files.live_namesakes",
        kind="page",
        table="files.files",
        source="modules/files/adapters/sql_repository.py::SqlFileRepository.live_namesakes",
        backs="the replacement rule (س-29) on every upload completion and every rename -- the "
        "predicate `0003_file_name_lookup` was measured for",
        needs=("workspace_id", "space_id", "file_name"),
        sql="""
        SELECT id FROM files.files
        WHERE workspace_id = :workspace_id
          AND space_id = :space_id
          AND name_key = lower(normalize(:file_name, NFC))
          AND deleted_at IS NULL
        """,
    ),
    HotPath(
        name="conversations.list_by_agent",
        kind="page",
        table="conversations.conversations",
        source="modules/conversations/adapters/sql_repository.py"
        "::SqlConversationRepository.list_by_agent",
        backs="§0 `browse` -- the thread list, with its correlated `message_count`",
        needs=("workspace_id", "agent_key"),
        sql="""
        SELECT c.*,
               (SELECT coalesce(max(m.seq), 0) FROM conversations.messages m
                 WHERE m.conversation_id = c.id AND m.workspace_id = c.workspace_id)
                 AS message_count
        FROM conversations.conversations c
        WHERE c.workspace_id = :workspace_id
          AND c.agent_key = :agent_key
          AND c.deleted_at IS NULL
        ORDER BY c.id DESC LIMIT :page
        """,
    ),
    HotPath(
        name="conversations.list_messages",
        kind="page",
        table="conversations.messages",
        source="modules/conversations/adapters/sql_repository.py"
        "::SqlConversationRepository.list_messages",
        backs="§0 `browse`/`stream` -- the transcript, the read that precedes every turn",
        needs=("workspace_id", "conversation_id"),
        sql="""
        SELECT * FROM conversations.messages
        WHERE conversation_id = :conversation_id
          AND workspace_id = :workspace_id
          AND deleted_at IS NULL
        ORDER BY seq LIMIT :page
        """,
    ),
    HotPath(
        name="conversations.counts_by_space",
        kind="aggregate",
        table="conversations.conversations",
        source="modules/conversations/adapters/sql_repository.py"
        "::SqlConversationRepository.counts_by_space",
        backs="the thread count beside each space in a space listing",
        needs=("workspace_id", "space_id"),
        sql="""
        SELECT space_id, count(*) FROM conversations.conversations
        WHERE workspace_id = :workspace_id
          AND space_id IN (:space_id)
          AND deleted_at IS NULL
        GROUP BY space_id
        """,
    ),
    HotPath(
        name="knowledge.documents.list",
        kind="page",
        table="knowledge.documents",
        source="modules/knowledge/adapters/sql_repository.py::SqlDocumentRepository.list",
        backs="§0 `browse` -- the corpus listing",
        needs=("workspace_id",),
        sql="""
        SELECT * FROM knowledge.documents
        WHERE workspace_id = :workspace_id
        ORDER BY id DESC LIMIT :page
        """,
    ),
    HotPath(
        name="knowledge.documents.ids_for_files",
        kind="page",
        table="knowledge.documents",
        source="modules/knowledge/adapters/sql_repository.py::SqlDocumentRepository.ids_for_files",
        backs="§0 `rag` -- file-scoped retrieval resolves the caller's file ids to documents "
        "BEFORE the vector search (ح-5/ح-11); also the duplicate check on every document "
        "registration",
        needs=("workspace_id", "file_ids"),
        sql="""
        SELECT id FROM knowledge.documents
        WHERE workspace_id = :workspace_id AND file_id = ANY(CAST(:file_ids AS uuid[]))
        """,
    ),
    HotPath(
        name="knowledge.chunks.vector_refs",
        kind="page",
        table="knowledge.chunks",
        source="modules/knowledge/adapters/sql_repository.py::SqlDocumentRepository.vector_refs",
        backs="re-index and purge -- the point ids to delete from Qdrant for one document",
        needs=("workspace_id", "document_id"),
        sql="""
        SELECT collection, point_id FROM knowledge.chunks
        WHERE document_id = :document_id AND workspace_id = :workspace_id
        """,
    ),
    HotPath(
        name="knowledge.chunks.parent_texts_for_chunk_ids",
        kind="page",
        table="knowledge.chunks",
        source="modules/knowledge/adapters/sql_repository.py"
        "::SqlDocumentRepository.parent_texts_for_chunk_ids",
        backs="§0 `rag` -- parent widening (P-34) on the SYNCHRONOUS answer path, once per "
        "retrieval, keyed by the Qdrant point ids the search just returned (ح-5/ح-11)",
        needs=("workspace_id", "point_ids"),
        sql="""
        SELECT c.point_id, p.id, p.text, p.is_complete
        FROM knowledge.chunks c
        JOIN knowledge.parent_chunks p ON c.parent_id = p.id
        WHERE c.point_id = ANY(CAST(:point_ids AS uuid[]))
          AND c.workspace_id = :workspace_id
          AND p.workspace_id = :workspace_id
        """,
    ),
    HotPath(
        name="knowledge.chunks.chunk_texts",
        kind="page",
        table="knowledge.chunks",
        source="modules/knowledge/adapters/sql_repository.py::SqlDocumentRepository.chunk_texts",
        backs="summarisation -- the one query in the module that can return a whole document body",
        needs=("workspace_id", "document_id"),
        sql="""
        SELECT c.text, p.id AS parent_id, p.text AS parent_text, p.is_complete
        FROM knowledge.chunks c
        LEFT JOIN knowledge.parent_chunks p ON c.parent_id = p.id
        WHERE c.document_id = :document_id AND c.workspace_id = :workspace_id
        ORDER BY c.seq
        """,
    ),
    HotPath(
        name="spaces.list",
        kind="page",
        table="spaces.spaces",
        source="modules/spaces/adapters/sql_repository.py::SqlSpaceRepository.list",
        backs="§0 `browse` -- the space switcher, read on nearly every screen",
        needs=("workspace_id",),
        sql="""
        SELECT * FROM spaces.spaces
        WHERE workspace_id = :workspace_id AND deleted_at IS NULL
        ORDER BY id DESC LIMIT :page
        """,
    ),
    HotPath(
        name="access.list_for_user",
        kind="point",
        table="access.role_assignments",
        source="modules/access/adapters/sql_repository.py::SqlRoleAssignmentRepository.list_for_user",
        backs="the auth path (ح-2) -- read on every request whose principal is not cached (1.1)",
        needs=("workspace_id", "user_id"),
        sql="""
        SELECT * FROM access.role_assignments
        WHERE workspace_id = :workspace_id AND user_id = :user_id
        ORDER BY created_at
        """,
    ),
)


# ---------------------------------------------------------------------------
# Tenant probes -- every parameter but the workspace, discovered inside it
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TenantProbes:
    """The parameter values for one tenant, all read as ``app_rw`` under that
    tenant's GUC.

    A probe that comes back ``None``/empty is not an error and is not
    substituted: the statements needing it are reported ``skipped`` with the
    probe named. A tenant with no documents cannot exercise the document
    plans, and inventing an id to bind would measure an empty result set and
    call it a fast query -- which is exactly the "query on an empty table
    measures the index, not the platform" failure 0.1's condition (3) exists
    to prevent.
    """

    workspace_id: str
    space_id: str | None
    conversation_id: str | None
    document_id: str | None
    agent_key: str | None
    user_id: str | None
    file_name: str | None
    file_ids: tuple[str, ...]
    point_ids: tuple[str, ...]

    def binding(self, name: str) -> object | None:
        value = getattr(self, name, None)
        return None if value in (None, (), "") else value

    def missing(self, needs: Sequence[str]) -> list[str]:
        return [name for name in needs if self.binding(name) is None]


_PROBE_SQL: dict[str, str] = {
    # The tenant's largest space: the one whose quota sum is most expensive,
    # which is the case worth planning. `deleted_at IS NULL` because a deleted
    # space's files are not what the quota reads.
    "space_id": """
        SELECT space_id FROM files.files
        WHERE workspace_id = :workspace_id AND space_id IS NOT NULL AND deleted_at IS NULL
        GROUP BY space_id ORDER BY count(*) DESC LIMIT 1
    """,
    # The longest thread, for the same reason: a transcript read is only
    # interesting on a conversation that has a transcript.
    "conversation_id": """
        SELECT conversation_id FROM conversations.messages
        WHERE workspace_id = :workspace_id
        GROUP BY conversation_id ORDER BY count(*) DESC LIMIT 1
    """,
    "document_id": """
        SELECT document_id FROM knowledge.chunks
        WHERE workspace_id = :workspace_id
        GROUP BY document_id ORDER BY count(*) DESC LIMIT 1
    """,
    "agent_key": """
        SELECT agent_key FROM conversations.conversations
        WHERE workspace_id = :workspace_id AND deleted_at IS NULL
        GROUP BY agent_key ORDER BY count(*) DESC LIMIT 1
    """,
    "user_id": """
        SELECT user_id FROM access.role_assignments
        WHERE workspace_id = :workspace_id LIMIT 1
    """,
    "file_name": """
        SELECT name FROM files.files
        WHERE workspace_id = :workspace_id AND deleted_at IS NULL LIMIT 1
    """,
}

_PROBE_LIST_SQL: dict[str, tuple[str, str]] = {
    # A page of file ids that ACTUALLY have documents -- binding ids with no
    # document would measure the same index scan returning nothing, which is
    # the fast half of the question.
    "file_ids": (
        """
        SELECT file_id FROM knowledge.documents
        WHERE workspace_id = :workspace_id AND file_id IS NOT NULL LIMIT :n
        """,
        "page",
    ),
    "point_ids": (
        """
        SELECT point_id FROM knowledge.chunks
        WHERE workspace_id = :workspace_id AND point_id IS NOT NULL LIMIT :n
        """,
        "k",
    ),
}


async def probe_tenant(conn: AsyncConnection, workspace_id: str) -> TenantProbes:
    """Read one tenant's parameter values, each in its own RLS-scoped
    transaction.

    Each probe runs under ``SET LOCAL app.workspace_id`` like any request-path
    statement: these reads are subject to exactly the policies the measured
    statements are, so a probe can never hand back an id from another tenant
    for the plans below to bind.
    """
    scalars: dict[str, str | None] = {}
    for name, sql in _PROBE_SQL.items():
        async with _tenant_tx(conn, workspace_id):
            value = await conn.scalar(text(sql), {"workspace_id": workspace_id})
        scalars[name] = None if value is None else str(value)

    lists: dict[str, tuple[str, ...]] = {}
    for name, (sql, size) in _PROBE_LIST_SQL.items():
        async with _tenant_tx(conn, workspace_id):
            rows = (
                await conn.execute(
                    text(sql),
                    {
                        "workspace_id": workspace_id,
                        "n": PAGE_LIMIT if size == "page" else RETRIEVAL_K,
                    },
                )
            ).scalars()
            lists[name] = tuple(str(row) for row in rows)

    return TenantProbes(
        workspace_id=workspace_id,
        space_id=scalars["space_id"],
        conversation_id=scalars["conversation_id"],
        document_id=scalars["document_id"],
        agent_key=scalars["agent_key"],
        user_id=scalars["user_id"],
        file_name=scalars["file_name"],
        file_ids=lists["file_ids"],
        point_ids=lists["point_ids"],
    )


# ---------------------------------------------------------------------------
# Running EXPLAIN, and reading the plan back
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """What one plan says, reduced to the four numbers 2.4 argues from."""

    rows_returned: int
    rows_read: int
    heap_fetches: int
    shared_blocks: int
    execution_ms: float
    planning_ms: float
    #: ``schema.table`` for every ``Seq Scan``/``Parallel Seq Scan`` node,
    #: tenant schemas only.
    seq_scans: tuple[str, ...]
    node_types: tuple[str, ...]

    @property
    def amplification(self) -> float:
        return self.rows_read / max(self.rows_returned, 1)

    @property
    def heap_fraction(self) -> float:
        return self.heap_fetches / max(self.rows_read, 1)


@dataclass(frozen=True, slots=True)
class Finding:
    """One catalogue entry's result."""

    name: str
    kind: Kind
    table: str
    source: str
    backs: str
    verdict: Verdict
    reason: str
    summary: PlanSummary | None = None
    skipped_for: tuple[str, ...] = ()


_SCAN_NODES = frozenset(
    {
        "Seq Scan",
        "Index Scan",
        "Index Only Scan",
        "Bitmap Heap Scan",
        "Tid Scan",
        "Function Scan",
        "Values Scan",
        "CTE Scan",
        "Subquery Scan",
        "Foreign Scan",
    }
)

_HEAP_SCAN_NODES = frozenset({"Seq Scan", "Bitmap Heap Scan", "Index Scan"})

_WRITE = re.compile(
    r"\b(insert|update|delete|truncate|merge|create|drop|alter|grant|revoke)\b", re.IGNORECASE
)


def _refuse_writes(entry: HotPath) -> None:
    """``EXPLAIN ANALYZE`` executes what it is given.

    A catalogue is a list of strings, and the day someone adds "the delete
    that purges a space" to it, this module would purge a space on a live
    database while reporting a plan. The check is crude on purpose -- a
    keyword scan, not a parser -- because the correct set here is exactly one
    shape (a read), and anything a keyword scan cannot clear does not belong
    in this file.
    """
    if not entry.sql.lstrip().lower().startswith("select"):
        raise SystemExit(
            f"explain_hot_paths refused: catalogue entry `{entry.name}` does not begin with "
            "SELECT. EXPLAIN ANALYZE EXECUTES the statement -- only reads belong in this "
            "catalogue."
        )
    if _WRITE.search(entry.sql):
        raise SystemExit(
            f"explain_hot_paths refused: catalogue entry `{entry.name}` contains a "
            "data- or schema-modifying keyword. EXPLAIN ANALYZE EXECUTES the statement."
        )


class _Tx:
    """``async with`` over one RLS-scoped transaction on ``conn``."""

    def __init__(self, conn: AsyncConnection, workspace_id: str) -> None:
        self._conn = conn
        self._workspace_id = workspace_id
        self._tx: Any = None

    async def __aenter__(self) -> AsyncConnection:
        self._tx = self._conn.begin()
        await self._tx.__aenter__()
        await self._conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"),
            {"ws": self._workspace_id},
        )
        return self._conn

    async def __aexit__(self, *exc: Any) -> Any:
        return await self._tx.__aexit__(*exc)


def _tenant_tx(conn: AsyncConnection, workspace_id: str) -> _Tx:
    """The GUC is set as the FIRST statement of the transaction and is
    transaction-scoped, exactly as ``TenantSessionFactory`` does it -- this
    tool measures the plan the application gets, so it must set the context
    the application sets, the way it sets it."""
    return _Tx(conn, workspace_id)


async def refuse_privileged_role(conn: AsyncConnection) -> None:
    """Refuse to run as a role RLS does not apply to (module docstring).

    ``load_seed`` refuses for the write side of the same rule; here the cost
    of ignoring it is a *report* rather than a corpus: every plan would be
    missing the policy's qual, and any plan involving a non-leakproof
    expression would show an index condition the request path can never get.
    """
    async with conn.begin():
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    if bool(row.rolsuper) or bool(row.rolbypassrls):
        raise SystemExit(
            "explain_hot_paths refused: this connection's role is a superuser or holds "
            "BYPASSRLS, so row-level security applies to none of the plans it would take. "
            "Capacity step 2.4 asks for the plan UNDER `SET LOCAL app.workspace_id` -- point "
            "DATABASE_URL at app_rw's own DSN (see this module's docstring) and run it again."
        )


async def explain(
    conn: AsyncConnection, entry: HotPath, probes: TenantProbes
) -> tuple[PlanSummary, dict[str, Any]]:
    """Run one catalogue entry under the tenant's RLS context and reduce its
    plan.

    ``FORMAT JSON`` rather than the text output an operator would read by eye:
    the text form is for a human, and every number this module argues from
    (rows, loops, buffers, heap fetches) is a labelled field in the JSON one.
    Parsing the text form to recover them would be reading a rendering.
    """
    _refuse_writes(entry)
    # `LIMIT :page` is `limit + 1` because every paginating adapter asks for
    # one row past the page to learn whether a next cursor exists
    # (`framework/pagination`); measuring `LIMIT 20` would measure a query the
    # application never issues.
    bindings: dict[str, object] = {"page": PAGE_LIMIT + 1}
    for name in entry.needs:
        value = probes.binding(name)
        # A LIST binds as a list, not as a `{a,b}` literal: asyncpg sends an
        # array parameter and refuses a string. The SQL says `= ANY(CAST(:ids
        # AS uuid[]))` where the adapter says `.in_(ids)`, and the two are the
        # SAME plan -- PostgreSQL rewrites `IN (const, const, ...)` to
        # `= ANY(ARRAY[...])` itself, which is visible in the plan text as
        # `Filter: (file_id = ANY ('{...}'::uuid[]))` even when the statement
        # was written with `IN`. Binding one array instead of twenty scalars
        # also keeps the statement shape constant across page sizes.
        bindings[name] = list(value) if isinstance(value, tuple) else value
    async with _tenant_tx(conn, probes.workspace_id):
        raw = await conn.scalar(
            text(f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) {entry.sql}"), bindings
        )
    document = json.loads(raw) if isinstance(raw, str) else raw
    plan = document[0]
    return summarise(plan), plan


def summarise(explained: Mapping[str, Any]) -> PlanSummary:
    """Reduce one ``EXPLAIN (FORMAT JSON)`` document to the numbers 2.4 reads.

    **Rows read counts LOOPS, and that is the whole point.** ``EXPLAIN``
    reports a node's ``Actual Rows`` as a per-loop average, so a correlated
    subquery executed 21 times reporting 20 rows read 420. A summary that
    ignored ``Actual Loops`` would report the ``list_by_agent`` plan as
    reading 21 rows when its ``message_count`` subquery alone reads several
    hundred -- and the N+1 shapes this step exists to find are precisely the
    ones that hide inside a loop count.

    **``Rows Removed by Filter`` counts too.** A scan that returned 20 rows
    after discarding 16,992 read 17,012, and the discarded ones are the entire
    finding: they are what an index would have skipped.
    """
    rows_read = 0
    heap_fetches = 0
    seq_scans: list[str] = []
    node_types: list[str] = []

    def walk(node: Mapping[str, Any]) -> None:
        nonlocal rows_read, heap_fetches
        node_type = str(node.get("Node Type", ""))
        node_types.append(node_type)
        loops = int(node.get("Actual Loops", 1) or 1)
        if node_type in _SCAN_NODES:
            actual = float(node.get("Actual Rows", 0) or 0)
            removed = float(node.get("Rows Removed by Filter", 0) or 0)
            recheck = float(node.get("Rows Removed by Index Recheck", 0) or 0)
            rows_read += round((actual + removed + recheck) * loops)
        if node_type in _HEAP_SCAN_NODES:
            heap_fetches += round(float(node.get("Actual Rows", 0) or 0) * loops)
        heap_fetches += int(node.get("Heap Fetches", 0) or 0)
        if "Seq Scan" in node_type:
            relation = f"{node.get('Schema', '?')}.{node.get('Relation Name', '?')}"
            if str(node.get("Schema", "")) in TENANT_SCHEMAS:
                seq_scans.append(relation)
        for child in node.get("Plans", ()) or ():
            walk(child)

    root = explained["Plan"]
    walk(root)
    return PlanSummary(
        rows_returned=round(float(root.get("Actual Rows", 0) or 0)),
        rows_read=rows_read,
        heap_fetches=heap_fetches,
        shared_blocks=int(explained["Plan"].get("Shared Hit Blocks", 0) or 0)
        + int(explained["Plan"].get("Shared Read Blocks", 0) or 0),
        execution_ms=round(float(explained.get("Execution Time", 0.0) or 0.0), 3),
        planning_ms=round(float(explained.get("Planning Time", 0.0) or 0.0), 3),
        seq_scans=tuple(dict.fromkeys(seq_scans)),
        node_types=tuple(node_types),
    )


def _judge_sequential_scan(summary: PlanSummary) -> tuple[Verdict, str]:
    """A ``Seq Scan`` on a tenant table, sized.

    A sequential scan of a SMALL relation is the planner being right, not a
    missing index -- reading 62 rows costs less than descending a tree to read
    them, and PostgreSQL switches on its own once the table grows. Failing it
    would make 2.4's criterion unpassable on a CORRECT plan. But it is not
    reported green either: what such a run has proven is that the statement is
    fast on a table that is small HERE, which is a fact about the corpus and
    not about the schema. The verdict says so, names the table, and points at
    0.1's condition (3) -- a seed that fills it -- rather than at an index.
    """
    scanned = ", ".join(summary.seq_scans)
    if summary.rows_read < AMPLIFICATION_FLOOR_ROWS:
        return (
            "small-scan",
            f"sequential scan on {scanned}, but only {summary.rows_read:,} rows read: at "
            "this size a scan is the cheaper plan and the planner is right. This statement "
            "is therefore NOT proven at scale -- the table is under-populated in this "
            "corpus, not unindexed (0.1 condition 3)",
        )
    return (
        "seq-scan",
        f"sequential scan on {scanned} -- a tenant table on the hot "
        f"path (2.4's acceptance criterion); {summary.rows_read:,} rows read for "
        f"{summary.rows_returned:,} returned",
    )


def _judge_aggregate(summary: PlanSummary) -> tuple[Verdict, str]:
    """An aggregate returns one row by definition, so rows read is not the
    defect -- touching the heap for them is."""
    if summary.heap_fraction > AGGREGATE_HEAP_FRACTION_MAX:
        return (
            "uncovered",
            f"{summary.heap_fetches:,} of {summary.rows_read:,} rows fetched from the heap "
            f"({summary.heap_fraction:.0%}) to return one row: the index does not carry the "
            "columns this aggregate reads, so every row costs a heap page",
        )
    return (
        "ok",
        f"index-only over {summary.rows_read:,} rows "
        f"({summary.heap_fetches:,} heap fetches, {summary.heap_fraction:.0%})",
    )


def _judge_point(summary: PlanSummary) -> tuple[Verdict, str]:
    if summary.rows_read > POINT_ROWS_MAX:
        return (
            "amplified",
            f"{summary.rows_read:,} rows read to resolve one key (allowance "
            f"{POINT_ROWS_MAX}): the key is not indexed",
        )
    return "ok", f"{summary.rows_read:,} rows read for a keyed lookup"


def _judge_page(summary: PlanSummary) -> tuple[Verdict, str]:
    if summary.amplification > PAGE_AMPLIFICATION_MAX:
        return (
            "amplified",
            f"{summary.rows_read:,} rows read for {summary.rows_returned:,} returned "
            f"({summary.amplification:,.0f}x, allowance {PAGE_AMPLIFICATION_MAX}x): the index "
            "carries the filter but not this predicate or this ordering",
        )
    return (
        "ok",
        f"{summary.rows_read:,} rows read for {summary.rows_returned:,} returned "
        f"({summary.amplification:,.1f}x)",
    )


_BY_KIND = {"aggregate": _judge_aggregate, "point": _judge_point, "page": _judge_page}


def judge(entry: HotPath, summary: PlanSummary) -> tuple[Verdict, str]:
    """The verdict, and the sentence that justifies it.

    A ``Seq Scan`` on a tenant table is checked FIRST and never overridden by
    a kind rule: it is 2.4's acceptance criterion written literally, and a plan
    can be both a sequential scan and, by the ratio, unremarkable -- a scan
    that returns most of a small table amplifies very little and is still the
    plan that stops scaling.

    Below ``AMPLIFICATION_FLOOR_ROWS`` nothing is judged at all. A query that
    read 60 rows to return one is not a capacity problem however bad the ratio
    looks, and a threshold without a floor turns every small correlated
    subquery into a finding.
    """
    if summary.seq_scans:
        return _judge_sequential_scan(summary)
    if summary.rows_read < AMPLIFICATION_FLOOR_ROWS:
        return (
            "ok",
            f"{summary.rows_read:,} rows read -- under the {AMPLIFICATION_FLOOR_ROWS}-row "
            "floor, so no amplification verdict is passed",
        )
    return _BY_KIND[entry.kind](summary)


async def run_catalogue(
    conn: AsyncConnection, probes: TenantProbes, *, only: Sequence[str] = ()
) -> list[Finding]:
    findings: list[Finding] = []
    for entry in CATALOGUE:
        if only and entry.name not in only:
            continue
        missing = probes.missing(entry.needs)
        if missing:
            findings.append(
                Finding(
                    name=entry.name,
                    kind=entry.kind,
                    table=entry.table,
                    source=entry.source,
                    backs=entry.backs,
                    verdict="skipped",
                    reason=(
                        "this tenant has no "
                        + ", ".join(missing)
                        + " -- a plan bound to an invented id would measure an empty result set"
                    ),
                    skipped_for=tuple(missing),
                )
            )
            continue
        summary, _plan = await explain(conn, entry, probes)
        verdict, reason = judge(entry, summary)
        findings.append(
            Finding(
                name=entry.name,
                kind=entry.kind,
                table=entry.table,
                source=entry.source,
                backs=entry.backs,
                verdict=verdict,
                reason=reason,
                summary=summary,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Choosing the tenant
# ---------------------------------------------------------------------------


def workspace_from_manifest(seed_id: str | None) -> tuple[str, str]:
    """The largest seeded tenant, off ``app.ops.load_seed``'s own manifest.

    Returns ``(workspace_id, provenance)``. The manifest is read rather than
    the database queried for the reason in the module docstring: finding the
    largest tenant is a cross-tenant question, and answering it would mean
    handing this process a role RLS does not constrain -- for one number that
    ``load_seed`` already wrote down.
    """
    if seed_id is not None:
        candidates = [MANIFEST_DIR / f"{seed_id}.json"]
    else:
        candidates = sorted(MANIFEST_DIR.glob("*.json"), reverse=True)
    for path in candidates:
        if not path.exists():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        largest = document.get("largest_workspaces") or []
        if largest:
            return str(largest[0]["workspace_id"]), f"{path} (largest_workspaces[0])"
    raise SystemExit(
        "no workspace to measure: pass --workspace-id, or run `python -m app.ops.load_seed run` "
        f"first so a manifest exists under {MANIFEST_DIR}/. The tenant cannot be chosen by "
        "query -- this tool connects as app_rw, and RLS is exactly what stops it from seeing "
        "across tenants (module docstring)."
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_MARK: dict[str, str] = {
    "ok": "🟢",
    "small-scan": "🟡",
    "skipped": "⬜",
    "amplified": "🟠",
    "uncovered": "🟠",
    "seq-scan": "🔴",
}


def render_table(findings: Sequence[Finding], *, workspace_id: str, provenance: str) -> str:
    header = (
        f"hot-path plans under RLS · workspace {workspace_id} · {provenance}\n"
        f"page limit {PAGE_LIMIT} · retrieval k {RETRIEVAL_K}"
    )
    columns = (
        f"    {'statement':<44}  {'kind':<9}  {'read':>10}  {'ret':>7}  "
        f"{'cost':>9}  {'blocks':>9}  {'ms':>9}"
    )
    lines = [header, "", columns, "-" * len(columns)]
    for finding in findings:
        mark = _MARK[finding.verdict]
        summary = finding.summary
        if summary is None:
            lines.append(f" {mark}  {finding.name:<44}  {finding.kind:<9}  {'skipped':>10}")
        else:
            # The `cost` column is the number that entry is JUDGED on, not one
            # number for all three kinds: printing `17,012x` beside an
            # aggregate that is judged on heap access invites the reader to
            # argue with a figure nothing in the verdict used.
            cost = (
                f"{summary.heap_fraction:>8.0%}h"
                if finding.kind == "aggregate"
                else f"{summary.amplification:>8,.0f}x"
            )
            lines.append(
                f" {mark}  {finding.name:<44}  {finding.kind:<9}  "
                f"{summary.rows_read:>10,}  {summary.rows_returned:>7,}  "
                f"{cost:>9}  {summary.shared_blocks:>9,}  "
                f"{summary.execution_ms:>9,.2f}"
            )
        lines.append(f"        {finding.reason}")
    return "\n".join(lines)


def render_json(findings: Sequence[Finding], *, workspace_id: str, provenance: str) -> str:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "workspace_provenance": provenance,
        "page_limit": PAGE_LIMIT,
        "retrieval_k": RETRIEVAL_K,
        "thresholds": {
            "page_amplification_max": PAGE_AMPLIFICATION_MAX,
            "amplification_floor_rows": AMPLIFICATION_FLOOR_ROWS,
            "point_rows_max": POINT_ROWS_MAX,
            "aggregate_heap_fraction_max": AGGREGATE_HEAP_FRACTION_MAX,
        },
        "findings": [
            {
                **{k: v for k, v in asdict(finding).items() if k != "summary"},
                "summary": None if finding.summary is None else asdict(finding.summary),
            }
            for finding in findings
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _run(args: argparse.Namespace) -> int:
    workspace_id, provenance = (
        (args.workspace_id, "--workspace-id")
        if args.workspace_id
        else workspace_from_manifest(args.seed_id)
    )
    engine: AsyncEngine = create_engine(
        DatabaseSettings(url=load_settings().database.url), poolclass=NullPool
    )
    try:
        async with engine.connect() as conn:
            await refuse_privileged_role(conn)
            probes = await probe_tenant(conn, workspace_id)
            findings = await run_catalogue(conn, probes, only=tuple(args.only or ()))
    finally:
        await engine.dispose()

    print(
        render_json(findings, workspace_id=workspace_id, provenance=provenance)
        if args.json
        else render_table(findings, workspace_id=workspace_id, provenance=provenance)
    )
    failing = [f for f in findings if f.verdict in FAILING_VERDICTS]
    skipped = [f for f in findings if f.verdict == "skipped"]
    unproven = [f for f in findings if f.verdict == "small-scan"]
    if unproven:
        print(
            f"🟡 {len(unproven)} statement(s) are fast only because a table this corpus barely "
            "populates is small: "
            + ", ".join(f.name for f in unproven)
            + ". Their plans are NOT proven at scale -- 0.1 condition (3) is the remedy, not an "
            "index.",
            file=sys.stderr,
        )
    if skipped:
        print(
            f"⚠️  {len(skipped)} statement(s) skipped for want of a parameter in this tenant: "
            + ", ".join(f.name for f in skipped)
            + ". A report with skips is not a review of the hot path.",
            file=sys.stderr,
        )
    _logger.info(
        "ops.explain_hot_paths.reported",
        extra={
            "workspace_id": workspace_id,
            "statements": len(findings),
            "failing": len(failing),
            "unproven": len(unproven),
            "skipped": len(skipped),
        },
    )
    if failing:
        print(
            f"❌ {len(failing)} of {len(findings)} statements fail 2.4: "
            + ", ".join(f"{f.name} ({f.verdict})" for f in failing),
            file=sys.stderr,
        )
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.explain_hot_paths",
        description="EXPLAIN (ANALYZE, BUFFERS) the request path's own statements under a "
        "tenant's RLS context (capacity step 2.4 -- module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    run_parser = sub.add_parser("run", help="explain every catalogued statement (reads only)")
    run_parser.add_argument(
        "--workspace-id",
        default=None,
        help="the tenant to measure. Default: the largest one in the newest seed manifest -- "
        "this tool connects as app_rw and RLS is what stops it choosing by query",
    )
    run_parser.add_argument(
        "--seed-id",
        default=None,
        help="read this seed's manifest instead of the newest one",
    )
    run_parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        metavar="NAME",
        help="explain only these catalogue entries (see `list`)",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the full report as JSON on stdout, for archiving beside a load run (0.5)",
    )

    sub.add_parser("list", help="print the catalogue without touching the database")
    return parser


def _list_catalogue() -> None:
    for entry in CATALOGUE:
        print(f"{entry.name:<44}  {entry.kind:<9}  {entry.table}")
        print(f"    mirrors  {entry.source}")
        print(f"    backs    {entry.backs}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    if args.action == "list":
        _list_catalogue()
        raise SystemExit(0)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()


__all__ = [
    "AGGREGATE_HEAP_FRACTION_MAX",
    "AMPLIFICATION_FLOOR_ROWS",
    "CATALOGUE",
    "FAILING_VERDICTS",
    "PAGE_AMPLIFICATION_MAX",
    "PAGE_LIMIT",
    "POINT_ROWS_MAX",
    "RETRIEVAL_K",
    "TENANT_SCHEMAS",
    "Finding",
    "HotPath",
    "PlanSummary",
    "TenantProbes",
    "explain",
    "judge",
    "probe_tenant",
    "refuse_privileged_role",
    "run_catalogue",
    "summarise",
    "workspace_from_manifest",
]
