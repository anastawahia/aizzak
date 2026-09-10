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

import re
import subprocess
import sys
from pathlib import Path

import yaml

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
_SUFFIX = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _to_bytes(raw: object) -> int:
    """Compose's own size grammar -- a bare integer is bytes, otherwise one of
    b/k/m/g with an optional trailing `b`. Shared with the script, which parses
    the same keys; `test_the_script_and_this_module_agree` is what keeps the
    two implementations from answering differently."""
    text = str(raw).strip().lower().removesuffix("b") or "0"
    if text[-1] in _SUFFIX:
        return int(float(text[:-1]) * _SUFFIX[text[-1]])
    return int(float(text))


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _limits(service: dict) -> dict:
    return ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}


def _classify() -> tuple[dict, dict, dict]:
    """standing / one-shot / profiled, by the same two keys the script reads."""
    standing, oneshot, profiled = {}, {}, {}
    for name, service in _compose()["services"].items():
        target = (
            profiled
            if service.get("profiles")
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
    return subprocess.run(
        ["bash", str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )


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
    assert len(standing) == 24, sorted(standing)
    assert len(oneshot) == 5, sorted(oneshot)
    assert len(profiled) == 3, sorted(profiled)
    assert _to_bytes("512m") == 512 * 1024**2
    assert _to_bytes("2g") == 2 * 1024**3
    assert _SCRIPT.exists() and _SCRIPT.stat().st_mode & 0o111, "the script is not executable"
    assert sys.version_info >= (3, 11)
