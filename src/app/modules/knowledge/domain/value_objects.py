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


class SummaryBlocked(StrEnum):
    """WHY a requested summary build was refused (scenarios plan section 5,
    ب-4ب, gap ف-7).

    Two members because ``RequestSummary`` refuses for exactly two reasons,
    and until this enum existed a caller could not tell them apart: both
    arrive as ``ConflictError`` and both carry ``common.conflict``, so the
    only thing separating "your summary is already being built" from "this
    document has no text to summarise" was an exception message written for
    a log. A caller that guessed said «ما زال قيد الإعداد» about a document
    for which nothing was being prepared and nothing ever would be.

    **A vocabulary, not a status.** Nothing stores this and nothing
    transitions through it. It names the outcome of ONE call at the moment
    it was refused, which is why it is not modelled on ``SummaryJobStatus``:
    ``in_progress`` here is not a job's state but the reason THIS request
    did not create a second one.

    It is deliberately NOT a second error catalogue entry (ق-6). The wire
    behaviour of the REST route is unchanged — the refusals stay
    ``common.conflict``, stay 409, and stay one documented code — because
    this distinction is for the caller that RENDERS a sentence, not for the
    client that reads a status line.
    """

    IN_PROGRESS = "in_progress"
    NOT_INDEXED = "not_indexed"


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


@dataclass(frozen=True, slots=True)
class ParentChunkText:
    """One ``knowledge.parent_chunks`` row's id + text, as the retrieval
    half's parent-widening lookup resolves it (rag-retrieval-plan.md §3.7,
    ``P-34``; ``ports/repository.py::DocumentRepository.
    parent_texts_for_chunk_ids``).

    ``id`` is the dedup key -- two retrieved (leaf) chunks that widen to the
    SAME ``parent_chunks`` row must substitute that row's text once, never
    twice (``application/retrieval.py``'s whole reason for keeping ``id``
    here at all, since ``text`` alone would make two coincidentally-identical
    parents indistinguishable from one shared parent). ``text`` is always the
    row's FULL, uncapped text: ``max_parent_chunk_chars`` truncation is
    applied by the caller at substitution time, not baked in here, so this
    value object stays a faithful copy of the row regardless of which caller
    reads it.

    ``is_complete`` carries ``knowledge.parent_chunks.is_complete`` --
    ``ExplodedTable.parent_is_complete`` (``domain/tables.py``) -- and it is
    NOT optional context: ``ChunkParent``'s own docstring states the rule
    that "every consumer that lets a parent stand IN PLACE OF its rows must
    read this bit first", and the parent-widening consumer
    (``application/retrieval.py::_widen_to_parents``) is exactly such a
    consumer. ``True`` means the row is the whole-table parent, which really
    does contain every row it parents; ``False`` means the header-only parent
    P-13 mints for a table past ``TABLE_PARENT_MAX_ROWS``, which holds the
    column names and NOT ONE of the values under them. Substituting a
    ``False`` parent replaces a retrieved passage with a string that does not
    contain it -- the same content loss ``DocumentRepository.chunk_texts``
    (``P-42``) already refuses on the summarisation side, which is why this
    field exists here rather than the caller re-deriving it from the text."""

    id: str
    text: str
    is_complete: bool
