"""``SystemStatsSource`` over the Linux kernel (``/proc``, ``/sys/fs/cgroup``)
and the NVIDIA driver (``nvidia-smi``) — ``BE-ADM-007``.

**Why the kernel's own files and a subprocess, rather than a library.** Every
number here is published by the kernel as plain text that has been stable for
two decades, and reading it is three ``open()`` calls; the alternatives are a
new unpinned runtime dependency (this repository has no lockfile — see
``pyproject.toml``'s own note on what that already cost once) to wrap those
same three files, and, for the GPU half, a Python binding to a driver library
whose version must match the installed driver. ``nvidia-smi`` ships WITH the
driver, so it cannot drift out of step with it.

**Nothing in here raises.** The port is total (see its module docstring), so
each section is wrapped and degrades to a message. That is not defensive
padding: the failure modes are real and routine — a driver reloading mid-scrape,
``/proc`` masked in a hardened container, ``nvidia-smi`` hanging on a wedged
card. The last one is why the subprocess carries a timeout and is killed on
expiry: an admin page that hangs its worker is worse than one that says the
GPU did not answer.

**The CPU baseline is the one piece of retained state, and it is retained
because a percentage cannot be measured without it.** Busy time over elapsed
time needs two readings of ``/proc/stat``; the first call has nothing to
subtract from and so takes its own short sample, and every later call
subtracts the previous reading instead — giving a window as wide as the
caller's poll interval rather than a jittery 150ms one. What is kept is a raw
monotonic kernel counter, not a value this process accumulated, and
``/proc/stat`` is host-global: any process on this machine reading it at time
*t* sees the same numbers, so the delta is correct even when the earlier
reading was taken by a different request in a different gunicorn sibling.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import time
from datetime import UTC, datetime
from pathlib import Path

from app.framework.ports.system_stats import (
    CpuStats,
    GpuStats,
    MemoryStats,
    SystemStats,
)

_BYTES_PER_GB = 1024**3
_KB_PER_GB = 1024**2

# Positions on `/proc/stat`'s aggregate `cpu` line, after the label:
# user nice system idle iowait irq softirq steal guest guest_nice. The tail
# has grown over kernel releases and may grow again, so only these two are
# addressed by index and `iowait` is treated as optional.
_STAT_IDLE_INDEX = 3
_STAT_IOWAIT_INDEX = 4

# The window used when there is no usable earlier reading of `/proc/stat` —
# only the first call of a process, and any call that arrives so soon after
# another that the delta would be noise. Short enough that an admin page does
# not visibly stall on it, long enough to cover several scheduler ticks.
_COLD_SAMPLE_SECONDS = 0.15
# A baseline younger than this makes the division unstable (two requests
# landing together would divide by ~0); older than this and the "percent" is
# an average over minutes, which is not what a live gauge means. Outside the
# band the call falls back to its own cold sample.
_MIN_BASELINE_SECONDS = 0.05
_MAX_BASELINE_SECONDS = 60.0

# A wedged card makes `nvidia-smi` block indefinitely; the request must not.
_NVIDIA_SMI_TIMEOUT_SECONDS = 5.0
_NVIDIA_SMI_FIELDS = (
    "index",
    "name",
    "utilization.gpu",
    "utilization.memory",
    "memory.total",
    "memory.used",
    "temperature.gpu",
    "power.draw",
)

# Error text is rendered in an admin page, so it is trimmed to one short line:
# a driver's multi-paragraph complaint is a layout accident, not information.
_MAX_ERROR_CHARS = 200


def _short_error(exc: BaseException | str) -> str:
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    collapsed = " ".join(text.split())
    if len(collapsed) <= _MAX_ERROR_CHARS:
        return collapsed
    return collapsed[: _MAX_ERROR_CHARS - 1] + "…"


def _maybe_float(raw: str) -> float | None:
    """A driver field that may honestly not exist.

    ``nvidia-smi`` prints ``[N/A]`` or ``[Not Supported]`` for a sensor the
    card lacks — a datacentre card with no fan reports no temperature, and
    that is not a fault to surface as one.
    """
    try:
        return float(raw)
    except ValueError:
        return None


class HostSystemStats:
    """Structural ``SystemStatsSource`` (Protocol match, no inheritance — the
    ``SqlRedisMetricsSource`` precedent).

    ``proc_root``/``cgroup_root`` are injected only so the parsing can be
    exercised against fixture files in unit tests; production always builds
    this with the real kernel paths.
    """

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        hostname: str | None = None,
    ) -> None:
        self._proc = proc_root
        self._cgroup = cgroup_root
        self._hostname = hostname or socket.gethostname()
        # (monotonic timestamp, busy jiffies, total jiffies) of the most
        # recent `/proc/stat` reading — see the module docstring.
        self._cpu_baseline: tuple[float, float, float] | None = None

    async def read(self) -> SystemStats:
        cpu, cpu_error = await self._read_cpu()
        memory, memory_error = self._read_memory()
        gpus, gpu_error = await self._read_gpus()
        return SystemStats(
            host=self._hostname,
            sampled_at=datetime.now(UTC),
            cpu=cpu,
            cpu_error=cpu_error,
            memory=memory,
            memory_error=memory_error,
            gpus=gpus,
            gpu_error=gpu_error,
        )

    # ---------------------------------------------------------------- CPU

    def _cpu_counters(self) -> tuple[float, float]:
        """``(busy, total)`` jiffies from the aggregate ``cpu`` line.

        Idle *and* iowait are both excluded from busy: a core waiting on a
        disk is not doing work, and counting iowait as load is the classic way
        to report a storage stall as a CPU problem.
        """
        first_line = (self._proc / "stat").read_text(encoding="utf-8").split("\n", 1)[0]
        fields = first_line.split()
        if not fields or fields[0] != "cpu":
            raise ValueError("unexpected /proc/stat layout")
        values = [float(field) for field in fields[1:]]
        total = sum(values)
        idle = values[_STAT_IDLE_INDEX]
        if len(values) > _STAT_IOWAIT_INDEX:
            idle += values[_STAT_IOWAIT_INDEX]
        return total - idle, total

    async def _read_cpu(self) -> tuple[CpuStats | None, str | None]:
        try:
            now = time.monotonic()
            busy, total = self._cpu_counters()
            baseline = self._cpu_baseline
            self._cpu_baseline = (now, busy, total)
            if baseline is None or not (
                _MIN_BASELINE_SECONDS <= now - baseline[0] <= _MAX_BASELINE_SECONDS
            ):
                await asyncio.sleep(_COLD_SAMPLE_SECONDS)
                later = time.monotonic()
                busy_later, total_later = self._cpu_counters()
                self._cpu_baseline = (later, busy_later, total_later)
                baseline = (now, busy, total)
                now, busy, total = later, busy_later, total_later
            elapsed_ticks = total - baseline[2]
            # A counter that did not advance is not 0% and not 100%; there is
            # simply nothing to divide. It happens when two samples land inside
            # one kernel tick.
            usage = 0.0 if elapsed_ticks <= 0 else (busy - baseline[1]) / elapsed_ticks * 100.0
            return (
                CpuStats(
                    usage_percent=round(max(0.0, min(100.0, usage)), 1),
                    cores=os.cpu_count() or 0,
                    interval_seconds=round(now - baseline[0], 3),
                    load_average=self._load_average(),
                ),
                None,
            )
        except (OSError, ValueError, IndexError) as exc:
            return None, _short_error(exc)

    @staticmethod
    def _load_average() -> tuple[float, float, float] | None:
        try:
            one, five, fifteen = os.getloadavg()
        except OSError:
            return None
        return round(one, 2), round(five, 2), round(fifteen, 2)

    # ------------------------------------------------------------- memory

    def _meminfo(self) -> dict[str, float]:
        """``/proc/meminfo`` as ``{key: kibibytes}``."""
        entries: dict[str, float] = {}
        for line in (self._proc / "meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            fields = rest.split()
            if fields:
                entries[key] = float(fields[0])
        return entries

    def _read_memory(self) -> tuple[MemoryStats | None, str | None]:
        try:
            info = self._meminfo()
            total_kb = info["MemTotal"]
            if total_kb <= 0:
                raise ValueError("MemTotal is not positive")
            # `MemAvailable` is the kernel's own estimate of what a new
            # allocation could actually get, cache reclaim included. Deriving
            # it from MemFree instead is what makes naive monitors report a
            # healthy Linux box at 99% memory forever.
            available_kb = info.get("MemAvailable", info.get("MemFree", 0.0))
            cached_kb = info.get("Cached", 0.0) + info.get("Buffers", 0.0)
            cached_kb += info.get("SReclaimable", 0.0)
            used_kb = total_kb - available_kb
            return (
                MemoryStats(
                    total_gb=round(total_kb / _KB_PER_GB, 2),
                    used_gb=round(used_kb / _KB_PER_GB, 2),
                    available_gb=round(available_kb / _KB_PER_GB, 2),
                    cached_gb=round(cached_kb / _KB_PER_GB, 2),
                    used_percent=round(used_kb / total_kb * 100.0, 1),
                    limit_gb=self._cgroup_memory_limit_gb(total_kb),
                ),
                None,
            )
        except (OSError, ValueError, KeyError) as exc:
            return None, _short_error(exc)

    def _cgroup_memory_limit_gb(self, host_total_kb: float) -> float | None:
        """This process's memory ceiling, when a cgroup imposes one.

        Both cgroup generations are read because the platform runs under
        Compose locally and whatever the GPU host provides in production, and
        the two spell the same fact differently: v2 writes the literal ``max``
        for "no limit", v1 writes a number near 2^63. A limit at or above the
        machine's own memory is not a limit either way, and reporting it would
        just be the total again under a name that implies a cap.
        """
        for relative, unlimited in (("memory.max", "max"), ("memory/memory.limit_in_bytes", None)):
            try:
                raw = (self._cgroup / relative).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if unlimited is not None and raw == unlimited:
                return None
            try:
                limit_bytes = float(raw)
            except ValueError:
                continue
            if limit_bytes <= 0 or limit_bytes / 1024 >= host_total_kb:
                return None
            return round(limit_bytes / _BYTES_PER_GB, 2)
        return None

    # ---------------------------------------------------------------- GPU

    async def _read_gpus(self) -> tuple[tuple[GpuStats, ...], str | None]:
        binary = shutil.which("nvidia-smi")
        if binary is None:
            # Not an error state to be alarmed by on a CPU-only replica, but
            # it is still reported rather than shown as "no GPUs": on a host
            # that is supposed to have a card, a missing driver toolkit is
            # precisely the fault the operator opened this page to find.
            return (), "nvidia-smi is not available on this host"
        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                f"--query-gpu={','.join(_NVIDIA_SMI_FIELDS)}",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return (), _short_error(exc)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=_NVIDIA_SMI_TIMEOUT_SECONDS
            )
        except TimeoutError:
            process.kill()
            # Reap it, or the event loop logs an orphaned-child warning long
            # after the request that started it has answered.
            await process.wait()
            return (), f"nvidia-smi did not answer within {_NVIDIA_SMI_TIMEOUT_SECONDS:g}s"
        if process.returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip() or f"exit {process.returncode}"
            return (), _short_error(detail)
        try:
            return self._parse_nvidia_smi(stdout.decode("utf-8", "replace")), None
        except (ValueError, IndexError) as exc:
            return (), _short_error(exc)

    @staticmethod
    def _parse_nvidia_smi(output: str) -> tuple[GpuStats, ...]:
        """One card per line, fields in ``_NVIDIA_SMI_FIELDS`` order."""
        cards: list[GpuStats] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != len(_NVIDIA_SMI_FIELDS):
                raise ValueError("unexpected nvidia-smi column count")
            total_mib = float(parts[4])
            used_mib = float(parts[5])
            cards.append(
                GpuStats(
                    index=int(parts[0]),
                    name=parts[1],
                    utilization_percent=float(parts[2]),
                    memory_utilization_percent=float(parts[3]),
                    memory_total_gb=round(total_mib / 1024, 2),
                    memory_used_gb=round(used_mib / 1024, 2),
                    memory_used_percent=round(used_mib / total_mib * 100.0, 1)
                    if total_mib > 0
                    else 0.0,
                    temperature_celsius=_maybe_float(parts[6]),
                    power_watts=_maybe_float(parts[7]),
                )
            )
        return tuple(cards)
