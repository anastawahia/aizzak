"""The Knowledge router — ``/api/v1/knowledge`` (03-api-spec §1 · 06 §7)
— Phase 6.1-و-3.

Twelve routes over ``KnowledgeUseCases``:

* ``POST /knowledge/search`` — top-``k`` chunks for a query (API-04 envelope);
* ``GET /knowledge/documents`` — this workspace's documents, every status;
* ``GET /knowledge/documents/{id}`` — one document's ingestion state;
* ``POST /knowledge/documents`` — index an uploaded file (**202**);
* ``POST /knowledge/reindex`` — rebuild one or more documents (**202**);
* ``GET /knowledge/reindex/{id}`` — that rebuild's progress;
* ``POST /knowledge/reindex/{id}/cancel`` — stop it;
* ``POST /knowledge/documents/{id}/summary`` — build a summary (**202**);
* ``GET /knowledge/documents/{id}/summary`` — the stored one, or 404;
* ``DELETE /knowledge/documents/{id}/summary`` — discard it;
* ``GET /knowledge/summary-jobs/{id}`` — a build's progress;
* ``POST /knowledge/summary-jobs/{id}/cancel`` — stop it.

**The summary five (BE-RAG-009/010/011) keep the same line the rest do.** A
request may ASK for a summary; it cannot produce one. ``POST`` queues a job
and publishes ``knowledge.summary.requested.v1``, and a worker decides when to
run — exactly what ``reindex`` does for ingestion. The one asymmetry is
deliberate: ``GET``/``DELETE`` here are shaped by the (document, kind, lang)
key rather than a job id, because the artefact and the operation that built it
are different things with different lifetimes.

**The three reads were the whole router until BE-RAG-007/008, and the line
they drew still holds.** Nothing here indexes a document: indexing is a
worker's (06 §7). The bundle carries no pipeline face, so «a request cannot
start a pipeline» is structural rather than a route someone remembered not to
add — and neither ``POST /documents`` nor ``reindex`` is the exception it
looks like, because all either does is register a document and publish the
``DocumentRegistered`` event a worker acts on. Asking for ingestion was always
allowed; performing it still is not.

**What DID change is who does the asking.** Registration used to be driven by
a file's upload completing: the knowledge worker consumed
``files.file.uploaded.v1`` and the corpus grew by itself. It no longer
subscribes, so an uploaded file is bytes in storage and nothing more until
``POST /knowledge/documents`` is called for it — one explicit request per
file, from the person who uploaded it. The cost is stated rather than hidden:
**a file nobody indexes answers no search, indefinitely, and the API says so
only through that file's absence from ``GET /knowledge/documents``.**

**``POST /search`` is built, wired, and served.** ``KnowledgeRetrievalService``
needs an ``EmbeddingProvider``, which had no adapter when this router was
written (a Phase-2 scheduling gap, not a decision taken here) — so the
Composition Root passed ``search=None`` and this route answered **503
``knowledge.search_unavailable``**. 2.10 filled that field
(``composition_root.py`` builds a REAL ``KnowledgeRetrievalService`` over
``ExternalEmbeddingProvider`` + Qdrant, ``docs/log/3.77.md``) and the branch
simply stopped firing, exactly as predicted here.

**The 503 branch stays anyway, and is not dead code.** ``search`` is still
``| None`` on the bundle: a deployment can be composed without it (the unit
suites do precisely that), and the alternative — not registering the route
when the field is empty — would have FastAPI answer 404, which says "no such
capability" about a capability the contract defines and the code implements.
503 says the true thing: it exists, this deployment cannot serve it. What
changed is only that no supported deployment configuration reaches it today.

**``knowledge.not_indexed`` (409, 03 §4) has no site among these three.** It
describes retrieving against something not yet indexed, and none of the three
routes is per-document retrieval: search spans the whole indexed corpus (an
empty corpus yields an empty result, not an error), and both document reads
answer for every status by design. Forcing the code onto "search found
nothing" would turn a legitimate empty result into a failure. Left to 6.2's
catalog pass, alongside ``credentials.none_available`` — the same kind of
entry whose real site is elsewhere.

**Auth on every route** via the router-level ``current_principal`` dependency
(03 §0); RBAC guards are 6.4's, like every other router's.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Response

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.knowledge import (
    DocumentOut,
    ExportFormatIn,
    IndexFileIn,
    KnowledgeSearchIn,
    ReindexIn,
    ReindexItemOut,
    ReindexJobOut,
    RetrievedChunkOut,
    SummaryDeletedOut,
    SummaryIn,
    SummaryJobOut,
    SummaryKindIn,
    SummaryLangIn,
    SummaryOut,
)
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.api.v1.idempotency import IdempotencyKey, idempotent
from app.framework.errors import AppError
from app.modules.access.domain.value_objects import Permission
from app.modules.knowledge.domain.entities import Document, ReindexJob, Summary, SummaryJob
from app.modules.knowledge.domain.value_objects import SummaryKind, SummaryLanguage
from app.modules.knowledge.ports.export import ExportFormat

# Folded into `ERROR_CATALOG` by 6.2, which is also where the 503 now comes
# from — this raise passes a code and no status. §3.64 minted the code here
# because 03 §4 had no entry for "this capability is not deployed"; the
# catalog pass adopted it rather than replace it.
_SEARCH_UNAVAILABLE = "knowledge.search_unavailable"

# Belt and braces, knowingly (the §3.56 precedent): every route builds `ctx`.
router = APIRouter(
    prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(current_principal)]
)


def _to_document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        file_id=document.file_id,
        status=document.status.value,
        chunk_count=document.chunk_count,
        created_at=document.created_at,
    )


def _to_job_out(job: ReindexJob) -> ReindexJobOut:
    current = job.current
    return ReindexJobOut(
        id=job.id,
        status=job.status.value,
        total=len(job.items),
        finished=job.finished,
        percent=job.percent,
        current_file_id=None if current is None else current.file_id,
        items=[
            ReindexItemOut(
                document_id=item.document_id,
                file_id=item.file_id,
                source_document_id=item.source_document_id,
                status=item.status.value,
            )
            for item in job.items
        ],
        created_at=job.created_at,
        cancelled_at=job.cancelled_at,
    )


# The DOCX media type, spelled once (it is long enough that a second copy
# would eventually differ by a character nobody notices until a download
# opens in the wrong application).
_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# What a download may be called. The client sends the file's own name so the
# saved document is recognisable, and this is the guard on it: an export
# filename ends up in a `Content-Disposition` header and then on someone's
# disk, so path separators, control characters and unbounded length are all
# refused rather than escaped. 120 characters is well under every filesystem's
# limit once the extension is added.
_MAX_FILENAME = 120
_FILENAME_FORBIDDEN = ("/", "\\", "\r", "\n", "\0", '"')
_FALLBACK_FILENAME = "summary"
# Below this codepoint everything is a C0 control character, and a header
# value is exactly where those do damage.
_FIRST_PRINTABLE = 32


def _export_title(filename: str) -> str:
    """The heading printed at the top of the exported document.

    The file's own name, cleaned — which is what the person exporting expects
    to see on the page, and is why the client sends it rather than the server
    inventing "Summary of document 0195…".
    """
    cleaned = _clean_filename(filename)
    # A name like `report.pdf` becomes `report`: the extension describes the
    # SOURCE file and would read as nonsense on the first line of a PDF.
    stem, _, _ = cleaned.rpartition(".")
    return stem or cleaned


def _export_filename(filename: str, fmt: str) -> str:
    return f"{_export_title(filename)}.{fmt}"


def _clean_filename(filename: str) -> str:
    """Reject rather than escape. A name that has to be rewritten to be safe
    is a name the client should not have sent, and quietly repairing it means
    the file saves under something the user did not choose."""
    candidate = filename.strip()
    if (
        not candidate
        or len(candidate) > _MAX_FILENAME
        or any(char in candidate for char in _FILENAME_FORBIDDEN)
        or any(ord(char) < _FIRST_PRINTABLE for char in candidate)
    ):
        return _FALLBACK_FILENAME
    return candidate


def _to_summary_out(summary: Summary) -> SummaryOut:
    return SummaryOut(
        id=summary.id,
        document_id=summary.document_id,
        kind=summary.kind.value,
        lang=summary.lang.value,
        text=summary.text,
        model=summary.model,
        source_chunks=summary.source_chunks,
        truncated=summary.truncated,
        built_at=summary.built_at,
    )


def _to_summary_job_out(job: SummaryJob) -> SummaryJobOut:
    return SummaryJobOut(
        id=job.id,
        document_id=job.document_id,
        kind=job.kind.value,
        lang=job.lang.value,
        status=job.status.value,
        total_chunks=job.total_chunks,
        done_chunks=job.done_chunks,
        percent=job.percent,
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
        cancelled_at=job.cancelled_at,
    )


@router.post("/search", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def search_knowledge(
    body: KnowledgeSearchIn, services: Services, ctx: Context
) -> Page[RetrievedChunkOut]:
    """Top-``k`` relevant chunks for ``query``, within ONE space.

    Wrapped in the API-04 envelope with ``next_cursor: null``: ``k`` is the
    bound, and "the next 5 most relevant" is not a thing a cursor can mean —
    a client that wants more asks for a larger ``k``.

    ``space_id`` is required on the BODY (س-32), not a query parameter like the
    three listings': this is a POST whose whole input is one object, and
    splitting one of its three narrowings out onto the URL would leave a client
    stating the search in two places.

    503 while the embedding adapter is missing (module docstring).
    """
    retrieval = services.knowledge.search
    if retrieval is None:
        raise AppError(
            "knowledge search is not available on this deployment", code=_SEARCH_UNAVAILABLE
        )
    chunks = await retrieval.retrieve(
        ctx,
        body.query,
        body.k,
        # ✅ س-32, CLOSED by owner decision 2026-08-26 (docs/rag-fidelity-audit.md
        # §4-هـ-3). This line read `space_id=None` from step 8 until then — the
        # ONE caller in the system that crossed the space boundary, carried as
        # a documented deferral because §3.7 had scheduled `?space_id=` onto the
        # three listings and not onto this body.
        #
        # What ended the deferral was not the schedule but a measurement: the
        # audit's "51% of the context budget on one duplicated page" was taken
        # through this route and attributed to product behaviour, then had to be
        # withdrawn — the two copies live in two different spaces and no thread
        # can ever see them together (within each space the corpus holds zero
        # duplicate-text groups). An endpoint that breaks the isolation produces
        # measurements nobody lives.
        #
        # The body now carries the space and the module refuses a call without
        # one, so this route can no longer be that endpoint.
        space_id=body.space_id,
    )
    data = [
        RetrievedChunkOut(
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            score=chunk.score,
            file_name=chunk.file_name,
            page_number=chunk.page_number,
            section=chunk.section,
        )
        for chunk in chunks
    ]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.get("/documents", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def list_documents(
    services: Services,
    ctx: Context,
    space_id: str,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> Page[DocumentOut]:
    """ONE space's documents, newest first, every lifecycle status —
    cursor-paginated (6.3-ب).

    The only listing here that pages: a corpus grows by a row per completed
    upload with no ceiling in the design, while ``POST /search`` is bounded
    by its own ``k`` and has no stable order a cursor could name.

    ``space_id`` is a REQUIRED query parameter (§3.7, step 12). A document's
    space is its FILE's, carried on the ``files.file.uploaded.v1`` envelope
    rather than asked for at ingest time (step 8) — so this narrowing lists
    exactly the documents whose files the sibling ``GET /files?space_id=``
    lists, which is the only way the two views can agree.

    ⚠️ **Documents indexed before the plan carry no space and appear in no
    space's listing.** That is §5-أ's consequence, and the answer is §5-أ's
    too: re-index through ``POST /knowledge/reindex``. Falling back to "or has
    no space" would leak every workspace's pre-plan corpus into every space
    created after it.
    """
    page = await services.knowledge.list_documents.execute(
        ctx, space_id=space_id, limit=limit, cursor=cursor
    )
    return Page(
        data=[_to_document_out(document) for document in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.get("/documents/{document_id}", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def get_document(document_id: str, services: Services, ctx: Context) -> DocumentOut:
    """One document's ingestion state. Unknown or another tenant's ⇒ 404
    (the §3.55 read precedent — 403 would confirm the id exists)."""
    document = await services.knowledge.get_document.execute(ctx, document_id=document_id)
    return _to_document_out(document)


@router.post(
    "/documents", status_code=202, dependencies=[Depends(require(Permission.KNOWLEDGE_MANAGE))]
)
async def index_file(
    body: IndexFileIn,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> DocumentOut:
    """Index an uploaded file — **202**, like every other route that queues a
    worker's work. Ordinarily the body answers with the ``pending`` document
    that was just registered, not with an indexed one: 201 would promise a
    corpus entry that does not exist until a worker has embedded it.

    This is the route that replaced automatic ingestion. A file is indexed
    because somebody asked for this, once, and never because it finished
    uploading.

    **The refusals are the interesting part**, and both are the use-case's:

    * a file that is not ``ready`` — still uploading, quarantined, deleted, or
      simply not this tenant's — is a **404**, indistinguishable between those
      causes on purpose (``ports/files.ReadableFiles``). It is also what makes
      "wait for the upload to finish" enforced rather than merely advised.
    * a file that already has a document is a **409**. Not because a second
      one cannot be minted — INV-K3 says it can — but because two live
      documents over one file make every search answer from it twice. Rebuild
      through ``POST /knowledge/reindex``, which destroys what it replaces.

    **One exception to both of those, added by plan step 15 (§3.6, decision
    س-14 = أ):** a file whose document is ALREADY indexed under today's
    ``PIPELINE_VERSION`` is neither a 409 nor a fresh registration. It answers
    **202 with that indexed document** — the same status, a different body
    shape than the paragraph above describes, and no work queued. The reply
    is honest either way: the client asked for this file to be in the corpus,
    and it already is, in exactly the shape re-indexing it would produce. A
    409 there would refuse a request that was already satisfied, and re-doing
    the embeddings would spend the workspace's budget to arrive at identical
    rows. The moment a parser changes, ``PIPELINE_VERSION`` is raised and the
    same call is a 409 again, pointing at ``reindex`` as before.

    ``Idempotency-Key`` is accepted for the reason it is on ``reindex``: a
    retried POST otherwise buys a second document, and the workspace pays for
    the same embeddings twice. The 409 above already stops the *double click*;
    the header stops the *retried request*, which never reaches the guard
    because the first attempt may have committed after the client gave up.
    """

    async def _start() -> DocumentOut:
        document = await services.knowledge.index_file.start(ctx, file_id=body.file_id)
        return _to_document_out(document)

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /knowledge/documents",
        key=idempotency_key,
        body=body,
        model=DocumentOut,
        run=_start,
    )


@router.post(
    "/reindex", status_code=202, dependencies=[Depends(require(Permission.KNOWLEDGE_MANAGE))]
)
async def reindex_documents(
    body: ReindexIn,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> ReindexJobOut:
    """Rebuild the index for one or more documents — **202**, like every other
    route that queues a worker's work (``POST /media/jobs``): 201 would
    promise a finished rebuild that does not exist yet.

    A replay of the same ``Idempotency-Key`` returns the job AS SUBMITTED, and
    this is the route where that matters most in the whole API: without it a
    retried POST destroys and rebuilds a second time, and the workspace pays
    for the embeddings twice. ``GET /knowledge/reindex/{id}`` is where the
    current state lives.
    """

    async def _start() -> ReindexJobOut:
        job = await services.knowledge.reindex.start(ctx, document_ids=body.document_ids)
        return _to_job_out(job)

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint="POST /knowledge/reindex",
        key=idempotency_key,
        body=body,
        model=ReindexJobOut,
        run=_start,
    )


@router.get("/reindex/{job_id}", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def get_reindex_job(job_id: str, services: Services, ctx: Context) -> ReindexJobOut:
    """One job's progress, derived from its documents at read time. Unknown
    or another tenant's ⇒ 404.

    ``knowledge:read`` and not ``knowledge:manage``: watching a rebuild is
    reading, and a member who can see the corpus can see what is happening to
    it — only starting and stopping one is privileged.
    """
    job = await services.knowledge.get_job.execute(ctx, job_id=job_id)
    return _to_job_out(job)


@router.post(
    "/reindex/{job_id}/cancel", dependencies=[Depends(require(Permission.KNOWLEDGE_MANAGE))]
)
async def cancel_reindex_job(job_id: str, services: Services, ctx: Context) -> ReindexJobOut:
    """Stop a running rebuild, and answer with the job as it now stands.

    ``POST .../cancel`` rather than ``DELETE .../{id}``: nothing is deleted —
    the job stays readable, which is the whole point of cancelling one rather
    than forgetting it. Cancelling twice is 200 and writes nothing; a job with
    nothing left to stop is 409.
    """
    job = await services.knowledge.cancel_job.cancel(ctx, job_id=job_id)
    return _to_job_out(job)


@router.post(
    "/documents/{document_id}/summary",
    status_code=202,
    dependencies=[Depends(require(Permission.KNOWLEDGE_MANAGE))],
)
async def build_summary(
    document_id: str,
    body: SummaryIn,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> SummaryJobOut:
    """Build this document's summary — **202**, like every other route that
    queues a worker's work.

    ``POST`` always builds; there is no ``force``. Reading what is already
    stored is ``GET`` on this same path, and a flag that meant "actually I
    wanted the GET" would be a route wearing a boolean's clothes. So this is
    "summarise" and "rebuild" at once, and the price is stated rather than
    hidden: a ``full`` summary maps over the whole document, which is the most
    expensive single call the API makes.

    Which is why ``Idempotency-Key`` matters here as much as it does on
    ``POST /knowledge/reindex``: a retried POST otherwise buys the same
    summary twice. A build already queued or running for this exact
    ``(document, kind, lang)`` is a **409** — the reply says a build is under
    way and names its job, rather than starting a second one that would race
    the first to the same row.

    ``knowledge:manage`` and not ``knowledge:read``: this spends the
    workspace's model budget.
    """

    async def _start() -> SummaryJobOut:
        job = await services.knowledge.request_summary.start(
            ctx,
            document_id=document_id,
            kind=SummaryKind(body.kind),
            lang=SummaryLanguage(body.lang),
        )
        return _to_summary_job_out(job)

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint=f"POST /knowledge/documents/{document_id}/summary",
        key=idempotency_key,
        body=body,
        model=SummaryJobOut,
        run=_start,
    )


@router.get(
    "/documents/{document_id}/summary",
    dependencies=[Depends(require(Permission.KNOWLEDGE_READ))],
)
async def get_summary(
    document_id: str,
    kind: SummaryKindIn,
    lang: SummaryLangIn,
    services: Services,
    ctx: Context,
) -> SummaryOut:
    """The stored summary under this exact key, or **404** (BE-RAG-010).

    ``kind`` and ``lang`` are required rather than defaulted: together with
    the path they ARE the resource's identity, and defaulting half an identity
    on a read means a client can be handed the Arabic overview while believing
    it asked for the English full text.

    404 and not ``{has_summary: false}``: a summary either exists under a key
    or it does not, and a client that has to destructure an optional body to
    find out has been handed a status code's job.
    """
    summary = await services.knowledge.get_summary.execute(
        ctx,
        document_id=document_id,
        kind=SummaryKind(kind),
        lang=SummaryLanguage(lang),
    )
    return _to_summary_out(summary)


@router.delete(
    "/documents/{document_id}/summary",
    dependencies=[Depends(require(Permission.KNOWLEDGE_MANAGE))],
)
async def delete_summary(
    document_id: str,
    kind: SummaryKindIn,
    lang: SummaryLangIn,
    services: Services,
    ctx: Context,
) -> SummaryDeletedOut:
    """Delete one stored summary (BE-RAG-011). Idempotent: 200 either way,
    with ``deleted`` saying whether anything was there.

    Not **204**, which is what a delete usually answers here — the flag is the
    point. The UI says "deleted" or "there was nothing saved", and both are
    successes; a 204 would make the client guess which happened, and a 404 on
    the second call would turn a satisfied request into an error.

    ``knowledge:manage``: this destroys an artefact the workspace paid a model
    to write.
    """
    deleted = await services.knowledge.delete_summary.execute(
        ctx,
        document_id=document_id,
        kind=SummaryKind(kind),
        lang=SummaryLanguage(lang),
    )
    return SummaryDeletedOut(deleted=deleted)


@router.get(
    "/documents/{document_id}/summary/export",
    dependencies=[Depends(require(Permission.KNOWLEDGE_READ))],
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, _DOCX_TYPE: {}}}},
)
async def export_summary(
    document_id: str,
    kind: SummaryKindIn,
    lang: SummaryLangIn,
    format: ExportFormatIn,
    services: Services,
    ctx: Context,
    filename: str = "summary",
) -> Response:
    """Download the stored summary as a PDF or DOCX (BE-RAG-012).

    **A GET returning bytes, not a job.** The plan proposed a pollable export
    job with an optional synchronous path for small summaries; every summary
    IS small — the pipeline caps its reduce step — so the machinery a job
    needs (a row, an event, an object in storage, a presigned URL, a polling
    loop, an expiry policy) would all exist to manage work that finishes
    before the client's first poll. This is another representation of a
    resource that already exists, it changes nothing, and it can be retried
    freely, which is what GET means.

    The render runs in a worker thread, so one export never becomes every
    other request's latency (``ExportSummary``).

    ``knowledge:read`` and not ``knowledge:manage``: this produces no new
    artefact and spends no model budget — it is the same summary the `GET`
    above returns, in a format a person can file. A member who may read the
    summary may download it.

    No stored summary under the key ⇒ 404, from the same ``GetSummary`` the
    JSON read uses: there is one definition of "this summary exists".
    """
    rendered = await services.knowledge.export_summary.execute(
        ctx,
        document_id=document_id,
        kind=SummaryKind(kind),
        lang=SummaryLanguage(lang),
        fmt=ExportFormat(format),
        title=_export_title(filename),
    )
    return Response(
        content=rendered.content,
        media_type=rendered.content_type,
        headers={
            # `filename*=UTF-8''…` and not a bare `filename=`: the names here
            # are Arabic more often than not, and RFC 6266's plain form is
            # latin-1 only — a browser handed raw Arabic bytes there saves the
            # file as mojibake or drops the name entirely.
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{quote(_export_filename(filename, format))}"
            )
        },
    )


@router.get("/summary-jobs/{job_id}", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def get_summary_job(job_id: str, services: Services, ctx: Context) -> SummaryJobOut:
    """One build's progress. Unknown or another tenant's ⇒ 404.

    ``knowledge:read`` and not ``knowledge:manage``, the same line
    ``GET /reindex/{id}`` draws: watching is reading, and only starting or
    stopping is privileged.
    """
    job = await services.knowledge.get_summary_job.execute(ctx, job_id=job_id)
    return _to_summary_job_out(job)


@router.post(
    "/summary-jobs/{job_id}/cancel",
    dependencies=[Depends(require(Permission.KNOWLEDGE_MANAGE))],
)
async def cancel_summary_job(job_id: str, services: Services, ctx: Context) -> SummaryJobOut:
    """Stop a build, and answer with the job as it now stands.

    The response says ``cancelled`` the moment the row is stamped, which is
    when the decision was taken — not when the worker notices. A build already
    mid-provider-call finishes that call and stops at the next step boundary
    (``SummaryJob.cancel``); claiming otherwise would be reporting a stop that
    had not happened.

    Cancelling twice is 200 and writes nothing; a job that already finished is
    409. A previously stored summary is untouched either way: this abandons
    the build, not the artefact of the one before it.
    """
    job = await services.knowledge.cancel_summary_job.cancel(ctx, job_id=job_id)
    return _to_summary_job_out(job)
