"""Live-Qdrant tests for the corpus resolution capacity step 4.5 added to
``QdrantVectorStore`` (``docs/capacity-plan.md`` §5, Wave 4).

**Why these cannot be unit tests.** Every behaviour here is a property of
Qdrant's own alias semantics, and each one was measured before it was relied
on: an alias may not take the name of an existing COLLECTION (409), an alias
name is absent from ``get_collections`` but present in ``get_aliases``,
``get_collection``/``upsert``/``delete``/``create_payload_index`` all follow
an alias to its target, and deleting a collection deletes the aliases over it.
A fake that agreed with those would only be agreeing with what someone
believed; a fake that disagreed would pass a suite and fail a deployment.

The three states ``_resolve`` distinguishes are the shape of this file: a
workspace born revisioned, a pre-4.5 corpus claimed by name, and a regime
whose corpus does not exist beside one another regime already owns. The
fourth test group is the width guard, which is the one failure 4.5 makes LOUD
rather than merely impossible.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from qdrant_client import AsyncQdrantClient

from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.ports.vector_store import VectorPoint
from app.infrastructure.vector.qdrant_store import (
    QdrantVectorStore,
    alias_target,
    switch_alias,
)

pytestmark = [pytest.mark.live_qdrant]

_DIM = 4
_REV_A = "aaaaaaaaaaaa"
_REV_B = "bbbbbbbbbbbb"


@pytest.fixture
async def corpus(qdrant_client: AsyncQdrantClient) -> AsyncIterator[str]:
    """A unique stable name whose every corpus and alias is swept afterwards.

    Named like a real one (``kn-<uuid>``) rather than ``aizzak-test-...``
    because the code under test parses and prefixes it -- a name that did not
    look like a workspace's would exercise the resolution without exercising
    the naming that feeds it.
    """
    name = f"kn-{new_uuid7()}"
    try:
        yield name
    finally:
        for revision in (_REV_A, _REV_B):
            await qdrant_client.delete_collection(f"{name}-{revision}")
        await qdrant_client.delete_collection(name)


def _point(point_id: str, value: float) -> VectorPoint:
    return VectorPoint(
        id=point_id,
        vector=[value, 0.0, 0.0, 0.0],
        payload={"workspace_id": "ws-1", "text": "content"},
    )


# --------------------------------------------------------------------------- #
# (1) a workspace born revisioned                                             #
# --------------------------------------------------------------------------- #
async def test_a_new_workspace_gets_a_revisioned_collection_and_no_legacy_name(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """Nothing ever creates the unrevisioned name again. A workspace that
    appeared after 4.5 has no adoption to do and no second name to keep
    coherent."""
    resolved = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)

    assert resolved == f"{corpus}-{_REV_A}"
    assert await qdrant_client.collection_exists(resolved)
    assert not await qdrant_client.collection_exists(corpus)
    assert await alias_target(qdrant_client, resolved) is None  # a collection, not an alias


async def test_resolution_is_idempotent_and_costs_no_second_collection(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    first = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)
    second = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)

    assert first == second
    names = {entry.name for entry in (await qdrant_client.get_collections()).collections}
    assert {name for name in names if name.startswith(corpus)} == {first}


# --------------------------------------------------------------------------- #
# (2) claiming a pre-4.5 corpus -- adoption, without moving a byte            #
# --------------------------------------------------------------------------- #
async def test_a_pre_45_corpus_is_claimed_by_name_and_keeps_its_points(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """The whole argument for an alias rather than a copy: the corpus does not
    move, so there is no window in which a search answers empty and no
    ``chunks.collection`` row stops resolving. The claim is one call and it is
    complete when it returns."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)  # the legacy shape: no revision
    await qdrant_store.upsert(corpus, [_point(new_uuid7(), 1.0)])

    resolved = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)

    assert resolved == f"{corpus}-{_REV_A}"
    # An ALIAS over the original collection -- not a copy of it.
    assert await alias_target(qdrant_client, resolved) == corpus
    assert (await qdrant_client.get_collection(resolved)).points_count == 1
    # And the row that names the unrevisioned collection still reaches it.
    assert (await qdrant_client.get_collection(corpus)).points_count == 1


async def test_a_claimed_corpus_is_written_and_read_through_its_new_name(
    qdrant_store: QdrantVectorStore, corpus: str
) -> None:
    """Reads and writes through the alias reach the collection behind it,
    which is what lets the claim be the whole of adoption."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)
    resolved = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)
    point_id = new_uuid7()

    await qdrant_store.upsert(resolved, [_point(point_id, 1.0)])
    hits = await qdrant_store.search(resolved, [1.0, 0.0, 0.0, 0.0], 5, {"workspace_id": "ws-1"})

    assert [hit.id for hit in hits] == [point_id]
    await qdrant_store.delete(resolved, [point_id])
    assert await qdrant_store.search(corpus, [1.0, 0.0, 0.0, 0.0], 5) == []


# --------------------------------------------------------------------------- #
# (3) a second regime NEVER takes the first one's corpus                      #
# --------------------------------------------------------------------------- #
async def test_a_second_regime_gets_its_own_empty_corpus_not_the_claimed_one(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """⚠️ The governing test of the whole step. Regime A has claimed the
    pre-4.5 corpus; regime B arrives (a model swap, or a deployment that
    changed the model and skipped the migration). If B were handed A's corpus,
    its vectors would join A's in one collection and every search over the
    result would rank one embedding space against another -- answers that look
    right and are random. B must get an empty corpus of its own instead."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)
    await qdrant_store.upsert(corpus, [_point(new_uuid7(), 1.0)])
    claimed = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)

    theirs = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_B)

    assert theirs == f"{corpus}-{_REV_B}"
    assert theirs != claimed
    # A real, separate, EMPTY collection -- not an alias onto A's points.
    assert await alias_target(qdrant_client, theirs) is None
    assert (await qdrant_client.get_collection(theirs)).points_count == 0
    assert (await qdrant_client.get_collection(claimed)).points_count == 1


async def test_a_second_regime_is_also_refused_the_corpus_a_build_already_made(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """The claim check has to read the COLLECTION listing as well as the alias
    table, and this is the half a listing-only check would get right while an
    alias-only check got wrong. Here A's corpus is a real collection (a
    ``build`` made it), so nothing appears in ``get_aliases`` at all."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)  # unclaimed pre-4.5 corpus
    built = await qdrant_store.ensure_hybrid_collection(f"{corpus}-{_REV_A}", _DIM)

    theirs = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_B)

    assert built == f"{corpus}-{_REV_A}"
    assert theirs == f"{corpus}-{_REV_B}"
    assert await alias_target(qdrant_client, theirs) is None


# --------------------------------------------------------------------------- #
# (4) the width guard -- the one mixing failure that can be made loud         #
# --------------------------------------------------------------------------- #
async def test_provisioning_refuses_a_corpus_built_at_another_width(
    qdrant_store: QdrantVectorStore, corpus: str
) -> None:
    """⚠️ Before 4.5 this was silent at provisioning time: ``ensure_*``
    returned as soon as the collection existed, so a deployment that changed
    ``dimensions`` under an unchanged model name went on writing 4-float
    vectors at an 8-wide collection and the first complaint came from Qdrant
    rejecting an ``upsert`` -- one FAILED DOCUMENT at a time, with a driver
    message, forever. Naming the fault once, where the corpus is chosen, is
    the difference."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)

    with pytest.raises(AppError) as excinfo:
        await qdrant_store.ensure_hybrid_collection(corpus, _DIM * 2, revision=_REV_A)

    assert excinfo.value.code == "common.internal"
    assert "embedding regime changed" in str(excinfo.value)


async def test_the_width_guard_also_covers_the_narrower_dense_only_port(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """``memory`` is deliberately NOT revisioned (its corpus is rebuildable
    from rows), so this guard is the only protection it has against a
    ``dimensions`` change -- which makes it the reason the check lives on the
    shared provisioning path rather than on the revisioned one."""
    name = f"mem-{new_uuid7()}"
    await qdrant_store.ensure_collection(name, _DIM)
    try:
        with pytest.raises(AppError):
            await qdrant_store.ensure_collection(name, _DIM * 2)
    finally:
        await qdrant_client.delete_collection(name)


# --------------------------------------------------------------------------- #
# (5) delete_everywhere -- deletion keeps meaning deletion with two corpora   #
# --------------------------------------------------------------------------- #
async def test_a_deleted_point_leaves_no_copy_in_the_other_regimes_corpus(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """⚠️ The shape a ``build`` produces: one point, the same deterministic
    id, in two corpora, while the ``chunks.collection`` row still names the
    first. Deleting only the recorded one leaves the copy answering searches
    -- and after the swap that copy is the LIVE corpus, so a file the user
    deleted comes back."""
    point_id = new_uuid7()
    live = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)
    shadow = await qdrant_store.ensure_hybrid_collection(f"{corpus}-{_REV_B}", _DIM)
    await qdrant_store.upsert(live, [_point(point_id, 1.0)])
    await qdrant_store.upsert(shadow, [_point(point_id, 1.0)])

    await qdrant_store.delete_everywhere(corpus, [point_id])

    assert (await qdrant_client.get_collection(live)).points_count == 0
    assert (await qdrant_client.get_collection(shadow)).points_count == 0


async def test_delete_everywhere_reaches_a_pre_45_collection_too(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """The unrevisioned name is itself one of the corpora to sweep: rows
    written before 4.5 name it, and a claim gives it a second name without
    moving it."""
    point_id = new_uuid7()
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)
    await qdrant_store.upsert(corpus, [_point(point_id, 1.0)])

    await qdrant_store.delete_everywhere(corpus, [point_id])

    assert (await qdrant_client.get_collection(corpus)).points_count == 0


async def test_delete_everywhere_with_no_ids_asks_the_store_for_nothing(
    qdrant_store: QdrantVectorStore, corpus: str
) -> None:
    """A workspace whose file had nothing indexed. The early return mirrors
    ``delete``'s and is what keeps "another tenant's id destroys nothing"
    assertable as no call at all."""
    await qdrant_store.delete_everywhere(corpus, [])  # no collection exists: must not raise


# --------------------------------------------------------------------------- #
# (6) switch_alias -- the repair verb, and the atomicity it is built on       #
# --------------------------------------------------------------------------- #
async def test_switching_a_name_onto_another_corpus_is_one_operation(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """Delete-then-create in ONE ``update_collection_aliases`` call, so the
    name never resolves to nothing in between. Sent as two calls there would
    be a window in which every read of that name answers empty -- which is
    exactly what an empty result does NOT look like from the outside."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)
    await qdrant_store.upsert(corpus, [_point(new_uuid7(), 1.0)])
    claimed = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)
    rebuilt = await qdrant_store.ensure_hybrid_collection(f"{corpus}-{_REV_B}", _DIM)
    await qdrant_store.upsert(rebuilt, [_point(new_uuid7(), 1.0), _point(new_uuid7(), 0.5)])

    await switch_alias(qdrant_client, claimed, to=rebuilt)

    assert await alias_target(qdrant_client, claimed) == rebuilt
    assert (await qdrant_client.get_collection(claimed)).points_count == 2


async def test_dropping_a_collection_takes_the_names_over_it_with_it(
    qdrant_store: QdrantVectorStore, qdrant_client: AsyncQdrantClient, corpus: str
) -> None:
    """Measured, and relied on by ``app.ops.embedding_migration drop``: no
    dangling alias survives a collection deletion, so the tool never has to
    sweep one."""
    await qdrant_store.ensure_hybrid_collection(corpus, _DIM)
    claimed = await qdrant_store.ensure_hybrid_collection(corpus, _DIM, revision=_REV_A)

    await qdrant_client.delete_collection(corpus)

    assert await alias_target(qdrant_client, claimed) is None
    assert not await qdrant_client.collection_exists(claimed)
