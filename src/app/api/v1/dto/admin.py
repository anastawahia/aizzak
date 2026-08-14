"""Wire shapes for the read-only platform-administration directory."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PlatformUserOut(BaseModel):
    id: str
    workspace_id: str
    email: str
    display_name: str
    status: str
    roles: list[str]
    last_seen_at: datetime | None
    online: bool
    created_at: datetime


class PlatformUserStatsOut(BaseModel):
    total: int
    active: int
    disabled: int
    online: int


class PlatformUsersPageOut(BaseModel):
    data: list[PlatformUserOut]
    stats: PlatformUserStatsOut
    limit: int
    offset: int
    next_offset: int | None


class PlatformUserStatusIn(BaseModel):
    status: Literal["active", "disabled"]
    reason: str = Field(min_length=3, max_length=500)


class PlatformUserStatusOut(BaseModel):
    id: str
    workspace_id: str
    status: Literal["active", "disabled"]
    changed: bool
    audit_id: str | None
    changed_at: datetime | None


class PlatformUserDeleteIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PlatformUserDeletionOut(BaseModel):
    id: str
    workspace_id: str
    deleted: bool
    roles_revoked: list[str]
    audit_id: str | None
    deleted_at: datetime | None


class SystemCpuOut(BaseModel):
    usage_percent: float
    cores: int
    interval_seconds: float
    load_average: list[float] | None


class SystemMemoryOut(BaseModel):
    total_gb: float
    used_gb: float
    available_gb: float
    cached_gb: float
    used_percent: float
    limit_gb: float | None


class SystemGpuOut(BaseModel):
    index: int
    name: str
    utilization_percent: float
    memory_utilization_percent: float
    memory_total_gb: float
    memory_used_gb: float
    memory_used_percent: float
    temperature_celsius: float | None
    power_watts: float | None


class SystemStatsOut(BaseModel):
    """One sample of one machine — see ``framework.ports.system_stats``.

    ``host`` and ``sampled_at`` are part of the reading, not metadata around
    it: unlike every other route here the answer depends on which process
    served the request, and a client that polls has to be able to tell a stale
    repeat from a fresh sample.
    """

    host: str
    sampled_at: datetime
    cpu: SystemCpuOut | None
    cpu_error: str | None
    memory: SystemMemoryOut | None
    memory_error: str | None
    gpus: list[SystemGpuOut]
    gpu_error: str | None


class ProviderRouteOut(BaseModel):
    namespace: Literal["llm", "embedding", "image"]
    capability: str
    model: str


class PlatformProviderKeyOut(BaseModel):
    """A stored platform key, metadata only.

    There is no field here that could carry the secret, and that is a property
    of the aggregate rather than of this class: the row holds a Vault
    ciphertext reference (INV-C2), so "the key is never returned" is not a
    redaction someone has to keep applying.
    """

    id: str
    label: str
    status: str
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class PlatformProviderOut(BaseModel):
    """One provider this deployment routes to.

    ``key`` absent means the PLATFORM supplies none — not that the provider is
    unusable. Resolution prefers a workspace's own credential and only falls
    back to the platform's (D-16), so a tenant holding its own key is
    unaffected by anything on this surface.
    """

    provider: str
    keyless: bool
    probeable: bool
    routes: list[ProviderRouteOut]
    key: PlatformProviderKeyOut | None


class PlatformProvidersOut(BaseModel):
    data: list[PlatformProviderOut]


class PlatformProviderKeyIn(BaseModel):
    secret: str = Field(min_length=1, max_length=8192)
    label: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=3, max_length=500)


class PlatformProviderKeyDeleteIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PlatformProviderKeyChangeOut(BaseModel):
    provider: str
    previous_status: Literal["absent", "active"]
    changed: bool
    key: PlatformProviderKeyOut | None
    audit_id: str | None


class PlatformProviderProbeIn(BaseModel):
    """``secret`` absent probes the STORED key; present probes a candidate.

    A candidate is never written anywhere — it is spent on one call and
    forgotten, which is what makes "test before you save" possible without a
    draft credential existing in the database.
    """

    secret: str | None = Field(default=None, min_length=1, max_length=8192)


class PlatformProviderProbeOut(BaseModel):
    provider: str
    ok: bool
    latency_ms: int
    detail: str | None
    checked_at: datetime


class PlatformWorkspaceRoleIn(BaseModel):
    role: Literal["owner", "admin", "member", "viewer"]
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class PlatformAdminRoleIn(BaseModel):
    enabled: bool
    reason: str = Field(min_length=3, max_length=500)


class PlatformRoleOut(BaseModel):
    id: str
    workspace_id: str
    role: str
    enabled: bool
    changed: bool
    audit_id: str | None
    changed_at: datetime | None
