"""Agent-facing dependency Protocols — the DIP seam between agents and modules
(the user-chosen resolution for 4.6, "Framework Protocols (DIP)").

Agents coordinate over MODULE inbound ports (knowledge retrieval, media
requests, file access…). But ``AgentDependencies`` lives in this framework
kernel, and the ``framework-kernel`` import-linter contract forbids
``app.framework`` from importing ``app.modules``. So — Dependency Inversion —
the framework declares the NEUTRAL abstractions it needs from its injected
collaborators HERE, and the concrete module use-cases satisfy them
STRUCTURALLY (structural ``Protocol`` typing, no nominal import). The
Composition Root / orchestrator (4.7) binds a real
``KnowledgeRetrieval`` instance to a ``KnowledgeAccess``-typed field; mypy
checks that binding AT THE WIRING SITE, so a module-port shape change turns
*that* line red — no silent drift. The kernel stays at 8/0 while agents remain
fully typed.

Everything here imports framework/stdlib only. Grows demand-driven as agents
appear (the ``AgentDependencies`` doctrine): 4.6-a adds ``ResolvedLLM`` +
``KnowledgeAccess`` (the RAG agent's needs); 4.6-b/c add the file/media seams.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.llm_provider import LLMProvider
from app.framework.types import Json, Uuid


@dataclass(frozen=True, slots=True)
class ResolvedLLM:
    """A provider-resolved LLM ready to call: the concrete adapter bound to its
    model + api_key.

    This packs what ``ProviderResolver.resolve_llm`` yields
    (``tuple[LLMProvider, ResolvedProvider]``) into the single value an agent
    needs. The orchestrator (4.7) builds one per request after resolving the
    route + credential; an agent then calls
    ``binding.provider.stream(messages, LlmParams(model=binding.model, …), binding.api_key)``.
    A frozen carrier — no behaviour, like every value object in this kernel.
    """

    provider: LLMProvider
    model: str
    api_key: str


class RetrievedChunkView(Protocol):
    """The read shape an agent needs from one retrieved chunk. The knowledge
    module's ``RetrievedChunk`` (``document_id``/``chunk_id``/``text``/``score``/
    ``file_name``/``page_number``/``section``) satisfies this structurally —
    no framework→module import.

    **Read-only ``@property`` members, not bare annotations** (fixed at the
    4.7-b-2 wiring site, which is exactly where 4.6-a said this binding would
    finally be type-checked). A bare ``x: str`` on a Protocol declares a
    MUTABLE attribute, and every module type that satisfies these seams is a
    ``frozen=True`` dataclass whose attributes are read-only — so mypy
    rejected the binding outright. Properties state the truth (an agent only
    ever reads these) and accept frozen carriers. ``ResolvedKeyView`` in
    ``framework/providers/resolver.py`` already had it right; these did not.

    ``file_name``/``page_number``/``section`` (retrieval plan §3.1, س-19,
    ``P-18``) are ``| None`` for the same reason they are on
    ``RetrievedChunk``: an older point or a parser that never emitted one of
    these carries no such payload key.
    """

    @property
    def document_id(self) -> str: ...
    @property
    def chunk_id(self) -> str: ...
    @property
    def text(self) -> str: ...
    @property
    def score(self) -> float: ...
    @property
    def file_name(self) -> str | None: ...
    @property
    def page_number(self) -> int | None: ...
    @property
    def section(self) -> str | None: ...


class DocumentNamesView(Protocol):
    """The read shape an agent needs from a corpus-name listing (retrieval
    plan §3.6/§4 row 6, ``P-36``, س-23 = ج): up to a caller-chosen cap of
    this workspace's document file names, plus the workspace's full document
    count. The knowledge module's ``DocumentNames``
    (``modules/knowledge/ports/inbound.py``) satisfies this structurally —
    no framework→module import. Read-only ``@property`` members for the
    reason on ``RetrievedChunkView`` (frozen carriers only).
    """

    @property
    def names(self) -> Sequence[str]: ...
    @property
    def total(self) -> int: ...


class KnowledgeAccess(Protocol):
    """The retrieval capability a RAG-style agent needs. Structurally satisfied
    by ``app.modules.knowledge.ports.inbound.KnowledgeRetrieval.retrieve``; the
    binding is type-checked at the 4.7 composition site.

    ``file_ids`` (BE-RAG-005) is the retrieval SCOPE, in file ids — the
    vocabulary the caller already has. ``None`` is unscoped (the whole
    workspace corpus) and stays the default, so an agent that has no notion of
    a scope keeps calling this exactly as it did.

    ``space_id`` (spaces plan step 8) is keyword-only and NOT defaulted, and
    the difference from ``file_ids`` is the point: a missing pin narrows
    nothing, a missing space widens across spaces. An agent that does not know
    its space has to write that down.

    ``list_document_names`` (retrieval plan §3.6/§4 row 6, ``P-36``, س-23 = ج)
    is a SECOND method on this SAME seed, not a second injected port — the RAG
    agent still reaches corpus awareness through the one ``self.deps.knowledge``
    it already calls for retrieval (ح-11). ``limit`` is the caller's display
    cap, passed as an argument exactly like ``retrieve``'s ``k`` (س-24 — no
    ``Settings``/``os.getenv`` on either side of this seam).
    """

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid | None,
    ) -> Sequence[RetrievedChunkView]: ...

    async def list_document_names(
        self, ctx: ExecutionContext, *, limit: int
    ) -> DocumentNamesView: ...


class FileReadView(Protocol):
    """The read shape an agent needs to fetch a file's bytes: its content type,
    size, and storage key. Declares only what ``read_text_file`` touches (a
    subset of the files module's ``FileView``, which satisfies it
    structurally). Read-only properties for the reason on
    ``RetrievedChunkView``.

    ``space_id`` (spaces plan step 10) is the OWNING space, and it is here so
    that ``read_text_file`` can refuse a file outside the caller's space —
    finding 2-ح, the read-any-file-by-id leak. It is a fact ABOUT the file, not
    a scope the lookup applied: this seam keeps asking "is this file
    readable?", and who may read it stays the caller's policy, exactly as
    ``conversations`` decides its own pin rule (§3.5) from the same projected
    field. One honest port, two consumers, two policies.

    ``str | None`` mirrors the column until plan row 8-b, for the reason
    ``FileView.space_id`` carries it: a seam that promises ``str`` and hands
    back ``None`` passes mypy green and lies at runtime.
    """

    @property
    def content_type(self) -> str: ...
    @property
    def size_bytes(self) -> int: ...
    @property
    def storage_key(self) -> str: ...
    @property
    def space_id(self) -> str | None: ...


class FilesAccess(Protocol):
    """The workspace-file lookup a data/file agent needs — metadata only, and
    only for a ``ready`` file (``None`` otherwise). Structurally satisfied by
    ``app.modules.files.ports.inbound.FilesQuery.get_readable``. The actual
    bytes are fetched separately via the ``StorageProvider`` framework port,
    keyed by ``storage_key`` (see ``file_reading.read_text_file``).

    **Reach this only through ``read_text_file``** (enforced by
    ``test_content_agents.test_no_agent_reads_the_files_seam_directly``): the
    space check that closes finding 2-ح lives there, so an agent that called
    this seam itself would be reading by id across spaces again. The lookup is
    deliberately NOT space-scoped — see ``FileReadView.space_id``.
    """

    async def get_readable(self, ctx: ExecutionContext, file_id: Uuid) -> FileReadView | None: ...


class RequestedMediaView(Protocol):
    """The handle an agent gets back after queuing a generation job — enough to
    tell the caller which job to await (delivered later over WebSocket, Phase
    5). Satisfied structurally by a 4.7 view over the media module's
    ``MediaJob``. Read-only properties for the reason on
    ``RetrievedChunkView``."""

    @property
    def job_id(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def kind(self) -> str: ...


class MediaRequesting(Protocol):
    """Queue ONE heavy media-generation job (D-04 — event-driven; the agent
    never waits or streams). The 4.6-c media inbound seam (the media module's
    ``ports/inbound.py`` was deliberately deferred to here). Wired at 4.7 over
    ``RequestMedia`` + the outbox; ``params`` shape is validated downstream by
    ``GenParams`` (image: width/height/model · video: duration_seconds/model)."""

    async def request(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: str,
        prompt: str,
        params: Json,
    ) -> RequestedMediaView: ...
