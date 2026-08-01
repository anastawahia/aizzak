"""The knowledge worker's ``DocumentContentResolver`` adapter
(deferred-adapters-plan.md step 16).

``build_knowledge_index_handler`` needs one call that turns a ``file_id``
into everything ``IndexRegisteredDocument.run`` consumes beyond the document
id: the file's bytes, fetched and parsed into a ``ParsedDocument``, plus the
embedding model/key to index it with. Every piece has existed for a while --
``SqlFileRepository`` (3.16), the ``MinioStorage`` adapter behind
``StorageHandle`` (2.4/step 15), ``DocumentContentExtractor``'s ten-extension
dispatch table (3.k1), and ``SettingsProviderResolver`` (2.9/2.10) -- but
nothing composed them, which is the whole reason the knowledge worker could
not boot.

**Why ``app.workers`` and not ``app.modules.knowledge``.** This class joins
TWO modules (``files`` + ``knowledge``) to infrastructure, which
``.importlinter``'s ``modules-independent`` contract forbids inside
``app.modules.*``. ``app.workers`` is where such joins are legal by design:
that contract's own header names worker entrypoints as "composition roots
for their process" (the ``ProcessedEventLedger``/``DocumentContentResolver``
seams in ``bootstrap.py`` stand on the same ground).

**``asyncio.to_thread`` is mandatory, not defensive.** ``ContentExtractor``
is synchronous BY DESIGN and its port docstring puts the offload obligation
on the caller in as many words: PyMuPDF/pandas/pytesseract are blocking
C-extension calls. Without the offload, the first PDF to arrive would freeze
this worker's entire event loop -- Redis heartbeats, the consumer's
``XREADGROUP`` cycle, everything -- for the whole parse.

**The provider resolver decides the model, not this class.** Indexing and
searching MUST agree on the embedding model: if a worker indexed with one
model while ``POST /search`` queried with another, retrieval would break
SILENTLY -- different vector spaces, no error anywhere, just useless
results. So the worker resolves through the SAME ``ProviderResolver``
abstraction the API path resolves through (over the same
``PROVIDER_ROUTING`` entry), rather than reading a model name out of
settings on its own.
"""

from __future__ import annotations

import asyncio

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError
from app.framework.ports.storage_provider import StorageProvider
from app.framework.providers.resolver import ProviderResolver
from app.framework.types import Uuid
from app.modules.files.ports.repository import FileRepository
from app.modules.knowledge.ports.content_extractor import ContentExtractor, ParsedDocument


class WorkerDocumentContentResolver:
    """The real ``DocumentContentResolver`` (``bootstrap.py``'s Protocol --
    structural conformance, proven at the wiring site)."""

    __slots__ = ("_extractor", "_files", "_providers", "_storage")

    def __init__(
        self,
        files: FileRepository,
        storage: StorageProvider,
        extractor: ContentExtractor,
        providers: ProviderResolver,
    ) -> None:
        self._files = files
        self._storage = storage
        self._extractor = extractor
        self._providers = providers

    async def resolve(
        self, ctx: ExecutionContext, *, file_id: Uuid
    ) -> tuple[ParsedDocument, str, str]:
        """Fetch → parse → resolve the embedding route, in that order.

        Order matters in one direction only: parsing is by far the most
        expensive step, so nothing that can fail cheaply is left until after
        it. The route resolution runs LAST because it is the only step whose
        failure (a missing credential) is a workspace-configuration problem
        rather than a property of the file.
        """
        file = await self._files.get(ctx, file_id)
        if file is None:
            raise NotFoundError(f"file {file_id} not found")

        data = await self._storage.get(file.storage_key.value)

        # See the module docstring -- the port itself requires this offload.
        parsed = await asyncio.to_thread(
            self._extractor.extract,
            data=data,
            filename=file.name.value,
            content_type=file.content_type.value,
        )

        # The EmbeddingProvider half of this tuple is deliberately dropped:
        # the pipeline (`IndexDocument`) already holds the one embedding
        # adapter this process built, and the worker's resolver is
        # constructed with exactly that adapter as its only wired embedding
        # provider -- so `_parse_namespace` has already proven the route
        # points at it. Only the model/key are per-call. (The API path drops
        # it identically, in `_RoutedEmbeddingResolver`.)
        _, resolved = await self._providers.resolve_embedding(ctx)
        return parsed, resolved.model, resolved.api_key
