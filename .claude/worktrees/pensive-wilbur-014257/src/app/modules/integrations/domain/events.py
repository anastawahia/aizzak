"""Integrations domain events (pure — 06-domain-models §9, 04-event-catalog §5).

**Internal, in-memory only**: unlike files/knowledge/media, none of these is
a global event in v1 — they never cross Redis Streams and have no JSON wire
schema (04 §5: "integrations … داخلية فقط ولا تُرقّى إلى مجارٍ في v1").
Field sets mirror the 06 §9 event list verbatim. Promoting them through the
Outbox is a documented future extension point, not v1 work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ConnectionEstablished:
    """A connector finished its OAuth handshake and is usable (06 §9)."""

    connection_id: str
    workspace_id: str
    connector_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ConnectionRevoked:
    """A connection was revoked and its stored secret dropped (06 §9)."""

    connection_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class TokenRefreshed:
    """A lazy (on-demand) token renewal was persisted (06 §9, FR-124)."""

    connection_id: str
    expires_at: datetime
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class McpServerRegistered:
    """A remote MCP server was registered for the workspace (06 §9)."""

    server_id: str
    workspace_id: str
    occurred_at: datetime


IntegrationsEvent = ConnectionEstablished | ConnectionRevoked | TokenRefreshed | McpServerRegistered
