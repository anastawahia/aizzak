"""``ProviderResolver`` — every provider/model choice comes from configuration
(02-port-contracts §3.5, D-16, FR-73; Phase 2.9).

A kernel component, not an infrastructure adapter: it performs zero I/O of its
own — dictionary lookups over injected state plus one delegated ``await`` to
the credential seam — which is why it lives in ``framework/`` (the Registries
family, architecture §05) exactly where the contract places it. The skeleton's
empty ``infrastructure/ai_providers/resolver.py`` guess was deleted in 2.9;
the design wins over the scaffold.

**Routing table.** ``Settings.provider_routing`` is parsed ONCE, at
construction, into typed routes::

    {"llm":       {<capability>: {"provider": "...", "model": "..."}},
     "embedding": {"default":    {"provider": "...", "model": "..."}},
     "image":     {"default":    {"provider": "...", "model": "..."}}}

Anything malformed refuses to construct (``ValidationError`` naming the broken
config path). 2.8-a recorded the reason when it refused to put ``base_url`` in
this table: an untyped capability table turns a typo into a silent default —
``"lllm"``, ``"modle"``, or 05 §2's abbreviated ``"default": "openai"`` string
form must die at boot, not misroute at request N. Routing to a provider whose
adapter is not in the injected mapping is the same boot-time error (routing to
``gemini`` before 2.8-ب-2 exists is an operator mistake to fix at startup, not
a 500 to discover later). An EMPTY table constructs fine — no routes, every
resolve fails closed — so the ``from_env`` default (``provider_routing={}``)
stays bootable.

**The ``image`` namespace** (step 18 of ``deferred-adapters-plan.md``) is
``embedding``'s literal twin: ``default`` only, because ``ImageProvider``
carries no capability parameter either, so any other key would be a route
nothing could ever reach. It is added BEFORE its adapter exists (step 19) on
purpose, and that ordering costs nothing precisely because the "no wired
adapter" rule above is not namespace-aware: against an EMPTY
``image_providers`` mapping, configuring an image route refuses to BOOT and
names the route. That is the honest answer for a capability this process
cannot perform — better than booting and discovering it as a 500 at request N.
**``video`` is deliberately NOT here.** ``VideoResult`` may carry a
``remote_url`` instead of bytes (``ports/video_provider.py``), so a video route
implies a fetch step with its own timeout, size cap and failure handling;
declaring the namespace early would buy only the right to configure something
no adapter answers — which this same rule would refuse anyway.

**Lookup.** Capability keys are opaque (a capability or an agent key —
02 §3.5's own words), matched literally first, then the reserved ``default``
entry, else fail CLOSED with 422: ``refs/llm-providers.md §6`` explicitly
drops alpha's "unknown name → ollama" as a D-16 violation. The ``default``
step is table semantics *inside the configured table*, not a cross-provider
fallback — 05 §2's canonical example is literally a lone ``default`` entry.
``model=`` overrides the MODEL within the routed provider only; the provider
choice never leaves configuration (FR-73), and there is no model→provider
inference (alpha's static ``provider_of`` catalog, dropped). Both call-time
arguments are validated as non-empty strings BEFORE any dict lookup (422): a
non-str ``capability`` is not "unknown" — it is a caller bug, and guessing
``default`` for it would mask it, while an unhashable one would surface as a
raw ``TypeError`` — the R6 family this repo has shipped three times (2.9
verifier findings RC-B/RC-C, hardened in step 2).

**Keys.** ``keyless_providers`` names the adapters that take no credential
(today: ``ollama``). Keyless-ness is a structural property of the adapter, so
it arrives from Composition-Root *code* next to the adapters it describes
(alpha's ``kind`` field precedent) — never from user-editable config, where a
typo would silently skip credential resolution for a cloud provider. Keyless
⇒ the key resolver is never called and ``api_key=""`` (``OllamaLLM`` ignores
the value by design, 2.8-a decision 2). Keyed ⇒ ``key_resolver.resolve(ctx,
provider)`` — user key first, then platform, no cross-provider fallback
(D-16) — and its errors pass through UNTRANSLATED: ``NotFoundError("no active
credential …")`` is already the correct taxonomy; wrapping it as
``agent.failed``/502 would blame the provider for a missing key.

**Layering.** ``app.framework`` may not import ``app.modules`` (the
import-linter ``layers`` contract carries no ``composition_root`` exception
until Phase 4), so the credentials inbound port is consumed through a
STRUCTURAL twin — ``KeyResolver``/``ResolvedKeyView`` below, which
``credentials.application.ResolveCredential``/``ResolvedKey`` satisfy without
either side importing the other (the ``media``-adapter/
``TenantSessionFactory`` precedent of §3.14, direction reversed). Wiring is
therefore deferred to Phase 4; the Composition Root docstring carries the
one-construction sketch, and every argument it needs except ``key_resolver``
already lives on the root today.

**Deliberately absent:** ``supports()`` consultation (a routing hint, not a
guarantee — 2.8-a decision 3, confirmed live in §3.24; its vocabulary
``tools|vision|streaming`` is not the routing-key namespace; per-model truth
lives in this same D-16 table), logging (deterministic lookups, nothing to
observe), and any network access.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import ValidationError
from app.framework.ports.embedding_provider import EmbeddingProvider
from app.framework.ports.image_provider import ImageProvider
from app.framework.ports.llm_provider import LLMProvider
from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    """The configuration-driven choice for one call (02-port-contracts §3.5)."""

    provider: str  # 'openai' | 'gemini' | 'claude' | 'ollama' | 'openrouter'
    model: str
    api_key: str  # from the key resolver (user → platform); "" for keyless local providers


class ResolvedKeyView(Protocol):
    """The one attribute this resolver reads off a resolved credential.

    A property-only structural view that ``credentials``' frozen
    ``ResolvedKey`` satisfies; interface-segregated on purpose (``provider``/
    ``scope`` are the credential module's business, not ours).
    """

    @property
    def api_key(self) -> str: ...


class KeyResolver(Protocol):
    """Structural twin of ``credentials.ports.inbound.CredentialResolver``.

    Same signature, narrower return view — the ``layers`` contract forbids
    importing the module from framework, so conformance is structural
    (proven end to end in ``tests/unit/test_provider_resolver.py`` with the
    real ``ResolveCredential``).
    """

    async def resolve(self, ctx: ExecutionContext, provider: str) -> ResolvedKeyView: ...


class ProviderResolver(Protocol):
    """All provider/model selection from configuration (02 §3.5, FR-73)."""

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[LLMProvider, ResolvedProvider]: ...

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]: ...

    async def resolve_image(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[ImageProvider, ResolvedProvider]: ...


@dataclass(frozen=True, slots=True)
class _Route:
    """One parsed, validated routing entry — call-time code never sees raw Json."""

    provider: str
    model: str


_NAMESPACES = ("llm", "embedding", "image")
_ENTRY_KEYS = frozenset({"provider", "model"})
_DEFAULT_CAPABILITY = "default"


@dataclass(frozen=True, slots=True)
class _Routes:
    """The parsed table, one field per namespace.

    A NAMED carrier rather than the tuple this returned while there were two
    namespaces: all three values have the identical type
    ``dict[str, _Route]``, so a positional return would let ``embedding`` and
    ``image`` be swapped at the unpacking site with nothing — not mypy, not a
    test that only exercises one namespace — noticing.
    """

    llm: dict[str, _Route]
    embedding: dict[str, _Route]
    image: dict[str, _Route]


def _parse_routing(
    routing: object,
    llm_wired: Mapping[str, object],
    embedding_wired: Mapping[str, object],
    image_wired: Mapping[str, object],
) -> _Routes:
    """Validate + parse the WHOLE table from untrusted input, loudly.

    Takes ``object``, not ``Json``: the constructor's parameter type documents
    the Settings contract, but hostile direct construction (the 2.9 verifier's
    R6 battery: ``None``, an int, and worst the iterable shapes — a list/
    tuple/set of exactly the right namespace NAMES, where the set literal
    ``{"llm", "embedding"}`` is one brace away from the dict form — used to
    sail past a bare namespace loop and detonate later as a raw
    ``AttributeError`` at ``.get``). Everything non-dict now refuses
    construction as a 422 naming ``provider_routing`` itself.
    """
    if not isinstance(routing, dict):
        raise ValidationError(
            f"provider_routing: must be an object with namespaces "
            f"{list(_NAMESPACES)}, got {type(routing).__name__}"
        )
    for namespace in routing:
        if namespace not in _NAMESPACES:
            raise ValidationError(
                f"provider_routing: unknown namespace {namespace!r} "
                f"(expected one of {list(_NAMESPACES)})"
            )
    return _Routes(
        llm=_parse_namespace("llm", routing.get("llm", {}), llm_wired, only_default=False),
        embedding=_parse_namespace(
            "embedding", routing.get("embedding", {}), embedding_wired, only_default=True
        ),
        image=_parse_namespace("image", routing.get("image", {}), image_wired, only_default=True),
    )


def _parse_namespace(
    namespace: str,
    raw: object,
    wired: Mapping[str, object],
    *,
    only_default: bool,
) -> dict[str, _Route]:
    """Parse one routing namespace strictly, or refuse construction loudly."""
    if not isinstance(raw, dict):
        raise ValidationError(
            f"provider_routing.{namespace}: must be an object of "
            f"capability -> {{provider, model}}, got {type(raw).__name__}"
        )
    routes: dict[str, _Route] = {}
    for capability, entry in raw.items():
        if not isinstance(capability, str) or not capability.strip():
            raise ValidationError(
                f"provider_routing.{namespace}: capability keys must be "
                f"non-empty strings, got {capability!r}"
            )
        if only_default and capability != _DEFAULT_CAPABILITY:
            raise ValidationError(
                f"provider_routing.{namespace}[{capability!r}]: only the 'default' route is "
                f"reachable (the port carries no capability parameter)"
            )
        if not isinstance(entry, dict):
            raise ValidationError(
                f"provider_routing.{namespace}[{capability!r}]: must be an object with exactly "
                f"the keys 'provider' and 'model', got {type(entry).__name__}"
            )
        if set(entry) != _ENTRY_KEYS:
            raise ValidationError(
                f"provider_routing.{namespace}[{capability!r}]: must have exactly the keys "
                f"'provider' and 'model', got {sorted(map(repr, entry))}"
            )
        provider = entry["provider"]
        model = entry["model"]
        if not isinstance(provider, str) or not provider.strip():
            raise ValidationError(
                f"provider_routing.{namespace}[{capability!r}].provider: must be a non-empty string"
            )
        if not isinstance(model, str) or not model.strip():
            raise ValidationError(
                f"provider_routing.{namespace}[{capability!r}].model: must be a non-empty string"
            )
        if provider not in wired:
            raise ValidationError(
                f"provider_routing.{namespace}[{capability!r}]: provider {provider!r} has no "
                f"wired adapter (wired: {sorted(wired)})"
            )
        routes[capability] = _Route(provider=provider, model=model)
    return routes


class SettingsProviderResolver:
    """The concrete ``ProviderResolver`` — strict parse at construction,
    fail-closed lookup at call time (module docstring has the full record)."""

    def __init__(
        self,
        *,
        routing: Json,
        llm_providers: Mapping[str, LLMProvider],
        embedding_providers: Mapping[str, EmbeddingProvider],
        image_providers: Mapping[str, ImageProvider],
        key_resolver: KeyResolver,
        keyless_providers: frozenset[str],
    ) -> None:
        self._llm_providers = llm_providers
        self._embedding_providers = embedding_providers
        self._image_providers = image_providers
        self._key_resolver = key_resolver
        self._keyless = keyless_providers
        # `image_providers` is REQUIRED, not defaulted to `{}` (step 18):
        # every construction site is in this repo, and a default would let a
        # root that builds an image adapter forget to hand it over -- which
        # surfaces as `provider_routing.image[...]: provider ... has no wired
        # adapter`, blaming the operator's config for OUR wiring bug. The same
        # argument the `keyless_providers` paragraph above makes for keeping
        # structural facts in Composition-Root code.
        routes = _parse_routing(routing, llm_providers, embedding_providers, image_providers)
        self._llm_routes = routes.llm
        self._embedding_routes = routes.embedding
        self._image_routes = routes.image

    async def resolve_llm(
        self, ctx: ExecutionContext, *, capability: str, model: str | None = None
    ) -> tuple[LLMProvider, ResolvedProvider]:
        # The `or` keeps the raise type-reachable (blank str) under
        # warn_unreachable while still catching runtime non-str garbage: an
        # unhashable capability would otherwise surface as a raw TypeError
        # from the dict lookup, and None/int would silently fall through to
        # 'default' — a guess that masks a caller bug (2.9 verifier, RC-C).
        if not isinstance(capability, str) or not capability.strip():
            raise ValidationError(
                f"capability must be a non-empty string (got {type(capability).__name__})"
            )
        route = self._llm_routes.get(capability)
        if route is None:
            route = self._llm_routes.get(_DEFAULT_CAPABILITY)
        if route is None:
            raise ValidationError(
                f"no llm route for capability {capability!r} and no 'default' route is configured"
            )
        resolved = ResolvedProvider(
            provider=route.provider,
            model=_override_or_routed(model, route),
            api_key=await self._api_key_for(ctx, route.provider),
        )
        return self._llm_providers[route.provider], resolved

    async def resolve_embedding(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[EmbeddingProvider, ResolvedProvider]:
        route = self._embedding_routes.get(_DEFAULT_CAPABILITY)
        if route is None:
            raise ValidationError("no 'default' embedding route is configured")
        resolved = ResolvedProvider(
            provider=route.provider,
            model=_override_or_routed(model, route),
            api_key=await self._api_key_for(ctx, route.provider),
        )
        return self._embedding_providers[route.provider], resolved

    async def resolve_image(
        self, ctx: ExecutionContext, *, model: str | None = None
    ) -> tuple[ImageProvider, ResolvedProvider]:
        route = self._image_routes.get(_DEFAULT_CAPABILITY)
        if route is None:
            raise ValidationError("no 'default' image route is configured")
        resolved = ResolvedProvider(
            provider=route.provider,
            model=_override_or_routed(model, route),
            api_key=await self._api_key_for(ctx, route.provider),
        )
        return self._image_providers[route.provider], resolved

    async def _api_key_for(self, ctx: ExecutionContext, provider: str) -> str:
        """ "" for keyless providers; otherwise the delegated resolution,
        whose errors pass through untranslated (module docstring, Keys)."""
        if provider in self._keyless:
            return ""
        resolved = await self._key_resolver.resolve(ctx, provider)
        return resolved.api_key


def _override_or_routed(model: str | None, route: _Route) -> str:
    """The routed model unless overridden; a blank or non-str override is a
    caller bug (422 here beats a 404→502 at the provider that would blame the
    vendor). ``model=`` is a CALL-TIME argument no Pydantic layer ever
    filters — a Phase-4 caller forwarding a user-supplied "preferred model"
    from an ``Any``-typed body used to crash as a raw ``AttributeError`` on
    ``.strip`` (2.9 verifier, RC-B); the ``isinstance`` half of the guard
    closes that while the blank half keeps the raise type-reachable."""
    if model is None:
        return route.model
    if not isinstance(model, str) or not model.strip():
        raise ValidationError(
            f"model override must be a non-empty string (got {type(model).__name__})"
        )
    return model


if TYPE_CHECKING:

    def _conforms(resolver: SettingsProviderResolver) -> ProviderResolver:
        """mypy-gate structural proof that the concrete class satisfies the
        §3.5 port — the Phase-4 wiring site that would otherwise prove it
        does not exist yet (``layers`` contract)."""
        return resolver
