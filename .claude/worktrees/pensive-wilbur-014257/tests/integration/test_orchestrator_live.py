"""End-to-end proof of the Phase 4.7 spine on a LIVE provider.

Everything in the chain is real: ``CompositionRoot.from_env()`` builds the
container, ``PluginLoader`` discovers the shipped agents by importlib, the
``AgentOrchestrator`` resolves the provider through ``SettingsProviderResolver``
and assembles ``AgentDependencies``, the real ``rag_agent`` runs, and the
tokens come off a real Ollama server through the real ``OllamaLLM`` adapter
using the ``LlmChunk`` contract 4.7-a amended.

Nothing is faked and nothing is stubbed — which is the point. The hermetic
suites prove each piece in isolation; only this one proves they were wired to
each other correctly, and a wiring bug is precisely the class of defect that
unit tests with mutually-agreeing fakes cannot see.

**No database is required, and that is by construction rather than luck:**
Ollama is in the root's ``keyless_providers``, so ``ProviderResolver`` skips
``CredentialResolver`` entirely and never touches Postgres. Marked
``live_ollama``, so it skips cleanly wherever Ollama is absent (CI).

**Since 2.10, ``deps.knowledge`` is ALWAYS wired** (``KnowledgeRetrievalService``
over a real ``ExternalEmbeddingProvider`` -- ``framework/di/composition_root.py``'s
own module docstring), so ``rag_agent`` no longer degrades to model-only by
construction the way this module's docstring used to describe. Exercising
that retrieval path for real needs a live central embedding service too --
``test_the_wired_orchestrator_runs_the_rag_agent_against_live_ollama`` below
is additionally marked ``live_embedding`` (the ``live_ollama``/``live_qdrant``
precedent: TCP-reachable -> real, unreachable -> clean skip) and, absent
Docker (09-testing-strategy §7's own documented limit -- the embedding
service is Docker-only, ``services/embedding/Dockerfile`` bakes its model at
build time), skips in every environment this repo's own gates run in today.
The other two tests below touch neither seam and are unaffected.
"""

from __future__ import annotations

import pytest

from app.framework.agent_runtime.base_agent import AgentRequest
from app.framework.context.execution_context import ExecutionContext
from app.framework.di.composition_root import CompositionRoot
from app.framework.errors import NotFoundError
from app.modules.knowledge.domain.collections import knowledge_collection
from tests.integration.conftest import LiveOllama

pytestmark = [pytest.mark.live_ollama]

# The pinned local model 2.10 bakes into the embedding service's image
# (``services/embedding/Dockerfile``) -- the SAME literal
# ``EmbeddingServiceSettings.model``'s own default carries, duplicated here
# rather than imported (a test-fixture literal, the ``live_ollama.model``
# precedent above).
_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_WORKSPACE = "018f0000-0000-7000-8000-000000000001"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=_WORKSPACE,
        user_id="018f0000-0000-7000-8000-0000000000ff",
        correlation_id="018f0000-0000-7000-8000-0000000000cc",
        roles=frozenset({"member"}),
        request_id="req-live-1",
    )


@pytest.fixture
def root(monkeypatch: pytest.MonkeyPatch, live_ollama: LiveOllama) -> CompositionRoot:
    """A real boot routed at the live Ollama model, reusing the same
    ``live_ollama`` fixture (and therefore the same reachability + model probe)
    the rest of the live LLM suite is gated on.

    ``OLLAMA_BASE_URL`` must be taken FROM that fixture rather than left to
    default: ``OllamaSettings.base_url`` defaults to ``http://ollama:11434``,
    the Docker-Compose service name (correct for deployment, unreachable from
    a dev shell), whereas the harness probes ``127.0.0.1``. Leaving it to the
    default here does not fail fast — it produces a 60s connect timeout
    surfacing as ``agent.failed``, which looks like a broken orchestrator
    rather than a misaimed URL.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("OLLAMA_BASE_URL", live_ollama.settings.base_url)
    monkeypatch.setenv(
        "PROVIDER_ROUTING",
        f'{{"llm":{{"default":{{"provider":"ollama","model":"{live_ollama.model}"}}}}}}',
    )
    return CompositionRoot.from_env()


@pytest.fixture
async def root_with_knowledge(
    monkeypatch: pytest.MonkeyPatch,
    live_ollama: LiveOllama,
    live_embedding: str,
    live_qdrant: str,
) -> CompositionRoot:
    """The ``root`` fixture's shape, PLUS a live central embedding service
    AND a live Qdrant (module docstring): 2.10 wires ``deps.knowledge`` for
    real, so exercising ``rag_agent``'s retrieval seam on this container
    needs both. Skips cleanly wherever either is unreachable (``live_
    embedding``/``live_qdrant``), the ``live_ollama`` precedent applied to
    the two ADDITIONAL live dependencies this one test now has.

    ``EMBEDDING_SERVICE_URL``/``QDRANT_URL`` are taken FROM their fixtures
    rather than left to default (the ``OLLAMA_BASE_URL`` paragraph above,
    verbatim reasoning): both settings default to their Docker-Compose
    service names, unreachable from a dev shell.

    ``RetrieveContext`` searches Qdrant directly with no lazy collection
    creation of its own (``qdrant_store.py``'s own docstring: a missing
    collection is a provisioning fault, not a normal "not found" a caller
    should branch on) — so this fixture provisions the empty hybrid
    collection this workspace's call searches, the SAME step
    ``IndexDocument.execute`` performs for a real document.
    """
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-project")
    monkeypatch.setenv("OLLAMA_BASE_URL", live_ollama.settings.base_url)
    monkeypatch.setenv("EMBEDDING_SERVICE_URL", live_embedding)
    monkeypatch.setenv("QDRANT_URL", live_qdrant)
    monkeypatch.setenv(
        "PROVIDER_ROUTING",
        (
            f'{{"llm":{{"default":{{"provider":"ollama","model":"{live_ollama.model}"}}}},'
            f'"embedding":{{"default":{{"provider":"embedding-local","model":"{_EMBEDDING_MODEL}"}}}}}}'
        ),
    )
    root = CompositionRoot.from_env()
    await root.vector_store.ensure_hybrid_collection(
        knowledge_collection(_WORKSPACE), 384, distance="cosine"
    )
    return root


@pytest.mark.live_embedding
@pytest.mark.live_qdrant
async def test_the_wired_orchestrator_runs_the_rag_agent_against_live_ollama(
    root_with_knowledge: CompositionRoot,
) -> None:
    """The full 4.7 spine, end to end, with zero test doubles anywhere --
    including retrieval, since 2.10 (module docstring)."""
    events = [
        e
        async for e in await root_with_knowledge.orchestrator.invoke(
            _ctx(),
            "rag_agent",
            AgentRequest(
                conversation_id=None,
                input={"text": "Reply with the single word: hello"},
            ),
        )
    ]

    assert [e.type for e in events[:-1]] == ["token"] * (len(events) - 1)
    assert events[-1].type == "final"
    # A real model produced real text.
    assert events[-1].data["text"].strip() != ""
    # The workspace's `knowledge` collection is real but EMPTY (the fixture
    # provisions it, nothing is ever indexed into it), so a real -- not
    # unwired -- retrieval still, honestly, finds nothing to cite.
    assert events[-1].data["citations"] == []


async def test_an_unknown_agent_raises_404_from_the_wired_orchestrator(
    root: CompositionRoot,
) -> None:
    """The pre-flight channel, proven on the real container: this is what lets
    Phase 6 answer 404 instead of streaming a broken body."""
    with pytest.raises(NotFoundError):
        await root.orchestrator.invoke(
            _ctx(), "no_such_agent", AgentRequest(conversation_id=None, input={})
        )


async def test_a_media_agent_needs_no_llm_credential_on_the_real_container(
    root: CompositionRoot,
) -> None:
    """``image_agent`` declares no ``chat`` capability, so the orchestrator
    resolves no LLM for it — it gets all the way to the media seam with no
    credential in play at all (4.7-b's design, on the real container).

    Since 4.7-d-2 that seam is the REAL ``MediaRequestService``, so the failure
    here is a 422 from ``GenParams``, not the old ``common.internal``/500 for an
    unbound seam — which is itself the proof that the wiring landed: an unwired
    seam could not produce a domain validation error.

    **Still database-free, deliberately**, keeping this module's opening
    promise: ``RequestMedia`` validates the params BEFORE it persists anything
    (INV-MJ3), so this request never reaches Postgres. The DB-backed half of the
    seam is proven in ``test_media_request_seam.py`` under ``live_db``.

    It also pins a real product gap this step made visible for the first time:
    an image request carrying only a prompt is REJECTED, because ``GenParams``
    treats ``width``/``height`` as required rather than defaulted. Whoever
    builds the Phase-6 media router (or a natural-language front door) has to
    supply dimensions; the platform will not invent them.
    """
    events = [
        e
        async for e in await root.orchestrator.invoke(
            _ctx(),
            "image_agent",
            AgentRequest(conversation_id=None, input={"prompt": "a red bicycle"}),
        )
    ]

    assert [e.type for e in events] == ["error"]
    assert events[0].data["status"] == 422
    assert events[0].data["code"] == "common.validation_error"
