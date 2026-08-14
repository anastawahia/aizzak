"""``SystemStatsSource`` driven port — the host telemetry the platform-admin
System Monitor tab reads (``BE-ADM-007``,
``/home/web_app/docs/AIZZAK_FEATURE_MIGRATION_PLAN.md`` §5.3).

**Why this is a second monitoring port and not two more gauges on
``MetricsSource``.** That port answers "is the platform degraded right now"
for a Prometheus scrape, and every value it returns is read out of Postgres
or Redis — state no process owns privately, which is exactly what makes it
identical no matter which gunicorn sibling computes it. Nothing here has that
property. CPU utilisation, resident memory and a GPU's temperature are
properties of ONE machine, measured through that machine's own kernel, and
the number depends entirely on which host answered. Mixing the two into one
port would put a value with no such invariance behind a docstring that
promises it.

That is also why ``SystemStats.host`` is not decoration. ``/metrics`` can be
scraped from any replica because the answer does not vary; this endpoint
CANNOT, and an operator reading 94% CPU needs to know whether that is the
inference box or the third API replica. The field names the machine that
took the sample, so a reading is never anonymous.

**Nothing here ever raises — the whole port is total.** A monitoring surface
that answers 500 because a kernel file moved or a driver is mid-reload tells
the operator strictly less than one that answers "GPU: unavailable, and here
is why", and it does it at exactly the moment they came to look. So each of
the three sections is independently optional and carries its own error
string: a host with no NVIDIA card still reports CPU and memory, and a broken
``nvidia-smi`` degrades that one card to a message rather than taking the
page with it. The paired ``X``/``X_error`` shape is deliberate over a single
union — the API layer renders both halves side by side, and a section can be
absent for a reason worth printing.

Implemented by ``infrastructure.monitoring.system_stats.HostSystemStats``
(the Composition Root's only caller); a fake substitutes it in
``tests/unit/test_api_admin_system.py`` so the route's shaping can be
exercised without a kernel to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CpuStats:
    """Processor load, measured over a stated window rather than "now".

    A CPU percentage is not an instantaneous quantity: it only exists as busy
    time divided by elapsed time between two samples. ``interval_seconds``
    publishes which window ``usage_percent`` describes, because the same host
    reads very differently over 100ms and over a minute, and a bare number
    invites the reader to assume whichever suits them.

    ``load_average`` is carried alongside rather than instead: utilisation
    saturates at 100% and stops telling you anything, while the run-queue
    length keeps climbing and is the signal that says how far past saturation
    the machine is. ``None`` where the kernel does not publish it.
    """

    usage_percent: float
    cores: int
    interval_seconds: float
    load_average: tuple[float, float, float] | None


@dataclass(frozen=True, slots=True)
class MemoryStats:
    """Physical memory on the host that answered.

    ``used_gb`` deliberately excludes reclaimable page cache: on a busy Linux
    box nearly all free memory is cache within minutes, so counting it as
    "used" would show every healthy host at ~100% forever. ``cached_gb`` is
    reported separately so the reader sees where the rest went.

    ``limit_gb`` is the cgroup ceiling on the process that took the sample,
    when it has one. The other numbers describe the machine — which is the
    only scope a GPU reading can have, so it is the scope this whole snapshot
    keeps — and this field is what stops that from being read as headroom the
    API can actually spend.
    """

    total_gb: float
    used_gb: float
    available_gb: float
    cached_gb: float
    used_percent: float
    limit_gb: float | None


@dataclass(frozen=True, slots=True)
class GpuStats:
    """One accelerator, as its own driver reports it.

    A list, not a single card: a multi-GPU inference host is the ordinary
    deployment for this platform, and summing or averaging cards would hide
    the single failure an operator opens this page for — one card pinned at
    100% while its siblings idle, or one running 20°C hotter than the rest.

    ``utilization_percent`` and ``memory_used_percent`` measure different
    things and routinely disagree: a loaded model occupies memory whether or
    not it is computing, so a card can sit at 90% memory and 0% utilisation
    and be perfectly healthy.
    """

    index: int
    name: str
    utilization_percent: float
    memory_utilization_percent: float
    memory_total_gb: float
    memory_used_gb: float
    memory_used_percent: float
    temperature_celsius: float | None
    power_watts: float | None


@dataclass(frozen=True, slots=True)
class SystemStats:
    """One sample of one machine, with each section free to be missing.

    ``gpus == ()`` with ``gpu_error is None`` means the driver answered and
    reported no cards — this host genuinely has no accelerator, which is not a
    fault. The two are kept distinguishable because "there is nothing to show"
    and "nothing answered" call for different reactions from whoever reads it.
    """

    host: str
    sampled_at: datetime
    cpu: CpuStats | None
    cpu_error: str | None
    memory: MemoryStats | None
    memory_error: str | None
    gpus: tuple[GpuStats, ...]
    gpu_error: str | None


class SystemStatsSource(Protocol):
    async def read(self) -> SystemStats:
        """One fresh sample. **Never raises** — see the module docstring."""
        ...
