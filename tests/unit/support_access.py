"""In-memory ``access`` wiring shared by the API router tests (6.4-ب).

Not a ``test_*`` module, so pytest never collects it — the
``support_workspace_usage`` precedent.

**The REAL ``Authorization``, not a fake decision.** Every router test now
builds an app whose routes are guarded, so what those tests exercise has to be
the actual ``RoleCatalog`` (05 §1.3, D-24): a stub that answered ``True`` would
turn 40 guards into decoration and hide precisely the mistake a guard exists to
catch — a route asking for a permission its intended role does not hold. Only
the REPOSITORY is faked here, and only because ``roles_of`` needs one; the
guards never call it, since the roles ride on ``ExecutionContext`` already.
"""

from __future__ import annotations

from app.framework.context.execution_context import ExecutionContext
from app.framework.types import Uuid
from app.modules.access.application.use_cases import Authorization
from app.modules.access.domain.entities import RoleAssignment
from app.modules.access.domain.value_objects import RoleName


class _EmptyRoleAssignments:
    """A ``RoleAssignmentRepository`` with nothing in it.

    ``roles_of`` is not on the API layer's path — the authentication path
    resolved the roles once, onto the context — so a router test that somehow
    reached this would be testing the wrong door, and an empty store makes that
    visible as "no roles" rather than as a plausible answer.
    """

    async def list_for_user(self, ctx: ExecutionContext, user_id: Uuid) -> list[RoleAssignment]:
        return []

    async def find(
        self, ctx: ExecutionContext, user_id: Uuid, role: RoleName
    ) -> RoleAssignment | None:
        return None

    async def add(self, ctx: ExecutionContext, assignment: RoleAssignment) -> None:
        raise AssertionError("the API layer never writes a role assignment")

    async def remove(self, ctx: ExecutionContext, assignment_id: Uuid) -> None:
        raise AssertionError("the API layer never writes a role assignment")

    async def count_by_role(self, ctx: ExecutionContext, role: RoleName) -> int:
        return 0


def build_authorization() -> Authorization:
    """The real authorization service over an empty assignment store."""
    return Authorization(_EmptyRoleAssignments())
