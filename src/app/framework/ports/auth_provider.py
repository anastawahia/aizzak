"""AuthProvider driven port (02-port-contracts §1.10 · Firebase, D-25).

Verifies a Firebase ID token locally against cached JWKS (no round-trip per
request) and returns the caller identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Json


@dataclass(frozen=True, slots=True)
class Identity:
    firebase_uid: str
    email: str | None
    email_verified: bool
    claims: Json


class AuthProvider(Protocol):
    async def verify_token(self, id_token: str) -> Identity: ...
