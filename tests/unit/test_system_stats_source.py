"""``HostSystemStats`` — the ``BE-ADM-007`` host telemetry adapter.

The parsing runs against fixture ``/proc`` and ``/sys/fs/cgroup`` trees rather
than this machine's, so the assertions can be about exact numbers instead of
"is between 0 and 100" — which is all a live kernel would let anyone claim.
The GPU half is exercised by putting a shell script named ``nvidia-smi`` on
``PATH``: the adapter's contract with the driver is a command line and its
stdout, and a fake that replaces the parsing method would test neither.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from app.infrastructure.monitoring.system_stats import HostSystemStats

# One `cpu` line, then the per-core lines the adapter must ignore. Fields:
# user nice system idle iowait irq softirq steal guest guest_nice.
_STAT_FIRST = "cpu  1000 0 500 8000 500 0 0 0 0 0\ncpu0 500 0 250 4000 250 0 0 0 0 0\n"
# 200 busy jiffies later (100 user + 100 system) and 800 total.
_STAT_SECOND = "cpu  1100 0 600 8500 600 0 0 0 0 0\ncpu0 550 0 300 4250 300 0 0 0 0 0\n"

_MEMINFO = "\n".join(
    (
        "MemTotal:       16777216 kB",
        "MemFree:         1048576 kB",
        "MemAvailable:    8388608 kB",
        "Buffers:          524288 kB",
        "Cached:          6291456 kB",
        "SReclaimable:     262144 kB",
        "SwapTotal:       2097152 kB",
    )
)


def _proc(tmp_path: Path, stat_text: str = _STAT_FIRST, meminfo: str = _MEMINFO) -> Path:
    root = tmp_path / "proc"
    root.mkdir(exist_ok=True)
    (root / "stat").write_text(stat_text, encoding="utf-8")
    (root / "meminfo").write_text(meminfo, encoding="utf-8")
    return root


def _fake_binary(tmp_path: Path, name: str, body: str) -> Path:
    """Put an executable `name` on a fresh directory and return it.

    Written in Python with this interpreter's own shebang rather than `sh`:
    the tests that follow point PATH at this directory, and a shell script
    would then be unable to find the coreutils it wants.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(f"#!{__import__('sys').executable}\n{body}", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bin_dir


def _fake_nvidia_smi(tmp_path: Path, stdout: str, *, exit_code: int = 0, stderr: str = "") -> Path:
    """A `nvidia-smi` on PATH that prints what the test wants it to."""
    return _fake_binary(
        tmp_path,
        "nvidia-smi",
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n",
    )


def test_the_first_reading_takes_its_own_window_because_it_has_nothing_to_subtract_from(
    tmp_path: Path,
) -> None:
    proc = _proc(tmp_path)
    source = HostSystemStats(proc_root=proc, cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.cpu_error is None
    assert stats.cpu is not None
    # The fixture does not move between the two samples, so a correct
    # implementation reports an idle host rather than dividing by zero.
    assert stats.cpu.usage_percent == 0.0
    assert stats.cpu.interval_seconds > 0.0


def test_a_later_reading_measures_against_the_previous_one(tmp_path: Path) -> None:
    proc = _proc(tmp_path)
    source = HostSystemStats(proc_root=proc, cgroup_root=tmp_path / "absent")
    asyncio.run(source.read())

    # 200 of the 800 jiffies that elapsed were busy.
    (proc / "stat").write_text(_STAT_SECOND, encoding="utf-8")
    stats = asyncio.run(source.read())

    assert stats.cpu is not None
    assert stats.cpu.usage_percent == 25.0


def test_iowait_is_not_counted_as_busy(tmp_path: Path) -> None:
    """A core waiting on a disk is not working; counting it as load is how a
    storage stall gets reported as a CPU problem."""
    proc = _proc(tmp_path)
    source = HostSystemStats(proc_root=proc, cgroup_root=tmp_path / "absent")
    asyncio.run(source.read())

    # 500 jiffies elapse and every one of them is iowait.
    (proc / "stat").write_text("cpu  1000 0 500 8000 1000 0 0 0 0 0\n", encoding="utf-8")
    stats = asyncio.run(source.read())

    assert stats.cpu is not None
    assert stats.cpu.usage_percent == 0.0


def test_reclaimable_cache_is_not_reported_as_used_memory(tmp_path: Path) -> None:
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.memory_error is None
    assert stats.memory is not None
    # 16 GiB total, 8 GiB available ⇒ 8 GiB used and exactly half, even though
    # only 1 GiB is strictly free. Deriving from MemFree would say 93.75%.
    assert stats.memory.total_gb == 16.0
    assert stats.memory.used_gb == 8.0
    assert stats.memory.used_percent == 50.0
    assert stats.memory.available_gb == 8.0
    # Buffers + Cached + SReclaimable, `free(1)`'s own buff/cache.
    assert stats.memory.cached_gb == 6.75


def test_a_cgroup_limit_below_the_machines_memory_is_reported(tmp_path: Path) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / "memory.max").write_text("2147483648\n", encoding="utf-8")
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=cgroup)

    stats = asyncio.run(source.read())

    assert stats.memory is not None
    assert stats.memory.limit_gb == 2.0
    # The rest still describes the machine — the only scope the GPU half can
    # have — so the two are never silently mixed.
    assert stats.memory.total_gb == 16.0


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        # cgroup v2 spells "no limit" as a word...
        ("memory.max", "max\n"),
        # ...and a limit nobody will reach is not a limit either.
        ("memory.max", str(2**62)),
    ],
)
def test_an_absent_ceiling_is_reported_as_none_not_as_the_total_again(
    tmp_path: Path, filename: str, content: str
) -> None:
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    (cgroup / filename).write_text(content, encoding="utf-8")
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=cgroup)

    stats = asyncio.run(source.read())

    assert stats.memory is not None
    assert stats.memory.limit_gb is None


def test_a_missing_proc_degrades_that_section_alone(tmp_path: Path) -> None:
    """The whole point of the port being total: memory failing must not take
    the CPU reading, the host name or the response with it."""
    proc = _proc(tmp_path)
    (proc / "meminfo").unlink()
    source = HostSystemStats(proc_root=proc, cgroup_root=tmp_path / "absent", hostname="gpu-1")

    stats = asyncio.run(source.read())

    assert stats.memory is None
    assert stats.memory_error is not None
    assert stats.cpu is not None
    assert stats.host == "gpu-1"


def test_every_card_is_reported_separately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = _fake_nvidia_smi(
        tmp_path,
        "0, NVIDIA A100-SXM4-40GB, 97, 45, 40960, 38912, 71, 312.5\n"
        "1, NVIDIA A100-SXM4-40GB, 0, 0, 40960, 512, 33, 58.1\n",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.gpu_error is None
    assert [gpu.index for gpu in stats.gpus] == [0, 1]
    busy, idle = stats.gpus
    assert busy.name == "NVIDIA A100-SXM4-40GB"
    assert busy.utilization_percent == 97.0
    assert busy.memory_used_gb == 38.0
    assert busy.memory_used_percent == 95.0
    assert busy.temperature_celsius == 71.0
    assert busy.power_watts == 312.5
    # The reason cards are never averaged: this pair would read as one card at
    # ~48% utilisation, which describes neither of them.
    assert idle.utilization_percent == 0.0


def test_a_sensor_the_card_does_not_have_is_none_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _fake_nvidia_smi(
        tmp_path, "0, NVIDIA H100 PCIe, 12, 3, 81559, 1024, [N/A], [Not Supported]\n"
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.gpu_error is None
    assert len(stats.gpus) == 1
    assert stats.gpus[0].temperature_celsius is None
    assert stats.gpus[0].power_watts is None
    assert stats.gpus[0].utilization_percent == 12.0


def test_a_driver_that_answers_with_no_cards_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gpus == ()` with no error is a CPU-only host, and the port promises
    that is distinguishable from nothing having answered."""
    monkeypatch.setenv("PATH", str(_fake_nvidia_smi(tmp_path, "")))
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.gpus == ()
    assert stats.gpu_error is None


def test_a_failing_driver_reports_its_reason_and_leaves_the_rest_standing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = _fake_nvidia_smi(
        tmp_path,
        "",
        exit_code=9,
        stderr="NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver.",
    )
    monkeypatch.setenv("PATH", str(bin_dir))
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.gpus == ()
    assert stats.gpu_error is not None
    assert "NVIDIA driver" in stats.gpu_error
    assert stats.cpu is not None
    assert stats.memory is not None


def test_a_host_without_the_driver_toolkit_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.gpus == ()
    assert stats.gpu_error == "nvidia-smi is not available on this host"


def test_a_hung_driver_is_given_up_on_rather_than_waited_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wedged card blocks `nvidia-smi` forever; the request must not."""
    bin_dir = _fake_binary(tmp_path, "nvidia-smi", "import time\ntime.sleep(30)\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(
        "app.infrastructure.monitoring.system_stats._NVIDIA_SMI_TIMEOUT_SECONDS", 0.2
    )
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.gpus == ()
    assert stats.gpu_error is not None
    assert "did not answer" in stats.gpu_error


def test_the_reading_names_the_machine_that_took_it(tmp_path: Path) -> None:
    """Every other route answers the same from any replica; this one cannot,
    so an operator reading 94% must be able to tell which box that is."""
    source = HostSystemStats(proc_root=_proc(tmp_path), cgroup_root=tmp_path / "absent")

    stats = asyncio.run(source.read())

    assert stats.host
    assert stats.host == os.uname().nodename
