"""Unit tests for ``app.ops.embedding_migration`` -- the model-swap procedure
of capacity step 4.5.

Pure: a stub client stands in for Qdrant, because what is pinned here is the
tool's REASONING and not the server's behaviour. Which workspaces are adopted,
which corpus a build reads from and writes to, what makes a verification a
shortfall, and which revisions it refuses to drop -- those are decisions this
module makes, and each one of them is a decision that, made wrongly, destroys
vectors or hides a corpus rather than raising anything. Qdrant's own alias
semantics are pinned live, in
``tests/integration/test_qdrant_corpus_revisions.py``.

⚠️ The ``drop`` tests carry the most weight. It is the one verb in the whole
sequence that cannot be undone by re-running an earlier one: every other step
leaves both corpora standing, which is what makes rolling back a deploy a
complete rollback.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from qdrant_client import AsyncQdrantClient, models

from app.modules.knowledge.domain.collections import EmbeddingRegime
from app.ops.embedding_migration import (
    _dense_width,
    _sparse_of,
    adopt_all,
    drop_revision,
    inventory,
    verify_workspace,
)

_WS_A = "019f3020-59d6-7ee5-a6df-913e44c5ecf0"
_WS_B = "01a08642-c8c3-7012-8b80-5be4fd3bab41"
_REGIME = EmbeddingRegime(model="m", dimensions=4, max_input_tokens=512)
_REV = _REGIME.revision
_OTHER = EmbeddingRegime(model="n", dimensions=4, max_input_tokens=512).revision


class _StubClient:
    """Enough of ``AsyncQdrantClient`` for the tool's read/decide paths.

    ``collections`` and ``aliases`` are kept as SEPARATE inputs on purpose:
    Qdrant reports them separately (an alias name is absent from
    ``get_collections`` -- measured), and every inventory bug this file is
    guarding against is a bug that reads one and forgets the other.
    """

    def __init__(
        self,
        *,
        collections: list[str] | None = None,
        aliases: dict[str, str] | None = None,
        points: dict[str, int] | None = None,
        widths: dict[str, int] | None = None,
    ) -> None:
        self._collections = collections or []
        self._aliases = aliases or {}
        self._points = points or {}
        self._widths = widths or {}
        self.alias_ops: list[tuple[str, str, str]] = []
        self.dropped: list[str] = []

    async def get_collections(self) -> Any:
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self._collections]
        )

    async def get_aliases(self) -> Any:
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(alias_name=alias, collection_name=target)
                for alias, target in self._aliases.items()
            ]
        )

    async def get_collection(self, collection_name: str) -> Any:
        if collection_name in self._aliases:
            collection_name = self._aliases[collection_name]
        if collection_name not in self._collections:
            raise KeyError(collection_name)
        return SimpleNamespace(
            points_count=self._points.get(collection_name, 0),
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=models.VectorParams(
                        size=self._widths.get(collection_name, 4),
                        distance=models.Distance.COSINE,
                    )
                )
            ),
        )

    async def update_collection_aliases(self, change_aliases_operations: Any) -> bool:
        for operation in change_aliases_operations:
            if isinstance(operation, models.CreateAliasOperation):
                create = operation.create_alias
                self.alias_ops.append(("create", create.alias_name, create.collection_name))
                self._aliases[create.alias_name] = create.collection_name
            else:
                alias_name = operation.delete_alias.alias_name
                self.alias_ops.append(("delete", alias_name, ""))
                self._aliases.pop(alias_name, None)
        return True

    async def delete_collection(self, collection_name: str) -> bool:
        self.dropped.append(collection_name)
        return True


def _client(**kwargs: Any) -> AsyncQdrantClient:
    return cast(AsyncQdrantClient, _StubClient(**kwargs))


# --------------------------------------------------------------------------- #
# inventory                                                                   #
# --------------------------------------------------------------------------- #
async def test_the_inventory_reads_collections_and_aliases_as_one_picture() -> None:
    """⚠️ A workspace adopted but never re-indexed exists ONLY in the alias
    table -- ``get_collections`` does not list alias names. An inventory built
    from collections alone would call it unadopted, and ``adopt`` would then
    try to claim a corpus that already has an owner."""
    client = _client(
        collections=[f"kn-{_WS_A}", f"kn-{_WS_B}-{_REV}", "mem-something", "aizzak-test-leftover"],
        aliases={f"kn-{_WS_A}-{_REV}": f"kn-{_WS_A}"},
    )

    corpora = {entry.workspace_id: entry for entry in await inventory(client)}

    assert set(corpora) == {_WS_A, _WS_B}
    assert corpora[_WS_A].unrevisioned is True
    assert corpora[_WS_A].revisions == {_REV: "alias"}
    assert corpora[_WS_A].adopted is True
    assert corpora[_WS_B].unrevisioned is False
    assert corpora[_WS_B].revisions == {_REV: "collection"}


async def test_a_workspace_born_revisioned_needs_no_adoption() -> None:
    """``adopted`` is about whether a pre-4.5 corpus is still unclaimed, so a
    workspace that never had one is finished before it starts. Reporting it as
    pending would make ``status``'s exit code fire forever, and an exit code
    that always fires is one a release stops reading."""
    client = _client(collections=[f"kn-{_WS_B}-{_REV}"])

    (entry,) = await inventory(client)

    assert entry.adopted is True


async def test_collections_this_platform_did_not_create_are_not_workspaces() -> None:
    """The inventory is what ``drop`` acts on, and Qdrant holds whatever an
    operator has put in it."""
    client = _client(collections=["mem-019f3020-59d6-7ee5-a6df-913e44c5ecf0", "scratch"])

    assert await inventory(client) == []


# --------------------------------------------------------------------------- #
# adopt                                                                       #
# --------------------------------------------------------------------------- #
async def test_adopting_names_the_pre_45_corpus_and_copies_nothing() -> None:
    """One alias creation per workspace, and the corpus does not move. That is
    the whole of adoption: no rewrite of a million points, no window in which a
    search answers empty, and no ``chunks.collection`` row that stops
    resolving."""
    client = _client(collections=[f"kn-{_WS_A}"], points={f"kn-{_WS_A}": 1234})

    (result,) = await adopt_all(client, _REGIME)

    assert result["action"] == "adopted"
    assert result["alias"] == f"kn-{_WS_A}-{_REV}"
    assert result["collection"] == f"kn-{_WS_A}"
    assert result["points"] == 1234
    assert cast(_StubClient, client).alias_ops == [("create", f"kn-{_WS_A}-{_REV}", f"kn-{_WS_A}")]
    assert cast(_StubClient, client).dropped == []


async def test_a_dry_run_reports_the_scope_and_creates_nothing() -> None:
    client = _client(collections=[f"kn-{_WS_A}"])

    (result,) = await adopt_all(client, _REGIME, dry_run=True)

    assert result["action"] == "would adopt"
    assert cast(_StubClient, client).alias_ops == []


async def test_a_corpus_another_revision_already_owns_is_skipped_not_reclaimed() -> None:
    """⚠️ Claiming it would put two embedding spaces in one collection, which
    is the failure the whole step exists to make unreachable. The tool says so
    and moves on rather than refusing the whole pass: the other workspaces in
    the same run are still adoptable."""
    client = _client(
        collections=[f"kn-{_WS_A}"],
        aliases={f"kn-{_WS_A}-{_OTHER}": f"kn-{_WS_A}"},
    )

    (result,) = await adopt_all(client, _REGIME)

    assert result["action"] == "skipped"
    assert result["revisions"] == [_OTHER]
    assert cast(_StubClient, client).alias_ops == []


async def test_a_corpus_of_the_wrong_width_is_refused() -> None:
    """The one form of "this corpus is not what you think it is" that IS
    detectable from Qdrant. A model change with the same width is not, which
    is why adoption belongs to the release that ships 4.5 -- and why it moves
    no data, so a wrong claim is undone by deleting one alias."""
    client = _client(collections=[f"kn-{_WS_A}"], widths={f"kn-{_WS_A}": 768})

    (result,) = await adopt_all(client, _REGIME)

    assert result["action"] == "refused"
    assert "768-dimensional" in str(result["reason"])
    assert cast(_StubClient, client).alias_ops == []


async def test_adoption_narrows_to_one_workspace_when_asked() -> None:
    client = _client(collections=[f"kn-{_WS_A}", f"kn-{_WS_B}"])

    results = await adopt_all(client, _REGIME, workspace=_WS_B)

    assert [row["workspace_id"] for row in results] == [_WS_B]


# --------------------------------------------------------------------------- #
# verify                                                                      #
# --------------------------------------------------------------------------- #
async def test_a_complete_build_verifies() -> None:
    client = _client(
        collections=[f"kn-{_WS_A}-{_REV}", f"kn-{_WS_A}-{_OTHER}"],
        points={f"kn-{_WS_A}-{_REV}": 100, f"kn-{_WS_A}-{_OTHER}": 100},
    )

    result = await verify_workspace(
        client,
        workspace_id=_WS_A,
        source_revision=_REV,
        target=EmbeddingRegime(model="n", dimensions=4, max_input_tokens=512),
    )

    assert result["ok"] is True
    assert result["missing"] == 0


async def test_a_target_holding_more_than_its_source_is_not_a_shortfall() -> None:
    """The named gap, in its harmless direction: live indexing writes into the
    new corpus while the build runs, so it legitimately overtakes. Requiring
    equality would fail a migration for succeeding."""
    client = _client(
        collections=[f"kn-{_WS_A}-{_REV}", f"kn-{_WS_A}-{_OTHER}"],
        points={f"kn-{_WS_A}-{_REV}": 100, f"kn-{_WS_A}-{_OTHER}": 107},
    )

    result = await verify_workspace(
        client,
        workspace_id=_WS_A,
        source_revision=_REV,
        target=EmbeddingRegime(model="n", dimensions=4, max_input_tokens=512),
    )

    assert result["ok"] is True
    assert result["missing"] == 0


async def test_a_short_target_is_reported_with_the_number_that_is_missing() -> None:
    client = _client(
        collections=[f"kn-{_WS_A}-{_REV}", f"kn-{_WS_A}-{_OTHER}"],
        points={f"kn-{_WS_A}-{_REV}": 100, f"kn-{_WS_A}-{_OTHER}": 61},
    )

    result = await verify_workspace(
        client,
        workspace_id=_WS_A,
        source_revision=_REV,
        target=EmbeddingRegime(model="n", dimensions=4, max_input_tokens=512),
    )

    assert result["ok"] is False
    assert result["missing"] == 39


async def test_a_target_of_the_wrong_width_fails_even_at_the_right_count() -> None:
    """Counting alone would pass a corpus built at another dimension, and the
    deployment that trusted it would fail one upsert at a time afterwards."""
    client = _client(
        collections=[f"kn-{_WS_A}-{_REV}", f"kn-{_WS_A}-{_OTHER}"],
        points={f"kn-{_WS_A}-{_REV}": 10, f"kn-{_WS_A}-{_OTHER}": 10},
        widths={f"kn-{_WS_A}-{_OTHER}": 768},
    )

    result = await verify_workspace(
        client,
        workspace_id=_WS_A,
        source_revision=_REV,
        target=EmbeddingRegime(model="n", dimensions=4, max_input_tokens=512),
    )

    assert result["ok"] is False


async def test_a_target_that_was_never_built_is_a_shortfall_not_a_crash() -> None:
    """The state before a build has run at all. A tool that raised here would
    make "am I ready" unanswerable for the whole fleet because one workspace
    had not started."""
    client = _client(collections=[f"kn-{_WS_A}-{_REV}"], points={f"kn-{_WS_A}-{_REV}": 10})

    result = await verify_workspace(
        client,
        workspace_id=_WS_A,
        source_revision=_REV,
        target=EmbeddingRegime(model="n", dimensions=4, max_input_tokens=512),
    )

    assert result["ok"] is False
    assert result["target_points"] == 0


# --------------------------------------------------------------------------- #
# drop -- the irreversible verb                                               #
# --------------------------------------------------------------------------- #
async def test_dropping_a_built_corpus_deletes_the_collection() -> None:
    client = _client(collections=[f"kn-{_WS_A}-{_OTHER}"])

    dropped = await drop_revision(client, _OTHER)

    assert [row["kind"] for row in dropped] == ["collection"]
    assert cast(_StubClient, client).dropped == [f"kn-{_WS_A}-{_OTHER}"]


async def test_dropping_a_claimed_revision_deletes_only_the_name() -> None:
    """⚠️ The distinction that keeps this verb survivable. A revision that is
    an ALIAS is a name over the pre-4.5 collection, not a corpus of its own --
    deleting the collection behind it would destroy vectors that other names
    still resolve, and no earlier verb can bring them back."""
    client = _client(
        collections=[f"kn-{_WS_A}"],
        aliases={f"kn-{_WS_A}-{_OTHER}": f"kn-{_WS_A}"},
    )

    dropped = await drop_revision(client, _OTHER)

    assert [row["kind"] for row in dropped] == ["alias"]
    assert cast(_StubClient, client).dropped == []
    assert cast(_StubClient, client).alias_ops == [("delete", f"kn-{_WS_A}-{_OTHER}", "")]


async def test_dropping_a_revision_no_workspace_has_touches_nothing() -> None:
    client = _client(collections=[f"kn-{_WS_A}-{_REV}"])

    assert await drop_revision(client, _OTHER) == []
    assert cast(_StubClient, client).dropped == []


async def test_dropping_narrows_to_one_workspace_when_asked() -> None:
    client = _client(collections=[f"kn-{_WS_A}-{_OTHER}", f"kn-{_WS_B}-{_OTHER}"])

    dropped = await drop_revision(client, _OTHER, workspace=_WS_A)

    assert [row["workspace_id"] for row in dropped] == [_WS_A]
    assert cast(_StubClient, client).dropped == [f"kn-{_WS_A}-{_OTHER}"]


# --------------------------------------------------------------------------- #
# the point-level helpers a build rebuilds through                            #
# --------------------------------------------------------------------------- #
def _record(vector: object) -> models.Record:
    return models.Record(id="p1", payload={"text": "hello"}, vector=cast(Any, vector))


def test_the_sparse_leg_is_carried_across_verbatim() -> None:
    """BM25 term ids hash the chunk's own words, so a text's sparse vector is
    the same under every embedding model there will ever be. Recomputing it
    would be a slower way to the same numbers, and a place for the tokenizer to
    drift between two corpora meant to differ in exactly one thing."""
    sparse = _sparse_of(
        _record({"": [1.0, 0.0], "text": models.SparseVector(indices=[3, 9], values=[1.0, 2.0])})
    )

    assert sparse is not None
    assert (sparse.indices, sparse.values) == ([3, 9], [1.0, 2.0])


@pytest.mark.parametrize(
    "vector",
    [[1.0, 0.0], {"": [1.0, 0.0]}, None],
)
def test_a_point_with_no_sparse_leg_rebuilds_without_one(vector: object) -> None:
    """``memory``'s shape, and any dense-only point: the rebuild must not
    invent an empty sparse vector, which would index every such point under
    zero terms."""
    assert _sparse_of(_record(vector)) is None


def test_the_dense_width_is_read_from_both_shapes_qdrant_reports() -> None:
    """A named-vector map for a hybrid collection, a bare ``VectorParams`` for
    a dense-only one -- the same two shapes the adapter's read path resolves,
    and the number ``verify`` refuses a mismatched corpus on."""
    named = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors={"": models.VectorParams(size=384, distance=models.Distance.COSINE)}
            )
        )
    )
    bare = SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(
                vectors=models.VectorParams(size=768, distance=models.Distance.COSINE)
            )
        )
    )

    assert _dense_width(cast(models.CollectionInfo, named)) == 384
    assert _dense_width(cast(models.CollectionInfo, bare)) == 768
    assert (
        _dense_width(
            cast(
                models.CollectionInfo,
                SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=None))),
            )
        )
        is None
    )
