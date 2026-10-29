"""The late-binding ``StorageProvider`` slot (Phase 6.1-هـ-1 — debt (ز)).

MinIO is the ONE adapter whose construction is asynchronous: its access/secret
keys are a Vault read (``await secrets.get_secret("secret/data/minio")``, 05
§3), and ``CompositionRoot.from_env`` is a plain synchronous classmethod that
cannot ``await`` anything (its own docstring has carried this debt since 2.4).
Everything built at ``from_env`` time that NEEDS storage — the orchestrator's
``AgentDependencies.storage`` seam, the Phase-6 files services — therefore
receives THIS handle instead: a structural ``StorageProvider`` whose real
adapter is bound later, inside the app's async startup
(``CompositionRoot.connect_storage``), before the process serves traffic.

Deliberately NOT a service locator: the handle resolves nothing by name and
holds exactly one provider, bound exactly once per process by the Composition
Root itself. It is the smallest possible bridge between a synchronous
construction phase and one async wiring step — the alternative (making the
whole root construction async) would force an async factory onto twenty
adapters that don't need one.

**Unbound calls fail with a clean ``common.internal``/500**, the SAME wire
outcome the ``deps.storage = None`` guard produced before this handle existed
(the uniform 4.4/4.6 precedent: a missing adapter is a composition/deployment
bug, never a user error). In a correctly-deployed process this path is
unreachable — the lifespan binds storage before the ASGI server accepts its
first request — so hitting it means startup was skipped or ``connect_storage``
was never called.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.framework.errors import AppError
from app.framework.ports.storage_provider import ObjectStorage, StoredPart


class StorageHandle:
    """An ``ObjectStorage`` (``StorageProvider`` plus ``MultipartStorage``,
    structural match) that delegates to an adapter bound after construction —
    ``None`` until ``bind`` runs at startup."""

    __slots__ = ("_provider",)

    def __init__(self) -> None:
        self._provider: ObjectStorage | None = None

    @property
    def is_bound(self) -> bool:
        return self._provider is not None

    def bind(self, provider: ObjectStorage) -> None:
        """Install the real adapter. Called once, by the Composition Root's
        ``connect_storage``; a second call replaces the provider, which is
        harmless because ``connect_storage`` itself is idempotent and nothing
        else holds a reference to bind."""
        self._provider = provider

    def _require(self) -> ObjectStorage:
        if self._provider is None:
            raise AppError("object storage is not configured", code="common.internal")
        return self._provider

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await self._require().put(key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await self._require().get(key)

    async def delete(self, key: str) -> None:
        await self._require().delete(key)

    async def presign_get(self, key: str, ttl_s: int) -> str:
        return await self._require().presign_get(key, ttl_s)

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        return await self._require().presign_put(key, ttl_s, content_type)

    async def start_multipart(self, key: str, content_type: str) -> str:
        return await self._require().start_multipart(key, content_type)

    async def presign_upload_part(
        self, key: str, upload_id: str, part_number: int, ttl_s: int
    ) -> str:
        return await self._require().presign_upload_part(key, upload_id, part_number, ttl_s)

    async def list_parts(self, key: str, upload_id: str) -> Sequence[StoredPart]:
        return await self._require().list_parts(key, upload_id)

    async def complete_multipart(
        self, key: str, upload_id: str, parts: Sequence[StoredPart]
    ) -> None:
        await self._require().complete_multipart(key, upload_id, parts)

    async def object_size(self, key: str) -> int | None:
        return await self._require().object_size(key)
