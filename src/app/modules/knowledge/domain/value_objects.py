"""Knowledge value objects (pure — 06-domain-models §7).

Frozen, self-validating primitives. ``VectorRef`` is a module-local copy of
the same-shaped value object in ``memory`` (module independence,
12-module-authoring-guide §3) — knowledge does not import another module's
domain, even one that happens to look identical. Identifiers stay plain
UUIDv7 text; the domain imports no framework.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.knowledge.domain.errors import InvalidKnowledgeInput


class IndexStatus(StrEnum):
    """A ``Document``'s ingestion lifecycle (06 §7, INV-K2): one-way
    ``pending -> indexing -> (indexed | failed)``."""

    PENDING = "pending"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class ReindexJobStatus(StrEnum):
    """A ``ReindexJob``'s state (06 §7, INV-K5) — **derived, never stored**.

    There is no transition table here because there are no transitions: the
    value is recomputed from the job's ``cancelled_at`` and the live statuses
    of the documents it created, every time it is read. That is what makes it
    impossible for the number a client sees to disagree with the corpus.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SummaryKind(StrEnum):
    """What a ``Summary`` is (06 §7 · BE-RAG-009).

    Two kinds, and the difference is how much of the document each one reads
    rather than how the prompt is worded: ``overview`` is a quick look bounded
    to the document's opening chunks, ``full`` is a map-reduce over all of
    them. Naming that in the vocabulary — instead of exposing a "depth"
    number — is what keeps the cost of each one a property of the contract
    rather than of whatever the caller happened to pass.
    """

    OVERVIEW = "overview"
    FULL = "full"


class SummaryLanguage(StrEnum):
    """The language a ``Summary`` was ASKED for (06 §7 · BE-RAG-009).

    ``auto`` is a real member and not a missing value: "answer in whatever
    language the document is written in" is a distinct instruction, and its
    output is a distinct artefact from the same document summarised into
    Arabic. All three are part of the stored key for exactly that reason —
    collapsing ``auto`` onto the language the model happened to pick would
    make the next read of ``auto`` return something that was never requested.
    """

    AUTO = "auto"
    AR = "ar"
    EN = "en"


class SummaryJobStatus(StrEnum):
    """A ``SummaryJob``'s state (06 §7 · BE-RAG-009/011) — **stored, unlike
    ``ReindexJobStatus``**.

    That divergence is deliberate and it is the interesting thing about this
    aggregate. A re-index job derives everything because the corpus already
    records it: its items point at documents, and those documents carry their
    own statuses, so a stored counter could only ever be a second opinion
    about a fact Postgres already holds. A summary job has no such witness —
    until it succeeds there is no summary row at all, and "step 7 of 42" is
    known only to the worker doing step 7. There is nothing to derive it FROM,
    so it is written down, and the honest cost of writing it down is stated on
    the table (``0003_summaries.py``): a worker that dies mid-run leaves the
    number stale rather than wrong-by-construction.

    ``queued -> running -> (succeeded | failed | cancelled)``; the three
    terminal members are also the set the worker's redelivery guard refuses to
    run against, which is what lets a cancellation of a job no worker has
    picked up yet need no cooperation from anyone.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class VectorRef:
    """A pointer to an indexed Qdrant point: the owning collection + point id."""

    collection: str
    point_id: str

    def __post_init__(self) -> None:
        if not self.collection:
            raise InvalidKnowledgeInput("vector ref collection must not be empty")
        if not self.point_id:
            raise InvalidKnowledgeInput("vector ref point id must not be empty")
