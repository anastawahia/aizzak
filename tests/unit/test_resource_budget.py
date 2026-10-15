"""Every container this stack can start, and the ceiling it may not pass --
capacity step 3.4 (``docs/capacity-plan.md`` §5 wave 3). The ledger this
computes is written out in prose in ``docs/design/08-local-runbook.md`` §2-ج,
and the last test here is what stops the two from drifting apart.

UNTIL THIS STEP NO SERVICE HAD A CEILING AT ALL. ``ح-8``'s own wording --
"``docker-compose.yml:610`` -- no ``deploy.replicas`` and no ``resources``" --
and the concrete shape 3.4 names for it: an indexing worker that opens a 50 MB
file can starve Postgres on the same host, and nothing anywhere says it may
not.

⭐ THE NUMBERS ARE NOT §6's, AND THE ARITHMETIC IS WHY. §6 of the capacity plan
is the table this step implements, and it does not add up:

    §6's fifteen rows sum to     34 vCPU / 67.75 GB
    §6's own total row claims   ~31 vCPU / ~59 GB
    §6's reference host is       32 vCPU / 64 GB

So the budget as written over-commits its own reference host by 2 vCPU and
3.75 GB, while the total row that would have revealed it understates the rows
by 3 vCPU and 8.75 GB. §6 also names fifteen services for a file that defines
TWENTY-NINE -- the two exporters, the two log services, the bridge, the WAL
shipper and every one-shot are simply absent from it. The ledger here keeps
§6's intent per service, reconciles it against what each container was measured
using, and fits: 31.50 vCPU and 50.50 GB, checked below against the reference
host rather than against whatever machine happens to run the test.

⚠️ A CPU LIMIT AND A MEMORY LIMIT ARE NOT THE SAME KIND OF PROMISE, and every
assertion here that treats them differently is doing so deliberately. ``cpus``
is a ceiling on a scheduler share: services capped at 2 on a 4-core host still
isolate each other, because no one of them can take more than its cap. Memory
is a kill threshold on a cgroup, and it isolates NOTHING once the sum passes
host RAM -- the OOM killer fires host-wide, by RSS, before any single cgroup
reaches its own limit. So the memory total is a hard bound and the CPU total is
an oversubscription ratio.

⚠️ AND DOCKER ACCEPTS A BUDGET FOR A HOST YOU DO NOT HAVE, IN SILENCE.
MEASURED, on the machine these numbers were taken on (10 vCPU, 12.67 GB):
``deploy.resources.limits: {cpus: "6.0", memory: 20G}`` produced
``HostConfig.Memory=21474836480`` and ``NanoCpus=6000000000`` with no warning,
no log line and no clamp. That is why ``deploy/resource-budget.sh`` exists and
why it is exercised here rather than described.

Same shape as ``test_connection_budget.py``: several files have to agree, the
document is recomputed rather than trusted, and every parser is checked for
matching something.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.unit.support_compose import (
    MODE_PROFILES,
    SIZES,
    compose_bytes,
    load_compose,
    read_size,
    size_compose,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_RUNBOOK = _REPO_ROOT / "docs" / "design" / "08-local-runbook.md"
_SCRIPT = _REPO_ROOT / "deploy" / "resource-budget.sh"

# `docs/capacity-plan.md` §6's reference host, which is what the standing
# budget is judged against. NEVER `nproc`/`MemTotal` of whatever runs the
# suite: a guard that passes on a big CI runner and fails on a small one is
# measuring the runner.
_REFERENCE_HOST_CPUS = 32.0
_REFERENCE_HOST_GB = 64.0

# Headroom the standing memory total must leave the host for the kernel, the
# Docker daemon, page cache and the one-shots that run before it. 10% of §6's
# reference host; the sum is the thing that has to fit, so the margin is
# stated rather than left to whoever next edits a limit.
_MEMORY_HEADROOM_GB = 6.4

_GB = 1024**3


_to_bytes = compose_bytes


def _compose() -> dict:
    return load_compose()


def _limits(service: dict) -> dict:
    return ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}


def _classify(
    compose: dict | None = None, profiles: frozenset[str] = frozenset()
) -> tuple[dict, dict, dict]:
    """standing / one-shot / profiled, by the same two keys the script reads.
    A profile in `profiles` is enabled, so its services are classified by `restart`."""
    standing, oneshot, profiled = {}, {}, {}
    for name, service in (compose or _compose())["services"].items():
        target = (
            profiled
            if set(service.get("profiles") or ()) - profiles
            else oneshot
            if service.get("restart") == "no"
            else standing
        )
        target[name] = service
    return standing, oneshot, profiled


def _totals(services: dict) -> tuple[float, float]:
    cpus = 0.0
    memory = 0
    for service in services.values():
        replicas = int((service.get("deploy") or {}).get("replicas", 1))
        limits = _limits(service)
        cpus += float(limits["cpus"]) * replicas
        memory += _to_bytes(limits["memory"]) * replicas
    return cpus, memory / _GB


# ------------------------------------------------------------- every service --


def test_every_service_declares_both_limits() -> None:
    """3.4's own wording: "**كلّ** خدمةٍ تنال `deploy.resources.limits`". An
    unbounded service is precisely the one that starves the others, so the
    guard is on the SET rather than on the numbers -- a service added next year
    fails here on the day it is written."""
    unbounded = sorted(
        name
        for name, service in _compose()["services"].items()
        if "cpus" not in _limits(service) or "memory" not in _limits(service)
    )
    assert not unbounded, (
        f"{unbounded} declare no `deploy.resources.limits`. Every service in this file "
        "gets a ceiling (capacity 3.4); the budget is docs/design/08-local-runbook.md §2-ج "
        "and `deploy/resource-budget.sh` recomputes it."
    )


def test_the_standing_budget_fits_the_reference_host() -> None:
    """The memory half, which is the half that is a real bound.

    A cgroup limit does not reserve anything. Once the declared maxima add up
    past host RAM the kernel's OOM killer -- which knows nothing about these
    numbers and picks by RSS -- fires first, and the isolation 3.4 exists to
    buy is simply absent while every limit still reads as set."""
    standing, _, _ = _classify()
    _, memory_gb = _totals(standing)

    assert memory_gb <= _REFERENCE_HOST_GB - _MEMORY_HEADROOM_GB, (
        f"the standing services may hold {memory_gb:.2f} GB at once against a reference "
        f"host of {_REFERENCE_HOST_GB:.0f} GB, leaving less than the "
        f"{_MEMORY_HEADROOM_GB:.1f} GB this ledger reserves for the kernel, the daemon and "
        "the one-shots. Memory limits isolate nothing once their sum passes host RAM."
    )


def test_the_cpu_total_is_a_ceiling_and_is_reported_as_one() -> None:
    """The CPU half, which is NOT a hard bound -- and this test says so by
    asserting the weaker property on purpose.

    Oversubscribing CPU caps is legitimate: a cap bounds one service's share,
    so twenty services capped at 2 on a 32-core host still cannot starve each
    other however loaded any one of them gets. What would be a defect is a cap
    so large that a single service could take the whole reference host, because
    then its cap is not a cap at all."""
    standing, _, _ = _classify()
    cpus, _ = _totals(standing)
    assert cpus > 0

    for name, service in standing.items():
        limit = float(_limits(service)["cpus"])
        assert limit < _REFERENCE_HOST_CPUS, (
            f"`{name}` may take all {_REFERENCE_HOST_CPUS:.0f} vCPU of the reference host; "
            "a cap that a service cannot reach the other side of is not isolating anything"
        )


def test_the_one_shots_really_do_finish_before_the_standing_stack() -> None:
    """The exclusion the totals rest on, checked rather than assumed -- the
    same argument, and the same shape of proof, as the connection ledger's
    `_PRE_FLIGHT_MODULES` (`test_the_pre_flight_migrator_really_does_finish_
    first`). A one-shot's memory cannot be concurrent with the steady state, so
    counting it would inflate the sum with a term that cannot happen; the day
    one of them becomes a standing service, that stops being free."""
    compose = _compose()
    _, oneshot, _ = _classify()
    assert oneshot, "the one-shot parser matched nothing"

    for name in sorted(oneshot):
        waited_on = [
            other
            for other, service in compose["services"].items()
            if (service.get("depends_on") or {}).get(name, {}).get("condition")
            == "service_completed_successfully"
        ]
        assert waited_on, (
            f"`{name}` is excluded from the standing budget because it completes first, "
            "but no service waits for it with `service_completed_successfully` any more. "
            "Either restore the edge or start counting its limits."
        )


# ------------------------------------------------- what replicas cost elsewhere --


def test_nothing_replicated_carries_a_container_name_or_a_published_port() -> None:
    """⚠️ Both are incompatible with `deploy.replicas`, and they fail in very
    different ways. MEASURED, on a two-replica probe:

        container_name:  "services.deploy.replicas: can't set container_name
                          and probe as container name must be unique"
                         -- refused at CONFIG PARSE. Loud, early, harmless.

        ports:           replica 1 Started; replica 2 "Bind for 0.0.0.0:39999
                          failed: port is already allocated"
                         -- HALF THE STACK CAME UP. One container serving, one
                            dead, and an error code on a command whose effect
                            was partial.

    The second is the one worth a guard: `app` is `expose`-only today and the
    edge reaches it by NAME through `resolver` (no `upstream` block -- the 7.1
    decision that 3.2 re-confirmed live), which is the entire reason 3.4 could
    add replicas without touching `deploy/nginx/`. A published port added here
    later would look like a convenience and would silently halve the fleet.
    """
    for name, service in _compose()["services"].items():
        replicas = int((service.get("deploy") or {}).get("replicas", 1))
        if replicas <= 1:
            continue
        assert "container_name" not in service, (
            f"`{name}` has {replicas} replicas and a container_name; Compose refuses this "
            "combination outright"
        )
        assert not service.get("ports"), (
            f"`{name}` has {replicas} replicas and publishes a host port. Replica 1 will "
            "bind it and every other replica will die with `port is already allocated` "
            "AFTER the first one started -- a half-started fleet, not a refusal."
        )


# ------------------------------------------------------------------ the script --


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    # Pinned to the file this module reads. The script takes Docker's own
    # `COMPOSE_FILE` variable as ONE path, and CI's integration job exports it
    # as the colon-separated pair `docker-compose.yml:docker-compose.test.yml`.
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "COMPOSE_FILE": str(_COMPOSE)},
        check=False,
    )


def _run_size(size: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), "--size", size, *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "COMPOSE_FILE": str(_COMPOSE), **env},
        check=False,
    )


def _size_totals(size: str) -> tuple[float, float, float]:
    parsed = read_size(size)
    compose = size_compose(parsed)
    standing, _, _ = _classify(compose, MODE_PROFILES[parsed.mode])
    cpus, memory_gb = _totals(standing)
    return cpus, memory_gb, parsed.host_memory_gb


def test_the_script_and_this_module_agree() -> None:
    """Two implementations of one sum, which is the point: the script is what
    an operator runs and this module is what CI runs, and a ledger that two
    readers disagree about is not a ledger."""
    standing, _, _ = _classify()
    cpus, memory_gb = _totals(standing)

    done = _run("--host-cpus", "32", "--host-memory-gb", "64")
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{cpus:6.2f} vCPU  {memory_gb:7.2f} GB" in done.stdout, done.stdout


def test_the_script_passes_on_the_reference_host_and_fails_on_a_small_one() -> None:
    """A check that cannot fail is not a check. Both directions, against hosts
    passed in rather than against the machine running the suite."""
    ok = _run("--host-cpus", "32", "--host-memory-gb", "64")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "MEMORY  OK" in ok.stdout

    small = _run("--host-cpus", "10", "--host-memory-gb", "12.67")
    assert small.returncode == 1, small.stdout
    assert "MEMORY  OVER" in small.stdout
    # ...and CPU oversubscription on that same host is REPORTED, not failed --
    # the asymmetry this whole module turns on.
    assert "CPU     OVERSUBSCRIBED" in small.stdout


def test_the_script_names_a_service_that_has_no_ceiling(tmp_path: Path) -> None:
    """The failure mode 3.4 exists to end, exercised on a throwaway file: a
    service with no limits must be NAMED, not silently skipped in the sum."""
    compose = _compose()
    del compose["services"]["qdrant"]["deploy"]["resources"]
    target = tmp_path / "docker-compose.yml"
    target.write_text(yaml.safe_dump(compose), encoding="utf-8")

    done = subprocess.run(
        ["bash", str(_SCRIPT), "--host-cpus", "32", "--host-memory-gb", "64"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={"PATH": "/usr/bin:/bin", "COMPOSE_FILE": str(target)},
        check=False,
    )
    assert done.returncode == 1, done.stdout
    assert "NO LIMIT  qdrant" in done.stdout, done.stdout


# -------------------------------------------------------------- the written ledger --


def test_the_runbook_ledger_carries_the_numbers_this_module_computes() -> None:
    """§2-ج states the budget as prose an operator reads before sizing a host.
    A document whose numbers no longer match the file it describes is worse
    than none -- it reads as authoritative."""
    standing, _, _ = _classify()
    cpus, memory_gb = _totals(standing)
    text = _RUNBOOK.read_text(encoding="utf-8")

    for label, value in (
        ("resources.standing_cpus", f"{cpus:.2f}"),
        ("resources.standing_memory_gb", f"{memory_gb:.2f}"),
        ("resources.reference_host_cpus", f"{_REFERENCE_HOST_CPUS:.0f}"),
        ("resources.reference_host_gb", f"{_REFERENCE_HOST_GB:.0f}"),
    ):
        # `\s*=\s*` and not a literal " = ": the block is column-aligned, the
        # same reason `test_connection_budget._LEDGER_LINE` is a regex. A
        # substring search over a whole page is also how a guard passes for the
        # wrong reason, so the label is anchored to a line start.
        assert re.search(rf"^{re.escape(label)}\s*=\s*{re.escape(value)}\b", text, re.M), (
            f"docs/design/08-local-runbook.md's resource ledger no longer says "
            f"`{label} = {value}`; recompute it with deploy/resource-budget.sh"
        )


def test_the_patterns_actually_find_something() -> None:
    """Every parser here can match nothing and leave a green sum of zero --
    the `test_deploy_worker_default.py` precedent, applied to this module's."""
    standing, oneshot, profiled = _classify()
    # 22 until capacity 5.2, which split `redis` into `redis-stream` +
    # `redis-cache` and `redis-exporter` into one per instance -- two rows
    # where there were two, and the standing budget moved 34.00/56.38 ->
    # 34.25/56.50 (the Redis pair DIVIDED the old 2.0 vCPU / 5 GB row rather
    # than doubling it; the extra 0.25 vCPU / 128 MB is the second exporter).
    # 25 since capacity 5.7: `ops-scheduler` is standing on purpose (a
    # nightly job that waits for someone to start its container is the gap
    # 5.7 closes), at the `backup` service's own 1.0 vCPU / 1 GB -> 35.25/57.50,
    # a tenth of a GB under the 57.60 this ledger allows.
    # 27 since capacity 7.3: `alertmanager` (0.25 vCPU / 64 MB) and
    # `alert-sink` (0.1 / 32 MB) -> 35.60/57.59, sized to fit the last tenth.
    # 29 since monitoring-host-postgres: node-exporter (0.25/64m) +
    # postgres-exporter (0.25/128m), paid for by redis-cache 2g -> 1792m (its
    # 15-day peak is 21 MiB under a 1 GiB maxmemory) -> 36.10/57.53, 70 MiB
    # under the ceiling.
    # profiled 3 -> 5 since gpu-compose 2.2: `ollama` (4 vCPU / 4g, an estimate)
    # and `ollama-pull` (0.25 / 64m) sit behind profile `local-llm`, so a default
    # `up -d` starts neither and the standing ledger does not move (36.10/57.53).
    # 28 since gpu-compose 2.3: `ollama-bridge` went behind profile `wsl-ollama`
    # (0.5 vCPU / 256m) -> 35.60/57.28; profiled 3 -> 6 with `ollama` and
    # `ollama-pull` (2.2) and the bridge. The owner's machine still runs the
    # bridge (COMPOSE_PROFILES=wsl-ollama in its .env), but a server without WSL
    # does not, so the REFERENCE host's ledger no longer counts it.
    assert len(standing) == 28, sorted(standing)
    assert len(oneshot) == 5, sorted(oneshot)
    assert len(profiled) == 6, sorted(profiled)
    assert _to_bytes("512m") == 512 * 1024**2
    assert _to_bytes("2g") == 2 * 1024**3
    assert _SCRIPT.exists() and _SCRIPT.stat().st_mode & 0o111, "the script is not executable"
    assert sys.version_info >= (3, 11)


# ------------------------------------------------------------------ sizes --


@pytest.mark.parametrize("size", SIZES)
def test_the_script_and_this_module_agree_per_size(size: str) -> None:
    cpus, memory_gb, _ = _size_totals(size)
    parsed = read_size(size)
    done = _run_size(
        size, "--host-cpus", str(parsed.host_cpus), "--host-memory-gb", str(parsed.host_memory_gb)
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{cpus:6.2f} vCPU  {memory_gb:7.2f} GB" in done.stdout, done.stdout
    assert f"size {size} (" in done.stdout and f"mode {parsed.mode}" in done.stdout


def test_the_script_refuses_an_unknown_or_missing_size() -> None:
    unknown = _run_size("huge")
    assert unknown.returncode == 2
    assert "unknown size 'huge'; available: large medium small" in unknown.stderr
    for args in (["--size"], ["--size", "--host-cpus", "1"]):
        done = subprocess.run(
            ["bash", str(_SCRIPT), *args],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            check=False,
        )
        assert done.returncode == 2, args


@pytest.mark.parametrize("size", SIZES)
def test_every_size_fits_the_host_its_header_names(size: str) -> None:
    assert pytest.approx(_REFERENCE_HOST_GB * 0.10) == _MEMORY_HEADROOM_GB
    _, memory_gb, host_gb = _size_totals(size)
    assert memory_gb <= host_gb * 0.9, f"{size}: {memory_gb:.2f} GB over {host_gb * 0.9:.2f}"


def test_a_size_over_its_host_fails_naming_it(tmp_path: Path) -> None:
    source = read_size("medium")
    _, memory_gb, host_gb = _size_totals("medium")
    allowed = host_gb * 0.9
    qdrant_gb = compose_bytes(source.values["QDRANT_MEM"]) / _GB
    # raise qdrant until the total is `allowed` + 0.1 GB (in whole MB so Compose parses it)
    new_mb = round((qdrant_gb + (allowed - memory_gb) + 0.1) * 1024)
    text = source.path.read_text(encoding="utf-8").replace(
        f"QDRANT_MEM={source.values['QDRANT_MEM']}", f"QDRANT_MEM={new_mb}m"
    )
    (tmp_path / "medium.env").write_text(text, encoding="utf-8")

    over = read_size(tmp_path / "medium.env")
    compose = size_compose(over)
    standing, _, _ = _classify(compose, MODE_PROFILES[over.mode])
    assert _totals(standing)[1] > allowed

    done = _run_size(
        "medium",
        "--host-cpus",
        str(source.host_cpus),
        "--host-memory-gb",
        str(source.host_memory_gb),
        SIZES_DIR=str(tmp_path),
    )
    assert done.returncode == 1, done.stdout
    assert "size medium" in done.stdout and "0.10 GB over" in done.stdout


def test_the_runbook_size_table_carries_the_numbers_this_module_computes() -> None:
    text = _RUNBOOK.read_text(encoding="utf-8")
    for size in SIZES:
        cpus, memory_gb, host_gb = _size_totals(size)
        host = read_size(size)
        row = (
            rf"^\|\s*`{size}`\s*\|\s*{host.mode}\s*\|\s*{host.host_cpus:.0f} / "
            rf"{host.host_memory_gb:.0f}\s*\|\s*{cpus:.2f}\s*\|\s*{memory_gb:.2f}\s*\|\s*"
            rf"{host_gb * 0.9:.2f}\s*\|"
        )
        assert re.search(row, text, re.M), f"08 §2-ز size table has no correct row for {size}"
