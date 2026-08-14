"""``ModelCatalog`` — the read-only view of the D-16 routing table
(02-port-contracts §3.5.1, FR-73).

**What this is not.** It is not a provider's remote catalogue. Asking OpenAI or
Ollama what it can serve would answer a question nobody can act on: `D-16` says
the provider/model choice comes from configuration, so a model the operator did
not route is a model ``resolve_llm`` will never take. Publishing one would let a
client pick something the platform then refuses. Every entry here is a route
that exists.

**Why it is a separate port from ``ProviderResolver``.** ``ResolvedProvider``
carries ``api_key`` — the decrypted secret ``INV-C2`` keeps out of the API layer
by making the decrypting face unreachable there (``ApiServices`` holds
list/add/revoke and never ``ResolveCredential``, for exactly this reason). A
router that held a ``ProviderResolver`` could call ``resolve_llm`` and read the
key off the result. Holding this protocol instead makes that a type error rather
than a rule someone has to remember. Same interface-segregation move as
``ResolvedKeyView`` in ``resolver.py``, and the same reason ``ApiServices``
names faces one at a time.

**Why it is not a second read of ``Settings``.** ``SettingsProviderResolver``
parses ``provider_routing`` once, strictly, at construction, and refuses to boot
on anything malformed. A catalogue that re-read the raw table would be a second
parser of the same input, free to drift from the first — and the drift would
surface as a client picking a model that then fails to route. So the ONE
implementation of this protocol is that same resolver object; this module owns
the shape, not a second copy of the table.

**Availability is per provider, not per route.** Several routes commonly share a
provider, and a credential lookup is I/O (repository + secrets backend). The
implementation resolves each distinct provider once per call. A provider with no
credential yields ``available: false`` on its routes rather than an error: a
catalogue that 404s because one of five providers is unconfigured hides the four
that work, and "configured but not usable by you" is a fact the UI needs to show
a disabled option instead of silently omitting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One routable (provider, model) pair as a caller may see it.

    ``capability`` is the routing key itself — a capability name or an agent
    key (02 §3.5's own words) — and it is the IDENTIFIER, not ``model``: the
    same model may be routed under two capabilities, and it is the capability
    that ``resolve_llm`` takes. A caller pinning a choice pins this.

    No ``api_key`` and no way to reach one: the whole point of the port.
    """

    capability: str
    provider: str
    model: str
    available: bool


class ModelCatalog(Protocol):
    """The configured LLM routes, read-only (02 §3.5.1)."""

    async def list_llm_models(self, ctx: ExecutionContext) -> list[ModelChoice]: ...
