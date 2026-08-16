"""Unit tests for ``WorkerDocumentContentResolver``
(deferred-adapters-plan.md step 16).

Hermetic: the repository, the storage provider and the provider resolver are
in-memory fakes; the EXTRACTOR is the real ``DocumentContentExtractor``
wherever the assertion is about routing/parsing, because the whole point of
this adapter is that it composes the real dispatch table -- a fake extractor
would prove only that the fake was called.
"""

from __future__ import annotations

import threading

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, UnsupportedTypeError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.providers.resolver import ResolvedProvider
from app.modules.files.domain.entities import File
from app.modules.files.domain.value_objects import ContentType, FileName, FileStatus, StorageKey
from app.modules.knowledge.adapters.parsers.extractor import DocumentContentExtractor
from app.modules.knowledge.ports.content_extractor import ParsedDocument
from app.workers.content_resolver import WorkerDocumentContentResolver


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id="ws-1", user_id=None, correlation_id="corr-1", roles=frozenset()
    )


def _file(*, name: str = "notes.txt", content_type: str = "text/plain") -> File:
    now = utc_now()
    return File(
        id=new_uuid7(),
        workspace_id="ws-1",
        space_id=None,
        name=FileName(name),
        content_type=ContentType(content_type),
        size_bytes=64,
        storage_key=StorageKey(f"ws-1/{name}"),
        checksum=None,
        status=FileStatus.READY,
        uploaded_by=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        version=1,
    )


class _FakeFiles:
    """Minimal ``FileRepository`` -- only ``get`` is ever reached."""

    def __init__(self, file: File | None) -> None:
        self._file = file
        self.calls: list[str] = []

    async def get(self, ctx: ExecutionContext, file_id: str) -> File | None:
        self.calls.append(file_id)
        return self._file


class _FakeStorage:
    """Minimal ``StorageProvider`` -- serves one blob, records every key."""

    def __init__(self, data: bytes = b"some plain text content") -> None:
        self._data = data
        self.keys: list[str] = []

    async def get(self, key: str) -> bytes:
        self.keys.append(key)
        return self._data


class _FakeProviders:
    """Minimal ``ProviderResolver`` -- the embedding half only."""

    def __init__(self, *, model: str = "minilm", api_key: str = "") -> None:
        self._resolved = ResolvedProvider(provider="embedding-local", model=model, api_key=api_key)
        self.calls = 0

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[object, ResolvedProvider]:
        self.calls += 1
        return object(), self._resolved


def _resolver(
    *,
    files: _FakeFiles,
    storage: _FakeStorage | None = None,
    providers: _FakeProviders | None = None,
) -> WorkerDocumentContentResolver:
    return WorkerDocumentContentResolver(
        files,  # type: ignore[arg-type]
        storage or _FakeStorage(),  # type: ignore[arg-type]
        DocumentContentExtractor(),
        providers or _FakeProviders(),  # type: ignore[arg-type]
    )


async def test_resolve_fetches_parses_and_routes_in_one_call() -> None:
    """The whole contract: a ``file_id`` in, a parsed document plus the
    embedding model/key out -- the tuple ``build_knowledge_index_handler``
    unpacks."""
    file = _file()
    storage = _FakeStorage(b"a reasonably long line of plain source text")
    providers = _FakeProviders(model="minilm", api_key="")
    resolved = _resolver(files=_FakeFiles(file), storage=storage, providers=providers)
    ctx = _ctx()

    parsed, model, api_key = await resolved.resolve(ctx, file_id=file.id)

    assert isinstance(parsed, ParsedDocument)
    assert parsed.source_ext == ".txt"
    assert parsed.content_type == "text/plain"
    assert "".join(chunk.text for chunk in parsed.chunks)
    # The bytes are addressed by the file's OWN storage key (INV-F1), never
    # rebuilt from the id or the name at this layer.
    assert storage.keys == [file.storage_key.value]
    assert (model, api_key) == ("minilm", "")
    assert providers.calls == 1


async def test_resolve_raises_not_found_for_a_file_that_is_not_there() -> None:
    """A missing file row is answered before any byte is fetched -- storage
    is never asked for a key that was never resolved."""
    storage = _FakeStorage()
    providers = _FakeProviders()
    resolved = _resolver(files=_FakeFiles(None), storage=storage, providers=providers)

    with pytest.raises(NotFoundError):
        await resolved.resolve(_ctx(), file_id="missing-1")

    assert storage.keys == []
    assert providers.calls == 0


async def test_resolve_surfaces_the_unsupported_type_the_dispatch_table_raises() -> None:
    """The real ``_ROUTES`` table has no ``.docx`` entry (3.k1 deferred it),
    so a DOCX upload raises ``UnsupportedTypeError`` from HERE -- which is
    exactly the error the index handler now turns into a ``failed`` document
    instead of a redelivery loop (§1-ج). The route resolution is never
    reached: no credential lookup is spent on a file that cannot be parsed."""
    file = _file(
        name="contract.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    providers = _FakeProviders()
    resolved = _resolver(files=_FakeFiles(file), providers=providers)

    with pytest.raises(UnsupportedTypeError):
        await resolved.resolve(_ctx(), file_id=file.id)

    assert providers.calls == 0


async def test_resolve_surfaces_a_validation_error_for_an_empty_file() -> None:
    """The extractor's other typed failure (``knowledge.empty_content``) --
    the second of the two the handler treats as terminal."""
    file = _file()
    resolved = _resolver(files=_FakeFiles(file), storage=_FakeStorage(b""))

    with pytest.raises(ValidationError):
        await resolved.resolve(_ctx(), file_id=file.id)


async def test_extraction_runs_off_the_event_loop_thread() -> None:
    """``ContentExtractor`` is synchronous BY DESIGN and its port docstring
    puts the offload obligation on the caller: PyMuPDF/pandas/pytesseract
    block. Calling ``extract`` inline would freeze this worker's whole event
    loop -- Redis heartbeats, the ``XREADGROUP`` cycle, every concurrent
    task -- for the length of the parse.

    Asserting the parse ran on a DIFFERENT thread than the loop is the only
    check that fails if the ``asyncio.to_thread`` is ever dropped; a test on
    the returned value passes either way.
    """
    parsed_on: list[int] = []

    class _ThreadRecordingExtractor:
        def supports(self, *, content_type: str, filename: str) -> bool:
            return True

        def extract(self, *, data: bytes, filename: str, content_type: str) -> ParsedDocument:
            parsed_on.append(threading.get_ident())
            return ParsedDocument(
                source_ext=".txt", content_type=content_type, chunks=(), metadata={}
            )

    file = _file()
    resolver = WorkerDocumentContentResolver(
        _FakeFiles(file),  # type: ignore[arg-type]
        _FakeStorage(),  # type: ignore[arg-type]
        _ThreadRecordingExtractor(),
        _FakeProviders(),  # type: ignore[arg-type]
    )

    await resolver.resolve(_ctx(), file_id=file.id)

    assert parsed_on and parsed_on[0] != threading.get_ident()
