"""Changing the embedding model without an outage -- capacity step 4.5
(``docs/capacity-plan.md`` §5, Wave 4).

**What goes wrong without this, in one sentence.** ``docker-compose.yml``'s
own comment says a change to the model or to ``EMB_MAX_SEQ_LEN`` invalidates
every vector already indexed; what it does not say is that nothing FAILS when
it happens. Cosine similarity between two embedding spaces is a real number,
retrieval ranks by it, and the answers come back looking exactly like answers
-- "نتائجُ تبدو صحيحةً وهي عشوائيّة", the plan's own words, and the reason
this is a procedure rather than an incident.

**The mechanism, and where the plan's own recipe had to change.** §5 asks for
an explicit version in the collection name, a new collection built in
parallel, an ATOMIC SWITCHOVER once indexing completes, and then a delete of
the old one. Three of those four survived contact. The switchover did not,
and the step that killed it is ``7.2``: deploys here are ROLLING, so there is
no instant at which the fleet changes model. An alias flipped before the
deploy hands old-model processes a new-model corpus; flipped after, it hands
new-model processes an old-model corpus; flipped during, it does both at once.
Every one of those is the failure above, lasting for as long as the deploy.

So the pointer is not an alias -- it is the REGIME ITSELF. Every process
reads and writes ``kn-<workspace_id>-<revision>``, where ``revision``
fingerprints ``(model, dimensions, max_input_tokens)`` (``knowledge.domain.
collections.EmbeddingRegime``). A process can only ever reach the vectors its
own model produced, which makes mixing unreachable rather than merely
unlikely, and it makes a rolling deploy two coherent fleets instead of one
corrupted corpus. The switchover is the deploy, and it is atomic where it has
to be: inside one process.

The whole swap, then::

    1. adopt                     # once, ever: name the pre-4.5 corpora
    2. build --model NEW ...     # fill the new corpus beside the live one
    3. verify --model NEW ...    # refuse to proceed on a shortfall
    4. deploy the new model      # rolling; each replica flips itself
    5. build/verify once more    # sweep documents indexed during (2)-(4)
    6. drop --revision OLD       # after the soak, and only then

**Rollback is step 4 in reverse**, and it needs nothing from this tool: the
previous regime's corpus is still standing until step 6, so redeploying the
old image is a complete rollback -- which is exactly the acceptance criterion's
"ما دام صندوقُه قائماً". Step 6 is therefore the one irreversible act in the
sequence and the only one that asks for ``--yes``.

**``adopt`` copies nothing.** A workspace indexed before 4.5 has its corpus at
the unrevisioned ``kn-<workspace_id>``, and Qdrant cannot rename a collection
-- but it can NAME one. So adoption creates ``kn-<workspace_id>-<revision>``
as an ALIAS over the existing collection: one atomic call per workspace,
nothing moved, no window in which a search answers empty, and every
``chunks.collection`` row ever written still resolves to a live collection.
The first alternative considered here -- copy the corpus out, delete the
collection, take its name -- costs a full rewrite of every point AND a window
of empty results, to arrive at the same place.

**``build`` never reads Postgres, an object store, or a parser** (the
``app.ops.payload_indexes`` precedent, for a stronger reason). Everything a
point needs to be rebuilt is already in the point: ``payload["text"]`` is the
chunk's text, the payload carries its citation keys, and the SPARSE leg is
copied verbatim because BM25 term ids are a function of the text and owe the
embedding model nothing. Only the dense vector is recomputed. Re-running the
indexing pipeline instead would re-parse every file to arrive at chunk texts
that are already stored, and would change chunk boundaries under a corpus
whose whole purpose is to be the same corpus with different vectors.

Point ids are preserved, which is what makes ``build`` **resumable and
idempotent**: they are derived from ``(document_id, seq)``, so re-running
after a crash upserts the same points, and ``--resume`` (the default) asks the
target which ids it already holds and pays for the embeddings of the rest.

⚠️ **What is NOT bought, written down rather than left to be discovered.**
A document indexed between the start of a ``build`` and the last replica's
restart lands only in the OLD regime's corpus, and is invisible to the new one
until a later ``build`` sweeps it -- which is why step 5 exists and why it must
run AFTER the deploy, when the old corpus has stopped growing. Nothing is
lost: the chunks, the rows and the file are all still there, and the sweep is
a re-embed of text that never moved.

⚠️ **And ``mem-`` corpora are deliberately untouched.** Memory's vectors are
derived from ``MemoryItem.content``, a row this platform owns, and
``vector_ref`` is nullable -- so a regime change there is repaired by clearing
the refs and letting ``IndexMemoryItem`` rebuild in place, with no parse, no
second collection, and no coherence to maintain while it happens. Knowledge
had none of those, which is why it is the corpus that needed a revision. What
memory does get from 4.5 is the adapter's width guard: a ``dimensions`` change
now fails at provisioning instead of one rejected upsert at a time.

Usage::

    python -m app.ops.embedding_migration status [--json]
    python -m app.ops.embedding_migration adopt  [--workspace ID] [--dry-run]
    python -m app.ops.embedding_migration build  --model M --dimensions D
                                                 --max-input-tokens T --url URL
                                                 [--workspace ID] [--batch N] [--no-resume]
    python -m app.ops.embedding_migration verify --model M --dimensions D
                                                 --max-input-tokens T [--workspace ID]
    python -m app.ops.embedding_migration drop   --revision REV [--workspace ID] --yes

``--json`` on the reporting verbs, because these numbers are archived beside
the plan and re-read months later (``deploy/load/results/`` precedent).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.framework.ports.vector_store import SparseVector, VectorPoint
from app.framework.settings.settings import EmbeddingServiceSettings
from app.infrastructure.ai_providers.embedding.external_embedding import (
    ExternalEmbeddingProvider,
    create_embedding_http_client,
)
from app.infrastructure.config.env_settings import load_settings
from app.infrastructure.vector.qdrant_store import QdrantVectorStore, create_qdrant_client
from app.modules.knowledge.domain.collections import (
    EmbeddingRegime,
    knowledge_collection,
    knowledge_collection_revision,
    split_knowledge_collection,
)

_logger = logging.getLogger(__name__)

# How many points one scroll page carries, and one embed+upsert unit. 256 is a
# compromise between two measured costs from 4.4: a Qdrant write of 500 points
# at a million-point collection took seconds (`_TIMEOUT_S` is 5), and the
# embedding fleet's own throughput peaks well above one text per call (4.3).
# Halving the write batch keeps it far from the adapter's timeout; the
# embedding side batches internally on `EmbeddingServiceSettings.batch`.
_BATCH = 256

# The payload key `IndexDocument._payload` writes the chunk's text under. The
# ONE field `build` needs from a point that it cannot recompute -- everything
# else is either copied wholesale (the rest of the payload, the sparse leg) or
# derived from this (the dense vector).
_TEXT_KEY = "text"

# `ExternalEmbeddingProvider.embed` takes an api key for the port's sake and
# the local service authenticates none -- the same empty string the worker
# bootstrap resolves for it.
_NO_API_KEY = ""


@dataclass(frozen=True, slots=True)
class WorkspaceCorpora:
    """Every corpus one workspace has, and how each of them is named.

    ``unrevisioned`` is the pre-4.5 collection (``kn-<workspace_id>``), which
    survives adoption untouched -- it merely gains a name. ``revisions`` maps
    a revision to whether it is a real collection (a ``build`` made it) or an
    alias (an ``adopt`` named the unrevisioned corpus with it); the two behave
    identically for reads and writes, and the difference matters only to
    ``drop``, which must not delete a collection two names point at.
    """

    workspace_id: str
    unrevisioned: bool
    revisions: dict[str, str] = field(default_factory=dict)

    @property
    def adopted(self) -> bool:
        """Has the pre-4.5 corpus been claimed by some regime?

        ``True`` when there is no pre-4.5 corpus at all: a workspace born
        revisioned has nothing to adopt, and reporting it as pending would
        make ``status``'s exit code fire forever.
        """
        return not self.unrevisioned or any(kind == "alias" for kind in self.revisions.values())


async def inventory(client: AsyncQdrantClient) -> list[WorkspaceCorpora]:
    """Every knowledge corpus Qdrant holds, grouped by workspace.

    Both listings are needed and neither is redundant: ``get_collections``
    reports collections and NOT alias names (measured against 1.18), while
    ``get_aliases`` reports only the aliases. A workspace adopted but never
    re-indexed appears in the second alone, and it is precisely the workspace
    an inventory built from the first would call unadopted.

    Qdrant is the only authority asked. A workspace whose rows are long gone
    still answers searches until someone drops its collection, and a workspace
    with no corpus has nothing for this tool to do -- so the question is
    "which corpora exist", and Postgres cannot answer it. That is also what
    keeps this process free of a DSN, a role and an RLS question
    (``app.ops.payload_indexes``' own reason).
    """
    found: dict[str, WorkspaceCorpora] = {}

    def entry(workspace_id: str) -> WorkspaceCorpora:
        existing = found.get(workspace_id)
        if existing is None:
            existing = WorkspaceCorpora(workspace_id=workspace_id, unrevisioned=False)
            found[workspace_id] = existing
        return existing

    listing = await client.get_collections()
    for collection in listing.collections:
        parsed = split_knowledge_collection(collection.name)
        if parsed is None:
            continue
        workspace_id, revision = parsed
        current = entry(workspace_id)
        if revision is None:
            found[workspace_id] = WorkspaceCorpora(
                workspace_id=workspace_id, unrevisioned=True, revisions=current.revisions
            )
        else:
            current.revisions[revision] = "collection"

    table = await client.get_aliases()
    for alias in table.aliases:
        parsed = split_knowledge_collection(alias.alias_name)
        if parsed is None:
            continue
        workspace_id, revision = parsed
        if revision is None:
            # An alias named exactly `kn-<workspace_id>` cannot exist -- Qdrant
            # refuses an alias colliding with a collection name and this tool
            # never creates one -- but reading it as a revision would be worse
            # than ignoring it, so it is ignored.
            continue
        entry(workspace_id).revisions[revision] = "alias"

    return sorted(found.values(), key=lambda corpora: corpora.workspace_id)


def deployment_regime(settings_embedding: EmbeddingServiceSettings) -> EmbeddingRegime:
    """The regime THIS deployment runs, read off the same settings object the
    indexer and the searcher read.

    Not a flag, deliberately: an operator who could type a regime here could
    type one the fleet is not running, and every verb below is about the
    relationship between the fleet and the corpora. The regime a ``build``
    targets IS a flag, because that one is by definition not this fleet's yet.
    """
    return EmbeddingRegime(
        model=settings_embedding.model,
        dimensions=settings_embedding.dimensions,
        max_input_tokens=settings_embedding.embedding_max_input_tokens,
    )


async def adopt_all(
    client: AsyncQdrantClient,
    regime: EmbeddingRegime,
    *,
    workspace: str | None = None,
    dry_run: bool = False,
) -> list[dict[str, object]]:
    """Claim every unclaimed pre-4.5 corpus for ``regime``, by alias.

    ⚠️ **The assumption this verb makes, stated where it is made:** that the
    unrevisioned corpus holds vectors from the regime the fleet is running
    now. That is true exactly once -- before any model has been swapped --
    which is why adoption belongs to the release that ships 4.5 and not to a
    later one, and why the width check below refuses the one form of the
    mistake that is detectable. A corpus whose model changed with no migration
    is NOT detectable from Qdrant, and no amount of care here would make it so;
    what makes it survivable is that adoption moves no data, so an adoption
    made under a wrong assumption is undone by deleting one alias.

    A workspace that already has ANY revision is skipped rather than
    re-claimed: another regime owns that corpus, and taking it would be the
    mixing this step exists to prevent.
    """
    results: list[dict[str, object]] = []
    for corpora in await inventory(client):
        if workspace is not None and corpora.workspace_id != workspace:
            continue
        if not corpora.unrevisioned:
            continue
        alias = knowledge_collection_revision(corpora.workspace_id, regime.revision)
        if corpora.revisions:
            results.append(
                {
                    "workspace_id": corpora.workspace_id,
                    "action": "skipped",
                    "reason": "another revision already owns this corpus",
                    "revisions": sorted(corpora.revisions),
                }
            )
            continue
        source = knowledge_collection(corpora.workspace_id)
        info = await client.get_collection(source)
        width = _dense_width(info)
        if width is not None and width != regime.dimensions:
            results.append(
                {
                    "workspace_id": corpora.workspace_id,
                    "action": "refused",
                    "reason": f"corpus is {width}-dimensional, this deployment embeds "
                    f"at {regime.dimensions}",
                }
            )
            continue
        if not dry_run:
            await client.update_collection_aliases(
                change_aliases_operations=[
                    models.CreateAliasOperation(
                        create_alias=models.CreateAlias(collection_name=source, alias_name=alias)
                    )
                ]
            )
        results.append(
            {
                "workspace_id": corpora.workspace_id,
                "action": "would adopt" if dry_run else "adopted",
                "alias": alias,
                "collection": source,
                "points": info.points_count,
            }
        )
    return results


@dataclass(frozen=True, slots=True)
class BuildResult:
    """One workspace's ``build``, as it actually went."""

    workspace_id: str
    source: str
    target: str
    source_points: int
    copied: int
    skipped: int
    seconds: float


async def build_workspace(
    client: AsyncQdrantClient,
    store: QdrantVectorStore,
    embeddings: ExternalEmbeddingProvider,
    *,
    workspace_id: str,
    source_revision: str,
    target: EmbeddingRegime,
    batch: int = _BATCH,
    resume: bool = True,
) -> BuildResult:
    """Fill ``target``'s corpus for one workspace from the corpus
    ``source_revision`` owns, re-embedding every point's stored text.

    The source is addressed by REVISION, never by "the biggest collection" or
    "the unrevisioned one": the corpus being copied has to be the one the
    fleet is serving, and only its revision names that unambiguously once two
    exist.

    The target is provisioned through the adapter with ``revision=None`` --
    i.e. by its exact name -- so it is created with the sparse vector and the
    three payload indexes every hybrid collection gets, and so this tool never
    accidentally resolves a corpus rather than naming one.
    """
    started = time.perf_counter()
    source = knowledge_collection_revision(workspace_id, source_revision)
    target_name = knowledge_collection_revision(workspace_id, target.revision)
    await store.ensure_hybrid_collection(target_name, target.dimensions, distance="cosine")

    source_points = (await client.get_collection(source)).points_count or 0
    copied = 0
    skipped = 0
    # `scroll`'s cursor is an OPAQUE token: handed straight back to
    # `scroll` and never inspected. Its declared union reaches into the
    # driver's protobuf types, which this module has no business naming
    # and `object` is not assignable to -- so `Any`, which is what "pass
    # this along untouched" honestly is.
    offset: Any = None
    while True:
        page, offset = await client.scroll(
            collection_name=source,
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if not page:
            break
        pending = page
        if resume:
            # One `retrieve` per page rather than one scroll of the whole
            # target: the answer needed is "which of THESE 256 ids are
            # already there", and asking it of the target directly costs a
            # single small round trip whatever the corpus size. Embeddings
            # are what a resume exists to avoid paying for twice, and they
            # are two orders of magnitude more expensive than this call.
            have = {
                str(point.id)
                for point in await client.retrieve(
                    collection_name=target_name,
                    ids=[point.id for point in page],
                    with_payload=False,
                    with_vectors=False,
                )
            }
            pending = [point for point in page if str(point.id) not in have]
            skipped += len(page) - len(pending)
        if pending:
            copied += await _rebuild_points(store, embeddings, target, target_name, pending)
        if offset is None:
            break

    return BuildResult(
        workspace_id=workspace_id,
        source=source,
        target=target_name,
        source_points=source_points,
        copied=copied,
        skipped=skipped,
        seconds=round(time.perf_counter() - started, 3),
    )


async def _rebuild_points(
    store: QdrantVectorStore,
    embeddings: ExternalEmbeddingProvider,
    target: EmbeddingRegime,
    target_name: str,
    page: Sequence[models.Record],
) -> int:
    """Re-embed one page and upsert it into ``target_name``.

    A point with no ``text`` payload cannot be rebuilt and is SKIPPED rather
    than copied with its old vector: carrying the old vector forward would put
    the previous model's arithmetic into the new corpus, which is the mixing
    this whole step forbids -- one point at a time instead of one collection
    at a time, and invisible instead of loud. Such a point does not exist in
    anything ``IndexDocument`` wrote (``_payload`` always writes ``text``), so
    reaching this is a hand-made point or a future producer, and losing it
    from the new corpus is the safe half of the choice.
    """
    rebuildable = [point for point in page if isinstance((point.payload or {}).get(_TEXT_KEY), str)]
    if not rebuildable:
        return 0
    texts = [str((point.payload or {})[_TEXT_KEY]) for point in rebuildable]
    result = await embeddings.embed(texts, target.model, _NO_API_KEY)
    points = [
        VectorPoint(
            id=str(point.id),
            vector=vector,
            payload=dict(point.payload or {}),
            sparse=_sparse_of(point),
        )
        for point, vector in zip(rebuildable, result.vectors, strict=True)
    ]
    await store.upsert(target_name, points)
    return len(points)


def _sparse_of(point: models.Record) -> SparseVector | None:
    """The point's BM25 leg, carried across VERBATIM.

    Not recomputed, and that is a statement about what a model swap is:
    ``build_document_terms`` hashes the chunk's own words, so the sparse
    vector of a text is the same under every embedding model there will ever
    be. Recomputing it would be a slower way to get the same numbers and a
    place for the tokenizer to drift between two corpora that are supposed to
    differ in exactly one thing.
    """
    vectors = point.vector
    if not isinstance(vectors, dict):
        return None
    sparse = vectors.get("text")
    if not isinstance(sparse, models.SparseVector):
        return None
    return SparseVector(indices=list(sparse.indices), values=list(sparse.values))


def _dense_width(info: models.CollectionInfo) -> int | None:
    """A collection's dense vector width, in the two shapes Qdrant reports it
    (the adapter's ``_dense_params`` argument, applied here without importing
    a private helper)."""
    vectors = info.config.params.vectors
    if isinstance(vectors, models.VectorParams):
        return int(vectors.size)
    if isinstance(vectors, dict):
        params = vectors.get("")
        if isinstance(params, models.VectorParams):
            return int(params.size)
    return None


async def verify_workspace(
    client: AsyncQdrantClient,
    *,
    workspace_id: str,
    source_revision: str,
    target: EmbeddingRegime,
) -> dict[str, object]:
    """Compare a built corpus against the one it was built from.

    Counts and widths, and nothing cleverer. A sample search would compare two
    models' opinions of the same question, which is not a thing that can pass
    or fail -- the new model is SUPPOSED to rank differently, that is why it
    was swapped in. What can be checked is that nothing was dropped on the
    floor, and a count is exactly that check: point ids are preserved, so
    equal counts over preserved ids means every chunk made the crossing.
    """
    source = knowledge_collection_revision(workspace_id, source_revision)
    target_name = knowledge_collection_revision(workspace_id, target.revision)
    source_info = await client.get_collection(source)
    try:
        target_info = await client.get_collection(target_name)
    except Exception:  # absent target is a REPORTED shortfall, not a crash
        return {
            "workspace_id": workspace_id,
            "ok": False,
            "reason": "target corpus does not exist",
            "source_points": source_info.points_count,
            "target_points": 0,
        }
    source_points = source_info.points_count or 0
    target_points = target_info.points_count or 0
    width = _dense_width(target_info)
    ok = target_points >= source_points and width == target.dimensions
    return {
        "workspace_id": workspace_id,
        "ok": ok,
        "source": source,
        "target": target_name,
        "source_points": source_points,
        "target_points": target_points,
        "target_dimensions": width,
        # `>=`, not `==`: the target legitimately holds MORE than the source
        # once live indexing has written into it (the module docstring's
        # named gap, in its harmless direction). Fewer is the shortfall.
        "missing": max(source_points - target_points, 0),
    }


async def drop_revision(
    client: AsyncQdrantClient,
    revision: str,
    *,
    workspace: str | None = None,
) -> list[dict[str, object]]:
    """Delete one revision's corpus, everywhere it exists.

    Two shapes, and they are not the same act. A revision that is a
    COLLECTION is that corpus, and dropping it destroys the vectors. A
    revision that is an ALIAS is a NAME over the pre-4.5 collection, and
    dropping it must delete the alias only -- deleting the collection behind
    it would take a corpus that other names may still resolve, and it is the
    one form of this command that cannot be undone by re-running an earlier
    verb.

    Measured against 1.18: deleting a collection deletes the aliases pointing
    at it, so no dangling name is left behind by the collection branch.
    """
    dropped: list[dict[str, object]] = []
    for corpora in await inventory(client):
        if workspace is not None and corpora.workspace_id != workspace:
            continue
        kind = corpora.revisions.get(revision)
        if kind is None:
            continue
        name = knowledge_collection_revision(corpora.workspace_id, revision)
        if kind == "alias":
            await client.update_collection_aliases(
                change_aliases_operations=[
                    models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=name))
                ]
            )
        else:
            await client.delete_collection(name)
        dropped.append({"workspace_id": corpora.workspace_id, "name": name, "kind": kind})
    return dropped


def _target_regime(args: argparse.Namespace) -> EmbeddingRegime:
    return EmbeddingRegime(
        model=args.model,
        dimensions=args.dimensions,
        max_input_tokens=args.max_input_tokens,
    )


def _emit(payload: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run_status(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = create_qdrant_client(settings.qdrant)
    try:
        regime = deployment_regime(settings.embedding_service)
        corpora = await inventory(client)
        rows = [
            {
                "workspace_id": entry.workspace_id,
                "unrevisioned": entry.unrevisioned,
                "adopted": entry.adopted,
                "revisions": dict(sorted(entry.revisions.items())),
                "serves_this_deployment": regime.revision in entry.revisions,
            }
            for entry in corpora
        ]
    finally:
        await client.close()

    pending = [row["workspace_id"] for row in rows if not row["adopted"]]
    dark = [row["workspace_id"] for row in rows if not row["serves_this_deployment"]]
    _emit(
        {
            "deployment": {
                "model": settings.embedding_service.model,
                "dimensions": settings.embedding_service.dimensions,
                "max_input_tokens": settings.embedding_service.embedding_max_input_tokens,
                "revision": regime.revision,
            },
            "workspaces": rows,
            "unadopted": pending,
            "no_corpus_for_this_deployment": dark,
        },
        as_json=args.json,
    )
    if pending:
        print(
            f"UNADOPTED: {len(pending)} workspace(s) still hold a pre-4.5 corpus that no "
            "revision names; retrieval answers them empty until `adopt` runs",
            file=sys.stderr,
        )
        return 1
    return 0


async def _run_adopt(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = create_qdrant_client(settings.qdrant)
    try:
        results = await adopt_all(
            client,
            deployment_regime(settings.embedding_service),
            workspace=args.workspace,
            dry_run=args.dry_run,
        )
    finally:
        await client.close()
    _emit(results, as_json=args.json)
    _logger.info("ops.embedding_migration.adopt", extra={"count": len(results)})
    return 1 if any(row["action"] == "refused" for row in results) else 0


async def _run_build(args: argparse.Namespace) -> int:
    settings = load_settings()
    target = _target_regime(args)
    source_revision = deployment_regime(settings.embedding_service).revision
    if target.revision == source_revision:
        print(
            "refused: the target regime is the one this deployment already runs -- there is "
            "nothing to build",
            file=sys.stderr,
        )
        return 2

    embedding_settings = settings.embedding_service.model_copy(
        update={
            "url": args.url,
            "model": target.model,
            "dimensions": target.dimensions,
            "embedding_max_input_tokens": target.max_input_tokens,
        }
    )
    client = create_qdrant_client(settings.qdrant)
    http = create_embedding_http_client(embedding_settings)
    results: list[BuildResult] = []
    try:
        store = QdrantVectorStore(client)
        embeddings = ExternalEmbeddingProvider(http, embedding_settings)
        for corpora in await inventory(client):
            if args.workspace is not None and corpora.workspace_id != args.workspace:
                continue
            if source_revision not in corpora.revisions:
                # Nothing to copy FROM. Reported by `status`, not repaired
                # here: a workspace whose live corpus this deployment does not
                # own is a state `adopt` or a previous swap left behind, and
                # guessing a source would be guessing which model wrote it.
                continue
            results.append(
                await build_workspace(
                    client,
                    store,
                    embeddings,
                    workspace_id=corpora.workspace_id,
                    source_revision=source_revision,
                    target=target,
                    batch=args.batch,
                    resume=not args.no_resume,
                )
            )
    finally:
        await http.aclose()
        await client.close()

    _emit(
        {
            "target_revision": target.revision,
            "source_revision": source_revision,
            "workspaces": [
                {
                    "workspace_id": result.workspace_id,
                    "source": result.source,
                    "target": result.target,
                    "source_points": result.source_points,
                    "copied": result.copied,
                    "skipped": result.skipped,
                    "seconds": result.seconds,
                }
                for result in results
            ],
        },
        as_json=args.json,
    )
    return 0


async def _run_verify(args: argparse.Namespace) -> int:
    settings = load_settings()
    target = _target_regime(args)
    source_revision = deployment_regime(settings.embedding_service).revision
    client = create_qdrant_client(settings.qdrant)
    try:
        rows = [
            await verify_workspace(
                client,
                workspace_id=corpora.workspace_id,
                source_revision=source_revision,
                target=target,
            )
            for corpora in await inventory(client)
            if (args.workspace is None or corpora.workspace_id == args.workspace)
            and source_revision in corpora.revisions
        ]
    finally:
        await client.close()
    _emit({"target_revision": target.revision, "workspaces": rows}, as_json=args.json)
    short = [row["workspace_id"] for row in rows if not row["ok"]]
    if short:
        print(
            f"SHORTFALL: {len(short)} workspace(s) are not ready to serve revision "
            f"{target.revision}: {', '.join(str(name) for name in short)}",
            file=sys.stderr,
        )
        return 1
    return 0


async def _run_drop(args: argparse.Namespace) -> int:
    settings = load_settings()
    live = deployment_regime(settings.embedding_service).revision
    if args.revision == live:
        print(
            f"refused: {args.revision} is the revision this deployment reads and writes -- "
            "dropping it would empty retrieval for every workspace",
            file=sys.stderr,
        )
        return 2
    client = create_qdrant_client(settings.qdrant)
    try:
        dropped = await drop_revision(client, args.revision, workspace=args.workspace)
    finally:
        await client.close()
    _emit(dropped, as_json=args.json)
    _logger.warning(
        "ops.embedding_migration.dropped",
        extra={"revision": args.revision, "count": len(dropped)},
    )
    return 0


def _add_regime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", required=True, help="the TARGET model name")
    parser.add_argument("--dimensions", type=int, required=True, help="its vector width")
    parser.add_argument(
        "--max-input-tokens",
        type=int,
        required=True,
        help="its `EMB_MAX_SEQ_LEN`; part of the regime fingerprint, so a wrong value here "
        "builds a corpus the fleet will never look for",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.embedding_migration",
        description="Move a knowledge corpus onto a new embedding model without an outage "
        "(capacity-plan 4.5; module docstring for the whole sequence).",
    )
    # `--json` rides a PARENT parser rather than sitting on the top level:
    # argparse binds a top-level flag before the subcommand only, so
    # `... status --json` -- the order the usage block shows and the order
    # anyone types -- would be an error. Every verb gets its own copy instead.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="one JSON object per line")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser(
        "status",
        parents=[common],
        help="what corpora exist, and which this deployment can read",
    )

    adopt = sub.add_parser(
        "adopt",
        parents=[common],
        help="name every pre-4.5 corpus for this deployment's regime",
    )
    adopt.add_argument("--workspace", default=None, help="narrow to ONE workspace id")
    adopt.add_argument("--dry-run", action="store_true", help="report; create nothing")

    build = sub.add_parser(
        "build", parents=[common], help="fill a new regime's corpus beside the live one"
    )
    _add_regime_args(build)
    build.add_argument("--url", required=True, help="an embedding service serving the TARGET model")
    build.add_argument("--workspace", default=None, help="narrow to ONE workspace id")
    build.add_argument("--batch", type=int, default=_BATCH, help=f"points per page ({_BATCH})")
    build.add_argument(
        "--no-resume",
        action="store_true",
        help="re-embed points the target already holds (a repair, not a speed-up)",
    )

    verify = sub.add_parser(
        "verify", parents=[common], help="refuse to call a corpus ready when it is not"
    )
    _add_regime_args(verify)
    verify.add_argument("--workspace", default=None, help="narrow to ONE workspace id")

    drop = sub.add_parser(
        "drop", parents=[common], help="delete one revision's corpus -- the irreversible verb"
    )
    drop.add_argument("--revision", required=True, help="the revision to delete")
    drop.add_argument("--workspace", default=None, help="narrow to ONE workspace id")
    drop.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="required: this destroys vectors, and rollback stops being possible",
    )
    return parser


_ACTIONS = {
    "status": _run_status,
    "adopt": _run_adopt,
    "build": _run_build,
    "verify": _run_verify,
    "drop": _run_drop,
}


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _build_parser().parse_args()
    raise SystemExit(asyncio.run(_ACTIONS[args.action](args)))


if __name__ == "__main__":
    main()
