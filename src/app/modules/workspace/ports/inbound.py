"""Workspace inbound port (02-port-contracts §2) — Phase 6.4.

``UserProvisioning`` is the ONE face the authentication path may reach into
this module through (ARC-07/08: a caller outside a module talks to it through
an injected inbound port, never by importing its use-cases). It exists because
JIT seeding is the authentication path's job — 05 §1.5 — and that path lives in
the API layer, which must be able to mirror a verified Firebase identity into a
workspace without also being able to rename, archive or read anything else.

**What this port omits is the point** (the ``ApiServices`` discipline, §3.63):
``provision_on_login`` is the whole surface. It cannot list users, cannot touch
another workspace, and cannot be handed an arbitrary ``ExecutionContext`` — it
TAKES no context, because there is none before an identity is resolved; it
MINTS the tenant scope it returns.

``ProvisionedUser`` carries exactly the four facts the auth path acts on, not
the ``User`` aggregate: the tenant and identity a ``Principal`` is built from,
whether the account may act at all (``active`` — read fresh on every single
request, the compensating control for v1's "no revocation check" decision,
``infrastructure/auth/firebase_auth.py`` D7), and whether this login was the
FIRST one (``created`` — the only moment 05 §1.5's owner grant is due). The
status enum is flattened to a bool deliberately: the auth path decides
"may act / may not", and a second ``UserStatus`` member (should one ever
arrive) is this module's business to interpret, not the API layer's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.framework.types import Uuid


@dataclass(frozen=True, slots=True)
class ProvisionedUser:
    """The identity facts the authentication path needs after a login."""

    workspace_id: Uuid
    user_id: Uuid
    active: bool
    created: bool


class UserProvisioning(Protocol):
    """Mirror a verified external identity into a workspace user (FR-01, 05 §1.5).

    Idempotent by contract: a repeat login for a known ``firebase_uid`` returns
    the existing user with ``created=False`` and writes nothing.
    """

    async def provision_on_login(
        self,
        *,
        firebase_uid: str,
        email: str,
        display_name: str | None,
        correlation_id: Uuid,
    ) -> ProvisionedUser: ...
