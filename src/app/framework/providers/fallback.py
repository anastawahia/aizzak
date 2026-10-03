"""``LlmFallback`` — where a chat turn goes when its cloud provider fails
(capacity-plan 6.4, owner decision 2026-10-03).

**The decision, and why it is one direction only.** A turn whose provider is
down before it has said a word is answered by the LOCAL model instead, and the
user is told so. Never the other way: the plan's three conditions for any
fallback are (1) the two models are functionally equivalent, (2) the tenant's
data policy allows the second destination, and (3) the call is idempotent or a
pure read — and (2) is the one that rules out local → cloud. A tenant that kept
its conversations local must not have them sent to a cloud because Ollama was
restarting. Cloud → local moves data the other way, onto the platform's own
host, which no policy forbids; so the direction is the policy, enforced where
the table is parsed (``SettingsProviderResolver`` refuses to boot on a
fallback route that needs a credential).

(1) is checked per call by the orchestrator (a call that carries tools is not
re-sent to a provider that cannot take them), and (3) holds by what a call is:
generating text has no side effect at the provider, and the switch only ever
happens BEFORE the first chunk reached the user — after it, a second answer
would be a different answer, and the turn is cut instead (ب-10).

**Its own port, not a fourth method on ``ProviderResolver``.** Only the chat
orchestrator asks this question; the workers, the workflow steps and every
test fake of the resolver have no business answering it. The ``ModelCatalog``
/``ProviderProbe`` precedent: one face per caller, all implemented by the one
object that parsed the table, so the fallback route can never name a route the
resolver does not know.
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.ports.llm_provider import LLMProvider
from app.framework.providers.resolver import ResolvedProvider


class LlmFallback(Protocol):
    """The configured fallback for a turn whose provider failed (6.4)."""

    async def resolve_llm_fallback(
        self, ctx: ExecutionContext, *, primary: str
    ) -> tuple[LLMProvider, ResolvedProvider] | None:
        """The binding to fall back to from ``primary``, or ``None`` when
        there is none: fallback disabled, ``primary`` already local (there is
        nowhere more local to go, and a cloud is not an option), or ``primary``
        IS the fallback's provider."""
        ...
