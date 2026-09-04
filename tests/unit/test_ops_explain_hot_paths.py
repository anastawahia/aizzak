"""``app.ops.explain_hot_paths`` -- the catalogue's honesty and the plan reader's arithmetic.

Two things are tested here and they are different in kind.

**The catalogue is a MIRROR, and a mirror rots silently.** Every entry claims
to reproduce the predicate one adapter method issues. Nothing in Python
connects the two, so a renamed or deleted method leaves the entry describing a
statement the application no longer makes -- and the report stays green while
measuring a query nobody runs. The ``source`` field exists to be checked, and
these tests read ``modules/*/adapters/sql_repository.py`` as text and fail when
a named method is not there. That is the ``load_seed`` ``FLOOR``-drift guard
applied to a different pair of files, for the same reason.

**The plan reader is arithmetic over a document**, and it can be tested
without a database. The fixtures below are reduced from REAL ``EXPLAIN
(ANALYZE, BUFFERS, FORMAT JSON)`` output taken on the Wave-0 seed (1,000,771
chunks · 100,004 files · 200 workspaces) as ``app_rw`` under ``SET LOCAL
app.workspace_id`` -- node types, row counts, loop counts and heap fetches are
the measured values; the columns and sub-plans that do not reach a verdict were
dropped so the shape stays readable. Two of them are the before/after pair for
``knowledge.chunks.parent_texts_for_chunk_ids``, which is 2.4's headline
finding.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.ops.explain_hot_paths import (
    AMPLIFICATION_FLOOR_ROWS,
    CATALOGUE,
    FAILING_VERDICTS,
    PAGE_AMPLIFICATION_MAX,
    TENANT_SCHEMAS,
    HotPath,
    TenantProbes,
    _refuse_writes,
    judge,
    summarise,
    workspace_from_manifest,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "app"

#: A bind placeholder, and NOT the right half of a PostgreSQL `::cast` --
#: `coalesce(...)::bigint` is a type name, which is exactly how SQLAlchemy's
#: own `text()` reads it too.
_PLACEHOLDER = re.compile(r"(?<!:):([a-z_]+)")


# ---------------------------------------------------------------------------
# The catalogue mirrors the adapters
# ---------------------------------------------------------------------------


def test_the_catalogue_is_not_empty() -> None:
    """The other tests in this file all pass vacuously over an empty tuple."""
    assert len(CATALOGUE) >= 10


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.name)
def test_every_entry_names_an_adapter_method_that_exists(entry: HotPath) -> None:
    """``source`` is ``module/path.py::Class.method``, and both halves are real.

    The failure this catches is the quiet one: a method renamed in a refactor
    leaves this catalogue measuring a predicate the application stopped
    issuing, and the report goes on printing a green row for it.
    """
    path_part, _, qualified = entry.source.partition("::")
    path = _SRC / path_part
    assert path.exists(), f"{entry.name}: {path_part} does not exist"

    class_name, _, method = qualified.partition(".")
    source = path.read_text(encoding="utf-8")
    assert re.search(rf"^class {re.escape(class_name)}\b", source, re.MULTILINE), (
        f"{entry.name}: {path_part} defines no class {class_name}"
    )
    assert re.search(rf"^    async def {re.escape(method)}\(", source, re.MULTILINE), (
        f"{entry.name}: {class_name} defines no method {method}"
    )


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.name)
def test_every_entry_is_judged_against_a_tenant_table(entry: HotPath) -> None:
    schema, _, table = entry.table.partition(".")
    assert table, f"{entry.name}: table must be schema-qualified"
    assert schema in TENANT_SCHEMAS, (
        f"{entry.name}: {schema} is not a tenant schema, so a Seq Scan on it is not 2.4's "
        "acceptance criterion failing"
    )


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.name)
def test_every_entry_is_a_read(entry: HotPath) -> None:
    """``EXPLAIN ANALYZE`` executes what it is given -- the whole reason
    ``_refuse_writes`` exists rather than a review convention."""
    _refuse_writes(entry)


def test_a_write_in_the_catalogue_is_refused() -> None:
    """The guard is proven able to fail, not merely to pass on today's list."""
    purge = HotPath(
        name="files.purge_space",
        kind="page",
        table="files.files",
        source="modules/files/adapters/sql_repository.py::SqlFileRepository.purge_space",
        backs="not a read",
        sql="DELETE FROM files.files WHERE workspace_id = :workspace_id RETURNING id",
    )
    with pytest.raises(SystemExit, match="EXECUTES"):
        _refuse_writes(purge)

    smuggled = HotPath(
        name="smuggled",
        kind="page",
        table="files.files",
        source="x::Y.z",
        backs="a SELECT with a write inside it",
        sql="SELECT * FROM files.files WHERE id IN (DELETE FROM files.files RETURNING id)",
    )
    with pytest.raises(SystemExit, match="EXECUTES"):
        _refuse_writes(smuggled)


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.name)
def test_every_placeholder_is_a_probe_or_the_page_size(entry: HotPath) -> None:
    """A ``:name`` the runner cannot bind raises at the database, one entry
    into a run that has already taken a dozen plans -- and a ``needs`` entry
    with no placeholder is a probe read for nothing."""
    bindable = set(entry.needs) | {"page"}
    used = set(_PLACEHOLDER.findall(entry.sql))
    assert used <= bindable, f"{entry.name}: unbindable placeholders {sorted(used - bindable)}"
    assert set(entry.needs) <= used, (
        f"{entry.name}: declares {sorted(set(entry.needs) - used)} in `needs` and never binds it"
    )


@pytest.mark.parametrize("entry", CATALOGUE, ids=lambda e: e.name)
def test_every_need_is_a_probe_the_tool_can_actually_read(entry: HotPath) -> None:
    fields = set(TenantProbes.__dataclass_fields__)
    assert set(entry.needs) <= fields, (
        f"{entry.name}: needs {sorted(set(entry.needs) - fields)}, which TenantProbes cannot read"
    )


def test_catalogue_names_are_unique() -> None:
    """``--only`` takes names, and a duplicate would silently run one twice."""
    names = [entry.name for entry in CATALOGUE]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# The plan reader
# ---------------------------------------------------------------------------


def _plan(root: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {"Plan": root, "Execution Time": 1.0, "Planning Time": 0.1, **top}


#: MEASURED, before `ix_chunks_ws_point`: `parent_texts_for_chunk_ids` on the
#: seed's largest tenant. A parallel sequential scan of the whole chunk table,
#: 333,586 rows per worker over three loops, to return nothing.
_SEQ_SCAN_ON_CHUNKS = _plan(
    {
        "Node Type": "Gather",
        "Actual Rows": 0,
        "Actual Loops": 1,
        "Shared Hit Blocks": 229,
        "Shared Read Blocks": 125161,
        "Plans": [
            {
                "Node Type": "Seq Scan",
                "Parallel Aware": True,
                "Schema": "knowledge",
                "Relation Name": "chunks",
                "Actual Rows": 4,
                "Actual Loops": 3,
                "Rows Removed by Filter": 333586,
            }
        ],
    },
    **{"Execution Time": 111.441, "Planning Time": 2.017},
)

#: MEASURED, after: an index scan on the same statement, 12 rows.
_INDEX_SCAN_ON_CHUNKS = _plan(
    {
        "Node Type": "Nested Loop",
        "Actual Rows": 0,
        "Actual Loops": 1,
        "Shared Hit Blocks": 39,
        "Shared Read Blocks": 12,
        "Plans": [
            {
                "Node Type": "Index Scan",
                "Schema": "knowledge",
                "Relation Name": "chunks",
                "Index Name": "ix_chunks_ws_point",
                "Actual Rows": 12,
                "Actual Loops": 1,
            }
        ],
    },
    **{"Execution Time": 0.142},
)

#: MEASURED, before `ix_doc_ws_file`: an INDEX scan that reads the tenant's
#: whole document set and filters -- 2.4's acceptance criterion would pass
#: this plan, which is why the tool does not judge on `Seq Scan` alone.
_AMPLIFIED_DOCUMENTS = _plan(
    {
        "Node Type": "Bitmap Heap Scan",
        "Schema": "knowledge",
        "Relation Name": "documents",
        "Actual Rows": 20,
        "Actual Loops": 1,
        "Rows Removed by Filter": 16992,
        "Shared Hit Blocks": 1,
        "Shared Read Blocks": 325,
        "Plans": [
            {
                "Node Type": "Bitmap Index Scan",
                "Index Name": "ix_doc_ws_status",
                "Actual Rows": 17012,
                "Actual Loops": 1,
            }
        ],
    },
    **{"Execution Time": 7.881},
)

#: MEASURED, before `ix_files_space_bytes`: `sum(size_bytes)` fetching every
#: one of a space's 8,506 rows from the heap to produce one number.
_UNCOVERED_AGGREGATE = _plan(
    {
        "Node Type": "Aggregate",
        "Actual Rows": 1,
        "Actual Loops": 1,
        "Shared Hit Blocks": 609,
        "Shared Read Blocks": 145,
        "Plans": [
            {
                "Node Type": "Bitmap Heap Scan",
                "Schema": "files",
                "Relation Name": "files",
                "Actual Rows": 8506,
                "Actual Loops": 1,
            }
        ],
    },
    **{"Execution Time": 17.542},
)

#: MEASURED, after: the same aggregate as an index-only scan. `Heap Fetches`
#: is not zero because the visibility map does not vouch for every page.
_COVERED_AGGREGATE = _plan(
    {
        "Node Type": "Aggregate",
        "Actual Rows": 1,
        "Actual Loops": 1,
        "Shared Hit Blocks": 3,
        "Shared Read Blocks": 64,
        "Plans": [
            {
                "Node Type": "Index Only Scan",
                "Schema": "files",
                "Relation Name": "files",
                "Index Name": "ix_files_space_bytes",
                "Actual Rows": 8506,
                "Actual Loops": 1,
                "Heap Fetches": 18,
            }
        ],
    },
    **{"Execution Time": 1.602},
)

#: MEASURED: `conversations.list_by_agent`, whose correlated `message_count`
#: subquery runs once per row of the page. Its rows are reported PER LOOP.
_CORRELATED_SUBQUERY = _plan(
    {
        "Node Type": "Limit",
        "Actual Rows": 21,
        "Actual Loops": 1,
        "Shared Hit Blocks": 757,
        "Shared Read Blocks": 79,
        "Plans": [
            {
                "Node Type": "Index Scan",
                "Schema": "conversations",
                "Relation Name": "conversations",
                "Index Name": "conversations_pkey",
                "Actual Rows": 21,
                "Actual Loops": 1,
                "Rows Removed by Filter": 687,
            },
            {
                "Node Type": "Aggregate",
                "Actual Rows": 1,
                "Actual Loops": 21,
                "Plans": [
                    {
                        "Node Type": "Bitmap Heap Scan",
                        "Schema": "conversations",
                        "Relation Name": "messages",
                        "Actual Rows": 20,
                        "Actual Loops": 21,
                    }
                ],
            },
        ],
    }
)

_PAGE = HotPath(
    name="probe.page",
    kind="page",
    table="knowledge.chunks",
    source="x::Y.z",
    backs="",
    sql="SELECT 1",
)
_AGGREGATE = HotPath(
    name="probe.aggregate",
    kind="aggregate",
    table="files.files",
    source="x::Y.z",
    backs="",
    sql="SELECT 1",
)
_POINT = HotPath(
    name="probe.point",
    kind="point",
    table="access.role_assignments",
    source="x::Y.z",
    backs="",
    sql="SELECT 1",
)


def test_rows_read_counts_rows_removed_by_filter() -> None:
    """The discarded rows ARE the finding: they are what an index would have
    skipped. A summary that counted only returned rows would call the
    17,012-row scan a 20-row query."""
    summary = summarise(_AMPLIFIED_DOCUMENTS)
    assert summary.rows_returned == 20
    assert summary.rows_read == 17012


def test_rows_read_multiplies_by_loops() -> None:
    """``EXPLAIN`` reports a node's rows PER LOOP. The N+1 shapes this step
    exists to find are exactly the ones that hide inside a loop count."""
    summary = summarise(_CORRELATED_SUBQUERY)
    # 21 + 687 from the outer scan, and 20 x 21 from the correlated subquery.
    assert summary.rows_read == 708 + 420
    assert summary.rows_returned == 21


def test_a_parallel_sequential_scan_on_a_tenant_table_is_found() -> None:
    """``Parallel Seq Scan`` is a different ``Node Type`` string from ``Seq
    Scan``, and the plan that made 2.4 necessary was the parallel one."""
    summary = summarise(_SEQ_SCAN_ON_CHUNKS)
    assert summary.seq_scans == ("knowledge.chunks",)
    # 333,586 per worker, three loops -- the whole table, not a third of it.
    assert summary.rows_read == 333_590 * 3


def test_a_sequential_scan_outside_a_tenant_schema_is_not_a_finding() -> None:
    """A scan of a catalogue or a temp relation is not what the criterion is
    about, and reporting it would train a reader to ignore the verdict."""
    summary = summarise(
        _plan(
            {
                "Node Type": "Seq Scan",
                "Schema": "pg_catalog",
                "Relation Name": "pg_class",
                "Actual Rows": 900,
                "Actual Loops": 1,
            }
        )
    )
    assert summary.seq_scans == ()


def test_a_big_sequential_scan_fails_the_acceptance_criterion() -> None:
    verdict, reason = judge(_PAGE, summarise(_SEQ_SCAN_ON_CHUNKS))
    assert verdict == "seq-scan"
    assert verdict in FAILING_VERDICTS
    assert "knowledge.chunks" in reason


def test_a_tiny_sequential_scan_is_the_planner_being_right() -> None:
    """A scan of 62 rows costs less than descending a tree to read them.
    Failing it would make the criterion unpassable on a CORRECT plan -- and
    passing it silently would hide that the table is under-populated, which is
    a fact about the corpus and the thing the reader needs told."""
    verdict, reason = judge(
        _PAGE,
        summarise(
            _plan(
                {
                    "Node Type": "Seq Scan",
                    "Schema": "knowledge",
                    "Relation Name": "parent_chunks",
                    "Actual Rows": 62,
                    "Actual Loops": 1,
                }
            )
        ),
    )
    assert verdict == "small-scan"
    assert verdict not in FAILING_VERDICTS
    assert "NOT proven at scale" in reason


def test_an_index_scan_that_reads_the_whole_tenant_is_still_a_finding() -> None:
    """The half of 2.4's criterion its own wording leaves out."""
    verdict, reason = judge(_PAGE, summarise(_AMPLIFIED_DOCUMENTS))
    assert verdict == "amplified"
    assert verdict in FAILING_VERDICTS
    assert f"allowance {PAGE_AMPLIFICATION_MAX}x" in reason


def test_the_fixed_index_scan_passes() -> None:
    verdict, _ = judge(_PAGE, summarise(_INDEX_SCAN_ON_CHUNKS))
    assert verdict == "ok"


def test_an_aggregate_is_judged_on_heap_access_and_not_on_ratio() -> None:
    """An aggregate returns one row by definition, so the page rule would flag
    every one of them forever."""
    uncovered, reason = judge(_AGGREGATE, summarise(_UNCOVERED_AGGREGATE))
    assert uncovered == "uncovered"
    assert uncovered in FAILING_VERDICTS
    assert "heap" in reason

    covered, _ = judge(_AGGREGATE, summarise(_COVERED_AGGREGATE))
    assert covered == "ok"


def test_the_same_uncovered_aggregate_would_pass_the_page_rule() -> None:
    """Stated as a test because it is the reason ``kind`` exists at all: the
    covered and uncovered plans read the SAME number of rows and return the
    same one row, so a ratio cannot tell them apart."""
    before, after = summarise(_UNCOVERED_AGGREGATE), summarise(_COVERED_AGGREGATE)
    assert before.rows_read == after.rows_read
    assert before.amplification == after.amplification
    assert before.heap_fraction > after.heap_fraction


def test_a_point_lookup_that_scans_is_a_finding() -> None:
    scanning = summarise(
        _plan(
            {
                "Node Type": "Bitmap Heap Scan",
                "Schema": "access",
                "Relation Name": "role_assignments",
                "Actual Rows": 3,
                "Actual Loops": 1,
                "Rows Removed by Filter": 40_000,
            }
        )
    )
    verdict, reason = judge(_POINT, scanning)
    assert verdict == "amplified"
    assert "not indexed" in reason


def test_nothing_is_judged_below_the_floor() -> None:
    """A query that read 60 rows to return one is not a capacity problem
    however bad the ratio looks."""
    small = summarise(
        _plan(
            {
                "Node Type": "Index Scan",
                "Schema": "files",
                "Relation Name": "files",
                "Actual Rows": 1,
                "Actual Loops": 1,
                "Rows Removed by Filter": AMPLIFICATION_FLOOR_ROWS - 2,
            }
        )
    )
    assert small.amplification > PAGE_AMPLIFICATION_MAX
    verdict, reason = judge(_PAGE, small)
    assert verdict == "ok"
    assert "floor" in reason


# ---------------------------------------------------------------------------
# Choosing the tenant
# ---------------------------------------------------------------------------


def test_the_workspace_comes_off_the_seed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "dev-2026-09-03.json"
    manifest.write_text(
        '{"largest_workspaces": [{"workspace_id": "019f3020-59d6-7ee5-a6df-913e44c5ecf0"}, '
        '{"workspace_id": "019f04d3-fad9-71f7-a599-558ecca12823"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr("app.ops.explain_hot_paths.MANIFEST_DIR", tmp_path)
    workspace_id, provenance = workspace_from_manifest(None)
    assert workspace_id == "019f3020-59d6-7ee5-a6df-913e44c5ecf0"
    assert "largest_workspaces[0]" in provenance


def test_no_manifest_says_which_two_things_would_fix_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The message has to name both remedies, because the reason a query
    cannot answer this is not obvious from the failure."""
    monkeypatch.setattr("app.ops.explain_hot_paths.MANIFEST_DIR", tmp_path)
    with pytest.raises(SystemExit) as raised:
        workspace_from_manifest(None)
    assert "--workspace-id" in str(raised.value)
    assert "load_seed" in str(raised.value)


def test_probes_report_what_they_could_not_read() -> None:
    """A missing probe skips its statements by NAME. Binding an invented id
    instead would measure an empty result set and print it as a fast query --
    0.1's condition (3), one table down."""
    probes = TenantProbes(
        workspace_id="019f3020-59d6-7ee5-a6df-913e44c5ecf0",
        space_id=None,
        conversation_id="019fc1ef-0b83-71e2-9580-de213df0190f",
        document_id=None,
        agent_key="researcher",
        user_id=None,
        file_name=None,
        file_ids=(),
        point_ids=("a8125322-74ca-50b3-8e1b-797e4a1c7e89",),
    )
    assert probes.missing(("workspace_id", "agent_key")) == []
    assert probes.missing(("space_id", "file_ids", "point_ids")) == ["space_id", "file_ids"]
    assert probes.binding("point_ids") == ("a8125322-74ca-50b3-8e1b-797e4a1c7e89",)
    assert probes.binding("file_ids") is None
