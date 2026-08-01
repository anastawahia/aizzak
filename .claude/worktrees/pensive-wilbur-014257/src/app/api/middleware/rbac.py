"""The RBAC route guard — ``require(Permission.X)`` (05-rbac-config-secrets
§1.4/§4 · D-24 · SEC-02) — Phase 6.4-ب.

One dependency per route, declared beside the route it protects:

    @router.delete("/{file_id}", dependencies=[Depends(require(Permission.FILES_DELETE))])

**The decision is pure.** By the time a guard runs, the caller's roles are
already on the ``ExecutionContext`` — read from ``access`` by the
authentication path (6.4-أ), fresh, on this very request. So the guard performs
no I/O at all: it asks ``AuthorizationService.is_allowed`` (a lookup in the
code-static ``RoleCatalog``, D-24) and raises or does not. 05 §1.4's "التفويض
دالة نقية قابلة للاختبار بلا I/O" is a property of this file, not an
aspiration: a guard that re-read the roles would double every request's queries
to prove something the request already knows, and would open a window in which
two guards on the same request could disagree.

**The permission is an enum, not a string.** 05 §1.4 writes
``require("files:write")``, and the string form is what the inbound port speaks
(02 §2, deliberately — a caller must not need this module's domain types). But
the ROUTE side gains something real from the closed vocabulary:
``Permission.FILES_WRTIE`` does not import, while ``require("files:wrtie")``
imports fine and then denies EVERY caller forever — ``is_allowed`` refuses an
unparseable permission, which is the right default and a silent one. This is
not hypothetical: two of the five bundled agents shipped a manifest requiring
``media:generate``, a permission that has never existed in 05 §1.2 (§3.73's
hand-off note), and nothing caught it because nothing read the field.

**A refusal is ``authz.forbidden``/403 (05 §1.4), and it names the permission.**
Unlike the 401s of the authentication path — where the reason is withheld
because it tells an attacker which credentials are worth forging — a 403 is
answered to someone we have already identified, and "which permission you
lack" is exactly what they need to know to ask an admin for it.

**What this layer does NOT decide: whose data.** RBAC says what a ROLE may do;
tenant isolation says which rows exist at all (RLS + the explicit
``workspace_id`` filter, DD-04/SEC-03). 05 §1.4 draws that line explicitly, and
it is why no guard here takes a resource id: a member of another workspace
holding ``files:read`` still gets a 404, because the row is not visible to the
transaction at all. Ownership WITHIN a workspace is likewise not modelled in
v1 — 05's matrix is role-based, not per-object (alpha's ``require_owned_agent``
has no equivalent because our resources are workspace-scoped, not user-scoped).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.api.v1.dependencies import Context, Services
from app.framework.errors import ForbiddenError
from app.modules.access.domain.value_objects import Permission


@dataclass(frozen=True, slots=True)
class PermissionGuard:
    """The dependency ``require`` builds — a callable object rather than a
    closure so the permission it enforces stays READABLE after the fact.

    That is what lets ``test_api_rbac.py`` walk the finished application and
    compare the guard on every operation against 05 §4's map, in both
    directions. A closure would hide the permission inside a cell, and the only
    remaining way to check the map would be to re-read 40 decorators by eye —
    which is precisely the kind of check §3.72 established a diff should do.
    """

    permission: Permission

    async def __call__(self, ctx: Context, services: Services) -> None:
        if not services.authorization.is_allowed(ctx.roles, self.permission.value):
            raise ForbiddenError(f"missing permission: {self.permission.value}")


def require(permission: Permission) -> PermissionGuard:
    """The route guard for one permission (05 §1.4)."""
    return PermissionGuard(permission)
