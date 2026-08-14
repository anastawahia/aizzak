"""Models resource DTOs (03-api-spec §2).

The wire shape for ``/api/v1/models``: the configured D-16 routing table as a
client sees it. Deliberately a flat projection of ``ModelChoice`` and not a
richer "model card" — context window, pricing and capability flags are per-model
facts this platform holds nowhere. Inventing fields the routing table cannot
answer would put guesses on the wire; ``LLMProvider.supports()`` is already
documented as a hint rather than a guarantee, so it is not a source either.
"""

from __future__ import annotations

from pydantic import BaseModel


class ModelOut(BaseModel):
    """One routable (provider, model) pair (03 §2).

    ``capability`` is the identifier a caller pins, not ``model``: the routing
    key is what ``resolve_llm`` takes, and the same model may be routed under
    two capabilities with different providers behind them.

    ``available: false`` means "configured, but no credential resolves for
    you" — never "unknown model". The entry is still listed, because a UI that
    silently omitted it would leave the user unable to tell a missing key from
    a missing feature.
    """

    capability: str
    provider: str
    model: str
    available: bool
