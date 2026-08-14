"""Platform-administration routes.

Cross-tenant reads live behind the dedicated ``platform:admin`` permission and
the platform-admin RLS sentinel; a tenant role cannot widen its SQL scope.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.admin import (
    PlatformAdminRoleIn,
    PlatformProviderKeyChangeOut,
    PlatformProviderKeyDeleteIn,
    PlatformProviderKeyIn,
    PlatformProviderKeyOut,
    PlatformProviderOut,
    PlatformProviderProbeIn,
    PlatformProviderProbeOut,
    PlatformProvidersOut,
    PlatformRoleOut,
    PlatformUserDeleteIn,
    PlatformUserDeletionOut,
    PlatformUserOut,
    PlatformUsersPageOut,
    PlatformUserStatsOut,
    PlatformUserStatusIn,
    PlatformUserStatusOut,
    PlatformWorkspaceRoleIn,
    ProviderRouteOut,
    SystemCpuOut,
    SystemGpuOut,
    SystemMemoryOut,
    SystemStatsOut,
)
from app.framework.clock import utc_now
from app.framework.errors import AppError
from app.modules.access.domain.value_objects import Permission
from app.modules.admin.application.providers import PlatformProviderUseCases
from app.modules.admin.ports.directory import PlatformUser
from app.modules.admin.ports.providers import PlatformKeyChange, PlatformProviderKey
from app.modules.admin.ports.roles import PlatformRoleChange

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(current_principal)])


def _to_user_out(user: PlatformUser) -> PlatformUserOut:
    return PlatformUserOut(
        id=user.id,
        workspace_id=user.workspace_id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        roles=list(user.roles),
        last_seen_at=user.last_seen_at,
        online=user.online,
        created_at=user.created_at,
    )


@router.get("/users", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))])
async def get_platform_users(
    services: Services,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> PlatformUsersPageOut:
    """Return a bounded, read-only cross-tenant directory page.

    ``q`` matches a case-insensitive substring of the email or display name.
    The stats then count the matching users rather than the whole platform,
    because ``total`` is also what bounds the offsets this route will serve.
    """
    if services.admin is None:
        raise AppError("platform administration is not configured", code="common.internal")
    page = await services.admin.users.execute(limit=limit, offset=offset, search=q)
    next_offset = offset + limit if offset + limit < page.total else None
    return PlatformUsersPageOut(
        data=[_to_user_out(user) for user in page.items],
        stats=PlatformUserStatsOut(
            total=page.total, active=page.active, disabled=page.disabled, online=page.online
        ),
        limit=limit,
        offset=offset,
        next_offset=next_offset,
    )


@router.patch("/users/{user_id}/status", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))])
async def patch_platform_user_status(
    user_id: UUID,
    body: PlatformUserStatusIn,
    services: Services,
    ctx: Context,
) -> PlatformUserStatusOut:
    """Enable or disable one account and retain the operator's reason."""
    if services.admin is None or services.session_revocations is None:
        raise AppError("platform administration is not configured", code="common.internal")
    change = await services.admin.accounts.execute(
        ctx,
        target_user_id=str(user_id),
        status=body.status,
        reason=body.reason,
    )
    # Persist first: a cache outage cannot leave an enabled account announced
    # as disabled. Disabled accounts are also refused on every fresh DB-backed
    # authentication, while this denylist immediately invalidates old tokens.
    if change.status == "disabled":
        await services.session_revocations.revoke(change.firebase_uid)
        await services.hub.disconnect_user(change.user_id)
    elif change.changed:
        # Re-enable is meant to be effective immediately, not after the old
        # token's one-hour denylist TTL. The account is active in PostgreSQL
        # before this delete, so a failed delete remains fail-closed.
        await services.session_revocations.clear(change.firebase_uid)
    return PlatformUserStatusOut(
        id=change.user_id,
        workspace_id=change.workspace_id,
        status=change.status,
        changed=change.changed,
        audit_id=change.audit_id,
        changed_at=change.changed_at,
    )


@router.delete("/users/{user_id}", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))])
async def delete_platform_user(
    user_id: UUID,
    body: PlatformUserDeleteIn,
    services: Services,
    ctx: Context,
) -> PlatformUserDeletionOut:
    """Retire one account for good and cut off the sessions it still holds.

    This is not a stronger disable. The account's email and display name are
    erased in the same transaction and its roles are revoked; nothing here is
    reversible, and only the audit row and a tombstone id survive. Repeating
    the call answers ``deleted: false`` with the original date rather than
    re-dating a finished event. The tenant's own data is untouched — that is a
    separate, still-missing sweep, not something this route silently implies.
    """
    if services.admin is None or services.session_revocations is None:
        raise AppError("platform administration is not configured", code="common.internal")
    deletion = await services.admin.deletions.execute(
        ctx, target_user_id=str(user_id), reason=body.reason
    )
    # Same ordering as disabling, for the same reason: PostgreSQL is the
    # authority, so a cache outage can never announce a deletion that did not
    # commit. The tombstone already refuses every fresh authentication; this
    # denylist is what invalidates the tokens already issued.
    await services.session_revocations.revoke(deletion.firebase_uid)
    await services.hub.disconnect_user(deletion.user_id)
    return PlatformUserDeletionOut(
        id=deletion.user_id,
        workspace_id=deletion.workspace_id,
        deleted=deletion.deleted,
        roles_revoked=list(deletion.roles_revoked),
        audit_id=deletion.audit_id,
        deleted_at=deletion.deleted_at,
    )


@router.patch(
    "/users/{user_id}/roles/workspace",
    dependencies=[Depends(require(Permission.PLATFORM_ADMIN))],
)
async def patch_platform_workspace_role(
    user_id: UUID,
    body: PlatformWorkspaceRoleIn,
    services: Services,
    ctx: Context,
) -> PlatformRoleOut:
    """Grant or revoke one workspace role, never leaving a workspace ownerless."""
    if services.admin is None:
        raise AppError("platform administration is not configured", code="common.internal")
    change = await services.admin.workspace_roles.execute(
        ctx,
        target_user_id=str(user_id),
        role=body.role,
        enabled=body.enabled,
        reason=body.reason,
    )
    if change.changed:
        await services.hub.disconnect_user(change.user_id)
    return _to_role_out(change)


@router.patch(
    "/users/{user_id}/roles/platform-admin",
    dependencies=[Depends(require(Permission.PLATFORM_ADMIN))],
)
async def patch_platform_admin_role(
    user_id: UUID,
    body: PlatformAdminRoleIn,
    services: Services,
    ctx: Context,
) -> PlatformRoleOut:
    """Grant or revoke the separate, cross-tenant platform-admin role."""
    if services.admin is None:
        raise AppError("platform administration is not configured", code="common.internal")
    change = await services.admin.platform_roles.execute(
        ctx,
        target_user_id=str(user_id),
        enabled=body.enabled,
        reason=body.reason,
    )
    if change.changed:
        await services.hub.disconnect_user(change.user_id)
    return _to_role_out(change)


@router.get("/system/stats", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))])
async def get_system_stats(services: Services) -> SystemStatsOut:
    """Report the answering machine's CPU, memory and accelerators.

    Deliberately not the browser's view of ``/metrics``: that endpoint is a
    Prometheus exposition blocked at the nginx edge, and its two gauges answer
    a different question (is the platform degraded) over state held in
    Postgres and Redis. This one reads the kernel of whichever process served
    the request, which is why ``host`` travels with the sample.

    Each section can be absent with a reason and the response is still 200 —
    a page that 500s the moment a driver reloads is a monitoring page that
    goes blind exactly when it is being read.
    """
    if services.system_stats is None:
        raise AppError("system monitoring is not configured", code="common.internal")
    stats = await services.system_stats.read()
    return SystemStatsOut(
        host=stats.host,
        sampled_at=stats.sampled_at,
        cpu=None
        if stats.cpu is None
        else SystemCpuOut(
            usage_percent=stats.cpu.usage_percent,
            cores=stats.cpu.cores,
            interval_seconds=stats.cpu.interval_seconds,
            load_average=None if stats.cpu.load_average is None else list(stats.cpu.load_average),
        ),
        cpu_error=stats.cpu_error,
        memory=None
        if stats.memory is None
        else SystemMemoryOut(
            total_gb=stats.memory.total_gb,
            used_gb=stats.memory.used_gb,
            available_gb=stats.memory.available_gb,
            cached_gb=stats.memory.cached_gb,
            used_percent=stats.memory.used_percent,
            limit_gb=stats.memory.limit_gb,
        ),
        memory_error=stats.memory_error,
        gpus=[
            SystemGpuOut(
                index=gpu.index,
                name=gpu.name,
                utilization_percent=gpu.utilization_percent,
                memory_utilization_percent=gpu.memory_utilization_percent,
                memory_total_gb=gpu.memory_total_gb,
                memory_used_gb=gpu.memory_used_gb,
                memory_used_percent=gpu.memory_used_percent,
                temperature_celsius=gpu.temperature_celsius,
                power_watts=gpu.power_watts,
            )
            for gpu in stats.gpus
        ],
        gpu_error=stats.gpu_error,
    )


@router.get("/providers", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))])
async def get_platform_providers(services: Services) -> PlatformProvidersOut:
    """List the providers this deployment routes to and the keys it supplies.

    The list is the routing table, not a vocabulary of provider names: a
    provider no capability points at is one whose key nothing would ever
    spend, so offering to store one would be offering a no-op. Each entry
    carries the routes that make it worth a key.

    A provider with no ``key`` is not disabled — see ``PlatformProviderOut``.
    """
    providers = _providers(services)
    views = await providers.list.execute()
    return PlatformProvidersOut(
        data=[
            PlatformProviderOut(
                provider=view.provider,
                keyless=view.keyless,
                probeable=view.probeable,
                routes=[
                    ProviderRouteOut(
                        namespace=route.namespace,
                        capability=route.capability,
                        model=route.model,
                    )
                    for route in view.routes
                ],
                key=_to_key_out(view.key),
            )
            for view in views
        ]
    )


@router.put("/providers/{provider}/key", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))])
async def put_platform_provider_key(
    provider: str,
    body: PlatformProviderKeyIn,
    services: Services,
    ctx: Context,
) -> PlatformProviderKeyChangeOut:
    """Store or rotate the platform key for one provider.

    ``PUT`` rather than ``POST`` because the resource is singular and the
    operation is idempotent in the shape that matters: a provider has one
    platform key, and sending a second one replaces the first rather than
    adding to it. The replacement is a single transaction, so the fleet never
    sees two active platform keys for one provider.

    Storing a key is also how a provider is ENABLED platform-wide; there is no
    separate flag, because there is nothing at runtime a flag could switch.
    """
    providers = _providers(services)
    change = await providers.set_key.execute(
        ctx, provider=provider, secret=body.secret, label=body.label, reason=body.reason
    )
    return _to_key_change_out(change)


@router.delete(
    "/providers/{provider}/key", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))]
)
async def delete_platform_provider_key(
    provider: str,
    body: PlatformProviderKeyDeleteIn,
    services: Services,
    ctx: Context,
) -> PlatformProviderKeyChangeOut:
    """Withdraw the platform key for one provider (idempotent).

    A body rather than a bare 204: the reason is required, like every other
    administrative mutation here, and the answer distinguishes "there was a key
    and it is gone" from "there was none" — a difference an operator chasing a
    broken provider needs and a 204 cannot express.

    Workspaces holding their own key for this provider keep working. That is
    ``D-16``'s user-before-platform preference, not an oversight.
    """
    providers = _providers(services)
    change = await providers.revoke_key.execute(ctx, provider=provider, reason=body.reason)
    return _to_key_change_out(change)


@router.post(
    "/providers/{provider}/probe", dependencies=[Depends(require(Permission.PLATFORM_ADMIN))]
)
async def probe_platform_provider(
    provider: str,
    body: PlatformProviderProbeIn,
    services: Services,
    ctx: Context,
) -> PlatformProviderProbeOut:
    """Spend one minimal call and report whether the key works.

    A rejected key is ``ok: false`` with a reason and a 200 — the request
    succeeded, and its answer is that the credential did not. Reserving the
    error channel for failures of the PROBE (an unconfigured provider, no
    stored key, the rate limit) is what lets a client tell "we asked and the
    vendor said no" from "we never got to ask".

    The reason is the adapter's own translated message. Adapters map failures
    from the HTTP status and never from the response body, so nothing a vendor
    echoed back — a credential included — can reach this field.
    """
    providers = _providers(services)
    outcome = await providers.probe.execute(ctx, provider=provider, secret=body.secret)
    return PlatformProviderProbeOut(
        provider=provider,
        ok=outcome.ok,
        latency_ms=outcome.latency_ms,
        detail=outcome.detail,
        checked_at=utc_now(),
    )


def _providers(services: Services) -> PlatformProviderUseCases:
    if services.providers is None:
        raise AppError("provider administration is not configured", code="common.internal")
    return services.providers


def _to_key_out(key: PlatformProviderKey | None) -> PlatformProviderKeyOut | None:
    if key is None:
        return None
    return PlatformProviderKeyOut(
        id=key.id,
        label=key.label,
        status=key.status,
        created_by=key.created_by,
        created_at=key.created_at,
        updated_at=key.updated_at,
    )


def _to_key_change_out(change: PlatformKeyChange) -> PlatformProviderKeyChangeOut:
    return PlatformProviderKeyChangeOut(
        provider=change.provider,
        previous_status=change.previous_status,
        changed=change.changed,
        key=_to_key_out(change.key),
        audit_id=change.audit_id,
    )


def _to_role_out(change: PlatformRoleChange) -> PlatformRoleOut:
    """Keep the public role-transition shape independent from the SQL adapter."""
    return PlatformRoleOut(
        id=change.user_id,
        workspace_id=change.workspace_id,
        role=change.role,
        enabled=change.enabled,
        changed=change.changed,
        audit_id=change.audit_id,
        changed_at=change.changed_at,
    )
