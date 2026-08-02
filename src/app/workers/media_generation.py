"""The media worker's ``MediaGenerator`` adapter
(deferred-adapters-plan.md step 19).

``RunMediaJob`` needs one call that turns a queued job into a stored
``files`` id: resolve the image route, call the provider, and persist the
returned bytes. Every piece has existed for a while -- the ``ImageProvider``
port since Phase 2, ``RegisterUpload``/``CompleteUpload`` since 3.16, the
MinIO adapter behind ``StorageHandle`` (2.4/step 15), and ``resolve_image``
since step 18 -- but nothing composed them, which is the whole reason
``media/ports/generation.py``'s seam has stood empty since Phase 3 and the
media worker could not boot.

**Why ``app.workers`` and not ``app.modules.media``.** This class joins TWO
modules (``media``'s vocabulary + ``files``' storage lifecycle) to
infrastructure ports, which ``.importlinter``'s ``modules-independent``
contract forbids inside ``app.modules.*``. ``app.workers`` is where such
joins are legal by design: that contract's own header names worker
entrypoints as "composition roots for their process" -- the
``WorkerDocumentContentResolver`` precedent (step 16), same argument, other
pair of modules.

**⚠️ The generated file is deliberately kept OUT of the ``FileUploaded``
event path.** ``CompleteUpload.execute`` RETURNS a ``FileUploaded`` event;
this class drops it on the floor and appends nothing to any outbox. That is
the single most important line of policy in this module, so it is stated
here rather than left to be inferred from an unused return value:
``files.file.uploaded.v1`` is the event that wakes the ``knowledge`` worker,
so publishing it would send every agent-generated image through OCR and into
the workspace's knowledge base. The agent would then retrieve its own output
as a "source" and cite what it invented -- a context-pollution loop, and the
one failure mode a retrieval system cannot detect from the inside. The
platform's other completion path (``CompleteUploadService``, 5.1-أ) exists
precisely to publish that event for genuine user uploads; this module calls
the bare use-case instead, so the difference is a deliberate choice of
collaborator rather than a forgotten call.

**Order: resolve → generate → register → put → complete.** Route resolution
runs FIRST because it is the only step that can fail on workspace
configuration (a missing credential) rather than on the work itself, and
paying for a generation before discovering the workspace has no key would
bill a tenant for bytes it can never receive. ``RegisterUpload`` runs
BEFORE ``storage.put`` because it is what mints the ``storage_key`` those
bytes go to, and it is where the configured limits (whitelist, max size,
per-workspace cap) are enforced -- against the size we actually have, not a
guess.

**No unit of work spans this method, on purpose.** ``RunMediaJob.run``
calls the generator OUTSIDE any transaction (R2: external I/O must never run
under an open DB transaction), so ``RegisterUpload`` and ``CompleteUpload``
each commit in their own short transaction, exactly as they do on the API's
own two-step upload path. A crash between them leaves an ``uploaded`` row
whose bytes may or may not have landed -- the identical residue a user
abandoning an upload leaves, reachable by the same cleanup, and strictly
better than the alternative of holding a transaction open across a
minutes-long provider call.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.image_provider import ImageRequest
from app.framework.ports.storage_provider import StorageProvider
from app.framework.providers.resolver import ProviderResolver
from app.framework.types import Uuid
from app.modules.files.application.use_cases import CompleteUpload, RegisterUpload
from app.modules.media.domain.value_objects import GenParams, MediaKind

# 03-api-spec §4 / `framework/errors.py`. A CLASSIFIED code, never a bare
# `NotImplementedError`: `RunMediaJob.run`'s broad catch stores `str(exc)` as
# the job's `error`, and that string is what an operator reads when a video
# job fails. "media.unsupported_kind" tells them the platform does not do
# this yet; a raw NotImplementedError traceback tells them nothing they can
# act on.
_UNSUPPORTED_KIND = "media.unsupported_kind"

# Cosmetic only -- the CONTENT TYPE is the truth, and `RegisterUpload`
# validates it against `Limits.allowed_mime`. The extension exists so a
# downloaded file opens in the right application; an unknown type simply
# gets no suffix rather than an invented one.
_EXTENSIONS: Mapping[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class WorkerMediaGenerator:
    """The real ``MediaGenerator`` (``media/ports/generation.py``'s Protocol
    -- structural conformance, proven at the wiring site)."""

    __slots__ = ("_complete", "_providers", "_register", "_storage")

    def __init__(
        self,
        providers: ProviderResolver,
        register: RegisterUpload,
        complete: CompleteUpload,
        storage: StorageProvider,
    ) -> None:
        self._providers = providers
        self._register = register
        self._complete = complete
        self._storage = storage

    async def generate(
        self, ctx: ExecutionContext, *, kind: MediaKind, prompt: str, params: GenParams
    ) -> Uuid:
        if kind is not MediaKind.IMAGE:
            raise AppError(f"{kind.value} generation is not supported yet", code=_UNSUPPORTED_KIND)

        width, height = params.width, params.height
        if width is None or height is None:
            # Unreachable through `RequestMedia` -- `GenParams.__post_init__`
            # already requires both for an IMAGE job (INV-MJ3). Kept as a
            # raise rather than an `assert` because the ONLY thing standing
            # between here and a hand-built `GenParams` is that invariant,
            # and a 422 naming the missing field beats a `TypeError` deep
            # inside the adapter if it is ever bypassed.
            raise ValidationError(
                "image params must carry width and height", code="media.invalid_params"
            )

        provider, resolved = await self._providers.resolve_image(ctx, model=params.model)
        result = await provider.generate(
            ImageRequest(prompt=prompt, width=width, height=height, model=resolved.model),
            resolved.api_key,
        )

        file = await self._register.execute(
            ctx,
            name=_generated_name(result.content_type),
            content_type=result.content_type,
            size_bytes=len(result.content),
        )
        await self._storage.put(file.storage_key.value, result.content, result.content_type)
        # The returned `FileUploaded` event is DROPPED, not forgotten -- see
        # the module docstring's ⚠️ paragraph. Appending it here would index
        # every generated image into the workspace's knowledge base.
        await self._complete.execute(
            ctx, file_id=file.id, checksum=hashlib.sha256(result.content).hexdigest()
        )
        return file.id


def _generated_name(content_type: str) -> str:
    """A unique, path-free basename for a generated file.

    Deliberately NOT derived from the prompt: a prompt is arbitrary user text
    (any script, any length, any control character), and squeezing it through
    ``FileName``'s sanitiser would produce names that are either mangled or
    collide. The name carries no information the ``media_jobs`` row does not
    already hold -- the job is what links a file back to its prompt.
    """
    return f"generated-{new_uuid7()}{_EXTENSIONS.get(content_type, '')}"
