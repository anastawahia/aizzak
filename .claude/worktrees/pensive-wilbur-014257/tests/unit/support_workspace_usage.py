"""In-memory workspace/usage wiring shared by the API router tests (6.1-و-1).

Not a ``test_*`` module, so pytest never collects it — the
``support_files_media`` precedent: every router test file constructs
``ApiServices``, and a per-file copy of these fakes is how copies drift.

The fakes are faithful on what the routes depend on: the workspace
repository scopes reads by ``ctx.workspace_id`` (so a foreign id is a 404,
the way RLS makes it one), and the usage ledger stores its limit set by
WHOLE-SET replacement, which is the one semantic ``PUT /usage/limits``
actually rides on. ``append``/``rollup`` are present to satisfy the port's
shape and are never called from the API — INV-U4 keeps metering out of this
layer, and a test that reached them would be testing the wrong door.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.framework.context.execution_context import ExecutionContext
from app.modules.usage.application.use_cases import (
    GetUsageSummary,
    ListLimits,
    SetLimits,
    UsageUseCases,
)
from app.modules.usage.domain.entities import UsageLimit
from app.modules.usage.domain.read_models import UsageRollup
from app.modules.usage.ports.inbound import UsageCharge
from app.modules.usage.ports.repository import UsageTotals
from app.modules.workspace.application.use_cases import (
    GetWorkspace,
    RenameWorkspace,
    WorkspaceUseCases,
)
from app.modules.workspace.domain.entities import Workspace
from app.modules.workspace.domain.value_objects import WorkspaceName, WorkspaceStatus

SEEDED_WORKSPACE_NAME = "Acme"
SEEDED_CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@dataclass
class InMemoryWorkspaceRepository:
    """A structural ``WorkspaceRepository`` over one dict."""

    rows: dict[str, Workspace] = field(default_factory=dict)

    async def get(self, ctx: ExecutionContext, workspace_id: str) -> Workspace | None:
        row = self.rows.get(workspace_id)
        if row is None or row.id != ctx.workspace_id:
            return None
        return row

    async def add(self, ctx: ExecutionContext, workspace: Workspace) -> None:
        self.rows[workspace.id] = workspace

    async def save(self, ctx: ExecutionContext, workspace: Workspace) -> None:
        self.rows[workspace.id] = workspace


@dataclass
class InMemoryUsageLedger:
    """A structural ``UsageLedgerRepository`` over two lists."""

    limits: list[UsageLimit] = field(default_factory=list)
    rollups: list[UsageRollup] = field(default_factory=list)

    async def append(self, ctx: ExecutionContext, charge: UsageCharge) -> bool:
        raise AssertionError("metering is not reachable from the API (INV-U4)")

    async def rollup(
        self, ctx: ExecutionContext, agent: str, provider: str, period: str
    ) -> UsageTotals:
        raise AssertionError("metering is not reachable from the API (INV-U4)")

    async def get_limits(self, ctx: ExecutionContext) -> list[UsageLimit]:
        return [row for row in self.limits if row.workspace_id == ctx.workspace_id]

    async def replace_limits(self, ctx: ExecutionContext, limits: Sequence[UsageLimit]) -> None:
        self.limits = [row for row in self.limits if row.workspace_id != ctx.workspace_id]
        self.limits.extend(limits)

    async def list_rollups(self, ctx: ExecutionContext, period: str) -> Sequence[UsageRollup]:
        return [
            row
            for row in self.rollups
            if row.workspace_id == ctx.workspace_id and row.period.value == period
        ]


@dataclass(frozen=True, slots=True)
class WorkspaceUsageStack:
    """Both bundles plus the fakes a test asserts against."""

    workspace: WorkspaceUseCases
    usage: UsageUseCases
    workspace_repository: InMemoryWorkspaceRepository
    ledger: InMemoryUsageLedger


def build_workspace_usage(workspace_id: str | None = None) -> WorkspaceUsageStack:
    """One repository behind every face of each module — the same
    single-instance wiring the Composition Root builds.

    Seeds the workspace row when ``workspace_id`` is given, because every
    route here reads the caller's OWN tenant root: an unseeded stack answers
    404 truthfully, which is a case worth testing but a poor default for the
    other routers' ``ApiServices``.
    """
    workspaces = InMemoryWorkspaceRepository()
    ledger = InMemoryUsageLedger()
    if workspace_id is not None:
        workspaces.rows[workspace_id] = Workspace(
            id=workspace_id,
            owner_user_id=workspace_id,
            name=WorkspaceName(SEEDED_WORKSPACE_NAME),
            status=WorkspaceStatus.ACTIVE,
            created_at=SEEDED_CREATED_AT,
            updated_at=SEEDED_CREATED_AT,
            version=1,
        )
    return WorkspaceUsageStack(
        workspace=WorkspaceUseCases(
            get=GetWorkspace(workspaces), rename=RenameWorkspace(workspaces)
        ),
        usage=UsageUseCases(
            summary=GetUsageSummary(ledger),
            list_limits=ListLimits(ledger),
            set_limits=SetLimits(ledger),
        ),
        workspace_repository=workspaces,
        ledger=ledger,
    )
