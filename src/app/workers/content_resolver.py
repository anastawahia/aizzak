"""The knowledge worker's ``DocumentContentResolver`` adapter
(deferred-adapters-plan.md step 16).

``build_knowledge_index_handler`` needs one call that turns a ``file_id``
into everything ``IndexRegisteredDocument.run`` consumes beyond the document
id: the file's bytes, fetched and parsed into a ``ParsedDocument``, the
embedding model/key to index it with, and (plan step 15, §3.6) the
``sha256`` fingerprint of those same bytes. Every piece has existed for a while --
``SqlFileRepository`` (3.16), the ``MinioStorage`` adapter behind
``StorageHandle`` (2.4/step 15), ``DocumentContentExtractor``'s eleven-extension
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

**``asyncio.wait_for`` around that same offload is the plan's parse timeout**
(rag-indexing-plan.md §3.7, decision س-13, plan step 14 / `P-01` `P-02`) --
never ``SIGALRM``, which does not fire off the main thread and which
``extractor.py``'s own docstring already records as never ported. A timeout
here is a genuine, ``asyncio.TimeoutError`` failure of ``resolve`` itself, so
it is caught and re-raised as the SAME ``ValidationError`` shape a zip-bomb
trip or any other parse failure already is -- `build_knowledge_index_handler`
(``bootstrap.py``)'s existing ``except (UnsupportedTypeError, ValidationError)``
clause needs no new branch to route it into ``IndexRegisteredDocument.fail``,
landing the document ``status='failed'`` with an explicit message (س-13 = أ:
never a silent skip, never a false success with zero chunks).

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
import hashlib

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.ports.storage_provider import StorageProvider
from app.framework.providers.resolver import ProviderResolver
from app.framework.settings.settings import Limits
from app.framework.types import Uuid
from app.modules.files.ports.repository import FileRepository
from app.modules.knowledge.ports.content_extractor import ContentExtractor, ParsedDocument


class WorkerDocumentContentResolver:
    """The real ``DocumentContentResolver`` (``bootstrap.py``'s Protocol --
    structural conformance, proven at the wiring site)."""

    __slots__ = ("_extractor", "_files", "_providers", "_storage", "_timeout_s")

    def __init__(
        self,
        files: FileRepository,
        storage: StorageProvider,
        extractor: ContentExtractor,
        providers: ProviderResolver,
        *,
        timeout_s: float = Limits().parser_timeout_seconds,
    ) -> None:
        self._files = files
        self._storage = storage
        self._extractor = extractor
        self._providers = providers
        # §3.7's parse timeout (`Limits.parser_timeout_seconds`) -- defaults
        # to the same value a bare `Limits()` carries, the `DocumentContentExtractor`
        # precedent, so a caller that does not care (a test) gets the plan's
        # own default rather than an unbounded wait.
        self._timeout_s = timeout_s

    async def resolve(
        self, ctx: ExecutionContext, *, file_id: Uuid
    ) -> tuple[ParsedDocument, str, str, str]:
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
        # §3.6 (plan step 15): `sha256(bytes)`, never `mtime` -- the model is
        # object-based, not file-based (rag-indexing-plan.md's own words).
        # Computed HERE and nowhere else: this is the one place downstream of
        # storage that still holds the raw bytes -- `ParsedDocument` never
        # carries them, and `IndexRegisteredDocument` only ever sees this
        # fingerprint, not the bytes it was made from.
        content_hash = hashlib.sha256(data).hexdigest()

        # See the module docstring -- the port itself requires this offload,
        # and `wait_for` around it is §3.7's parse timeout (plan step 14).
        try:
            parsed = await asyncio.wait_for(
                asyncio.to_thread(
                    self._extractor.extract,
                    data=data,
                    filename=file.name.value,
                    content_type=file.content_type.value,
                ),
                timeout=self._timeout_s,
            )
        except TimeoutError as exc:
            raise ValidationError(
                f"parsing {file.name.value!r} exceeded the {self._timeout_s:g}s timeout",
                code="knowledge.parse_timeout",
            ) from exc

        # The EmbeddingProvider half of this tuple is deliberately dropped:
        # the pipeline (`IndexDocument`) already holds the one embedding
        # adapter this process built, and the worker's resolver is
        # constructed with exactly that adapter as its only wired embedding
        # provider -- so `_parse_namespace` has already proven the route
        # points at it. Only the model/key are per-call. (The API path drops
        # it identically, in `_RoutedEmbeddingResolver`.)
        _, resolved = await self._providers.resolve_embedding(ctx)
        return parsed, resolved.model, resolved.api_key, content_hash
