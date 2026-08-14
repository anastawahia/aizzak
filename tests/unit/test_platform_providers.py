"""Unit tests for the provider administration surface (BE-ADM-010/011/012).

Hermetic throughout: the inventory is a pure read of the parsed routing table,
and the probe is driven against recording adapters, so nothing here touches a
network, Vault or Postgres.

Two claims get more attention than the rest, because they are the ones a
regression would make silently untrue:

* the probe goes through the ROUTED adapter and the ROUTED model — a probe of
  something the platform would never call is a green light for a platform that
  cannot serve a request;
* the raw secret reaches the ``SecretsProvider`` and nothing else — the store
  is asserted to receive the ciphertext, never the plaintext.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime

import pytest

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, RateLimitedError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.ports.embedding_provider import EmbeddingResult
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult
from app.framework.providers import SettingsProviderResolver
from app.framework.types import Json
from app.modules.admin.application.providers import (
    _TENANT_SECRETS_KEY,
    ListPlatformProviders,
    ProbePlatformProvider,
    RevokePlatformProviderKey,
    SetPlatformProviderKey,
)
from app.modules.admin.ports.providers import (
    KeyPresence,
    PlatformKeyChange,
    PlatformProviderKey,
    StoredCipher,
)
from app.modules.credentials.application.use_cases import (
    _TENANT_SECRETS_KEY as _CREDENTIALS_TRANSIT_KEY,
)

# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


class _RecordingLLM:
    """An ``LLMProvider`` that records its one call, or fails as instructed."""

    def __init__(self, name: str, *, failure: AppError | None = None) -> None:
        self.provider = name
        self._failure = failure
        self.calls: list[tuple[list[LlmMessage], LlmParams, str]] = []

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:
        self.calls.append((list(messages), params, api_key))
        if self._failure is not None:
            raise self._failure
        return LlmResult(content="pong", finish_reason="stop", prompt_tokens=1, completion_tokens=1)

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        raise AssertionError("the probe never streams")

    def supports(self, capability: str) -> bool:
        raise AssertionError("the probe never consults supports()")


class _RecordingEmbedding:
    def __init__(self, name: str) -> None:
        self.provider = name
        self.calls: list[tuple[list[str], str, str]] = []

    async def embed(self, texts: Sequence[str], model: str, api_key: str) -> EmbeddingResult:
        self.calls.append((list(texts), model, api_key))
        return EmbeddingResult(vectors=[[0.0]], model=model, dimensions=1, tokens=1)

    def dimensions(self, model: str) -> int:
        return 1


class _UncallableImage:
    """An image adapter the probe must never reach — probing it costs money."""

    def __init__(self, name: str) -> None:
        self.provider = name

    async def generate(self, request: object, api_key: str) -> object:
        raise AssertionError("an image provider is never probed")


class _NoKeys:
    """A ``KeyResolver`` the inventory and probe paths must never consult."""

    async def resolve(self, ctx: ExecutionContext, provider: str) -> object:
        raise AssertionError("neither the inventory nor the probe resolves a stored key")


class _FakeStore:
    """An in-memory ``PlatformCredentialStore`` that records what it was told."""

    def __init__(self, keys: dict[str, PlatformProviderKey] | None = None) -> None:
        self._keys = keys or {}
        self.ciphers: dict[str, StoredCipher] = {}
        self.stored: list[dict[str, str]] = []
        self.revoked: list[str] = []

    async def active_keys(self) -> tuple[PlatformProviderKey, ...]:
        return tuple(self._keys.values())

    async def active_cipher(self, provider: str) -> StoredCipher | None:
        return self.ciphers.get(provider)

    async def store(
        self,
        *,
        provider: str,
        ciphertext: str,
        key_name: str,
        label: str,
        actor_user_id: str,
        reason: str,
    ) -> PlatformKeyChange:
        self.stored.append(
            {
                "provider": provider,
                "ciphertext": ciphertext,
                "key_name": key_name,
                "label": label,
                "actor": actor_user_id,
                "reason": reason,
            }
        )
        previous: KeyPresence = "active" if provider in self._keys else "absent"
        key = _key(provider, label=label)
        self._keys[provider] = key
        return PlatformKeyChange(
            provider=provider,
            key=key,
            previous_status=previous,
            changed=True,
            audit_id=new_uuid7(),
        )

    async def revoke(self, *, provider: str, actor_user_id: str, reason: str) -> PlatformKeyChange:
        self.revoked.append(provider)
        existing = self._keys.pop(provider, None)
        if existing is None:
            return PlatformKeyChange(
                provider=provider,
                key=None,
                previous_status="absent",
                changed=False,
                audit_id=None,
            )
        return PlatformKeyChange(
            provider=provider,
            key=existing,
            previous_status="active",
            changed=True,
            audit_id=new_uuid7(),
        )


class _FakeSecrets:
    """Reversible stand-in for Vault Transit; records every key name used."""

    def __init__(self) -> None:
        self.encrypted: list[tuple[str, bytes]] = []

    async def get_secret(self, path: str) -> Json:
        raise AssertionError("this surface reads no KV secret")

    async def encrypt(self, key_name: str, plaintext: bytes) -> str:
        self.encrypted.append((key_name, plaintext))
        return f"vault:{plaintext.decode()}"

    async def decrypt(self, key_name: str, ciphertext: str) -> bytes:
        return ciphertext.removeprefix("vault:").encode()


class _FakeCache:
    """Enough of ``CacheProvider`` for a fixed-window counter, with a log."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expires: list[tuple[str, int]] = []

    async def get(self, key: str) -> bytes | None:
        raise AssertionError("the probe budget is counted, not read")

    async def set(self, key: str, value: bytes, ttl_s: int | None = None) -> None:
        raise AssertionError("the probe budget is counted, not set")

    async def delete(self, key: str) -> None:
        raise AssertionError("the probe budget is never cleared by hand")

    async def incr(self, key: str, amount: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + amount
        return self.counters[key]

    async def expire(self, key: str, ttl_s: int) -> None:
        self.expires.append((key, ttl_s))


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _key(
    provider: str, *, label: str = "****abcd", moment: datetime | None = None
) -> PlatformProviderKey:
    now = moment or utc_now()
    return PlatformProviderKey(
        id=new_uuid7(),
        provider=provider,
        label=label,
        status="active",
        created_by=new_uuid7(),
        created_at=now,
        updated_at=now,
    )


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"platform_admin"}),
    )


def _resolver(
    *,
    llm: _RecordingLLM | None = None,
    embedding: _RecordingEmbedding | None = None,
    image: _UncallableImage | None = None,
    routing: Json | None = None,
    # The real Composition Root's set: a local model server and a local
    # embedding model both authenticate to nothing.
    keyless: frozenset[str] = frozenset({"ollama", "embedding-local"}),
) -> SettingsProviderResolver:
    llm = llm if llm is not None else _RecordingLLM("openai")
    embedding = embedding if embedding is not None else _RecordingEmbedding("embedding-local")
    image = image if image is not None else _UncallableImage("image:openai")
    table: Json = (
        routing
        if routing is not None
        else {
            "llm": {
                "rag_agent": {"provider": "openai", "model": "gpt-4o-mini"},
                "default": {"provider": "openai", "model": "gpt-4o"},
            },
            "embedding": {"default": {"provider": "embedding-local", "model": "bge-small"}},
            "image": {"default": {"provider": "image:openai", "model": "gpt-image-1"}},
        }
    )
    return SettingsProviderResolver(
        routing=table,
        llm_providers={llm.provider: llm},  # type: ignore[dict-item]
        embedding_providers={embedding.provider: embedding},  # type: ignore[dict-item]
        image_providers={image.provider: image},  # type: ignore[dict-item]
        key_resolver=_NoKeys(),  # type: ignore[arg-type]
        keyless_providers=keyless,
    )


# --------------------------------------------------------------------------- #
# The Transit key coupling                                                    #
# --------------------------------------------------------------------------- #


def test_the_platform_key_is_encrypted_under_the_same_transit_key_credentials_reads() -> None:
    """The one literal two modules must agree on, asserted rather than trusted.

    Import-linter forbids the admin module importing the credentials module, so
    the constant is repeated there. A drift would store and list perfectly and
    then fail at the only moment that matters — the first time an agent asks
    ``ResolveCredential`` to open the platform key.
    """
    assert _TENANT_SECRETS_KEY == _CREDENTIALS_TRANSIT_KEY


# --------------------------------------------------------------------------- #
# The inventory (BE-ADM-010)                                                  #
# --------------------------------------------------------------------------- #


def test_the_inventory_is_the_routing_table_transposed_onto_providers() -> None:
    providers = {entry.provider: entry for entry in _resolver().configured_providers()}

    assert sorted(providers) == ["embedding-local", "image:openai", "openai"]
    openai = providers["openai"]
    assert [(route.namespace, route.capability, route.model) for route in openai.routes] == [
        ("llm", "default", "gpt-4o"),
        ("llm", "rag_agent", "gpt-4o-mini"),
    ]


def test_a_provider_the_table_does_not_route_to_is_absent_entirely() -> None:
    """A key for an unrouted provider is a key nothing would ever spend."""
    providers = {entry.provider for entry in _resolver().configured_providers()}

    assert "claude" not in providers


def test_keylessness_comes_from_the_wiring_not_from_the_routing_table() -> None:
    resolver = _resolver(
        llm=_RecordingLLM("ollama"),
        routing={"llm": {"default": {"provider": "ollama", "model": "gemma3:1b"}}},
        keyless=frozenset({"ollama"}),
    )

    (only,) = resolver.configured_providers()
    assert only.keyless is True


def test_an_image_only_provider_is_reported_as_unprobeable() -> None:
    """Testing an image key means buying an image; the contract says so up
    front so a client disables the control instead of being refused."""
    providers = {entry.provider: entry for entry in _resolver().configured_providers()}

    assert providers["image:openai"].probeable is False
    assert providers["openai"].probeable is True
    assert providers["embedding-local"].probeable is True


def test_the_inventory_order_is_stable_rather_than_insertion_dependent() -> None:
    routing: Json = {
        "llm": {
            "zeta": {"provider": "openai", "model": "z"},
            "alpha": {"provider": "openai", "model": "a"},
        }
    }
    (openai,) = _resolver(routing=routing).configured_providers()

    assert [route.capability for route in openai.routes] == ["alpha", "zeta"]


# --------------------------------------------------------------------------- #
# The probe (BE-ADM-012)                                                      #
# --------------------------------------------------------------------------- #


async def test_the_probe_spends_one_capped_call_on_the_routed_model() -> None:
    llm = _RecordingLLM("openai")

    outcome = await _resolver(llm=llm).probe("openai", "sk-live")

    assert outcome.ok is True
    assert outcome.detail is None
    (messages, params, api_key) = llm.calls[0]
    # The first route by capability, which is `default` here -- not the
    # `rag_agent` one, and never a model the operator did not configure.
    assert params.model == "gpt-4o"
    assert params.max_tokens == 1
    assert api_key == "sk-live"
    assert [message.role for message in messages] == ["user"]


async def test_a_rejected_key_is_an_outcome_and_not_an_exception() -> None:
    """The caller asked whether the key works; "no" is the answer, not a fault."""
    llm = _RecordingLLM("openai", failure=AppError("openai rejected the api key", status=502))

    outcome = await _resolver(llm=llm).probe("openai", "sk-wrong")

    assert outcome.ok is False
    assert outcome.detail == "openai rejected the api key"


async def test_an_embedding_only_provider_is_probed_through_its_own_port() -> None:
    embedding = _RecordingEmbedding("embedding-local")
    resolver = _resolver(
        embedding=embedding,
        routing={"embedding": {"default": {"provider": "embedding-local", "model": "bge-small"}}},
    )

    outcome = await resolver.probe("embedding-local", "")

    assert outcome.ok is True
    assert embedding.calls[0][1] == "bge-small"


async def test_probing_a_provider_with_only_an_image_route_is_refused() -> None:
    resolver = _resolver(
        routing={"image": {"default": {"provider": "image:openai", "model": "gpt-image-1"}}}
    )

    with pytest.raises(ValidationError):
        await resolver.probe("image:openai", "sk-live")


# --------------------------------------------------------------------------- #
# Listing (BE-ADM-010)                                                        #
# --------------------------------------------------------------------------- #


async def test_the_listing_joins_the_platform_key_onto_its_configured_provider() -> None:
    store = _FakeStore({"openai": _key("openai", label="prod")})

    views = {
        view.provider: view for view in await ListPlatformProviders(_resolver(), store).execute()
    }

    assert views["openai"].key is not None
    assert views["openai"].key.label == "prod"
    assert views["embedding-local"].key is None


async def test_a_stored_key_for_an_unrouted_provider_is_not_surfaced_as_a_provider() -> None:
    """It is dead weight nothing can spend; listing it would suggest this
    deployment can reach something it cannot."""
    store = _FakeStore({"claude": _key("claude")})

    views = {view.provider for view in await ListPlatformProviders(_resolver(), store).execute()}

    assert "claude" not in views


# --------------------------------------------------------------------------- #
# Storing and revoking (BE-ADM-011)                                           #
# --------------------------------------------------------------------------- #


async def test_the_raw_secret_reaches_vault_and_the_store_receives_only_ciphertext() -> None:
    store, secrets = _FakeStore(), _FakeSecrets()
    use_case = SetPlatformProviderKey(_resolver(), store, secrets)

    await use_case.execute(
        _ctx(), provider="openai", secret="sk-secret-1234", label=None, reason="initial setup"
    )

    assert secrets.encrypted == [(_TENANT_SECRETS_KEY, b"sk-secret-1234")]
    written = store.stored[0]
    assert written["ciphertext"] == "vault:sk-secret-1234"
    assert "sk-secret-1234" not in written["label"]


async def test_an_unnamed_key_is_labelled_with_its_masked_last_four_characters() -> None:
    store, secrets = _FakeStore(), _FakeSecrets()

    await SetPlatformProviderKey(_resolver(), store, secrets).execute(
        _ctx(), provider="openai", secret="sk-secret-1234", label=None, reason="initial setup"
    )

    assert store.stored[0]["label"] == "****1234"


async def test_a_keyless_provider_refuses_a_key_rather_than_storing_a_dead_one() -> None:
    resolver = _resolver(
        llm=_RecordingLLM("ollama"),
        routing={"llm": {"default": {"provider": "ollama", "model": "gemma3:1b"}}},
    )
    store = _FakeStore()

    with pytest.raises(ValidationError) as raised:
        await SetPlatformProviderKey(resolver, store, _FakeSecrets()).execute(
            _ctx(), provider="ollama", secret="anything", label=None, reason="mistaken paste"
        )

    assert raised.value.code == "credentials.provider_unknown"
    assert store.stored == []


async def test_a_provider_this_deployment_does_not_route_to_is_a_422() -> None:
    with pytest.raises(ValidationError) as raised:
        await SetPlatformProviderKey(_resolver(), _FakeStore(), _FakeSecrets()).execute(
            _ctx(), provider="claude", secret="sk-live", label=None, reason="new vendor"
        )

    assert raised.value.code == "credentials.provider_unknown"


async def test_the_provider_name_is_matched_case_insensitively() -> None:
    store = _FakeStore()

    await SetPlatformProviderKey(_resolver(), store, _FakeSecrets()).execute(
        _ctx(), provider="OpenAI", secret="sk-live", label=None, reason="initial setup"
    )

    assert store.stored[0]["provider"] == "openai"


async def test_a_reason_shorter_than_the_audit_minimum_is_refused() -> None:
    with pytest.raises(ValidationError):
        await SetPlatformProviderKey(_resolver(), _FakeStore(), _FakeSecrets()).execute(
            _ctx(), provider="openai", secret="sk-live", label=None, reason="x"
        )


async def test_revoking_a_provider_that_has_no_platform_key_changes_nothing() -> None:
    store = _FakeStore()

    change = await RevokePlatformProviderKey(_resolver(), store).execute(
        _ctx(), provider="openai", reason="rotating out"
    )

    assert change.changed is False
    assert change.audit_id is None


# --------------------------------------------------------------------------- #
# The probe use case (BE-ADM-012)                                             #
# --------------------------------------------------------------------------- #


def _probe_use_case(
    resolver: SettingsProviderResolver, store: _FakeStore, cache: _FakeCache
) -> ProbePlatformProvider:
    return ProbePlatformProvider(resolver, store, _FakeSecrets(), resolver, cache)


async def test_a_candidate_key_is_spent_without_ever_touching_the_store() -> None:
    """ "Test before you save" must not need a draft credential in the database."""
    llm = _RecordingLLM("openai")
    resolver, store = _resolver(llm=llm), _FakeStore()

    outcome = await _probe_use_case(resolver, store, _FakeCache()).execute(
        _ctx(), provider="openai", secret="sk-candidate"
    )

    assert outcome.ok is True
    assert llm.calls[0][2] == "sk-candidate"
    assert store.stored == []


async def test_omitting_the_secret_probes_the_stored_key_after_decrypting_it() -> None:
    llm = _RecordingLLM("openai")
    resolver, store = _resolver(llm=llm), _FakeStore()
    store.ciphers["openai"] = StoredCipher(
        ciphertext="vault:sk-stored", key_name=_TENANT_SECRETS_KEY
    )

    await _probe_use_case(resolver, store, _FakeCache()).execute(_ctx(), provider="openai")

    assert llm.calls[0][2] == "sk-stored"


async def test_probing_a_provider_with_no_stored_key_names_the_missing_credential() -> None:
    with pytest.raises(AppError) as raised:
        await _probe_use_case(_resolver(), _FakeStore(), _FakeCache()).execute(
            _ctx(), provider="openai"
        )

    assert raised.value.code == "credentials.none_available"


async def test_the_probe_budget_is_per_provider_and_refuses_the_seventh_attempt() -> None:
    resolver, store, cache = _resolver(), _FakeStore(), _FakeCache()
    use_case = _probe_use_case(resolver, store, cache)

    for _attempt in range(6):
        await use_case.execute(_ctx(), provider="openai", secret="sk-candidate")

    with pytest.raises(RateLimitedError) as raised:
        await use_case.execute(_ctx(), provider="openai", secret="sk-candidate")

    assert raised.value.retry_after_s == 60


async def test_the_window_ttl_is_set_once_rather_than_refreshed_on_every_probe() -> None:
    """A TTL pushed forward on each call is a ban that never expires while
    anyone keeps trying — a sliding block, not the fixed window promised."""
    resolver, cache = _resolver(), _FakeCache()
    use_case = _probe_use_case(resolver, _FakeStore(), cache)

    for _attempt in range(3):
        await use_case.execute(_ctx(), provider="openai", secret="sk-candidate")

    assert cache.expires == [("admin:provider-probe:openai", 60)]


async def test_one_provider_exhausting_its_budget_leaves_the_others_probeable() -> None:
    resolver, cache = _resolver(), _FakeCache()
    use_case = _probe_use_case(resolver, _FakeStore(), cache)
    for _attempt in range(6):
        await use_case.execute(_ctx(), provider="openai", secret="sk-candidate")

    outcome = await use_case.execute(_ctx(), provider="embedding-local")

    assert outcome.ok is True
