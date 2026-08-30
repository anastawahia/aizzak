"""Knowledge persistence port (02-port-contracts §2).

Outbound repository contract for the ``Document`` aggregate + its ``Chunk``
rows. Every method takes ``ExecutionContext`` first so the SQL adapter can
apply the RLS guard (``SET LOCAL app.workspace_id``) and the ``WHERE
workspace_id`` filter (DD-04) — the files/memory port precedent.

``ReindexJobRepository`` (BE-RAG-007/008) is the module's second persistence
port, for the manual-rebuild aggregate. It reads its items' statuses out of
``documents`` rather than storing them, which is why it can be a read/write
port with no update method beyond ``mark_cancelled``.

``SummaryRepository``/``SummaryJobRepository`` (BE-RAG-009/010/011) are the
third and fourth. The job port DOES have a general ``save``, unlike the
re-index one — the difference is not a style choice: that aggregate stores
its own progress because nothing else records it (``SummaryJobStatus``), and
a port with only ``mark_cancelled`` could not write a number down.

``add`` persists the document's ``space_id`` and ``set_status`` deliberately
does not touch it (spaces plan, step 8): a document's space comes from its
file and a file does not move between spaces (decision 3), so leaving the
column out of every UPDATE is what makes that true of the database and not
only of the type — the ``files`` repository's own argument.

``add_parent_chunks``/``parent_chunk_texts`` (P-14, rag-indexing-plan.md
§3.2) are ``add_chunks``/``chunk_texts`` mirrored onto the coarser-grained
``knowledge.parent_chunks`` table: same idempotent-upsert contract, same
``seq``-ordered read, over the row a matched ``Chunk`` widens to via
``Chunk.parent_id`` rather than the window that was actually indexed.

``parent_texts_for_chunk_ids`` (rag-retrieval-plan.md §3.7, ``P-34``) is the
retrieval half's OWN parent-widening lookup, and a THIRD method over the same
table for a reason neither method above can stand in for: it is keyed by the
Qdrant POINT id (``RetrievedChunk.chunk_id`` / ``chunks.point_id``), never a
``doc_id``, because ``RetrieveContext`` holds a batch of candidates spanning
possibly several documents and has no ``chunks.id`` to join with in the first
place (``domain/collections.py::chunk_point_id`` is deliberately distinct
from that row-identity column). ``ports/retrieval.py::ParentChunkRepository``
is the narrower seam ``RetrieveContext`` actually depends on -- this method
is what makes ``DocumentRepository`` (and therefore ``SqlDocumentRepository``)
satisfy it structurally, with no adapter of its own.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.pagination import Page
from app.framework.types import Uuid
from app.modules.knowledge.domain.entities import (
    Chunk,
    Document,
    ParentChunk,
    ReindexJob,
    Summary,
    SummaryJob,
)
from app.modules.knowledge.domain.value_objects import (
    ParentChunkText,
    SummaryKind,
    SummaryLanguage,
    VectorRef,
)


class DocumentRepository(Protocol):
    """Tenant-scoped persistence for the ``Document`` aggregate + its
    ``Chunk`` rows (02 §2, verbatim contract)."""

    async def get(self, ctx: ExecutionContext, doc_id: Uuid) -> Document | None: ...

    async def list(
        self, ctx: ExecutionContext, *, space_id: Uuid | None, limit: int, cursor: str | None
    ) -> Page[Document]:
        """This workspace's registered documents, newest first — every
        lifecycle status included (6.1-و-3), cursor-paginated (6.3-ب).

        ``space_id`` narrows the page to one space's documents; ``None``
        returns the workspace's. ``?space_id=`` became mandatory on ``GET
        /knowledge/documents`` at step 12 (§3.7), so the router names one.
        A REQUIRED keyword with no default, so "all spaces" is a decision
        written at the call site and never one a caller falls into by
        omission — the ``FileRepository.list`` rule, for the same reason.

        A document's whole point is that it has a status: ``pending`` while a
        worker has not reached it, ``failed`` with the reason it could not be
        indexed. Filtering to ``indexed`` would leave a client that just
        uploaded a file unable to see anything at all, and unable to explain
        why a search never finds it — the exact question this collection is
        asked.

        No ``Chunk`` rows are read: the chunk text belongs to retrieval
        (``RetrievedChunk``), and ``chunk_count`` on the row already answers
        "how much of it was indexed" without loading the corpus.

        Paginated, unlike the other collections 03 §2 grouped it with. That
        grouping held while every listing was small, and 6.3 tested the
        assumption per collection: ``credentials`` is one active key per
        provider over a closed vocabulary, ``connectors`` is a static
        catalog, ``mcp-servers``/``connections`` are administered by hand.
        A corpus is none of those — it grows by one row per completed upload,
        with no ceiling anywhere in the design — so this is the one listing
        whose unbounded response was a matter of time rather than of taste,
        and it takes the general 02 §2 signature the rest of the repositories
        already have.
        """
        ...

    async def count(self, ctx: ExecutionContext, *, space_id: Uuid | None) -> int:
        """How many documents this workspace has under ``space_id`` — the same
        population ``list`` pages, counted in one query rather than walked.

        **The number ``list`` cannot give cheaply.** A corpus header shows a
        handful of names and then "and N more", so its walk needs a full count
        it never displays. Paging the whole corpus for it spends one round trip
        per page hydrating rows nobody reads, to arrive at a single integer
        (branch review ب-2) — this is that integer, priced as one ``COUNT(*)``.

        **The same predicate as ``list``, deliberately**: the same workspace,
        the same ``space_id`` narrowing with the same ``None`` meaning ("this
        workspace's", never "the spaceless ones"), and the same
        every-lifecycle-status rule for the same reason — a ``pending``
        document is part of the corpus a client was told it has. A count that
        filtered NARROWER than the listing it accompanies would make the
        header's "and N more" a promise the next page could not keep.

        ``space_id`` is a REQUIRED keyword with no default, exactly as on
        ``list``: "every space" is a decision written at the call site and
        never one a caller falls into by omission.

        No cursor and no cap. A count is not a page — there is nothing to
        resume, and bounding it would answer a different question than the one
        the header asks.
        """
        ...

    async def ids_for_files(
        self, ctx: ExecutionContext, file_ids: Sequence[Uuid]
    ) -> Sequence[Uuid]:
        """The ids of this workspace's documents built from ``file_ids``
        (BE-RAG-005) — the "file ⇒ document" translation retrieval scoping
        needs, kept inside this module.

        Callers outside ``knowledge`` pin FILES, because a file is what they
        uploaded and what they can name; the Qdrant payload, meanwhile, carries
        ``document_id`` and not ``file_id`` (``indexing._payload``). Someone has
        to bridge the two, and it is this module: a caller that had to do it
        would first have to learn that documents exist.

        Unmatched file ids simply do not appear in the result — a file with no
        document (never indexed, or deleted after it was pinned) is not an
        error, it is a scope entry with nothing behind it. An EMPTY ``file_ids``
        returns an empty list rather than every document: "no files" is not
        "all files", and the caller that means the whole corpus says so by not
        scoping at all.

        No paging and no status filter. The caller's list is already bounded by
        what it could pin, and a ``pending`` document belongs in the filter for
        the same reason it belongs in ``list``: it will have chunks shortly,
        and excluding it would make the scope silently narrower than the pins.

        ``Sequence[Uuid]`` and not ``list[Uuid]``: inside this class body
        ``list`` resolves to the METHOD above, so the obvious annotation is a
        type error rather than the builtin. Sequence is also the honester
        contract — nothing here mutates the result.
        """
        ...

    async def add(self, ctx: ExecutionContext, doc: Document) -> None: ...

    async def set_status(
        self,
        ctx: ExecutionContext,
        doc_id: Uuid,
        status: str,
        error: str | None = None,
        *,
        content_hash: str | None = None,
        pipeline_version: int | None = None,
        text_chunks: int = 0,
        table_chunks: int = 0,
        image_chunks: int = 0,
    ) -> None:
        """Persist a ``Document`` status transition (+ optional ``error``),
        RLS-guarded and ``WHERE workspace_id``-filtered like every other
        method here.

        When ``status == 'indexed'`` the adapter ALSO refreshes the
        denormalized ``documents.chunk_count`` column by counting the
        ``knowledge.chunks`` rows already persisted (via ``add_chunks``) for
        this document, so the row store's count can never drift from what
        was actually persisted.

        ``content_hash``/``pipeline_version`` (plan step 15, §3.6) are
        written ONLY on that same ``'indexed'`` transition — the ONE call
        site that has them (``IndexRegisteredDocument.finalize``, alongside
        ``Document.complete_indexing``) — and left untouched by every other
        transition, the same "leave the column out of the UPDATE" rule
        ``space_id`` already follows on this method (this port's own
        docstring): a document's fingerprint describes what its ``indexed``
        row IS, and has no meaning to write on a ``failed``/``indexing``
        transition that produced no output to fingerprint.

        ``text_chunks``/``table_chunks``/``image_chunks`` (plan step 16,
        `P-05`, decision س-15 = أ) follow the identical rule: written ONLY
        on ``'indexed'``, from ``IndexOutcome``'s own per-kind breakdown
        (``application/indexing.py::_count_by_kind``), and left at their
        column default (``0``) on every other transition — a document that
        never finished indexing has no breakdown to report, and ``0`` is
        that truth, not a placeholder.
        """
        ...

    async def add_chunks(self, ctx: ExecutionContext, chunks: Sequence[Chunk]) -> None:
        """Idempotently persist ``chunks`` (INV-K1, DD-09): the SQL adapter
        upserts via ``ON CONFLICT (document_id, seq) DO NOTHING``, so
        re-delivering the same batch (a retry after a worker crash) leaves
        the first-written rows' ids untouched instead of duplicating or
        overwriting them.

        A ``Chunk.parent_id`` (P-14) is written verbatim, including ``None``
        — a chunk minted before its parent chunk exists (or from a pipeline
        stage that has none to offer) is a normal shape, not an error.
        """
        ...

    async def add_parent_chunks(
        self, ctx: ExecutionContext, parents: Sequence[ParentChunk]
    ) -> None:
        """Idempotently persist ``parents`` (P-14, plan §3.2) — the
        ``add_chunks`` contract, mirrored: the SQL adapter upserts via
        ``ON CONFLICT (document_id, seq) DO NOTHING`` against
        ``uq_parent_seq``, so a redelivered batch after a worker crash leaves
        the first-written rows' ids untouched rather than duplicating or
        overwriting them — the same INV-K1/DD-09 reasoning, applied to the
        coarser-grained table.

        Every ``ParentChunk.id`` here is application-minted (UUIDv7) BEFORE
        the call: the ``Chunk`` rows referencing it via ``parent_id`` are
        built from that same id, so the id must already exist when a caller
        constructs them — the adapter cannot mint it as a side effect of
        this insert without breaking that ordering.
        """
        ...

    async def vector_refs(self, ctx: ExecutionContext, doc_id: Uuid) -> Sequence[VectorRef]:
        """Where this document's chunks live in the vector store (BE-RAG-007).

        Read from the ``chunks`` rows rather than recomputed from
        ``chunk_point_id(doc_id, seq)``: the ids are deterministic today, but
        what must be deleted is what was actually WRITTEN, and only the rows
        know that. A document with no chunks (``pending``, or a failure before
        the first batch) yields an empty sequence, which is a normal answer
        and not an error.

        Always called BEFORE ``purge`` — afterwards nothing remains to say
        which points to delete.
        """
        ...

    async def chunk_texts(self, ctx: ExecutionContext, doc_id: Uuid) -> Sequence[str]:
        """This document's chunk text in ``seq`` order (BE-RAG-009).

        The summarisation pipeline's input. It reads the chunks rather than
        re-fetching and re-parsing the file because the parse already happened
        once, and because a summary built from anything other than the indexed
        text would describe a document no search can return.

        **P-42 (plan §4 step 18, §3.10): summarises from PARENT chunks, not
        leaves, wherever one exists AND holds what it parents.** A chunk
        whose parent is COMPLETE (``ParentChunk.is_complete``) is represented
        once by that parent's coarser text — several leaf rows collapse to
        the one section they came from (the plan's own "~40 coherent sections
        instead of ~240 fragments").

        Everything else falls back to its own leaf text, individually, with
        no dedup applied to it — the property that makes this a strict
        superset of the pre-step-18 behaviour: a document with no table
        (today, the common case) round-trips through this method exactly as
        before. That fallback covers a chunk with no parent AND a chunk whose
        parent is INCOMPLETE — P-13's header-only parent for a table past
        ``TABLE_PARENT_MAX_ROWS``, which holds the column names and not one
        value under them. Letting that stand in for its rows would summarise
        a data file as a list of headings, which is precisely the content
        loss this method's "no dedup" half exists to prevent. The rule itself
        is ``domain/tables.py::collapse_parent_runs``, so an adapter cannot
        implement half of it by accident.

        Ordered by ``seq``, which is the document's own reading order
        (``chunking.chunk_segments``) — a summary assembled out of order is a
        summary of a different document. A document with no chunks yields an
        empty sequence, which the caller turns into a failed job with a reason
        rather than an empty summary.

        No paging: a document's chunks are bounded by the file it came from,
        the caller consumes all of them, and a cursor here would only be a
        place for the reading order to get lost.
        """
        ...

    async def parent_chunk_texts(self, ctx: ExecutionContext, doc_id: Uuid) -> Sequence[str]:
        """This document's parent-chunk text, in ``seq`` order (P-14, plan
        §3.2): the ``chunk_texts`` contract, over the coarser-grained table.

        Read by the retrieval half's parent-widening lookup (``chunk_id ->
        chunks.parent_id -> parent_chunks``, rag-retrieval-plan.md §3.7
        `P-34`), not by ``chunk_texts`` -- that method resolves each chunk's
        own parent directly with a JOIN (plan step 18, `P-42`) rather than
        reading every one of a document's parents up front and matching them
        back up itself. A document with no parent chunks yields an empty
        sequence rather than an error — a normal answer, and a RARER one than
        it was: the table exploder (P-13) was the only writer of this table
        until `P-34` gave prose a page parent of its own
        (``application/indexing.py::_attach_text_parents``), so coming back
        empty now takes a document with no table AND no prose.
        """
        ...

    async def parent_texts_for_chunk_ids(
        self, ctx: ExecutionContext, chunk_ids: Sequence[Uuid]
    ) -> Mapping[Uuid, ParentChunkText]:
        """The parent-widening lookup itself (retrieval plan §3.7, ``P-34``):
        for every id in ``chunk_ids`` that names a chunk WITH a parent under
        this tenant, the resolved ``ParentChunkText``.

        ``chunk_ids`` are Qdrant POINT ids -- ``RetrievedChunk.chunk_id`` /
        ``chunks.point_id`` -- and NEVER ``chunks.id`` (this port's own
        module docstring, and ``domain/collections.py::chunk_point_id``): the
        join this method performs internally is
        ``chunks.point_id -> chunks.parent_id -> parent_chunks``, one round
        trip for the whole batch rather than one per candidate.

        A chunk id with no parent (``chunks.parent_id IS NULL`` -- ordinary
        prose, or a chunk indexed before P-14), or one that simply is not
        found (predates this feature entirely, or names a chunk this tenant
        cannot see), is silently ABSENT from the result -- never an error,
        never a placeholder entry keyed to ``None``. The caller's
        ``.get(chunk_id)`` is the one honest way to ask "does this chunk have
        a parent to widen to", and its own fallback (this chunk's OWN text)
        is what makes that degrade honestly rather than drop the chunk.

        ⚠️ **A resolved parent is not automatically a substitutable one.**
        The mapping carries ``ParentChunkText.is_complete`` because the
        widening caller must read it before letting a parent stand in place
        of its rows (``ChunkParent``'s docstring makes that binding on every
        such consumer, and ``chunk_texts`` above obeys it for ``P-42``): an
        INCOMPLETE parent is the header-only row for a table past
        ``TABLE_PARENT_MAX_ROWS``, and substituting it would replace the
        retrieved passage with a string that does not contain it. This method
        returns the row either way — deciding is the caller's, and hiding
        incomplete parents here would silently make them unresolvable rather
        than un-substitutable.

        An empty ``chunk_ids`` returns an empty mapping without a query --
        the ``add_parent_chunks`` precedent, applied to a read.
        """
        ...

    async def purge(self, ctx: ExecutionContext, doc_id: Uuid) -> None:
        """Destroy a document: its ``summaries``, its ``reindex_job_items``,
        then its ``chunks`` rows, then its own row (BE-RAG-007 · INV-K4), in
        ONE transaction — the missing ``ON DELETE`` on ``fk_chunk_doc``/
        ``fk_summary_doc``/``fk_reindex_item_doc`` makes the order mandatory
        rather than stylistic.

        **``reindex_job_items`` is part of the contract, not an FK footnote.**
        The only caller is a re-index, so the document being destroyed is
        routinely one an EARLIER re-index minted — and that document is named
        by that earlier job's item row. Leaving the row makes the SECOND
        re-index of any file a ``23503``. Deleting it hides nothing: the job
        read joins ``documents`` to derive every number it reports (INV-K5),
        so an item whose document is gone had already dropped out of the
        job's view.

        Summaries are deleted here and not cascaded for the reason the whole
        destruction is explicit: re-indexing exists because a file's text may
        now parse differently, and a summary that outlived the text it was
        written from would go on being served as current. A cascade would do
        the same deletion while hiding it from the invariant that documents
        it.

        Two rows are deliberately LEFT behind, and both times because the
        caller is a re-index that keeps the file: a ``summary_jobs`` row
        (nothing references it, and the queued build wants to find the
        replacement), and the ``reindex_jobs`` row once this was its last
        item. ``purge_space``/``purge_file`` take both, because a job emptied
        by a corpus-wide cascade can never report on anything again; here an
        item-less job is a defined answer (``percent`` 100, ``completed``)
        over content that still exists under a new document id.

        The only hard delete in this module, and it exists because a
        superseded document cannot be allowed to survive: Qdrant point ids
        derive from the document id, so two live documents over one file
        would answer every search twice. A soft delete would not help — the
        vector store has no view of a ``deleted_at`` column.

        Purging a document that is not there is a no-op, not an error: the
        caller has already read it, and losing a race to another purge is the
        outcome it wanted anyway.
        """
        ...

    async def vector_refs_in_space(
        self, ctx: ExecutionContext, space_id: Uuid
    ) -> Sequence[VectorRef]:
        """Where EVERY chunk of one space's documents lives in the vector
        store — step 2 of the cascade (``docs/spaces-backend-plan.md`` §3.6,
        step 11).

        ``vector_refs`` above, widened from one document to a space, and for
        the same reason: what must be deleted is what was actually WRITTEN, and
        only the ``chunks`` rows know that. Recomputing the ids from
        ``chunk_point_id(doc_id, seq)`` would miss precisely the points a
        half-finished indexing run left behind, which are the ones a cascade
        most needs to reach.

        Read BEFORE ``purge_space``: afterwards nothing remains to say which
        points were the space's, and a point that outlives its row is
        unreachable from Postgres yet still answers searches (retrieval filters
        on the payload and never joins the database).
        """
        ...

    async def purge_space(self, ctx: ExecutionContext, space_id: Uuid) -> int:
        """HARD-delete one space's whole corpus; returns how many DOCUMENTS
        went (step 4 of the cascade, §3.6).

        ``purge`` above, widened from one document to a space — and widened in
        what it reaches, not only in how many rows it touches. ``purge`` may
        leave a ``summary_jobs`` row behind (nothing references it, so nothing
        breaks); here that row would outlive the space it describes, so this
        takes it too, along with the ``reindex_job_items`` whose
        ``fk_reindex_item_doc`` would otherwise refuse the document deletion
        outright.

        **A ``reindex_jobs`` row is deleted only once it has no items left.**
        A job is a request, not content, and one request may name documents
        from two spaces; deleting the job with the first space would erase the
        other space's progress view. Emptied jobs do go — a job with no items
        reports on nothing and can never gain another.

        Deleting nothing is a no-op: the cascade must be re-runnable (§3.6).
        """
        ...

    async def vector_refs_for_file(
        self, ctx: ExecutionContext, file_id: Uuid
    ) -> Sequence[VectorRef]:
        """Where EVERY chunk of EVERY document built from one file lives in
        the vector store — step 2 of the file cascade
        (``framework/di/file_deletion.py``).

        ``vector_refs_in_space`` narrowed from a space to a single file, and it
        exists for that method's reason rather than as a convenience over
        ``ids_for_files`` + ``vector_refs``: the ids must stay inside the
        database, so the corpus this method collects and the corpus
        ``purge_file`` destroys a moment later are defined by the same
        predicate and cannot be split by a re-index landing between the two.

        **Every document of the file, not the newest one.** A re-index leaves
        the replacement pointing at the same ``file_id``, and a half-finished
        one can leave two — exactly the "الملفّ مفهرَس مرّتين" shape a delete has
        to be able to clear. Reading them all is what makes this the LAST
        thing that can be said about that file's presence in the index.

        A file with nothing indexed yields an empty sequence — a normal
        answer (never indexed, or indexed and already purged), not an error.

        Read BEFORE ``purge_file``, for ``vector_refs_in_space``'s reason: a
        point that outlives its row is unreachable from Postgres yet still
        answers searches, and retrieval filters on the payload without ever
        joining the database.
        """
        ...

    async def purge_file(self, ctx: ExecutionContext, file_id: Uuid) -> int:
        """HARD-delete every document built from one file; returns how many
        DOCUMENTS went (step 3 of the file cascade).

        ``purge_space`` narrowed to one file, reaching the same tables in the
        same FK order — summaries, ``summary_jobs``, ``reindex_job_items``,
        chunks, documents, then childless ``reindex_jobs``. Narrowing
        ``purge`` (one document) instead would have been the smaller change
        and the wrong one twice over: it would leave a ``reindex_job_items``
        row referencing a document it is about to delete (``fk_reindex_item_doc``
        carries no ``ON DELETE``, so deleting a re-indexed file would be a
        ``23503``), and it would run one transaction per document, which for a
        file with a superseded document and its replacement is the one moment
        the two must go together.

        **Hard, not soft, and this is not a preference.** ``purge``'s docstring
        argues it for a superseded document and the argument is the same one
        here: Qdrant point ids derive from the document id and the vector store
        has no view of a ``deleted_at`` column, so a corpus "hidden" by a flag
        goes on answering every search. There is no half-measure available at
        this boundary.

        Deleting nothing is a no-op: a file that was never indexed is the
        common case on this path, and the cascade must be re-runnable.
        """
        ...


class ReindexJobRepository(Protocol):
    """Tenant-scoped persistence for the ``ReindexJob`` aggregate + its item
    rows (02 §2 · BE-RAG-007/008).

    A separate port from ``DocumentRepository`` even though both adapters
    speak to the ``knowledge`` schema: the two aggregates have separate
    lifetimes, and a caller that only reads a job's progress has no business
    holding a handle that can purge a document.
    """

    async def add(self, ctx: ExecutionContext, job: ReindexJob) -> None:
        """Persist a job and its items together. Called inside the caller's
        unit of work, alongside the new documents the items point at — an
        item whose document was rolled back would report a status nobody can
        read (``fk_reindex_item_doc`` turns that into a loud failure rather
        than a dangling row)."""
        ...

    async def get(self, ctx: ExecutionContext, job_id: Uuid) -> ReindexJob | None:
        """One job with its items, each item's ``status`` **joined from
        ``knowledge.documents``** rather than stored (INV-K5).

        That join is the whole progress mechanism: no counter is maintained
        anywhere, so none can drift, and a worker that dies mid-run leaves the
        job reporting exactly what the corpus says. Another tenant's job is
        indistinguishable from a missing one (``None`` either way).
        """
        ...

    async def mark_cancelled(self, ctx: ExecutionContext, job_id: Uuid, at: datetime) -> None:
        """Stamp ``cancelled_at``. Writes unconditionally — the aggregate has
        already decided whether cancelling changes anything, and a job that
        was cancelled twice never reaches here a second time."""
        ...


class SummaryRepository(Protocol):
    """Tenant-scoped persistence for the ``Summary`` aggregate (02 §2 ·
    BE-RAG-009/010/011)."""

    async def get(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> Summary | None:
        """The stored summary under this exact key, or ``None``.

        The whole triple is the key — a request for the Arabic overview does
        not fall back to the English one, or to the full summary, because a
        fallback here would answer a question the caller did not ask and label
        it as the answer to the one they did.
        """
        ...

    async def newest_in_other_language(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> Summary | None:
        """The most recently built summary of this document and kind in ANY
        language OTHER than ``lang``, or ``None`` (plan step 20, `P-44`).

        The one read that makes ``SummarizeDocument.translate`` reachable: a
        build asked for in a language nothing is stored under can be answered
        by translating a few kilobytes of already-reduced Markdown instead of
        mapping and reducing the whole document a second time.
        ``BuildSummary.claim`` is the caller, and the rule for WHEN
        translating is allowed lives there rather than here — this method
        only answers "is there one in another language", never "should you
        use it".

        This does not contradict ``get``'s no-fallback rule. ``get`` answers
        a READ, where handing back another language would mislabel the
        answer; this feeds a BUILD, whose whole job is to produce the
        requested language and which stores its result under the requested
        key like any other build.

        "Most recently built" (``built_at``, then ``id`` to break a tie
        deterministically — ids are UUIDv7, so the later row wins) is a
        choice rather than an accident of row order: with two other languages
        stored, the newest is the one whose wording the workspace most
        recently accepted, and a caller must never get a different source
        from one claim to the next for the same unchanged corpus.
        """
        ...

    async def upsert(self, ctx: ExecutionContext, summary: Summary) -> None:
        """Store a summary, REPLACING any previous one under the same key
        (``uq_summary_key``).

        Replace and not insert-if-absent: a rebuild is the reason this method
        is ever called twice, and its whole purpose is that the newer text
        wins. The previous body is not versioned anywhere — a summary is
        derived data that can be rebuilt from a corpus that still exists,
        which is precisely what makes keeping its history storage spent on
        something nobody would read.
        """
        ...

    async def delete(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> bool:
        """Delete one stored summary; returns whether a row was there.

        The boolean is the contract's, not a convenience: BE-RAG-011's delete
        is idempotent, and the caller answers "deleted: false" for a summary
        that was already gone rather than 404 — the client asked for a state,
        and that state now holds.
        """
        ...

    async def delete_for_document(self, ctx: ExecutionContext, document_id: Uuid) -> None:
        """Delete every summary of a document — ``purge``'s first step
        (INV-K4). Deleting none is a no-op, not an error."""
        ...


class SummaryJobRepository(Protocol):
    """Tenant-scoped persistence for the ``SummaryJob`` aggregate (02 §2 ·
    BE-RAG-009/011).

    A separate port from ``SummaryRepository`` for the reason
    ``ReindexJobRepository`` is separate from ``DocumentRepository``: a caller
    watching a build's progress has no business holding a handle that can
    overwrite the artefact.
    """

    async def add(self, ctx: ExecutionContext, job: SummaryJob) -> None:
        """Queue a job. Racing another queued/running build of the same
        ``(document_id, kind, lang)`` violates ``uq_summary_job_active`` and
        must surface as a conflict rather than a second paid-for build — see
        ``active_for``, which is the check that turns the race into a 409 in
        the ordinary case, and this constraint, which catches the two that
        checked at the same instant."""
        ...

    async def get(self, ctx: ExecutionContext, job_id: Uuid) -> SummaryJob | None:
        """One job, or ``None`` — another tenant's is indistinguishable from
        a missing one."""
        ...

    async def active_for(
        self,
        ctx: ExecutionContext,
        document_id: Uuid,
        kind: SummaryKind,
        lang: SummaryLanguage,
    ) -> SummaryJob | None:
        """The queued/running job for this key, if one exists."""
        ...

    async def count_active(self, ctx: ExecutionContext) -> int:
        """How many queued/running jobs this workspace holds, over every
        document and every key (ب-10, gap ف-7).

        The whole-workspace counterpart to ``active_for``, reading the same
        two statuses: a job counted here is exactly a job that could collide
        there. Keeping the two definitions of "active" identical is the point
        of saying so — a cap that counted a wider set than the key check would
        refuse builds for jobs the key check considers finished.

        A count rather than a list. The caller compares it with a ceiling and
        has no use for a row, and the rows it would otherwise load are other
        documents' builds it has no business holding in memory.
        """
        ...

    async def save(self, ctx: ExecutionContext, job: SummaryJob) -> None:
        """Persist the job's mutable state: status, progress, error and the
        two instants.

        A whole-row write rather than a set of targeted mutators. The
        aggregate is the only thing that decides these fields, it decides
        several of them in one transition (``succeed`` moves status,
        ``done_chunks`` and ``finished_at``), and a port with one method per
        column would let a caller persist half a transition.

        Called at exactly two points — the worker's claim and its terminal
        write — never for progress. See ``record_progress`` for why that
        separation is load-bearing rather than tidy.
        """
        ...

    async def record_progress(self, ctx: ExecutionContext, job_id: Uuid, done_chunks: int) -> None:
        """Write ONLY ``done_chunks``, and only while the row still says
        ``running``.

        This exists because ``save`` cannot be used here. A worker holds its
        job in memory for the whole build; a cancellation arrives as a write
        to the ROW by another process. If progress were persisted with a
        whole-row ``save``, the next map step would write that in-memory
        job — still ``running``, because nothing told it otherwise — straight
        over the cancellation, and the job would resurrect itself seconds
        after the user stopped it.

        The ``WHERE status = 'running'`` guard is what makes that structurally
        impossible rather than a timing question: after a cancellation the
        progress write matches no row and silently does nothing, which is
        exactly what a cancelled job's progress should do.
        """
        ...
