"""Credentials inbound port (02-port-contracts §2, §3.5).

``CredentialResolver`` is what the ``ProviderResolver`` (agent layer) injects to
turn a provider name into a usable key, without importing the credentials module
directly (ARC-07/08). Resolution prefers the active user key, then the platform
key, with no cross-provider fallback (D-16). ``ResolvedKey`` carries the
decrypted key across the platform boundary only — never out through the API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.context.execution_context import ExecutionContext


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    """A resolved provider key (decrypted, internal use only)."""

    provider: str
    api_key: str
    scope: str


class CredentialResolver(Protocol):
    """Injected key resolution — user key preferred over platform, no cross-provider fallback."""

    async def resolve(self, ctx: ExecutionContext, provider: str) -> ResolvedKey: ...
