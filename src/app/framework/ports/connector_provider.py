"""ConnectorProvider driven port (02-port-contracts §1.11 · integrations OAuth,
FR-120/124).

Third-party OAuth connectors. ``refresh`` implements lazy (on-demand) token
renewal — there is no scheduled refresh in v1 (FR-124).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: tuple[str, ...]


class ConnectorProvider(Protocol):
    connector: str  # 'github' | 'slack' | … from the catalog

    def authorize_url(self, redirect_uri: str, state: str, scopes: Sequence[str]) -> str: ...

    async def exchange_code(self, code: str, redirect_uri: str) -> OAuthTokens: ...

    async def refresh(self, refresh_token: str) -> OAuthTokens: ...  # lazy renewal (FR-124)
