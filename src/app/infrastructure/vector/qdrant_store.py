"""Qdrant adapter for the ``VectorStore``/``HybridVectorStore`` ports
(02-port-contracts §1.5, D-01). Phase 2.5.

Same split as ``cache/redis_cache.py`` (2.3) and ``storage/minio_storage.py``
(2.4): a factory builds the technology client from ``Settings`` (called only
by the Composition Root), and a thin adapter class implements the ports over
it (structural Protocol match -- no inheritance). One class serves both
``VectorStore`` and ``HybridVectorStore``: the hybrid methods are additive,
so a single ``QdrantVectorStore`` satisfies the narrower Protocol by
construction too (Interface Segregation, per the port module's own
docstring).

Dense-default / sparse-``"text"`` convention (mirrors the port docstring
exactly): every collection keeps Qdrant's **unnamed default vector** for the
dense leg, so plain ``VectorStore`` collections (``memory``) and the dense
half of a hybrid collection (``knowledge``) share identical ``search``/
``upsert``/``ensure_collection``/``delete`` code paths -- ``ensure_hybrid_
collection`` only *adds* a second, named sparse vector called ``"text"``.

Deferred IDF: ``ensure_hybrid_collection`` configures the ``"text"`` sparse
vector with Qdrant's server-side ``Modifier.IDF``. IDF re-weighting happens
inside Qdrant at index/query time, so every ``SparseVector.values`` crossing
this adapter's boundary (from ``knowledge``'s term-frequency pipeline) is
always a raw term frequency, never pre-weighted (see the port docstring).

Error policy (R6, the same precedent as every other adapter): EVERY driver
failure -- connection refused, timeout, a malformed request Qdrant rejects,
... -- folds into the 500-class ``common.internal``. There is still no
caller-branchable *exception* here (unlike 2.4's ``NoSuchKey`` ->
``NotFoundError`` split): nothing this adapter raises tells a caller "that
collection is missing".

The one exception to "every failure is a fault" is the READ path over a
collection that was never created. Collections here are provisioned lazily
at first write (``knowledge`` creates ``kn-<workspace_id>`` when it indexes
a workspace's first document; ``memory`` likewise), so a workspace that has
simply never indexed anything has no collection -- and asking it a question
is a normal state, not an internal error. ``search``/``search_sparse``
therefore answer Qdrant's "collection doesn't exist" 404 with an EMPTY
result (a search over nothing legitimately finds nothing), and ``delete``
answers it with a silent no-op (the postcondition -- those ids are not in
the store -- already holds; the same silently-idempotent delete as
``storage.delete``/``cache.delete``). ``upsert`` deliberately does NOT get
that treatment: its caller is contractually required to have called
``ensure_collection``/``ensure_hybrid_collection`` first, so a missing
collection there is a real provisioning fault and swallowing it would
silently drop data. Every OTHER 404, and every other ``UnexpectedResponse``
status, still translates to ``AppError`` -- a genuinely broken store fails
loudly.

``ValidationError`` raised by this module's own pure helpers (an unknown
``distance`` name, an unsupported filter-value shape) is a different,
caller-caused failure and is deliberately NOT part of that translation: it
is never wrapped by ``_translate``, so it always reaches the caller as
itself.

Fail-fast timeout (the 2.3/2.4 precedent): ``_TIMEOUT_S`` keeps a dead/slow
Qdrant from hanging a request-handling coroutine (07-nfr latency budgets are
sub-second). ``check_compatibility=False`` on the client constructor skips
qdrant-client's own startup version-compatibility handshake -- which would
otherwise make client *construction itself* perform a blocking network round
trip -- so the client can be built eagerly at Composition Root startup,
offline, and only actually talks to the server on the first real call.
"""

from __future__ import annotations

from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.common.client_exceptions import QdrantException
from qdrant_client.http.exceptions import ApiException, UnexpectedResponse

from app.framework.errors import AppError, ValidationError
from app.framework.ports.vector_store import SparseVector, VectorHit, VectorPoint
from app.framework.settings.settings import QdrantSettings
from app.framework.types import Json, Uuid

# Fail fast instead of hanging a request-handling coroutine on a dead vector
# store (the 2.3/2.4 precedent; 07-nfr latency budgets are sub-second).
# ``AsyncQdrantClient``'s own ``timeout`` parameter is typed ``int | None``
# (unlike redis-py/minio-py's ``float`` socket timeouts), so this stays an
# ``int``.
_TIMEOUT_S = 5

# The named sparse vector every hybrid collection provisions (port
# docstring, 02 §1.5).
_SPARSE_NAME = "text"

# Qdrant's own unnamed/default dense-vector name. This mirrors
# ``qdrant_client``'s internal ``DEFAULT_VECTOR_NAME`` constant (as of 1.18
# it lives in ``qdrant_client.local.local_collection``, not re-exported from
# the public ``models`` surface) -- a fragile literal duplicated here rather
# than importing a private module. The live ``test_qdrant_store.py``
# hybrid-collection tests are this literal's regression guard: if it were
# ever wrong, dense search against a hybrid collection would start failing
# loudly, not silently.
_DEFAULT_VECTOR_NAME = ""

# HTTP Conflict: ``create_collection`` returns this when a concurrent caller
# won a create-race for the same name -- ``ensure_collection``/
# ``ensure_hybrid_collection`` treat it as success, not a failure.
_HTTP_CONFLICT = 409

# HTTP Not Found, plus the two phrases Qdrant's own body carries for the one
# 404 that means "this collection was never created" (measured live against
# Qdrant 1.x: ``{"status": {"error": "Not found: Collection `kn-...`
# doesn't exist!"}, ...}``). Matching on the message text is admittedly
# brittle -- the status code alone is not enough, because a bare 404 also
# covers other missing entities -- so BOTH the code and the wording must
# agree before a failure is downgraded to an empty result. The live
# ``test_qdrant_store.py`` missing-collection tests are this literal's
# regression guard: if Qdrant ever rewords the message, a fresh workspace
# starts raising ``common.internal`` again and those tests go red.
_HTTP_NOT_FOUND = 404
_MISSING_COLLECTION_PHRASES = ("doesn't exist", "not found")

# Case-insensitive distance-metric name -> Qdrant enum (``VectorStore``'s
# ``distance: str = "cosine"`` parameter, 02 §1.5).
_DISTANCES: dict[str, models.Distance] = {
    "cosine": models.Distance.COSINE,
    "euclid": models.Distance.EUCLID,
    "euclidean": models.Distance.EUCLID,
    "dot": models.Distance.DOT,
    "manhattan": models.Distance.MANHATTAN,
}


def create_qdrant_client(settings: QdrantSettings) -> AsyncQdrantClient:
    """Build the shared async Qdrant client (Composition Root only).

    ``check_compatibility=False`` avoids a blocking version handshake at
    construction time (see the module docstring); ``prefer_grpc=False`` keeps
    this on the same plain-HTTP transport as every other driven adapter, one
    network posture for 07-nfr's deployment to firewall. ``api_key`` is
    omitted (``None``) -- Qdrant API-key auth is a production concern whose
    value will come from Vault once the 2.6 ``SecretsProvider`` adapter
    exists (the same deferred-wiring precedent as MinIO's access/secret
    keys, ``storage/minio_storage.py``).
    """
    return AsyncQdrantClient(
        url=settings.url,
        prefer_grpc=False,
        timeout=_TIMEOUT_S,
        check_compatibility=False,
    )


def _to_distance(distance: str) -> models.Distance:
    """Map a port-level distance name to Qdrant's enum, case-insensitively.

    Raises ``ValidationError`` (never a driver exception) for an unknown
    name: this is a caller/config mistake, not an infrastructure fault, so it
    must not be folded into ``common.internal`` by ``_translate``.
    """
    mapped = _DISTANCES.get(distance.lower())
    if mapped is None:
        raise ValidationError(f"unsupported distance metric: {distance!r}")
    return mapped


def _build_filter(flt: Json | None) -> models.Filter | None:
    """Translate a port-level ``flt`` (plain JSON) into a Qdrant ``Filter``.

    ``None``/``{}`` both mean "no filter". Every key becomes a ``must``
    (AND) ``FieldCondition``: a scalar (``str``/``int``/``bool``) becomes an
    exact ``MatchValue``; a ``list`` whose elements all share one scalar type
    becomes ``MatchAny`` (OR within that one key, still AND-ed against every
    other key). Any other value shape (``dict``, ``None``, ``float``, a
    mixed-type or float-bearing list, ...) raises ``ValidationError`` rather
    than being silently dropped or coerced -- DD-04: a silently-dropped
    ``workspace_id`` condition would be a tenant-isolation bypass, so an
    unsupported shape must fail loudly, never degrade to "no filter". floats
    are deliberately excluded: Qdrant's own ``MatchValue`` only accepts
    ``bool | int | str``, so an equality match against a float would be
    silently unreliable even if this helper let it through.
    """
    if not flt:
        return None
    must: list[models.FieldCondition] = []
    for key, value in flt.items():
        if isinstance(value, (str, int, bool)):
            must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        elif isinstance(value, list):
            # MatchAny's wire type is a *homogeneous* list (List[StrictInt] |
            # List[StrictStr] | List[StrictBool]): a mixed-type or
            # float-bearing list would raise pydantic's own ValidationError
            # from inside ``search``/``search_sparse`` -- a foreign exception
            # this adapter must never leak (R6) -- so it is rejected here,
            # loudly, for the same DD-04 reason as the scalar branch.
            if any(not isinstance(item, (str, int, bool)) for item in value) or (
                len({type(item) for item in value}) > 1
            ):
                raise ValidationError(
                    f"unsupported filter list for key {key!r}: elements must "
                    "share one scalar type (str, int, or bool)"
                )
            must.append(models.FieldCondition(key=key, match=models.MatchAny(any=value)))
        else:
            raise ValidationError(
                f"unsupported filter value for key {key!r}: {type(value).__name__}"
            )
    return models.Filter(must=must)


def _to_point(p: VectorPoint) -> models.PointStruct:
    """Translate a port ``VectorPoint`` into Qdrant's wire ``PointStruct``.

    A dense-only point keeps ``vector`` as a bare list (Qdrant's unnamed
    default vector). A point carrying ``.sparse`` becomes a *named* vector
    map instead -- ``_DEFAULT_VECTOR_NAME`` for the dense leg alongside
    ``_SPARSE_NAME`` for the sparse leg -- so both legs of one chunk stay one
    Qdrant point with one payload and one id, never two points (port
    docstring). ``str(p.id)`` matches Qdrant's ``id: int | str | UUID``
    surface to this codebase's text-``Uuid`` convention (DD-02).
    """
    if p.sparse is None:
        return models.PointStruct(id=str(p.id), vector=list(p.vector), payload=dict(p.payload))
    vector: dict[str, list[float] | models.SparseVector] = {
        _DEFAULT_VECTOR_NAME: list(p.vector),
        _SPARSE_NAME: models.SparseVector(
            indices=list(p.sparse.indices), values=list(p.sparse.values)
        ),
    }
    return models.PointStruct(id=str(p.id), vector=vector, payload=dict(p.payload))


def _to_hit(point: models.ScoredPoint) -> VectorHit:
    """Translate a Qdrant ``ScoredPoint`` into the port's ``VectorHit``.

    Explicit ``str``/``float``/``dict`` conversions (rather than trusting the
    driver's own fields as-is) keep every field a concrete type mypy can
    verify -- ``qdrant_client`` ships no ``py.typed`` marker (this repo's
    mypy config treats it under ``ignore_missing_imports``), so
    ``point.id``/``.score``/``.payload`` are ``Any`` on mypy's side and would
    otherwise trip ``warn_return_any``.
    """
    return VectorHit(id=str(point.id), score=float(point.score), payload=dict(point.payload or {}))


def _is_missing_collection(exc: Exception) -> bool:
    """True ONLY for Qdrant's "that collection does not exist" 404.

    Narrow on purpose (see the module docstring): a 404 whose body does not
    name a collection, any other status, and every non-HTTP driver failure
    (connection refused, timeout, ...) all answer ``False`` and keep taking
    the ``_translate`` -> ``common.internal`` path, so a genuinely broken
    store still fails loudly instead of masquerading as an empty one.
    """
    if not isinstance(exc, UnexpectedResponse) or exc.status_code != _HTTP_NOT_FOUND:
        return False
    body = exc.content.decode("utf-8", errors="replace").lower()
    return "collection" in body and any(phrase in body for phrase in _MISSING_COLLECTION_PHRASES)


def _translate(exc: Exception) -> AppError:
    """Map ANY Qdrant driver failure onto the shared framework hierarchy
    (03-api-spec §4, R6) -- ``qdrant_client`` exception types never escape
    this adapter. Every failure that reaches here folds into
    ``common.internal`` rather than a caller-branchable 404, the opposite of
    2.4's ``NoSuchKey``; the sole failure that never reaches here is the
    missing-collection 404 the read/delete paths absorb (module docstring)."""
    return AppError("vector store operation failed", code="common.internal")


class QdrantVectorStore:
    """Qdrant-backed ``VectorStore``/``HybridVectorStore`` (02 §1.5,
    structural Protocol match)."""

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def ensure_collection(self, name: str, dim: int, distance: str = "cosine") -> None:
        try:
            if await self._client.collection_exists(name):
                return
            try:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(size=dim, distance=_to_distance(distance)),
                )
            except UnexpectedResponse as exc:
                if exc.status_code != _HTTP_CONFLICT:  # not a lost create-race -> real failure
                    raise
        except (ApiException, QdrantException) as exc:
            raise _translate(exc) from exc

    async def ensure_hybrid_collection(
        self, name: str, dim: int, *, distance: str = "cosine"
    ) -> None:
        try:
            if await self._client.collection_exists(name):
                return
            try:
                await self._client.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(size=dim, distance=_to_distance(distance)),
                    sparse_vectors_config={
                        _SPARSE_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)
                    },
                )
            except UnexpectedResponse as exc:
                if exc.status_code != _HTTP_CONFLICT:
                    raise
        except (ApiException, QdrantException) as exc:
            raise _translate(exc) from exc

    async def upsert(self, collection: str, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        try:
            await self._client.upsert(
                collection_name=collection, points=[_to_point(p) for p in points], wait=True
            )
        except (ApiException, QdrantException) as exc:
            raise _translate(exc) from exc

    async def search(
        self, collection: str, vector: list[float], k: int, flt: Json | None = None
    ) -> list[VectorHit]:
        try:
            response = await self._client.query_points(
                collection_name=collection,
                query=list(vector),
                using=None,
                query_filter=_build_filter(flt),
                limit=k,
                with_payload=True,
            )
        except (ApiException, QdrantException) as exc:
            # A never-indexed workspace has no collection yet; searching one
            # legitimately finds nothing (module docstring).
            if _is_missing_collection(exc):
                return []
            raise _translate(exc) from exc
        return [_to_hit(point) for point in response.points]

    async def search_sparse(
        self, collection: str, sparse: SparseVector, k: int, flt: Json | None = None
    ) -> list[VectorHit]:
        try:
            response = await self._client.query_points(
                collection_name=collection,
                query=models.SparseVector(indices=list(sparse.indices), values=list(sparse.values)),
                using=_SPARSE_NAME,
                query_filter=_build_filter(flt),
                limit=k,
                with_payload=True,
            )
        except (ApiException, QdrantException) as exc:
            if _is_missing_collection(exc):  # same as ``search``, sparse leg
                return []
            raise _translate(exc) from exc
        return [_to_hit(point) for point in response.points]

    async def delete(self, collection: str, ids: Sequence[Uuid]) -> None:
        if not ids:
            return
        try:
            await self._client.delete(
                collection_name=collection,
                points_selector=models.PointIdsList(points=[str(i) for i in ids]),
                wait=True,
            )
        except (ApiException, QdrantException) as exc:
            # No collection -> those ids are already absent: the port's
            # delete is silently idempotent (module docstring). ``upsert``
            # above deliberately keeps raising instead.
            if _is_missing_collection(exc):
                return
            raise _translate(exc) from exc
