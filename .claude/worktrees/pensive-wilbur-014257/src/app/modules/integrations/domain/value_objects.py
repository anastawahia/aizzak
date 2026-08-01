"""Integrations value objects (pure — 06-domain-models §9).

Shape-only validation lives here; catalog membership, configured transport
allow-lists and numeric ceilings are the application's concern against
injected ``Settings``/``Limits`` (the files/media precedent). ``CipherRef``
is a module-local copy of the credentials shape — module domains are
self-contained (§5.1 option A, the ``knowledge.VectorRef`` precedent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.integrations.domain.errors import (
    InvalidIntegrationInput,
    UnsupportedMcpTransport,
)


class ConnectionStatus(StrEnum):
    """Lifecycle of a third-party OAuth connection (01 §2.9 CHECK)."""

    PENDING = "pending"
    CONNECTED = "connected"
    REVOKED = "revoked"
    ERROR = "error"


class McpStatus(StrEnum):
    """Lifecycle of a registered remote MCP server (01 §2.9 CHECK)."""

    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


# Lowercase slug, no dots: both ``ConnectorKey`` and ``McpServerName`` become
# the ``<prefix>`` of a discovered tool name (``<prefix>.<tool>``), so keeping
# dots out of the prefix makes ``name.partition(".")`` routing unambiguous.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MAX_SLUG_LEN = 64

# Remote transports only in v1 (INV-I2, ARC-15): stdio/subprocess MCP belongs
# to the reserved ``sandbox`` module. The configured runtime allow-list
# (``IntegrationsSettings.mcp_allowed_transports``) can only narrow this.
_ALLOWED_TRANSPORTS = ("http", "sse")


@dataclass(frozen=True, slots=True)
class ConnectorKey:
    """Catalog identifier of a connector — 'github', 'slack', … (06 §9).

    Validates *shape* only; whether the key exists in the workspace's
    connector catalog is checked by the application against the injected
    provider map (the ``credentials.ProviderRef`` precedent).
    """

    value: str

    def __post_init__(self) -> None:
        raw = self.value
        normalized = raw.strip().lower()
        if not normalized or len(normalized) > _MAX_SLUG_LEN or not _SLUG_RE.match(normalized):
            raise InvalidIntegrationInput(f"invalid connector key: {raw!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class McpServerName:
    """Workspace-unique slug naming a registered MCP server (06 §9).

    Same slug rules as ``ConnectorKey`` (in particular: no dots), so a
    server name can serve as the tool-name prefix without colliding with the
    ``<prefix>.<tool>`` split.
    """

    value: str

    def __post_init__(self) -> None:
        raw = self.value
        normalized = raw.strip().lower()
        if not normalized or len(normalized) > _MAX_SLUG_LEN or not _SLUG_RE.match(normalized):
            raise InvalidIntegrationInput(f"invalid MCP server name: {raw!r}")
        object.__setattr__(self, "value", normalized)


@dataclass(frozen=True, slots=True)
class CipherRef:
    """Vault Transit ciphertext + the Transit key that encrypted it (06 §9).

    Never a raw secret (INV-I1, FR-121): the plaintext OAuth/auth material is
    encrypted through ``SecretsProvider`` before it ever reaches the domain,
    and only this opaque reference is persisted (``token_ref``/``auth_ref`` +
    ``key_id`` columns, 01 §2.9). Module-local copy — see module docstring.
    """

    ciphertext: str
    key_name: str

    def __post_init__(self) -> None:
        if not self.ciphertext or not self.key_name:
            raise InvalidIntegrationInput("CipherRef requires both ciphertext and key_name")


@dataclass(frozen=True, slots=True)
class McpEndpoint:
    """Remote MCP endpoint: URL + transport ∈ {http, sse} (06 §9, INV-I2).

    A non-HTTP(S) URL (stdio/file/…) or an unlisted transport is rejected in
    the constructor — stdio/subprocess MCP is out of v1 entirely (ARC-15).
    """

    url: str
    transport: str

    def __post_init__(self) -> None:
        if self.transport not in _ALLOWED_TRANSPORTS:
            raise UnsupportedMcpTransport(
                f"MCP transport {self.transport!r} is not supported in v1 "
                f"(allowed: {', '.join(_ALLOWED_TRANSPORTS)})"
            )
        if not (self.url.startswith("http://") or self.url.startswith("https://")):
            raise InvalidIntegrationInput(
                f"MCP endpoint must be a remote http(s) URL: {self.url!r}"
            )
