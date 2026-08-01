"""Unit tests for ``framework/providers/resolver.py`` (Phase 2.9, 02 §3.5).

Purely hermetic — the resolver performs no I/O of its own, so there is no
``live_*`` marker here at all (the 2.7 precedent: a live tag would only exist
to skip a service that does not exist). Focus areas, one per section below:

* the strict construction-time parse of ``provider_routing`` (the v2.8
  lesson: a typo in an untyped table must refuse to BOOT, never misroute
  silently at request N);
* fail-closed lookup semantics (exact → ``default`` → 422; D-16: no
  cross-provider fallback, no "unknown → ollama");
* the keyless/keyed key-resolution split (spy proves the keyless branch
  never touches the resolver; errors pass through untranslated);
* the cross-boundary structural proof: the REAL
  ``credentials.application.ResolveCredential`` driven through the
  ``KeyResolver`` seam end to end — the Liskov check that the ``layers``
  contract forbids expressing inside ``src`` (framework may not import the
  module; this test file may import both sides).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import FrozenInstanceError, dataclass, fields
from typing import cast

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ConflictError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingProvider, EmbeddingResult
from app.framework.ports.llm_provider import (
    LlmChunk,
    LlmMessage,
    LlmParams,
    LLMProvider,
    LlmResult,
)
from app.framework.providers import (
    KeyResolver,
    ProviderResolver,
    ResolvedProvider,
    SettingsProviderResolver,
)
from app.framework.types import Json
from app.modules.credentials.application.use_cases import ResolveCredential
from app.modules.credentials.domain.entities import Credential
from app.modules.credentials.domain.value_objects import (
    CipherRef,
    CredentialScope,
    CredentialStatus,
    ProviderRef,
)

# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class FakeLLMProvider:
    """Structurally an ``LLMProvider``; every method raises on purpose.

    The resolver's contract is to RETURN the injected adapter, never to call
    it — and never to consult ``supports()`` (decision 7: a routing hint, not
    a guarantee). A resolver that starts calling any of these goes red here.
    """

    def __init__(self, name: str) -> None:
        self.provider = name

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        raise AssertionError("the resolver must not call the provider")

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        raise AssertionError("the resolver must not call the provider")

    def supports(self, capability: str) -> bool:
        raise AssertionError("decision 7: the resolver never consults supports()")


class FakeEmbeddingProvider:
    """Structurally an ``EmbeddingProvider``; same never-called contract."""

    def __init__(self, name: str) -> None:
        self.provider = name

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        raise AssertionError("the resolver must not call the provider")

    def dimensions(self, model: str) -> int:
        raise AssertionError("the resolver must not call the provider")


@dataclass(frozen=True, slots=True)
class _Key:
    """Minimal frozen carrier satisfying ``ResolvedKeyView`` structurally."""

    api_key: str


class SpyKeyResolver:
    """Records every delegation — the keyless branch must leave it empty."""

    def __init__(self, api_key: str = "sk-spy") -> None:
        self._api_key = api_key
        self.calls: list[tuple[ExecutionContext, str]] = []

    async def resolve(self, ctx: ExecutionContext, provider: str) -> _Key:
        self.calls.append((ctx, provider))
        return _Key(api_key=self._api_key)


class RaisingKeyResolver:
    """Simulates ``ResolveCredential``'s no-credential outcome — which since
    6.2 is ``credentials.none_available``/409, not a 404."""

    async def resolve(self, ctx: ExecutionContext, provider: str) -> _Key:
        raise ConflictError(
            f"no active credential for provider {provider}",
            code="credentials.none_available",
        )


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset(),
    )


_DEFAULT_ROUTING: Json = {"llm": {"default": {"provider": "openai", "model": "gpt-test"}}}


def make_resolver(
    routing: Json | None = None,
    *,
    embedding: dict[str, EmbeddingProvider] | None = None,
    key_resolver: KeyResolver | None = None,
    keyless: frozenset[str] = frozenset({"ollama"}),
) -> tuple[SettingsProviderResolver, SpyKeyResolver]:
    """Build a resolver over the standard two fake LLM adapters + a fresh spy."""
    spy = SpyKeyResolver()
    llm_providers: dict[str, LLMProvider] = {
        "ollama": FakeLLMProvider("ollama"),
        "openai": FakeLLMProvider("openai"),
    }
    resolver = SettingsProviderResolver(
        routing=_DEFAULT_ROUTING if routing is None else routing,
        llm_providers=llm_providers,
        embedding_providers={} if embedding is None else embedding,
        key_resolver=spy if key_resolver is None else key_resolver,
        keyless_providers=keyless,
    )
    return resolver, spy


# --------------------------------------------------------------------------- #
# Contract literals (02 §3.5)                                                 #
# --------------------------------------------------------------------------- #


def test_resolved_provider_is_a_frozen_slots_triple() -> None:
    resolved = ResolvedProvider(provider="openai", model="m", api_key="k")
    assert [f.name for f in fields(ResolvedProvider)] == ["provider", "model", "api_key"]
    with pytest.raises(FrozenInstanceError):
        resolved.api_key = "x"  # type: ignore[misc]
    assert not hasattr(resolved, "__dict__")


async def test_settings_resolver_is_usable_through_the_port_type() -> None:
    """The typed assignment is the structural claim; the call through the
    port-typed name proves both §3.5 signatures at runtime (keyword-only
    ``capability``/``model``, tuple return)."""
    resolver, _ = make_resolver()
    port: ProviderResolver = resolver
    provider, resolved = await port.resolve_llm(make_ctx(), capability="default", model=None)
    assert provider.provider == "openai"
    assert resolved == ResolvedProvider(provider="openai", model="gpt-test", api_key="sk-spy")


# --------------------------------------------------------------------------- #
# Construction-time strictness (decision 2/3: die at boot, loudly)            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("routing", "fragment"),
    [
        pytest.param({"lllm": {}}, "unknown namespace 'lllm'", id="typo-namespace"),
        pytest.param(
            {"llm": "openai"}, "must be an object of capability", id="namespace-not-object"
        ),
        pytest.param(
            {"llm": {"default": "openai"}},
            "exactly the keys",
            id="abbreviated-string-entry",
        ),
        pytest.param(
            {"llm": {"default": {"provider": "openai"}}}, "exactly the keys", id="missing-model"
        ),
        pytest.param(
            {"llm": {"default": {"provider": "openai", "model": "m", "modle": "x"}}},
            "exactly the keys",
            id="typo-extra-key",
        ),
        pytest.param(
            {"llm": {"default": {"provider": "", "model": "m"}}},
            ".provider",
            id="empty-provider",
        ),
        pytest.param(
            {"llm": {"default": {"provider": 7, "model": "m"}}},
            ".provider",
            id="non-str-provider",
        ),
        pytest.param(
            {"llm": {"default": {"provider": "openai", "model": "  "}}},
            ".model",
            id="blank-model",
        ),
        pytest.param(
            {"llm": {"default": {"provider": "openai", "model": 3}}},
            ".model",
            id="non-str-model",
        ),
        pytest.param(
            {"llm": {"default": {"provider": "gemini", "model": "m"}}},
            "no wired adapter",
            id="unwired-provider",
        ),
        pytest.param(
            {"llm": {"": {"provider": "openai", "model": "m"}}},
            "capability keys",
            id="empty-capability",
        ),
        pytest.param(
            {"llm": {5: {"provider": "openai", "model": "m"}}},
            "capability keys",
            id="non-str-capability",
        ),
        pytest.param(
            {"embedding": {"rag": {"provider": "openai", "model": "m"}}},
            "only the 'default' route",
            id="embedding-non-default-capability",
        ),
        pytest.param(
            {"embedding": {"default": {"provider": "openai", "model": "m"}}},
            "no wired adapter",
            id="embedding-route-checked-against-embedding-mapping",
        ),
    ],
)
def test_a_malformed_routing_table_refuses_to_construct(routing: Json, fragment: str) -> None:
    """Each case is a config mistake that, unguarded, would silently misroute
    or 500 at request N. The last case proves llm/embedding routes are checked
    against their OWN adapter mapping ('openai' IS wired as an LLM here)."""
    with pytest.raises(ValidationError) as exc_info:
        make_resolver(routing)
    assert fragment in str(exc_info.value)
    assert exc_info.value.status == 422


@pytest.mark.parametrize(
    "routing",
    [
        pytest.param(None, id="none"),
        pytest.param(123, id="int"),
        pytest.param(True, id="bool"),
        pytest.param(3.14, id="float"),
        pytest.param([], id="empty-list"),
        pytest.param(["llm", "embedding"], id="list-of-namespace-names"),
        pytest.param(("llm", "embedding"), id="tuple-of-namespace-names"),
        pytest.param({"llm", "embedding"}, id="set-literal-one-brace-from-dict"),
    ],
)
def test_a_non_object_routing_value_refuses_to_construct(routing: object) -> None:
    """Root-cause-A regression (2.9 verifier finding, R6 family): a non-dict
    ``routing`` used to crash as a raw TypeError/AttributeError — worst, the
    iterable shapes carrying exactly the right namespace NAMES (the set
    literal is one brace away from the dict form) sailed PAST the namespace
    loop and detonated later at ``.get``. All must refuse construction as a
    422 naming ``provider_routing`` itself."""
    with pytest.raises(ValidationError) as exc_info:
        SettingsProviderResolver(
            routing=cast("Json", routing),
            llm_providers={"openai": FakeLLMProvider("openai")},
            embedding_providers={},
            key_resolver=SpyKeyResolver(),
            keyless_providers=frozenset(),
        )
    assert "provider_routing: must be an object" in str(exc_info.value)
    assert exc_info.value.status == 422


def test_the_old_abbreviated_env_example_shape_is_rejected_by_name() -> None:
    """`{"llm":{"default":"openai"}}` (the pre-2.9 ``.env.example`` literal)
    carries no model, so it cannot satisfy ``ResolvedProvider.model`` — it
    must fail at construction, not resolve to something invented."""
    with pytest.raises(ValidationError) as exc_info:
        make_resolver({"llm": {"default": "openai"}})
    assert "provider_routing.llm['default']" in str(exc_info.value)


async def test_an_empty_routing_table_constructs_and_fails_closed_at_call_time() -> None:
    """`from_env`'s default (``provider_routing={}``) must stay bootable
    (decision 3); the failure moves to the request, fail-closed."""
    resolver, _ = make_resolver({})
    with pytest.raises(ValidationError) as exc_info:
        await resolver.resolve_llm(make_ctx(), capability="default")
    assert exc_info.value.status == 422


# --------------------------------------------------------------------------- #
# resolve_llm lookup semantics (decisions 4/5)                                #
# --------------------------------------------------------------------------- #


async def test_an_exact_capability_match_wins_over_default_and_returns_the_injected_instance() -> (
    None
):
    ollama = FakeLLMProvider("ollama")
    openai = FakeLLMProvider("openai")
    spy = SpyKeyResolver()
    resolver = SettingsProviderResolver(
        routing={
            "llm": {
                "chat": {"provider": "ollama", "model": "llama3.2:1b"},
                "default": {"provider": "openai", "model": "gpt-test"},
            }
        },
        llm_providers={"ollama": ollama, "openai": openai},
        embedding_providers={},
        key_resolver=spy,
        keyless_providers=frozenset({"ollama"}),
    )
    provider, resolved = await resolver.resolve_llm(make_ctx(), capability="chat")
    assert provider is ollama
    assert resolved == ResolvedProvider(provider="ollama", model="llama3.2:1b", api_key="")


async def test_an_unrouted_capability_falls_back_to_the_default_route() -> None:
    resolver, _ = make_resolver()
    provider, resolved = await resolver.resolve_llm(make_ctx(), capability="not-routed")
    assert (provider.provider, resolved.provider, resolved.model) == (
        "openai",
        "openai",
        "gpt-test",
    )


async def test_an_unrouted_capability_with_no_default_fails_closed() -> None:
    """D-16 / refs §6: no "unknown → ollama", no cross-provider fallback —
    an unresolvable capability is a 422, never a guess."""
    resolver, _ = make_resolver({"llm": {"chat": {"provider": "openai", "model": "m"}}})
    with pytest.raises(ValidationError) as exc_info:
        await resolver.resolve_llm(make_ctx(), capability="other")
    assert exc_info.value.status == 422
    assert "no llm route for capability 'other'" in str(exc_info.value)


async def test_a_model_override_replaces_the_model_but_never_the_provider() -> None:
    """FR-73: provider choice never leaves configuration; ``model=`` swaps
    only the model within the routed provider."""
    resolver, _ = make_resolver()
    provider, resolved = await resolver.resolve_llm(
        make_ctx(), capability="default", model="gpt-custom"
    )
    assert provider.provider == "openai"
    assert (resolved.provider, resolved.model) == ("openai", "gpt-custom")


async def test_a_blank_model_override_is_rejected_as_caller_error() -> None:
    """422 here beats the 404→502 the provider would return — which would
    blame the vendor for our caller's bug."""
    resolver, _ = make_resolver()
    with pytest.raises(ValidationError):
        await resolver.resolve_llm(make_ctx(), capability="default", model="   ")


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(123, id="int"),
        pytest.param(True, id="bool"),
        pytest.param([], id="list"),
        pytest.param({}, id="dict"),
        pytest.param(3.14, id="float"),
    ],
)
async def test_a_non_string_model_override_is_rejected_on_both_ports(model: object) -> None:
    """Root-cause-B regression (2.9 verifier finding, R6 family): ``model=``
    is a CALL-TIME argument no Pydantic layer ever filters; a Phase-4 caller
    forwarding a user-supplied "preferred model" from an ``Any``-typed body
    used to crash as a raw ``AttributeError`` on ``.strip``. The guard is the
    shared ``_override_or_routed`` — asserted through both port methods."""
    resolver, _ = make_resolver()
    with pytest.raises(ValidationError) as exc_llm:
        await resolver.resolve_llm(make_ctx(), capability="default", model=cast("str", model))
    assert "model override must be a non-empty string" in str(exc_llm.value)
    assert exc_llm.value.status == 422

    embedding_resolver = SettingsProviderResolver(
        routing={"embedding": {"default": {"provider": "e", "model": "m"}}},
        llm_providers={},
        embedding_providers={"e": FakeEmbeddingProvider("e")},
        key_resolver=SpyKeyResolver(),
        keyless_providers=frozenset({"e"}),
    )
    with pytest.raises(ValidationError):
        await embedding_resolver.resolve_embedding(make_ctx(), model=cast("str", model))


@pytest.mark.parametrize(
    "capability",
    [
        pytest.param([1, 2], id="list-unhashable"),
        pytest.param({"a": 1}, id="dict-unhashable"),
        pytest.param(None, id="none"),
        pytest.param(123, id="int"),
        pytest.param("   ", id="blank"),
    ],
)
async def test_a_non_string_capability_is_rejected_not_guessed(capability: object) -> None:
    """Root-cause-C regression (2.9 verifier finding) + a deliberate
    tightening: unhashable values used to crash raw at the dict lookup, while
    None/int/blank silently fell through to the ``default`` route — a guess
    that masks a caller bug. All now fail closed 422 (D-16's fail-closed
    posture applied to the argument, not just the table)."""
    resolver, _ = make_resolver()
    with pytest.raises(ValidationError) as exc_info:
        await resolver.resolve_llm(make_ctx(), capability=cast("str", capability))
    assert "capability must be a non-empty string" in str(exc_info.value)
    assert exc_info.value.status == 422


# --------------------------------------------------------------------------- #
# Key resolution (decision 6: keyless skip, untranslated pass-through)        #
# --------------------------------------------------------------------------- #


async def test_a_keyless_provider_never_touches_the_key_resolver_and_gets_an_empty_key() -> None:
    resolver, spy = make_resolver(
        {"llm": {"default": {"provider": "ollama", "model": "llama3.2:1b"}}}
    )
    _, resolved = await resolver.resolve_llm(make_ctx(), capability="default")
    assert resolved.api_key == ""
    assert spy.calls == []


async def test_a_keyed_provider_resolves_through_the_key_resolver_once() -> None:
    resolver, spy = make_resolver()
    ctx = make_ctx()
    _, resolved = await resolver.resolve_llm(ctx, capability="default")
    assert resolved.api_key == "sk-spy"
    assert spy.calls == [(ctx, "openai")]


async def test_keylessness_is_per_provider_name_not_global() -> None:
    """The same resolver serves both branches: ollama skips, openai delegates."""
    resolver, spy = make_resolver(
        {
            "llm": {
                "local": {"provider": "ollama", "model": "llama3.2:1b"},
                "default": {"provider": "openai", "model": "gpt-test"},
            }
        }
    )
    ctx = make_ctx()
    _, local = await resolver.resolve_llm(ctx, capability="local")
    _, cloud = await resolver.resolve_llm(ctx, capability="default")
    assert (local.api_key, cloud.api_key) == ("", "sk-spy")
    assert [provider for _, provider in spy.calls] == ["openai"]


async def test_a_key_resolution_failure_passes_through_untranslated() -> None:
    """``credentials.none_available`` is already the correct taxonomy —
    wrapping it (into 502/agent.failed or a resolver-flavoured error) would
    blame the provider for a missing key. 6.2 changed the code and the status
    at the source; what this pins is that the resolver still changes NEITHER."""
    resolver, _ = make_resolver(key_resolver=RaisingKeyResolver())
    with pytest.raises(ConflictError) as exc_info:
        await resolver.resolve_llm(make_ctx(), capability="default")
    assert exc_info.value.code == "credentials.none_available"
    assert exc_info.value.status == 409
    assert "no active credential for provider openai" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# resolve_embedding (decision 9: same table, 'default' only, own mapping)     #
# --------------------------------------------------------------------------- #


async def test_resolve_embedding_uses_the_default_route_and_the_embedding_mapping() -> None:
    emb = FakeEmbeddingProvider("openai")
    spy = SpyKeyResolver()
    resolver = SettingsProviderResolver(
        routing={"embedding": {"default": {"provider": "openai", "model": "text-emb-test"}}},
        llm_providers={},
        embedding_providers={"openai": emb},
        key_resolver=spy,
        keyless_providers=frozenset(),
    )
    provider, resolved = await resolver.resolve_embedding(make_ctx())
    assert provider is emb
    assert resolved == ResolvedProvider(provider="openai", model="text-emb-test", api_key="sk-spy")
    assert [name for _, name in spy.calls] == ["openai"]


async def test_resolve_embedding_honours_a_model_override() -> None:
    resolver = SettingsProviderResolver(
        routing={"embedding": {"default": {"provider": "openai", "model": "text-emb-test"}}},
        llm_providers={},
        embedding_providers={"openai": FakeEmbeddingProvider("openai")},
        key_resolver=SpyKeyResolver(),
        keyless_providers=frozenset(),
    )
    _, resolved = await resolver.resolve_embedding(make_ctx(), model="text-emb-large")
    assert resolved.model == "text-emb-large"


async def test_resolve_embedding_without_a_route_fails_closed() -> None:
    resolver, _ = make_resolver()  # llm-only routing
    with pytest.raises(ValidationError) as exc_info:
        await resolver.resolve_embedding(make_ctx())
    assert exc_info.value.status == 422
    assert "no 'default' embedding route" in str(exc_info.value)


async def test_a_keyless_embedding_provider_skips_key_resolution_too() -> None:
    """``keyless_providers`` is a name set, not an llm-namespace feature."""
    spy = SpyKeyResolver()
    resolver = SettingsProviderResolver(
        routing={"embedding": {"default": {"provider": "local-emb", "model": "minilm"}}},
        llm_providers={},
        embedding_providers={"local-emb": FakeEmbeddingProvider("local-emb")},
        key_resolver=spy,
        keyless_providers=frozenset({"local-emb"}),
    )
    _, resolved = await resolver.resolve_embedding(make_ctx())
    assert resolved.api_key == ""
    assert spy.calls == []


async def test_embedding_local_the_2_10_composition_root_provider_name_resolves_keyless() -> None:
    """The EXACT name the Composition Root wires (2.10:
    ``keyless_providers=frozenset({"ollama", "embedding-local"})``,
    ``framework/di/composition_root.py``) -- a real regression the generic
    ``local-emb`` case above would not catch if that literal ever drifted
    from what the root actually builds."""
    spy = SpyKeyResolver()
    resolver = SettingsProviderResolver(
        routing={
            "embedding": {
                "default": {
                    "provider": "embedding-local",
                    "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                }
            }
        },
        llm_providers={},
        embedding_providers={"embedding-local": FakeEmbeddingProvider("embedding-local")},
        key_resolver=spy,
        keyless_providers=frozenset({"ollama", "embedding-local"}),
    )
    provider, resolved = await resolver.resolve_embedding(make_ctx())
    assert provider.provider == "embedding-local"
    assert resolved.api_key == ""
    assert spy.calls == []


# --------------------------------------------------------------------------- #
# Cross-boundary structural proof: the REAL ResolveCredential over the seam   #
# --------------------------------------------------------------------------- #


class _StubCredentialRepository:
    """Structurally a ``CredentialRepository``; only ``find_active`` matters
    to the resolution path (user scope hit; platform scope empty)."""

    def __init__(self, credential: Credential | None) -> None:
        self._credential = credential
        self.requested: list[tuple[str, CredentialScope]] = []

    async def add(self, ctx: ExecutionContext, credential: Credential) -> None:
        raise AssertionError("not part of the resolution path")

    async def get(self, ctx: ExecutionContext, credential_id: str) -> Credential | None:
        raise AssertionError("not part of the resolution path")

    async def find_active(
        self, ctx: ExecutionContext, provider: ProviderRef, scope: CredentialScope
    ) -> Credential | None:
        self.requested.append((provider.value, scope))
        return self._credential if scope is CredentialScope.USER else None

    async def save(self, ctx: ExecutionContext, credential: Credential) -> None:
        raise AssertionError("not part of the resolution path")


class _StubSecrets:
    """Transit decrypt only — returns the plaintext the test asserts on."""

    async def get_secret(self, path: str) -> Json:
        raise AssertionError("not part of the resolution path")

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        raise AssertionError("not part of the resolution path")

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        assert (key_name, ciphertext) == ("tenant-secrets", "vault:v1:ciphertext")
        return b"sk-decrypted-live"


def _credential_for(ctx: ExecutionContext) -> Credential:
    now = utc_now()
    return Credential(
        id=new_uuid7(),
        workspace_id=ctx.workspace_id,
        provider=ProviderRef("openai"),
        scope=CredentialScope.USER,
        label="****t42x",
        ciphertext_ref=CipherRef(ciphertext="vault:v1:ciphertext", key_name="tenant-secrets"),
        status=CredentialStatus.ACTIVE,
        created_by=ctx.user_id or new_uuid7(),
        created_at=now,
        updated_at=now,
        version=1,
    )


async def test_the_real_resolve_credential_satisfies_the_key_resolver_seam_end_to_end() -> None:
    """The Liskov proof the ``layers`` contract forbids writing inside
    ``src``: framework may not import the credentials module, so the seam is
    structural — and here the REAL ``ResolveCredential`` (fake repo + fake
    Transit underneath) is injected as ``key_resolver`` and driven through
    ``resolve_llm``. If either side's signature or return shape drifts, this
    breaks at runtime, wiring-site or not."""
    ctx = make_ctx()
    key_resolver: KeyResolver = ResolveCredential(
        _StubCredentialRepository(_credential_for(ctx)), _StubSecrets()
    )
    resolver = SettingsProviderResolver(
        routing={"llm": {"default": {"provider": "openai", "model": "gpt-test"}}},
        llm_providers={"openai": FakeLLMProvider("openai")},
        embedding_providers={},
        key_resolver=key_resolver,
        keyless_providers=frozenset({"ollama"}),
    )
    _, resolved = await resolver.resolve_llm(ctx, capability="default")
    assert resolved == ResolvedProvider(
        provider="openai", model="gpt-test", api_key="sk-decrypted-live"
    )


async def test_the_real_resolve_credential_no_key_outcome_escapes_untranslated() -> None:
    """Same seam, miss path: both scopes empty ⇒ the module's own
    ``credentials.none_available``/409 (user → platform order asserted)
    reaches the caller exactly as raised. The point survives 6.2's status
    change untouched: the failure still names CREDENTIALS, and the resolver
    does not wrap it into a provider failure."""
    repo = _StubCredentialRepository(None)
    resolver = SettingsProviderResolver(
        routing={"llm": {"default": {"provider": "openai", "model": "gpt-test"}}},
        llm_providers={"openai": FakeLLMProvider("openai")},
        embedding_providers={},
        key_resolver=ResolveCredential(repo, _StubSecrets()),
        keyless_providers=frozenset(),
    )
    with pytest.raises(ConflictError) as exc_info:
        await resolver.resolve_llm(make_ctx(), capability="default")
    assert exc_info.value.code == "credentials.none_available"
    assert "no active credential for provider openai" in str(exc_info.value)
    assert repo.requested == [
        ("openai", CredentialScope.USER),
        ("openai", CredentialScope.PLATFORM),
    ]
