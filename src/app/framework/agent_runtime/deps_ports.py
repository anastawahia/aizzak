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


class RoutedAnswerView(Protocol):
    """The read shape an agent needs from a routed question (retrieval plan
    §3.4/§4 row 11, ``P-21``, س-16 = أ): which intent the question was
    classified as, the CONTENT route's chunks, and the SUMMARIZE_DOC route's
    queued job id. The knowledge module's ``RoutedAnswer``
    (``modules/knowledge/ports/inbound.py``) satisfies this structurally — no
    framework→module import. Read-only ``@property`` members for the reason on
    ``RetrievedChunkView`` (frozen carriers only).

    ``intent`` is ``str`` here and a ``StrEnum`` there, which is exactly the
    widening this seam exists for: the framework must not learn the module's
    vocabulary as a TYPE, and an agent comparing against a string literal is
    reading the same value the module wrote. ``summary_job_id`` is ``Uuid |
    None`` — ``None`` whenever the summarisation route did not run, including
    when the question WAS classified as a summarisation but its target
    document could not be identified (plan step 13/14's job).

    ``clarification_options`` (retrieval plan §3.5/§4 row 14, ``P-04``, س-18
    = أ) is that last case made actionable: the file names the question could
    have meant, when the module's resolver refused to choose between them.
    Non-empty means the honest answer this turn is a QUESTION back — "which
    of these did you mean?" — and the agent renders it as ordinary answer
    text on the existing ``token``/``final`` stream. Nothing about that
    stream changes; a structured ``clarification`` event is out of scope by
    س-18 (plan §7), which is why this is a plain sequence of names and not a
    new event shape sitting on this seam.

    ``summary_target_name`` (scenarios plan §4, ب-7أ, gap ف-2) is the
    file name behind ``summary_job_id`` — what the module RESOLVED the
    build to, so a receipt can say which document it accepted.

    **It is the reason an agent may utter this name at all.** An agent
    reading a file name off the user's own question and echoing it back
    would be asserting a resolution nobody performed; that is why the
    receipt named nothing before this field. A name that arrived ACROSS
    this seam is different in kind — the module ran its resolver, chose
    one document, and is reporting its own choice. The agent repeats what
    it was told and still resolves nothing itself.

    ``None`` whenever there is no build to name (every CONTENT answer,
    every clarification) and whenever the module queued one but could not
    name the target — a document whose file is no longer readable. Both
    mean "no name arrived", and the agent's answer to both is its
    existing unnamed wording, never a blank left where a name would go.

    ``summary_blocked`` (scenarios plan section 5, ب-4ب, gap ف-7) is why
    a build the module was asked for was NOT queued. ``str`` here and a
    ``StrEnum`` there, for ``intent``'s reason word for word: the
    framework must not learn the module's vocabulary as a TYPE, and an
    agent comparing against a string literal reads the same value the
    module wrote.

    **And the widening buys forward compatibility this field will need.**
    A reason this agent has no sentence for — a third refusal added on
    the module side — must degrade to the neutral wording rather than
    raise or render a blank. Crossing as a string is what makes an
    unrecognised value merely unrecognised; an imported enum would make
    the agent's release the thing gating the module's.

    ``None`` on every outcome that was not refused, which is all of them
    but one. It is never set together with ``summary_job_id``: a build is
    accepted or refused, not both.

    ``stored_summary_text`` (scenarios plan section 6, ب-8, gap ف-3) is the
    summary this question asked for, ALREADY BUILT and read back in the turn
    that asked — the one routed outcome that carries an answer rather than a
    fact about a build.

    **It arrives rendered, and that is deliberate.** Everything else on this
    seam is deliberately raw, because س-18 puts the wording with whoever
    speaks; this is not the module wording anything, it is the artefact
    itself, framed the way the same artefact is framed when a worker delivers
    it into a thread. An agent that composed that framing itself would be
    writing the second of two deliveries of one stored row, and the two would
    drift.

    So the agent EMITS it and does not edit it: no corpus header, no
    prefix, no truncation notice of its own — the notice, when the build hit
    the map ceiling, is already inside this string.

    ``None`` whenever nothing was stored, which is every other outcome —
    including a build that was queued, since a queued build is what "nothing
    was stored" leads to.
    """

    @property
    def intent(self) -> str: ...
    @property
    def chunks(self) -> Sequence[RetrievedChunkView]: ...
    @property
    def summary_job_id(self) -> Uuid | None: ...
    @property
    def clarification_options(self) -> Sequence[str]: ...
    @property
    def summary_target_name(self) -> str | None: ...
    @property
    def summary_blocked(self) -> str | None: ...
    @property
    def stored_summary_text(self) -> str | None: ...


class KnowledgeAccess(Protocol):
    """The retrieval capability a RAG-style agent needs. Structurally satisfied
    by ``app.modules.knowledge.ports.inbound.KnowledgeRetrieval.retrieve``; the
    binding is type-checked at the 4.7 composition site.

    ``file_ids`` (BE-RAG-005) is the retrieval SCOPE, in file ids — the
    vocabulary the caller already has. ``None`` is unscoped (the whole
    workspace corpus) and stays the default, so an agent that has no notion of
    a scope keeps calling this exactly as it did.

    ``space_id`` (spaces plan step 8) is keyword-only, NOT defaulted and — since
    س-32 (owner decision 2026-08-26) — NOT nullable, and the difference from
    ``file_ids`` is the point: a missing pin narrows nothing, a missing space
    widened across spaces. An agent that does not know its space cannot call
    this at all now, which is the only shape that makes the isolation real: it
    reads its space from ``AgentDependencies.space_id``, filled by the
    orchestrator from the thread the turn belongs to, and a turn with no space
    on the bundle does not retrieve (see the RAG agent).

    ``list_document_names`` (retrieval plan §3.6/§4 row 6, ``P-36``, س-23 = ج)
    is a SECOND method on this SAME seed, not a second injected port — the RAG
    agent still reaches corpus awareness through the one ``self.deps.knowledge``
    it already calls for retrieval (ح-11).

    ``k`` is OPTIONAL since retrieval plan §4 row 18 (``P-40``): omitting it
    asks the module for however many chunks the DEPLOYMENT is configured to
    return (``Settings.retrieval.default_k``). This default is the whole
    mechanism by which a Settings-owned number can reach an agent at all —
    an agent reads no configuration and imports nothing (ح-11), so before it
    existed the RAG agent had no choice but to hold its own ``_TOP_K = 5``.

    ``list_document_names`` takes the SAME required ``space_id`` since س-32:
    the corpus header it feeds names the files a follow-up question could be
    answered from, so a header listing another space's files is the same leak
    as retrieving from one — it just says it in words instead of chunks.

    ``limit`` is OPTIONAL for exactly that reason and in exactly that shape.
    It used to be required, on the argument that a DISPLAY cap the plan itself
    fixes (§3.6, "سقف عرض 50 اسمًا") is not a retrieval tuning knob — true
    about the NUMBER, and beside the point about where it lived: the cap sat in
    the agent as ``_MAX_CORPUS_NAMES = 50``, so shortening a header was a code
    edit in the agents layer, the one layer ``Settings`` cannot reach. Omitting
    it now asks the module for the deployment's configured cap
    (``Settings.retrieval.max_corpus_names``), and no ``Settings``/``os.getenv``
    is read on either side of this seam (س-24).

    ``answer`` (retrieval plan §3.4/§4 row 11, ``P-21``, س-16 = أ) is the
    THIRD, and it is the one the RAG agent actually calls: it takes
    ``retrieve``'s retrieval arguments unchanged — plus one that is not
    retrieval's at all (``conversation_id``, below) — and returns a
    ``RoutedAnswerView``,
    because the classification and the dispatch between the module's two
    routes are the MODULE's business. An agent that classified for itself
    would have to import the classifier and then hold a second seam for the
    route it chose — which is precisely the convention ح-11 records this
    agent as keeping. ``retrieve`` stays on the seam for callers that mean
    retrieval and nothing else.

    ``answer``'s ``conversation_id`` (`F-7`) is the ONE argument on this seam
    that is not about retrieval, and that is exactly why it sits on ``answer``
    alone: a summarisation is built by a worker minutes after the turn has
    ended, so the module has to be told where to deliver it, and the agent is
    the only layer that knows which thread it is answering in
    (``AgentRequest.conversation_id``). It is data the agent passes through
    and never interprets — the ``knowledge_scope`` precedent exactly — and it
    is defaulted, so a caller with no thread keeps calling this as it did.
    """

    async def retrieve(
        self,
        ctx: ExecutionContext,
        query: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid,
    ) -> Sequence[RetrievedChunkView]: ...

    async def answer(
        self,
        ctx: ExecutionContext,
        question: str,
        k: int | None = None,
        file_ids: Sequence[Uuid] | None = None,
        *,
        space_id: Uuid,
        conversation_id: Uuid | None = None,
    ) -> RoutedAnswerView: ...

    async def list_document_names(
        self, ctx: ExecutionContext, *, space_id: Uuid, limit: int | None = None
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
