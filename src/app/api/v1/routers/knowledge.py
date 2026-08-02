"""The Knowledge router — ``/api/v1/knowledge`` (03-api-spec §1 · 06 §7)
— Phase 6.1-و-3.

Three routes over ``KnowledgeUseCases``, exactly 03 §1's ``POST /search ·
GET /documents · GET /documents/{id}``:

* ``POST /knowledge/search`` — top-``k`` chunks for a query (API-04 envelope);
* ``GET /knowledge/documents`` — this workspace's documents, every status;
* ``GET /knowledge/documents/{id}`` — one document's ingestion state.

**Reads only, and that is the whole module's shape here.** Nothing in this
router creates or indexes a document: registration is driven by a file's
upload completing, and indexing is a worker's (06 §7). The bundle
``ApiServices`` holds carries neither ingestion face, so «a request cannot
start a pipeline» is structural rather than a route someone remembered not to
add.

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

from fastapi import APIRouter, Depends

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.knowledge import DocumentOut, KnowledgeSearchIn, RetrievedChunkOut
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.framework.errors import AppError
from app.modules.access.domain.value_objects import Permission
from app.modules.knowledge.domain.entities import Document

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


@router.post("/search", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def search_knowledge(
    body: KnowledgeSearchIn, services: Services, ctx: Context
) -> Page[RetrievedChunkOut]:
    """Top-``k`` relevant chunks for ``query``, within this workspace.

    Wrapped in the API-04 envelope with ``next_cursor: null``: ``k`` is the
    bound, and "the next 5 most relevant" is not a thing a cursor can mean —
    a client that wants more asks for a larger ``k``.

    503 while the embedding adapter is missing (module docstring).
    """
    retrieval = services.knowledge.search
    if retrieval is None:
        raise AppError(
            "knowledge search is not available on this deployment", code=_SEARCH_UNAVAILABLE
        )
    chunks = await retrieval.retrieve(ctx, body.query, body.k)
    data = [
        RetrievedChunkOut(
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            score=chunk.score,
        )
        for chunk in chunks
    ]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.get("/documents", dependencies=[Depends(require(Permission.KNOWLEDGE_READ))])
async def list_documents(
    services: Services, ctx: Context, limit: Limit = DEFAULT_LIMIT, cursor: Cursor = None
) -> Page[DocumentOut]:
    """The workspace's documents, newest first, every lifecycle status —
    cursor-paginated (6.3-ب).

    The only listing here that pages: a corpus grows by a row per completed
    upload with no ceiling in the design, while ``POST /search`` is bounded
    by its own ``k`` and has no stable order a cursor could name.
    """
    page = await services.knowledge.list_documents.execute(ctx, limit=limit, cursor=cursor)
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
