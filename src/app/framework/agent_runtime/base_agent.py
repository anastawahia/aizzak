"""``BaseAgent`` + request/event carriers (02-port-contracts §3.2, D-05/13).

The inheritable half of the plugin contract (ABC, not Protocol — 02's header
draws exactly this line): an agent folder's ``agent.py`` subclasses
``BaseAgent``, binds its manifest via ``metadata = METADATA``, and implements
``initialize``/``run``. Instances are STATELESS per request: created by
``AgentRegistry.create`` with ``(ctx, deps)``, driven once, disposed
(FR-10/11) — the driving itself (the FR-15 state machine, dispose-always) is
the 4.2 lifecycle executor's work, not this class's.

**``run`` is ``def``, not ``async def`` — a conscious deviation from 02
§3.2's prose, mirroring the ``LLMProvider.stream`` port built since 2.1**
(the recorded 02 §1.1 doc-sync item). The reason here is subclass-author
compatibility under ``mypy --strict``: the canonical authoring style
(11-agent-authoring-guide §3) is an async *generator* method
(``async def run(...): yield ...``), whose called type is
``AsyncGenerator[AgentEvent, None]``. Against a ``def`` base returning
``AsyncIterator[AgentEvent]`` that override is a clean subtype; against an
``async def`` base (called type ``Coroutine[..., AsyncIterator]``) the same
guide-approved override is an incompatible-override ERROR. The
``_GuideStyleConformance`` proof below pins this: flip the base to ``async
def`` and the ``mypy src`` gate goes red naming this file. Callers are
unaffected either way: ``async for event in agent.run(req)``.

``AgentDependencies`` is the injected-ports bundle 02 §3.2's closing note
names (llm, tools, conversations, memory, knowledge… — never
infrastructure). It began FIELD-LESS in 4.1 (its members are exactly the
seams the orchestrator wires, and inventing them early would guess wrong five
times); 4.7 still owns the bulk of that growth. **4.4 adds the FIRST field —
``web_search: WebSearchProvider | None`` — because its ``WebSearchTool`` is
the first concrete tool with a port, and a ``BaseTool`` reaches its ports
ONLY through ``deps`` (the uniform ``tool_cls(deps)`` construction), while a
subclass may not narrow the ``deps`` parameter type (LSP/mypy). Growth is
demand-driven and additive, exactly as 4.1 anticipated ("additive, not a
rewrite").** It stays a frozen, slotted dataclass — every field OPTIONAL
(default ``None``) so ``AgentDependencies()`` keeps working for agents/tools
that need nothing, and frozen+slots still seals it against ad-hoc attributes
(the empty-bundle guarantee, preserved: no service locator smuggled in).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from app.framework.agent_runtime.deps_ports import (
    FilesAccess,
    KnowledgeAccess,
    MediaRequesting,
    ResolvedLLM,
)
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.storage_provider import StorageProvider
from app.framework.ports.web_search_provider import WebSearchProvider
from app.framework.types import Json, Uuid


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """One invocation's input (02 §3.2)."""

    conversation_id: Uuid | None
    input: Json
    stream: bool = False
    # `docs/spaces-backend-plan.md` step 12 — the space this invocation is
    # working in, when it opens a NEW thread. Defaulted (unlike every other
    # `space_id` this plan added) because it genuinely has a default meaning
    # here: a request that names a `conversation_id` inherits that thread's
    # space and must not be able to state a different one, so `None` is the
    # correct and common value rather than a forgotten one. The orchestrator
    # refuses the one combination that would file a thread nowhere —
    # no thread and no space — in `_open_turn`.
    space_id: Uuid | None = None


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """One streamed output event: token | tool_call | final | error (02 §3.2)."""

    type: str
    data: Json


@dataclass(frozen=True, slots=True)
class AgentDependencies:
    """The injected-ports bundle handed to every agent (02 §3.2's closing note).

    Started field-less in 4.1; 4.4 added ``web_search`` (the first tool with a
    port); **4.6 adds ``llm`` + ``knowledge`` — the first MODULE-facing seams,
    typed via the framework-owned DIP Protocols in ``deps_ports`` (``ResolvedLLM``
    is what the orchestrator resolves per request; ``KnowledgeAccess`` is
    satisfied structurally by the knowledge module's ``KnowledgeRetrieval``, so
    the kernel never imports a module — see ``deps_ports`` for why)**. 4.6-b/c
    and 4.7 grow the rest (files/media/conversations). Every field is optional
    so the bundle stays permissive (an agent/tool that needs nothing still
    constructs it as ``AgentDependencies()``); frozen+slots keeps it sealed
    against ad-hoc attributes.
    """

    web_search: WebSearchProvider | None = None
    llm: ResolvedLLM | None = None
    knowledge: KnowledgeAccess | None = None
    # BE-RAG-005 — the file ids this run's retrieval is scoped to, resolved by
    # the orchestrator from the thread's pins. A plain tuple and not a port:
    # it is per-request DATA, like ``llm``'s resolved binding, and the agent
    # only passes it through to ``knowledge.retrieve``.
    #
    # ``()`` and not ``None``: the empty tuple means UNSCOPED here — search
    # everything — because that is what an un-pinned thread and a caller with
    # no notion of pins both mean, and giving those two the same value is what
    # keeps the default (an ``AgentDependencies()`` with nothing set) correct.
    # The ``None``-vs-``[]`` distinction that matters lives one layer down, in
    # ``KnowledgeAccess``/``RetrieveContext``, where "pinned files that
    # resolved to no documents" has to stay distinguishable from "unpinned".
    knowledge_scope: tuple[Uuid, ...] = ()
    # س-32 (owner decision 2026-08-26) — the SPACE this run is working in,
    # resolved by the orchestrator from the turn's thread: the thread's own
    # space when the request continues one, `AgentRequest.space_id` when it
    # opens one. Per-request DATA like `knowledge_scope`, not a port.
    #
    # It is what closed the last three cross-space reads in the agents layer at
    # once — the RAG agent's retrieval, the corpus header it prepends, and the
    # file the Data-Analysis / File-Editing agents read — all three of which
    # passed `space_id=None` because there was nothing on this bundle to read.
    # The comments at those call sites each pointed here.
    #
    # ``None`` and not ``()``'s "unscoped" reading, and the difference from
    # ``knowledge_scope`` above is the whole of the decision: an absent pin
    # means "search everything I can see", an absent space means "I do not know
    # what I can see". The first is a legitimate default; the second is a
    # question that must not be answered by guessing, so an agent that finds
    # ``None`` here does not read and does not retrieve. It stays optional on
    # the dataclass so `AgentDependencies()` keeps constructing for the agents
    # and tools that touch neither files nor knowledge.
    space_id: Uuid | None = None
    # 4.6-b — the Data-Analysis / File-Editing agents read workspace files:
    # ``files`` (a DIP seam over the files module's ``FilesQuery``) yields
    # metadata + ``storage_key``; ``storage`` (a framework port, so no DIP
    # mirror) fetches the bytes. See ``file_reading.read_text_file``.
    files: FilesAccess | None = None
    storage: StorageProvider | None = None
    # 4.6-c — the Image / Video agents queue heavy generation jobs (D-04) via
    # this DIP seam; wired at 4.7 over the media module's ``RequestMedia``.
    media: MediaRequesting | None = None


class BaseAgent(ABC):
    """Contract every agent plugin inherits (02 §3.2; authoring guide 11 §3).

    Subclasses MUST set ``metadata = METADATA`` (their manifest's object —
    the ``PluginLoader`` enforces identity) and implement ``initialize`` and
    ``run``. ``dispose`` has a default no-op so trivial agents stay trivial;
    the 4.2 executor calls it ALWAYS, success or failure.
    """

    metadata: ClassVar[AgentMetadata]

    def __init__(self, ctx: ExecutionContext, deps: AgentDependencies) -> None:
        # The signature IS the per-request statelessness contract (FR-10/11):
        # everything an agent knows arrives here, per instance, per request.
        self.ctx = ctx
        self.deps = deps

    @abstractmethod
    async def initialize(self) -> None:
        """Load conversation/memory/context — no persistent RAM state."""

    @abstractmethod
    def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        """Yield ``AgentEvent``s (token/tool_call/final/error) for one request."""

    async def dispose(self) -> None:
        """Release resources; default no-op. The executor guarantees the call."""
        return None


if TYPE_CHECKING:

    class _GuideStyleConformance(BaseAgent):
        """mypy-gate proof that 11 §3's canonical async-generator ``run``
        override type-checks against this ABC — the reason ``run`` is ``def``
        (module docstring). Turning the base ``async def`` makes THIS class an
        incompatible override and the gate red."""

        async def initialize(self) -> None: ...

        async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
            yield AgentEvent(type="final", data={})
