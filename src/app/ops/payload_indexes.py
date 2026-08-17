"""One-off payload-index backfill for Qdrant collections that predate the
indexes (docs/spaces-backend-plan.md §5-ب, step 16).

**Why a tool exists at all.** ``QdrantVectorStore.ensure_hybrid_collection``
indexes ``workspace_id``/``document_id``/``space`` right after it creates a
collection -- and returns EARLY for a collection that already exists, so it
never reaches that code for one created before it shipped. That early return
is deliberate (it is what makes provisioning cheap on every write path), and
it is pinned by ``tests/unit/test_qdrant_mapping.py::test_an_existing_
collection_gains_no_payload_index_here``. The consequence is this module:
every ``kn-<workspace_id>`` collection that existed before the spaces work
carries ZERO payload indexes -- the project had none anywhere at all
(spaces plan §2-ب) -- and nothing on any hot path will ever give it one.

**Knowledge collections only.** ``kn-`` is the hybrid prefix
(``knowledge_collection``); ``mem-`` collections are provisioned through the
narrower ``VectorStore``/``ensure_collection`` contract, which asks for no
index and never carried a ``space`` payload key to index (spaces plan §3.4,
[§3.147]). Indexing them here would invent a policy that no code path
anywhere else in this repository holds -- and ``is_tenant`` on a key no point
carries is an ordering instruction over nothing.

**The target set comes from Qdrant, not from Postgres.** The question is
"which collections EXIST", and Qdrant is the only authority on that: a
workspace with no collection yet gets its indexes at creation time and needs
nothing from this tool, and a collection whose workspace rows are long gone
still answers searches until someone drops it. Reading the list from the
server is also what keeps this process free of a database entirely -- no
DSN, no role, no RLS question, unlike every other ``app.ops.*`` sweep.

**No ``--yes``, unlike ``app.ops.purge``/``app.ops.dlq purge``.** Creating a
payload index adds no data, removes no data and rewrites no payload; the
worst case is a slower few seconds while Qdrant builds it. ``--dry-run`` is
still offered because an operator should be able to see the scope of a pass
before running it, not because the pass is dangerous.

**Assert, then verify.** Every collection is re-read from the server AFTER
its indexes are asserted, and the result reports what the SERVER holds, not
what the calls returned. A ``create_payload_index`` that answers
``completed`` while leaving the schema untouched would otherwise be reported
as a success -- and the whole failure mode this step exists to close is a
collection that looks provisioned and is not. The process exits non-zero if
any collection is left incomplete.

**No per-collection error handling.** A failure aborts the pass, and that is
the cheap outcome: asserting an index is idempotent (verified live -- a
re-assert returns ``completed``, and a re-assert with a DIFFERENT
``is_tenant`` replaces the index rather than conflicting), so re-running
after the fault costs a few no-op round trips and nothing else. Catching
per collection would buy a report that ends in "mostly succeeded", which is
the one answer an operator cannot act on.

Usage::

    python -m app.ops.payload_indexes run [--collection NAME] [--dry-run]

``--collection`` NARROWS the discovered set and never widens it: a name that
is not a live ``kn-`` collection is refused, so the tool cannot be pointed at
a ``mem-`` collection (or a typo) by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient

from app.infrastructure.config import load_settings
from app.infrastructure.vector.qdrant_store import (
    HYBRID_PAYLOAD_INDEXES,
    QdrantVectorStore,
    create_qdrant_client,
    payload_index_flags,
)
from app.modules.knowledge.domain.collections import knowledge_collection

_logger = logging.getLogger(__name__)

# Derived from the naming function itself rather than re-typing "kn-": the
# prefix and the collection names must not be able to drift apart.
KNOWLEDGE_PREFIX = knowledge_collection("")


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """One collection's outcome. ``pending`` is what was missing or wrongly
    flagged BEFORE the pass (on a dry run, what a real pass WOULD assert);
    ``indexed`` is what the server holds correctly AFTER it -- read back, not
    inferred from the calls made."""

    collection: str
    pending: tuple[str, ...]
    indexed: tuple[str, ...]
    dry_run: bool

    @property
    def complete(self) -> bool:
        """True iff the server now holds every expected key with the right
        tenant flag. Always the post-pass truth, so a dry run over an
        unindexed collection reports ``False`` -- which is the honest answer
        to "is this collection done", and the reason the CLI's exit code
        keys off a real run only."""
        return set(self.indexed) == {field for field, _ in HYBRID_PAYLOAD_INDEXES}


async def list_target_collections(
    client: AsyncQdrantClient, *, collection: str | None = None
) -> list[str]:
    """Every live ``kn-`` collection, sorted; narrowed to one by
    ``collection``.

    A ``collection`` that is not in the discovered set raises ``ValueError``
    naming it (``app.ops.dlq.requeue``'s convention) rather than running over
    nothing: the operator named something specific, and an empty run would
    read as "already indexed" (module docstring).
    """
    response = await client.get_collections()
    targets = sorted(
        description.name
        for description in response.collections
        if description.name.startswith(KNOWLEDGE_PREFIX)
    )
    if collection is None:
        return targets
    if collection not in targets:
        raise ValueError(
            f"{collection!r} is not a live {KNOWLEDGE_PREFIX!r} collection -- "
            "--collection narrows the discovered set, it never widens it "
            "(`mem-` collections are deliberately not indexed)"
        )
    return [collection]


async def pending_indexes(client: AsyncQdrantClient, collection: str) -> tuple[str, ...]:
    """Which expected keys this collection is missing, or holds with the
    wrong ``is_tenant`` flag -- read-only."""
    flags = await payload_index_flags(client, collection)
    return tuple(field for field, tenant in HYBRID_PAYLOAD_INDEXES if flags.get(field) != tenant)


async def backfill_collection(
    store: QdrantVectorStore,
    client: AsyncQdrantClient,
    collection: str,
    *,
    dry_run: bool = False,
) -> CollectionResult:
    """Assert the missing indexes on ONE collection, then re-read the server
    to report what it actually holds (module docstring's "assert, then
    verify"). ``dry_run`` asserts nothing, so both reads see the same state.
    """
    pending = await pending_indexes(client, collection)
    if not dry_run:
        for field, tenant in HYBRID_PAYLOAD_INDEXES:
            if field in pending:
                await store.ensure_payload_index(collection, field, tenant=tenant)

    flags = await payload_index_flags(client, collection)
    indexed = tuple(field for field, tenant in HYBRID_PAYLOAD_INDEXES if flags.get(field) == tenant)
    return CollectionResult(
        collection=collection, pending=pending, indexed=indexed, dry_run=dry_run
    )


async def backfill_all(
    store: QdrantVectorStore,
    client: AsyncQdrantClient,
    *,
    collection: str | None = None,
    dry_run: bool = False,
) -> list[CollectionResult]:
    """Run the pass over every target collection, in name order."""
    return [
        await backfill_collection(store, client, target, dry_run=dry_run)
        for target in await list_target_collections(client, collection=collection)
    ]


def _print_result(result: CollectionResult) -> None:
    payload = {
        "collection": result.collection,
        "pending": list(result.pending),
        "indexed": list(result.indexed),
        "complete": result.complete,
        "dry_run": result.dry_run,
    }
    print(json.dumps(payload, ensure_ascii=False))
    _logger.info("ops.payload_indexes.collection", extra=payload)


async def _run_cli(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = create_qdrant_client(settings.qdrant)
    try:
        results = await backfill_all(
            QdrantVectorStore(client), client, collection=args.collection, dry_run=args.dry_run
        )
    except ValueError as exc:  # --collection named something that is not a target
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        # A short-lived process still closes what it opened (`revoke.py`'s
        # own reason: a connection the server holds open until its timeout).
        await client.close()

    for result in results:
        _print_result(result)

    incomplete = [result.collection for result in results if not result.complete]
    if incomplete and not args.dry_run:
        print(
            f"INCOMPLETE: {len(incomplete)} collection(s) still missing indexes: "
            f"{', '.join(incomplete)}",
            file=sys.stderr,
        )
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.payload_indexes",
        description="Create the hybrid payload indexes on knowledge collections that were "
        "created before the adapter provisioned them (module docstring).",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    run_parser = sub.add_parser(
        "run", help="index every live `kn-` collection that is missing an expected key"
    )
    run_parser.add_argument(
        "--collection",
        default=None,
        help="narrow to ONE live `kn-` collection (refused for any other name)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what WOULD be indexed; creates nothing",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(asyncio.run(_run_cli(_build_parser().parse_args())))


if __name__ == "__main__":
    main()
