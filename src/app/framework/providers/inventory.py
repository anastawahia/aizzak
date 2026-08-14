"""``ProviderInventory`` / ``ProviderProbe`` — the operator's view of the
configured providers, and the one live check on a key (BE-ADM-010/012).

**Why this is the routing table and not a provider vocabulary.** The obvious
list — the five base providers ``ProviderRef`` accepts — would name providers
this deployment cannot reach: ``D-16`` puts the provider/model choice in
configuration, and ``SettingsProviderResolver`` refuses to BOOT when a route
names a provider with no wired adapter. So the set of providers a key could
ever be spent through is exactly the set the routing table names, and every
entry here is therefore a provider some capability actually resolves to.
Listing the vocabulary instead would invite an operator to store a key that
nothing on this deployment would ever pick up. Same argument
``catalog.ModelCatalog`` makes against publishing a vendor's remote catalogue,
one level up: the honest list is the one resolution will use.

**There is deliberately no ``enabled`` flag.** Alpha's panel had a per-provider
switch because in alpha the provider list *was* the configuration. Here it is
not: the table is parsed once, strictly, at construction, and a runtime toggle
would be a second source of truth the resolver does not consult — a provider
"disabled" in the database would keep answering every ``resolve_llm`` that
routes to it. What an operator can genuinely change while the process runs is
whether the PLATFORM supplies a key, which is why enablement lives on the
credential (``modules/admin/ports/providers.py``) rather than on a flag here.
This module reports what is CONFIGURED; it does not know, and must not guess,
what is credentialed.

**``routes`` is per provider, and it is the point.** A provider is worth a key
because of the capabilities pointing at it; showing them is what lets an
operator tell "the key our RAG agent answers with" from "the key one unused
route mentions". The three namespaces are reported together — ``embedding``
needs a key exactly as much as ``llm`` does — which is one thing Alpha's
LLM-only panel could not express.

**The probe issues a real, minimal call** (``ProviderProbe``). Any cheaper
check — a models-list endpoint, a HEAD on the base URL — exercises a different
path from the one that matters: keys exist that can list models and still fail
every completion (wrong project, exhausted quota, model not enabled for the
account). The probe therefore goes through the SAME adapter and the SAME
routed model a request would, capped at one token, so a pass means the thing
the operator cares about actually works. The cost is one token per probe, and
it is stated in the contract rather than hidden.

``probe`` TAKES a key and never returns one. That direction is what lets the
admin surface hold this protocol safely: a caller can spend a key through it
and can learn whether the key worked, but there is no shape here that hands a
secret back — the same interface-segregation move ``ResolvedKeyView`` and
``ModelCatalog`` make, and the reason ``ProbeOutcome`` carries a classified
reason instead of an upstream body.

**Image routes are not probeable.** ``ImageProvider``'s only call generates an
image, so "testing" an image key means buying one — a check with a price and a
latency measured in seconds is not a check. ``probeable`` says so up front, so
a client disables the control rather than offering a request that would be
refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

# The three routing namespaces, as a closed type: a namespace is a fixed part
# of the table's shape, so a caller mapping one onto the wire should not have
# to widen it back out of `str` with a cast.
Namespace = Literal["llm", "embedding", "image"]

# The namespaces whose ports have a call cheap and side-effect-free enough to
# be spent on a liveness check (`llm.complete` capped at one token,
# `embedding.embed` of one short string). `image` is absent on purpose — see
# the module docstring.
PROBEABLE_NAMESPACES = frozenset({"llm", "embedding"})


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """One configured route, named by the namespace it lives in.

    ``capability`` is the routing key — the identifier ``resolve_llm`` takes,
    and always the literal ``'default'`` for the single-route namespaces.
    """

    namespace: Namespace
    capability: str
    model: str


@dataclass(frozen=True, slots=True)
class ConfiguredProvider:
    """One provider this deployment can route to, with everything the
    configuration knows about it and nothing it does not.

    ``keyless`` is a structural property of the adapter (Composition-Root
    code, never user-editable config): a keyless provider takes no credential
    at all, so an operator storing one for it would be storing something
    nothing reads.
    """

    provider: str
    keyless: bool
    probeable: bool
    routes: tuple[ProviderRoute, ...]


class ProviderInventory(Protocol):
    """The configured providers, read-only and synchronous (no I/O, no keys)."""

    def configured_providers(self) -> tuple[ConfiguredProvider, ...]: ...


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """The result of one live check — never the key, never an upstream body.

    ``detail`` on a failure is the adapter's own translated message. That is
    safe to surface BECAUSE of a property the adapters already guarantee:
    every LLM adapter maps a failure from the HTTP STATUS, never from the
    response body (``openai_llm._translate_status``'s own docstring: "a pure
    function of ``status``… nothing read from the response"), so the message
    comes from a small fixed vocabulary written in this repo rather than from
    a vendor that might echo the credential back. ``None`` on success: there
    is nothing to say beyond the latency.
    """

    ok: bool
    latency_ms: int
    detail: str | None


class ProviderProbe(Protocol):
    """Spend one minimal call on ``api_key`` and report whether it worked.

    Never raises for a provider-side failure — that is the ``ok=False``
    outcome, which is the answer the caller asked for. It DOES raise for a
    provider with no probeable route: that is a caller mistake, not a
    verdict on a key.
    """

    async def probe(self, provider: str, api_key: str) -> ProbeOutcome: ...
