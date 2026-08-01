"""Credentials domain events (pure — 06-domain-models §3, 04-event-catalog §5).

In-memory domain events (internal only — not promoted to streams in v1). The
``provider``/``scope`` are carried as their string values for stable
serialization when later dispatched to the event bus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CredentialAdded:
    """A provider credential was stored for a user or the platform."""

    credential_id: str
    provider: str
    scope: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CredentialRevoked:
    """A stored credential was revoked."""

    credential_id: str
    occurred_at: datetime


CredentialEvent = CredentialAdded | CredentialRevoked
