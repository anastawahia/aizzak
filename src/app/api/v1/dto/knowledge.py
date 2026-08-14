"""Knowledge DTOs — 03-api-spec §2 (``KnowledgeSearchIn`` ·
``RetrievedChunkOut`` · ``DocumentOut``), Phase 6.1-و-3.

``k`` keeps the spec's ``le=50`` ceiling and gains a ``ge=1`` floor the spec
leaves implicit: ``k=0`` would be a well-formed request for nothing, and
``k=-1`` a request the vector store would have to interpret. Both are
argument errors, and 422 says so before a single embedding is computed.

``DocumentOut`` deliberately carries no ``error`` field even though the
aggregate has one (06 §7). An indexing failure's reason is an internal
diagnostic — a parser stack trace, a provider's message — and 03 §2's shape
stops at ``status``. A client learns THAT indexing failed, which is what
changes its behaviour; WHY belongs in logs and events, not in a tenant-facing
payload that would leak whatever the pipeline happened to say.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# The closed vocabularies, spelled once. They mirror `SummaryKind`/
# `SummaryLanguage` without importing them: a DTO that imported the module's
# enums would publish whatever the domain adds next straight into the wire
# contract, and the API layer is where a vocabulary change should have to be
# decided rather than inherited. The unit suite asserts the two agree.
SummaryKindIn = Literal["overview", "full"]
SummaryLangIn = Literal["auto", "ar", "en"]
ExportFormatIn = Literal["pdf", "docx"]


class KnowledgeSearchIn(BaseModel):
    """A retrieval request over this workspace's indexed corpus."""

    query: str = Field(min_length=1)
    k: int = Field(default=5, ge=1, le=50)


class RetrievedChunkOut(BaseModel):
    """One retrieved chunk — 1:1 with the module's ``RetrievedChunk``."""

    document_id: str
    chunk_id: str
    text: str
    score: float


class DocumentOut(BaseModel):
    """A document's ingestion state. No chunk text, no failure reason."""

    id: str
    file_id: str
    status: str
    chunk_count: int
    created_at: datetime


class ReindexIn(BaseModel):
    """The documents to rebuild (BE-RAG-007).

    ``min_length=1`` because an empty rebuild is a request for nothing, and
    ``max_length=50`` because each entry destroys a document's vectors: an
    unbounded list would let one call empty an unbounded slice of the corpus,
    and its progress would be unreadable besides. Duplicates are accepted by
    the DTO and collapsed by the use-case — naming a document twice is a slip,
    not a request to rebuild the rebuild.
    """

    document_ids: list[str] = Field(min_length=1, max_length=50)


class ReindexItemOut(BaseModel):
    """One document in a job. ``source_document_id`` is the destroyed
    original — a historical id with no row behind it, kept so the job can say
    what it replaced and not only what it made."""

    document_id: str
    file_id: str
    source_document_id: str
    status: str


class ReindexJobOut(BaseModel):
    """A job's live progress (BE-RAG-008).

    Every number here is derived at read time from the documents the job
    created (INV-K5) — nothing on the row counts anything, so nothing can
    drift from the corpus it describes.

    ``current_file_id`` is a reference and not a name, the same choice the pin
    list made: the client already joins ``GET /files`` by id, and duplicating
    the name here would let a renamed file show two spellings on one screen.
    """

    id: str
    status: str
    total: int
    finished: int
    percent: int
    current_file_id: str | None
    items: list[ReindexItemOut]
    created_at: datetime
    cancelled_at: datetime | None


class SummaryIn(BaseModel):
    """What to build (BE-RAG-009).

    Both fields are defaulted, unlike the same pair on the read routes: this
    is a request to PRODUCE something, and "the full summary, in whatever
    language the document is written in" is what a caller who says nothing
    else means. On ``GET``/``DELETE`` the same two values are the resource's
    identity, and half an identity is not a thing to default.

    **There is no ``force``.** The plan proposed one; the two operations it
    distinguished are the two verbs — ``GET`` reads what is stored, ``POST``
    builds. See ``RequestSummary``.
    """

    kind: SummaryKindIn = "full"
    lang: SummaryLangIn = "auto"


class SummaryOut(BaseModel):
    """A stored summary (BE-RAG-010).

    ``model`` is published, unlike the indexing ``error`` ``DocumentOut``
    withholds, and the difference is what the field IS: an indexing failure's
    reason is an internal diagnostic that could carry anything a parser said,
    while the model that wrote a summary is a property of the artefact the
    reader is looking at — the answer to "why does this read differently from
    last month's".

    ``source_chunks``/``truncated`` say how much of the document the summary
    covers. A ``full`` summary of a document past the map ceiling is a true
    summary of a prefix, and a client that cannot tell will present it as a
    summary of the whole.
    """

    id: str
    document_id: str
    kind: str
    lang: str
    text: str
    model: str
    source_chunks: int
    truncated: bool
    built_at: datetime


class SummaryJobOut(BaseModel):
    """A summary build's progress (BE-RAG-009/011).

    Unlike ``ReindexJobOut``, every number here is READ FROM THE ROW rather
    than derived — there is no corpus that already records how far a build
    got, so the job counts (``SummaryJobStatus``). ``percent`` is the
    aggregate's own property, not the client's arithmetic: a succeeded job is
    100 even though the reduce step it just finished was never counted in
    ``total_chunks``.

    ``error`` IS published here, where ``DocumentOut`` withholds indexing's.
    A summary failure is a message this module wrote for this reader — "no
    indexed text", "cancelled" — not a provider's raw output, and it is the
    entire explanation the person who pressed the button will get.
    """

    id: str
    document_id: str
    kind: str
    lang: str
    status: str
    total_chunks: int
    done_chunks: int
    percent: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    cancelled_at: datetime | None


class SummaryDeletedOut(BaseModel):
    """The outcome of a delete (BE-RAG-011). ``false`` means there was
    nothing stored under that key — a 200, not a 404: the caller asked for a
    state and that state now holds."""

    deleted: bool
