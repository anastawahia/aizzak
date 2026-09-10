"""Knowledge Qdrant collection naming + deterministic point ids (06-domain-
models §7, DD-04; 3.k3).

Mirrors ``memory``'s ``mem-<workspace_id>`` per-workspace collection scheme
(``memory/application/use_cases.py::RecallRelevant``) with a ``kn-`` prefix
instead — one Qdrant collection per workspace, shared by every document in
it; per-document/per-chunk isolation is a payload filter (``workspace_id``,
``document_id``), not a collection-per-document scheme.

``chunk_point_id`` derives a **deterministic**, idempotent Qdrant point id
from ``(document_id, seq)`` via ``uuid5`` over a fixed namespace: re-indexing
the same document/seq (a retry, a worker crash-restart, at-least-once event
redelivery) always upserts the *same* point instead of leaking duplicates —
this is what keeps ``IndexDocument`` naturally idempotent without a separate
dedup step, mirroring INV-K1 (``UNIQUE(document_id, seq)``, `01-data-model
§2.7`) projected onto the vector store.

Collection names carry an EMBEDDING REVISION since capacity step 4.5
(``docs/capacity-plan.md`` §5, Wave 4). ``knowledge_collection`` is now the
**stable read name** — a Qdrant ALIAS once a workspace has been adopted —
and ``knowledge_collection_revision`` is the PHYSICAL collection behind it,
one per embedding regime. The whole argument for the split, and the one-time
adoption that introduces it, live in ``app.ops.embedding_migration``; what
belongs here is the naming and the fingerprint that decides it.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

# Fixed, frozen namespace UUID for chunk_point_id's uuid5 derivation --
# generated once (uuid.uuid4()) and never regenerated. Changing it would
# silently re-point every previously-indexed chunk at a brand-new Qdrant
# point id on the next index run.
_KNOWLEDGE_NS = uuid.UUID("5561cc57-1a7e-43a9-81de-1c93c6192ccf")


# The knowledge prefix, shared by the read name and by every physical
# revision behind it — `app.ops.payload_indexes` discovers its targets by
# this same string.
_PREFIX = "kn-"


def knowledge_collection(workspace_id: str) -> str:
    """The workspace's STABLE READ NAME (DD-04 tenant isolation) — mirrors
    ``memory``'s ``mem-<workspace_id>``.

    Since capacity step 4.5 this is what retrieval asks for and, for an
    ADOPTED workspace, it is a Qdrant alias rather than a collection: the
    corpus lives in ``knowledge_collection_revision(workspace_id,
    revision)`` and the alias is what an atomic model swap re-points. The
    name is unchanged, and deliberately so — every ``chunks.collection``
    row ever written names it, and a delete through an alias reaches the
    collection behind it, so not one row has to be rewritten for the switch
    to stay safe.

    Before adoption it is a plain collection, exactly as it always was.
    """
    return f"{_PREFIX}{workspace_id}"


def chunk_point_id(document_id: str, seq: int) -> str:
    """Deterministic Qdrant point id for one chunk, derived from
    ``document_id`` plus its 0-based ``seq`` (INV-K1) via ``uuid5`` —
    re-indexing always upserts the same point, never a duplicate.

    Distinct from the future ``Chunk.id`` (a fresh UUIDv7 minted per DD-02):
    this is the vector-store identity, not the row identity.
    """
    return str(uuid.uuid5(_KNOWLEDGE_NS, f"{document_id}:{seq}"))


# The width of an embedding revision, in hex characters (6 digest bytes).
# Long enough that the birthday bound over the handful of regimes one
# deployment will ever run is not worth a sentence; short enough that
# `kn-<uuid>-<rev>` stays far inside Qdrant's 255-character collection-name
# ceiling (3 + 36 + 1 + 12 = 52).
#
# ⚠️ It is a FINGERPRINT, not a counter, and that is the load-bearing
# choice. A counter would make "roll back to the previous model" mean
# "build a third collection": going back to a regime the platform has
# already run must land on the collection that ALREADY HOLDS its vectors,
# and only a name derived from the regime itself does that. It is also what
# lets two replicas that were never told about each other compute the same
# write target from the same settings, with nothing to coordinate.
_REVISION_BYTES = 6
REVISION_HEX_LEN = _REVISION_BYTES * 2

# The separator between a workspace id and its revision. A UUID already
# contains four of these, which is why `split_knowledge_collection` parses
# from the RIGHT and validates both halves rather than splitting on the
# first one it finds.
_REVISION_SEP = "-"

_HEX_DIGITS = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class EmbeddingRegime:
    """Everything about a deployment that decides what a stored vector MEANS
    (capacity step 4.5).

    Three fields, and the third is the one that gets forgotten. The model
    name and the dimension are the obvious inputs; ``max_input_tokens`` is
    the ceiling at which the model stops reading, and two deployments that
    differ only there produce different vectors for every text longer than
    the smaller of them — ``docker-compose.yml``'s own comment on
    ``EMB_MAX_SEQ_LEN`` says exactly that, and
    ``CachingEmbeddingProvider`` (4.3) already keys on the same three for
    the same reason. A collection is coherent only while all three hold.

    ⚠️ **The dimension is here even though the fingerprint would survive
    without it.** Two regimes of different width can never share a
    collection anyway — Qdrant refuses the upsert — so the digest does not
    NEED it to keep them apart. It is included because the regime is also
    what PROVISIONS a collection, and a fingerprint that omitted the number
    it provisions with would let a ``dimensions`` change silently reuse a
    name whose collection was built at the old width: the one mixing
    failure that is loud, made quiet.
    """

    model: str
    dimensions: int
    max_input_tokens: int

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("embedding regime model must not be blank")
        if self.dimensions <= 0:
            raise ValueError("embedding regime dimensions must be positive")
        if self.max_input_tokens <= 0:
            raise ValueError("embedding regime max_input_tokens must be positive")

    @property
    def revision(self) -> str:
        """This regime's stable 12-hex-character fingerprint.

        ``blake2s`` with an explicit ``digest_size`` rather than a sliced
        SHA-256: the truncation is then the algorithm's own parameter
        instead of a slice a later edit can widen without noticing that
        every existing collection name just moved. The canonical form is
        newline-joined so no field's value can impersonate a boundary.
        """
        canonical = f"{self.model}\n{self.dimensions}\n{self.max_input_tokens}"
        return hashlib.blake2s(canonical.encode("utf-8"), digest_size=_REVISION_BYTES).hexdigest()


def knowledge_collection_revision(workspace_id: str, revision: str) -> str:
    """The PHYSICAL collection holding one workspace's corpus under ONE
    embedding regime — ``kn-<workspace_id>-<revision>`` (capacity step 4.5).

    Distinct from ``knowledge_collection`` above, which is the name readers
    ask for and which resolves here through a Qdrant alias. WRITERS resolve
    this one, so an indexer running a regime the alias does not yet point at
    writes into its own corpus instead of into the live one — which is the
    whole of what "لا خلطَ أبعادٍ في صندوقٍ واحدٍ أبداً" needs from the
    naming layer.
    """
    _guard_revision(revision)
    return f"{knowledge_collection(workspace_id)}{_REVISION_SEP}{revision}"


def split_knowledge_collection(name: str) -> tuple[str, str | None] | None:
    """Parse a live Qdrant collection name back into
    ``(workspace_id, revision)``, or ``None`` when it is not a knowledge
    collection at all.

    ``revision`` is ``None`` for a LEGACY ``kn-<workspace_id>`` collection —
    one created before 4.5 and not yet adopted. That is a real state with a
    real meaning (``app.ops.embedding_migration status`` reports it, and
    ``adopt`` is what ends it), not a parse failure.

    Parses from the RIGHT and validates BOTH halves, because a workspace id
    is a UUID and already carries four separators: splitting on the first
    would read ``kn-019f...`` as workspace ``019f`` with a revision made of
    everything after it. A trailing group that is not exactly
    ``REVISION_HEX_LEN`` lowercase hex characters is therefore part of the
    workspace id, not a revision.
    """
    if not name.startswith(_PREFIX):
        return None
    rest = name[len(_PREFIX) :]
    if _is_uuid(rest):
        return (rest, None)
    workspace_id, sep, revision = rest.rpartition(_REVISION_SEP)
    if not sep or not _is_revision(revision) or not _is_uuid(workspace_id):
        return None
    return (workspace_id, revision)


def _guard_revision(revision: str) -> None:
    if not _is_revision(revision):
        raise ValueError(f"not an embedding revision: {revision!r}")


def _is_revision(candidate: str) -> bool:
    return len(candidate) == REVISION_HEX_LEN and all(char in _HEX_DIGITS for char in candidate)


def _is_uuid(candidate: str) -> bool:
    try:
        parsed = uuid.UUID(candidate)
    except ValueError:
        return False
    # `uuid.UUID` also accepts braces, urns and bare undashed hex; only the
    # canonical dashed form is what `knowledge_collection` has ever
    # produced, and accepting the rest would let two spellings of one
    # workspace parse out of two different collection names.
    return str(parsed) == candidate
