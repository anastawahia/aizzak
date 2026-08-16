"""Unit tests for ``WorkerMediaGenerator`` (``workers/media_generation.py``,
step 19 of ``deferred-adapters-plan.md``) -- the adapter that finally fills
``media/ports/generation.py``'s seam.

Fakes stop at the two genuinely external edges (the image provider and
object storage). Everything between them is the REAL collaborator: the real
``SettingsProviderResolver`` over a real routing table, the real
``RegisterUpload``/``CompleteUpload`` use-cases over a real ``Limits``, and
the shared ``InMemoryFileRepository``. A test that faked the use-cases would
prove the generator calls something, not that a generated image ends up a
``ready`` file inside the configured whitelist and size cap.
"""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import dataclass

import pytest

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, UnsupportedTypeError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.event_outbox import EventOutbox
from app.framework.ports.image_provider import ImageRequest, ImageResult
from app.framework.providers.resolver import SettingsProviderResolver
from app.framework.settings.settings import Limits
from app.framework.types import Json
from app.modules.files.application.use_cases import CompleteUpload, RegisterUpload
from app.modules.files.domain.value_objects import FileStatus
from app.modules.media.domain.value_objects import GenParams, MediaKind
from app.workers.media_generation import WorkerMediaGenerator, _generated_name
from tests.unit.support_files_media import InMemoryFileRepository, InMemorySpaces

_PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
_KEY = "sk-fake"
_ROUTING: Json = {"image": {"default": {"provider": "image:openai", "model": "gpt-image-1"}}}


class _FakeImageProvider:
    """A structural ``ImageProvider`` recording every call."""

    provider = "image:openai"

    def __init__(
        self,
        *,
        content: bytes = _PNG,
        content_type: str = "image/png",
        error: Exception | None = None,
    ) -> None:
        self._content = content
        self._content_type = content_type
        self._error = error
        self.calls: list[tuple[ImageRequest, str]] = []

    async def generate(self, req: ImageRequest, api_key: str) -> ImageResult:
        self.calls.append((req, api_key))
        if self._error is not None:
            raise self._error
        return ImageResult(content=self._content, content_type=self._content_type, model=req.model)


class _FakeStorage:
    """A ``StorageProvider`` that actually stores, so a test can prove the
    bytes landed under the key ``RegisterUpload`` minted."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if self._error is not None:
            raise self._error
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def presign_get(self, key: str, ttl_s: int) -> str:
        raise AssertionError("not exercised by the generator")

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        raise AssertionError("not exercised by the generator")


@dataclass(frozen=True, slots=True)
class _StubKey:
    """Structurally a ``ResolvedKeyView`` -- one attribute, which is all the
    resolver reads."""

    api_key: str


class _StubKeyResolver:
    """A ``KeyResolver`` that hands back one fixed key -- Vault and the
    credentials repository are not what this module is testing (the real
    cross-boundary proof lives in ``test_provider_resolver.py``)."""

    async def resolve(self, ctx: ExecutionContext, provider: str) -> _StubKey:
        return _StubKey(api_key=_KEY)


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


def _build(
    *,
    provider: _FakeImageProvider | None = None,
    storage: _FakeStorage | None = None,
    routing: Json | None = None,
    limits: Limits | None = None,
) -> tuple[WorkerMediaGenerator, _FakeImageProvider, _FakeStorage, InMemoryFileRepository]:
    image = provider or _FakeImageProvider()
    store = storage or _FakeStorage()
    files = InMemoryFileRepository()
    resolver = SettingsProviderResolver(
        routing=_ROUTING if routing is None else routing,
        llm_providers={},
        embedding_providers={},
        image_providers={image.provider: image},
        key_resolver=_StubKeyResolver(),
        keyless_providers=frozenset(),
    )
    generator = WorkerMediaGenerator(
        resolver,
        # The generator registers with `space_id=None` (it has no space to
        # name until step 7), so the seam is never consulted — wired real
        # anyway, because a fake that answers "yes" to everything would hide
        # the day this process DOES start naming one.
        RegisterUpload(files, limits or Limits(), InMemorySpaces()),
        CompleteUpload(files),
        store,
    )
    return generator, image, store, files


def _params(**overrides: object) -> GenParams:
    return GenParams.from_dict(MediaKind.IMAGE, {"width": 1024, "height": 1024, **overrides})


# --------------------------------------------------------------------------- #
# the happy path                                                               #
# --------------------------------------------------------------------------- #
async def test_a_generated_image_becomes_a_ready_file_holding_the_provider_bytes() -> None:
    generator, image, storage, files = _build()
    ctx = _ctx()

    file_id = await generator.generate(ctx, kind=MediaKind.IMAGE, prompt="a cat", params=_params())

    stored = await files.get(ctx, file_id)
    assert stored is not None
    assert stored.status is FileStatus.READY
    assert stored.content_type.value == "image/png"
    assert stored.size_bytes == len(_PNG)
    # The checksum is computed HERE, over the exact bytes stored -- not
    # supplied by the provider and not left `None`.
    assert stored.checksum is not None
    assert stored.checksum.value == hashlib.sha256(_PNG).hexdigest()
    # The bytes landed under the key `RegisterUpload` minted, with the same
    # content type the file row claims.
    assert storage.objects[stored.storage_key.value] == (_PNG, "image/png")
    assert stored.name.value.endswith(".png")

    (req, api_key) = image.calls[0]
    assert req.prompt == "a cat"
    assert (req.width, req.height) == (1024, 1024)
    assert req.model == "gpt-image-1"
    assert api_key == _KEY


async def test_a_per_job_model_overrides_the_routed_one() -> None:
    """``GenParams.model`` is the job's own override and must reach the
    resolver, which is the only thing entitled to decide the final model."""
    generator, image, _storage, _files = _build()
    await generator.generate(
        _ctx(), kind=MediaKind.IMAGE, prompt="a cat", params=_params(model="dall-e-3")
    )
    assert image.calls[0][0].model == "dall-e-3"


# --------------------------------------------------------------------------- #
# the FileUploaded decision                                                    #
# --------------------------------------------------------------------------- #
def test_the_generator_is_wired_with_no_event_outbox_at_all() -> None:
    """The structural half of the ⚠️ decision in the module docstring:
    generated files stay OUT of the ``files.file.uploaded.v1`` path, or every
    agent-generated image gets indexed into the workspace knowledge base and
    the agent starts citing its own output.

    ``CompleteUpload`` RETURNS that event; this class drops it. The only way
    a future edit could publish it is by acquiring an outbox, so that is
    what this pins -- cheaply, and in the one place where "we simply do not
    append" is otherwise invisible. The behavioural proof (no outbox ROW
    after a real generation) lives in
    ``tests/integration/test_media_worker_live.py``.
    """
    parameters = inspect.signature(WorkerMediaGenerator.__init__).parameters
    assert [name for name in parameters if name != "self"] == [
        "providers",
        "register",
        "complete",
        "storage",
    ]
    annotations = {p.annotation for p in parameters.values()}
    assert EventOutbox not in annotations
    assert "EventOutbox" not in {str(a) for a in annotations}


# --------------------------------------------------------------------------- #
# failure paths                                                                #
# --------------------------------------------------------------------------- #
async def test_a_video_job_fails_with_a_classified_code_not_a_bare_not_implemented() -> None:
    """``RunMediaJob.run`` catches this and stores ``str(exc)`` as the job's
    error, so the message is what an operator actually reads. A
    ``NotImplementedError`` traceback would tell them nothing actionable --
    and, being uncaught policy rather than a classified failure, would look
    like a bug rather than a documented gap."""
    generator, image, storage, files = _build()
    ctx = _ctx()

    with pytest.raises(AppError) as exc_info:
        await generator.generate(
            ctx,
            kind=MediaKind.VIDEO,
            prompt="a cat",
            params=GenParams.from_dict(MediaKind.VIDEO, {"duration_seconds": 5}),
        )

    assert exc_info.value.code == "media.unsupported_kind"
    assert exc_info.value.status == 422
    assert "video generation is not supported yet" in str(exc_info.value)
    # Refused before ANY side effect: no provider call, no file row, no bytes.
    assert image.calls == []
    assert storage.objects == {}
    assert await files.count(ctx) == 0


async def test_a_provider_failure_leaves_no_file_row_behind() -> None:
    """Route resolution and the provider call both run BEFORE
    ``RegisterUpload``, so the expensive, most-likely-to-fail step cannot
    leave a half-made file for someone to clean up."""
    failure = AppError("image:openai call failed", code="agent.failed", status=502)
    generator, _image, storage, files = _build(provider=_FakeImageProvider(error=failure))
    ctx = _ctx()

    with pytest.raises(AppError) as exc_info:
        await generator.generate(ctx, kind=MediaKind.IMAGE, prompt="a cat", params=_params())

    assert exc_info.value is failure
    assert await files.count(ctx) == 0
    assert storage.objects == {}


async def test_a_storage_failure_leaves_the_file_uploaded_never_ready() -> None:
    """The honest residue of having no transaction across the two commits
    (module docstring): the row exists but never reaches ``ready``, so
    nothing downstream can read a file whose bytes are not there."""
    generator, _image, _storage, files = _build(storage=_FakeStorage(error=OSError("minio down")))
    ctx = _ctx()

    with pytest.raises(OSError, match="minio down"):
        await generator.generate(ctx, kind=MediaKind.IMAGE, prompt="a cat", params=_params())

    (row,) = files.rows.values()
    assert row.status is FileStatus.UPLOADED


async def test_a_content_type_outside_the_whitelist_is_refused_like_any_upload() -> None:
    """Generated bytes go through the SAME ``RegisterUpload`` gate a user
    upload does -- a provider that starts answering in an un-whitelisted
    format is stopped here, not discovered later by whatever tries to read
    the file."""
    generator, _image, storage, files = _build(
        provider=_FakeImageProvider(content_type="image/gif")
    )
    ctx = _ctx()

    with pytest.raises(UnsupportedTypeError) as exc_info:
        await generator.generate(ctx, kind=MediaKind.IMAGE, prompt="a cat", params=_params())

    assert exc_info.value.code == "files.unsupported_type"
    assert await files.count(ctx) == 0
    assert storage.objects == {}


async def test_an_oversized_image_is_refused_by_the_configured_upload_cap() -> None:
    generator, _image, storage, files = _build(limits=Limits(max_upload_bytes=4))
    ctx = _ctx()

    with pytest.raises(AppError) as exc_info:
        await generator.generate(ctx, kind=MediaKind.IMAGE, prompt="a cat", params=_params())

    assert exc_info.value.code == "files.too_large"
    assert storage.objects == {}
    assert await files.count(ctx) == 0


async def test_an_unrouted_image_namespace_fails_closed() -> None:
    """An EMPTY routing table constructs fine (2.9) -- the refusal happens at
    resolve time, naming the missing route rather than crashing the worker at
    boot over a job it has not been asked to run yet."""
    generator, image, _storage, _files = _build(routing={})

    with pytest.raises(ValidationError, match="no 'default' image route is configured"):
        await generator.generate(_ctx(), kind=MediaKind.IMAGE, prompt="a cat", params=_params())
    assert image.calls == []


# --------------------------------------------------------------------------- #
# the generated name                                                           #
# --------------------------------------------------------------------------- #
def test_the_generated_name_is_unique_and_carries_the_right_extension() -> None:
    assert _generated_name("image/png").endswith(".png")
    assert _generated_name("image/jpeg").endswith(".jpg")
    assert _generated_name("image/webp").endswith(".webp")
    # An unknown type gets NO suffix rather than an invented one -- the
    # content type is the truth, the extension is a convenience.
    assert "." not in _generated_name("application/octet-stream")
    assert _generated_name("image/png") != _generated_name("image/png")
