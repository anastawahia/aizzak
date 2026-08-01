"""Access inbound ports (02-port-contracts §2).

``AuthorizationService`` is what the API's RBAC guard (and other callers) invoke
instead of importing the access module directly (ARC-07/08). ``roles_of`` reads
the caller's roles for a workspace; ``is_allowed`` is the pure, synchronous
decision over role/permission *strings* (adapted to the domain enums inside the
implementation).

``RoleSeeding`` (6.4) is the authentication path's separate, deliberately
one-method door: 05 §1.5 gives a just-provisioned user the ``owner`` role of the
workspace that was minted for them, and nothing else in this system may grant a
role without a route and a guard. The port names ``seed_owner`` rather than
``assign(role)`` for exactly that reason — a caller holding it can create the
first owner of a brand-new workspace and cannot make itself an admin of an
existing one. (``platform_admin`` stays unreachable from here too, but by a
second, independent rule: ``AssignRole`` refuses it outright, 05 §1.5.)
"""

from __future__ import annotations

from typing import Protocol

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid


class AuthorizationService(Protocol):
    """Injected authorization facade — no cross-module imports for callers."""

    async def roles_of(self, ctx: ExecutionContext, user_id: Uuid) -> frozenset[str]: ...

    def is_allowed(self, roles: frozenset[str], permission: str) -> bool: ...


class RoleSeeding(Protocol):
    """The JIT provisioning path's only write into this module (05 §1.5).

    Idempotent: seeding an owner who already holds the role is a no-op, so a
    login that races another login for the same brand-new user cannot fail on
    the uniqueness invariant (INV-A3).
    """

    async def seed_owner(self, ctx: ExecutionContext, user_id: Uuid) -> None: ...
