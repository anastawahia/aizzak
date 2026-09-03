"""Hermetic tests for ``app.ops.load_seed`` -- the corpus generator capacity
step 0.1's condition (3) requires.

**What these can prove, and what they deliberately leave to the live test.**
Everything here is arithmetic and derivation: that the allocation is exact,
that the identifiers are the shape the database and DD-02 expect, that the
Postgres rows and the Qdrant points describe the SAME chunks, and that the
declared numbers cannot drift away from the floor the k6 harness checks
against. None of it touches a database -- the one claim that needs one
("written through RLS, readable only by its own tenant") is
``tests/integration/test_load_seed_live.py``, because a seeder that respects
RLS and one that merely does not crash are indistinguishable until a policy
actually evaluates.

The arithmetic is worth testing precisely because it is boring: a corpus that
declares a million messages and holds 999,998 makes every later comparison
between two load runs quietly wrong, and nothing downstream would ever
notice.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.modules.knowledge.domain.collections import chunk_point_id
from app.modules.knowledge.domain.sparse import Bm25Params
from app.ops import load_seed
from app.ops.load_seed import (
    FLOOR,
    MESSAGES_PER_CONVERSATION,
    CorpusSize,
    TextPool,
    VectorFactory,
    allocate,
    build_plan,
    chunk_rows,
    conversation_rows,
    document_rows,
    export_block,
    file_rows,
    manifest_document,
    meets_floor,
    message_rows,
    vector_points,
    zipf_weights,
)

_ANCHOR = datetime(2026, 9, 3, tzinfo=UTC)
_BM25 = Bm25Params(k1=1.5, b=0.75, avg_len=32.0)
_CONFIG_JS = Path("deploy/load/lib/config.js")


def _plan(**overrides: object) -> load_seed.SeedPlan:
    kwargs: dict[str, object] = {
        "seed_id": "unit",
        "anchor": _ANCHOR,
        "target": CorpusSize(workspaces=6, messages=400, files=60, vectors=180),
        "skew": 1.0,
    }
    kwargs.update(overrides)
    return build_plan(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The floor, and the copy of it the harness reads                             #
# --------------------------------------------------------------------------- #


def test_the_floor_matches_the_one_the_k6_harness_enforces() -> None:
    """``FLOOR`` and ``lib/config.js``'s ``SEED_FLOOR`` are the same four
    numbers written twice, in two languages, and only one of them decides
    whether an archived run says ``"valid": true``. If they ever diverge the
    seeder would report a corpus as sufficient that the harness rejects --
    or, far worse, the reverse."""
    source = _CONFIG_JS.read_text(encoding="utf-8")
    match = re.search(r"const SEED_FLOOR = \{([^}]*)\}", source)
    assert match, f"{_CONFIG_JS} no longer declares SEED_FLOOR in the expected shape"
    declared = {
        key: int(value.replace("_", ""))
        for key, value in re.findall(r"(\w+):\s*([\d_]+)", match.group(1))
    }
    assert declared == {
        "messages": FLOOR.messages,
        "files": FLOOR.files,
        "vectors": FLOOR.vectors,
        "workspaces": FLOOR.workspaces,
    }


def test_the_plans_own_numbers_are_the_floor() -> None:
    """``docs/capacity-plan.md`` §0.1 condition (3), verbatim: مليون رسالة ·
    100 ألف ملف · مليون متّجه · 200 مساحة عمل."""
    assert CorpusSize(workspaces=200, messages=1_000_000, files=100_000, vectors=1_000_000) == FLOOR


def test_a_scaled_corpus_never_meets_the_floor() -> None:
    assert meets_floor(FLOOR)
    assert not meets_floor(FLOOR.scaled(0.5))
    # Floored at one of everything: a --scale small enough to round a count
    # to zero must still produce a runnable corpus, not an empty one.
    tiny = FLOOR.scaled(0.0000001)
    assert min(tiny.workspaces, tiny.messages, tiny.files, tiny.vectors) == 1


# --------------------------------------------------------------------------- #
# Allocation                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("total", [0, 1, 7, 199, 1_000_000])
@pytest.mark.parametrize("count", [1, 3, 200])
def test_allocation_is_exact(total: int, count: int) -> None:
    """Largest remainder, so the shares always sum to the total.

    ``round(total * weight)`` per share does not: the errors do not cancel,
    and the manifest would then declare a number the database does not
    hold."""
    shares = allocate(total, zipf_weights(count, 1.0))
    assert sum(shares) == total
    assert len(shares) == count
    assert all(share >= 0 for share in shares)


def test_allocation_is_descending_under_skew_and_flat_without_it() -> None:
    skewed = allocate(1_000_000, zipf_weights(200, 1.0))
    assert skewed == sorted(skewed, reverse=True)
    assert skewed[0] > 20 * skewed[-1], "skew=1 should span more than an order of magnitude"
    flat = allocate(1_000_000, zipf_weights(200, 0.0))
    assert max(flat) - min(flat) <= 1


def test_zipf_weights_are_normalised() -> None:
    for exponent in (0.0, 0.5, 1.0, 1.5):
        assert sum(zipf_weights(200, exponent)) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Identifiers                                                                 #
# --------------------------------------------------------------------------- #


def test_seeded_ids_are_valid_uuid7() -> None:
    """Version and variant bits, because these strings go into ``uuid``
    columns and are read back by code that assumes DD-02's shape."""
    at = datetime(2026, 6, 1, 12, tzinfo=UTC)
    value = load_seed._seeded_uuid7("s", _ANCHOR, "kind", 3, at)
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    # The 48-bit timestamp field is the row's own instant, not the clock's --
    # that is what makes the ids time-ordered without being non-deterministic.
    assert int(value.replace("-", "")[:12], 16) == int(at.timestamp() * 1000)


def test_seeded_ids_are_deterministic_and_time_ordered() -> None:
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 8, 1, tzinfo=UTC)
    first = load_seed._seeded_uuid7("s", _ANCHOR, "msg", 1, earlier)
    assert first == load_seed._seeded_uuid7("s", _ANCHOR, "msg", 1, earlier)
    assert first != load_seed._seeded_uuid7("s", _ANCHOR, "msg", 2, earlier)
    assert first != load_seed._seeded_uuid7("other", _ANCHOR, "msg", 1, earlier)
    # The property `uuid5` would lose, and the reason the layout is built by
    # hand: a later row sorts after an earlier one, so the primary-key index
    # fills at its right-hand edge exactly as production's does.
    assert first < load_seed._seeded_uuid7("s", _ANCHOR, "msg", 1, later)


def test_the_anchor_separates_two_corpora_of_the_same_seed_id() -> None:
    """Re-running tomorrow must not half-overwrite today's corpus."""
    today = _plan()
    tomorrow = _plan(anchor=datetime(2026, 9, 4, tzinfo=UTC))
    assert {w.workspace_id for w in today.workspaces}.isdisjoint(
        w.workspace_id for w in tomorrow.workspaces
    )


# --------------------------------------------------------------------------- #
# Planning                                                                    #
# --------------------------------------------------------------------------- #


def test_the_plan_sums_to_exactly_what_was_asked_for() -> None:
    plan = _plan()
    assert plan.actual == plan.target


def test_the_full_plan_meets_the_floor() -> None:
    plan = build_plan(seed_id="unit", anchor=_ANCHOR, target=FLOOR, skew=1.0)
    assert meets_floor(plan.actual)
    assert plan.actual.workspaces == FLOOR.workspaces


def test_a_workspace_with_no_files_gets_no_vectors() -> None:
    """A chunk's ``document_id`` names a document, and a document is the
    indexed form of a file. Drawing the vector allocation independently would
    plan chunks for documents that were never planned -- an FK violation on
    the very first batch."""
    plan = build_plan(
        seed_id="unit",
        anchor=_ANCHOR,
        target=CorpusSize(workspaces=50, messages=100, files=3, vectors=90),
        skew=2.0,
    )
    assert any(w.files == 0 for w in plan.workspaces), "this shape should starve small tenants"
    for workspace in plan.workspaces:
        if workspace.files == 0:
            assert workspace.vectors == 0


def test_included_workspaces_come_first_and_keep_their_identity() -> None:
    """The tenants behind real Firebase tokens take the largest shares, and
    their account rows are never rewritten (``pre_existing``)."""
    existing = ("11111111-1111-7111-8111-111111111111",)
    plan = _plan(include=existing)
    assert plan.workspaces[0].workspace_id == existing[0]
    assert plan.workspaces[0].pre_existing
    assert plan.workspaces[0].messages == max(w.messages for w in plan.workspaces)
    assert not any(w.pre_existing for w in plan.workspaces[1:])


def test_a_plan_is_reproducible() -> None:
    first, second = _plan(), _plan()
    assert [w.workspace_id for w in first.workspaces] == [w.workspace_id for w in second.workspaces]
    assert [w.space_ids for w in first.workspaces] == [w.space_ids for w in second.workspaces]


# --------------------------------------------------------------------------- #
# Generated rows                                                              #
# --------------------------------------------------------------------------- #


def test_messages_are_gap_free_and_one_based_within_a_thread() -> None:
    """``Conversation.append_message`` assigns ``seq`` starting at 1 with no
    gaps (INV-CV1), and ``UNIQUE(conversation_id, seq)`` enforces it. A
    0-based corpus would load and would be wrong."""
    plan = _plan()
    workspace = plan.workspaces[0]
    threads: dict[str, list[int]] = {}
    for row in message_rows(plan, workspace, TextPool("unit", size=8, bm25=_BM25)):
        threads.setdefault(str(row["conversation_id"]), []).append(int(row["seq"]))
    assert threads
    for sequence in threads.values():
        assert sequence == list(range(1, len(sequence) + 1))
        assert len(sequence) <= MESSAGES_PER_CONVERSATION


def test_message_and_thread_counts_match_the_plan() -> None:
    plan = _plan()
    pool = TextPool("unit", size=8, bm25=_BM25)
    for workspace in plan.workspaces:
        assert sum(1 for _ in message_rows(plan, workspace, pool)) == workspace.messages
        assert sum(1 for _ in conversation_rows(plan, workspace)) == workspace.conversations


def test_every_message_row_carries_its_tenant_and_parses_as_json() -> None:
    """``content`` reaches Postgres as a ``jsonb`` cast of this string, and
    ``workspace_id`` is what the RLS ``WITH CHECK`` compares -- a row with the
    wrong one is rejected, not silently misfiled."""
    plan = _plan()
    workspace = plan.workspaces[1]
    for row in message_rows(plan, workspace, TextPool("unit", size=8, bm25=_BM25)):
        assert row["workspace_id"] == workspace.workspace_id
        assert set(json.loads(str(row["content"]))) == {"text", "attachments"}
        assert row["role"] in {"user", "assistant"}


def test_file_storage_keys_are_unique_and_tenant_prefixed() -> None:
    """``files.storage_key`` is UNIQUE across the whole table, and
    ``app.ops.purge`` deletes objects by the ``<workspace_id>/`` prefix
    (INV-F1) -- so even with no bytes written the key must be the one storage
    would have used."""
    plan = _plan()
    keys: set[str] = set()
    for workspace in plan.workspaces:
        for row in file_rows(plan, workspace):
            key = str(row["storage_key"])
            assert key.startswith(f"{workspace.workspace_id}/")
            assert key not in keys
            keys.add(key)
    assert len(keys) == plan.actual.files


def test_document_chunk_counts_sum_to_the_planned_vectors() -> None:
    """``documents.chunk_count`` is what an operator reads to know a document
    is fully indexed; a corpus whose counters disagree with its own chunks
    would make every knowledge read look broken."""
    plan = _plan()
    pool = TextPool("unit", size=8, bm25=_BM25)
    for workspace in plan.workspaces:
        documents = list(document_rows(plan, workspace))
        assert len(documents) == workspace.documents
        assert sum(int(row["chunk_count"]) for row in documents) == workspace.vectors
        assert sum(1 for _ in chunk_rows(plan, workspace, pool)) == workspace.vectors


def test_chunk_rows_and_vector_points_describe_the_same_chunks() -> None:
    """The two halves of one corpus. They are computed independently from the
    seed (never passed between each other), so this is the only thing that
    holds them together -- and a Postgres row whose ``point_id`` names a
    vector Qdrant does not have is a retrieval that returns nothing with no
    error anywhere."""
    plan = _plan()
    pool = TextPool("unit", size=16, bm25=_BM25)
    factory = VectorFactory("unit", dimensions=8)
    for workspace in plan.workspaces:
        rows = {str(row["point_id"]) for row in chunk_rows(plan, workspace, pool)}
        points = {point.id for point in vector_points(plan, workspace, pool, factory)}
        assert rows == points


def test_point_ids_are_the_ones_the_knowledge_module_would_mint() -> None:
    """``chunk_point_id`` and not a fresh id, so re-indexing a seeded
    document upserts the point it already has instead of doubling it."""
    plan = _plan()
    workspace = plan.workspaces[0]
    pool = TextPool("unit", size=8, bm25=_BM25)
    for row in chunk_rows(plan, workspace, pool):
        assert row["point_id"] == chunk_point_id(str(row["document_id"]), int(row["seq"]))


def test_every_point_carries_the_payload_knowledge_writes() -> None:
    plan = _plan()
    workspace = plan.workspaces[0]
    pool = TextPool("unit", size=8, bm25=_BM25)
    factory = VectorFactory("unit", dimensions=8)
    points = list(vector_points(plan, workspace, pool, factory))
    assert points
    for point in points:
        assert set(point.payload) == {
            "workspace_id",
            "document_id",
            "chunk_id",
            "seq",
            "text",
            "kind",
            "space",
        }
        assert point.payload["workspace_id"] == workspace.workspace_id
        assert point.payload["space"] in workspace.space_ids
        assert point.sparse is not None
        assert len(point.sparse.indices) == len(point.sparse.values)
        assert point.sparse.indices, "a chunk with no sparse terms is invisible to hybrid search"


def test_every_insert_is_idempotent() -> None:
    """Every statement the tool issues ends ``ON CONFLICT DO NOTHING``.

    That single clause is what makes an interrupted run resumable -- and the
    corpus takes long enough that "run it again" has to be the recovery
    procedure, not "purge and start over"."""
    statements = [
        value
        for name, value in vars(load_seed).items()
        if name.startswith("_INSERT_") and isinstance(value, str)
    ]
    # workspaces · users · spaces · conversations · messages · files ·
    # documents · chunks -- every table the corpus touches.
    assert len(statements) == 8
    for statement in statements:
        assert statement.strip().endswith("ON CONFLICT DO NOTHING"), statement


# --------------------------------------------------------------------------- #
# Synthetic content                                                           #
# --------------------------------------------------------------------------- #


def test_the_text_pool_is_deterministic_and_carries_bm25_terms() -> None:
    first = TextPool("unit", size=32, bm25=_BM25)
    second = TextPool("unit", size=32, bm25=_BM25)
    assert first.text(5) == second.text(5)
    assert first.text(5) != first.text(6)
    assert first.terms(5).indices == second.terms(5).indices
    assert first.terms(5).indices, "a text with no terms would index an empty sparse vector"
    # Indices ascending and de-duplicated -- `SparseTerms`' own contract, and
    # what Qdrant expects on the wire.
    indices = first.terms(5).indices
    assert list(indices) == sorted(set(indices))


def test_the_pool_wraps_rather_than_raising() -> None:
    """Chunk index runs to a million; the pool holds a few thousand."""
    pool = TextPool("unit", size=4, bm25=_BM25)
    assert pool.text(0) == pool.text(4) == pool.text(400_000)


def test_vectors_are_deterministic_and_clustered() -> None:
    """Points that share a centroid are close; points that do not are not.

    Uniformly random high-dimensional vectors are near-orthogonal, and an
    HNSW graph over them degenerates -- the corpus would then measure a
    pathological index rather than a realistic one (module docstring)."""
    factory = VectorFactory("unit", dimensions=64)
    workspace = "11111111-1111-7111-8111-111111111111"
    assert factory.vector(workspace, 3) == factory.vector(workspace, 3)
    assert len(factory.vector(workspace, 3)) == 64

    def cosine(left: list[float], right: list[float]) -> float:
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        norm = (sum(a * a for a in left) ** 0.5) * (sum(b * b for b in right) ** 0.5)
        return dot / norm

    centroids = load_seed.CENTROIDS_PER_WORKSPACE
    same_cluster = cosine(factory.vector(workspace, 0), factory.vector(workspace, centroids))
    other_cluster = cosine(factory.vector(workspace, 0), factory.vector(workspace, 1))
    assert same_cluster > 0.6
    assert same_cluster > other_cluster + 0.3


def test_two_workspaces_do_not_share_a_vector_space() -> None:
    factory = VectorFactory("unit", dimensions=32)
    left = factory.vector("11111111-1111-7111-8111-111111111111", 0)
    right = factory.vector("22222222-2222-7222-8222-222222222222", 0)
    assert left != right


# --------------------------------------------------------------------------- #
# Manifest                                                                    #
# --------------------------------------------------------------------------- #


def test_the_manifest_declares_what_was_written_not_what_was_asked() -> None:
    plan = _plan()
    document = manifest_document(plan, wrote=["postgres", "qdrant"])
    assert document["size"] == {
        "workspaces": plan.actual.workspaces,
        "messages": plan.actual.messages,
        "files": plan.actual.files,
        "vectors": plan.actual.vectors,
    }
    assert document["meets_floor"] is False  # this plan is far below it


def test_a_half_written_corpus_never_claims_to_meet_the_floor() -> None:
    """``--only postgres`` leaves Qdrant empty. A manifest that still said
    ``meets_floor`` would turn condition (3) into a rubber stamp: the k6 run
    would archive ``"valid": true`` for a corpus with no vectors at all."""
    plan = build_plan(seed_id="unit", anchor=_ANCHOR, target=FLOOR, skew=1.0)
    assert manifest_document(plan, wrote=["postgres", "qdrant"])["meets_floor"] is True
    assert manifest_document(plan, wrote=["postgres"])["meets_floor"] is False


def test_the_export_block_is_what_run_sh_reads() -> None:
    plan = _plan()
    document = manifest_document(plan, wrote=["postgres", "qdrant"])
    exported = dict(
        line.removeprefix("export ").split("=", 1) for line in export_block(document).splitlines()
    )
    assert exported == {
        "LOAD_SEED_ID": "unit",
        "LOAD_SEED_MESSAGES": str(plan.actual.messages),
        "LOAD_SEED_FILES": str(plan.actual.files),
        "LOAD_SEED_VECTORS": str(plan.actual.vectors),
        "LOAD_SEED_WORKSPACES": str(plan.actual.workspaces),
    }


def test_the_manifest_names_the_tenants_a_token_pool_should_use() -> None:
    """A load run authenticates as specific workspaces; if those are the
    small ones, the corpus's bulk is invisible to it."""
    plan = _plan()
    document = manifest_document(plan, wrote=["postgres", "qdrant"])
    largest = document["largest_workspaces"]
    assert largest[0]["workspace_id"] == plan.workspaces[0].workspace_id
    assert largest[0]["space_ids"] == list(plan.workspaces[0].space_ids)
    assert [entry["messages"] for entry in largest] == sorted(
        (entry["messages"] for entry in largest), reverse=True
    )
