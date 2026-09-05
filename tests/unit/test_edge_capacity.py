"""The descriptor budget for §0's 1,500 concurrent WebSockets -- capacity step
3.1 (docs/capacity-plan.md §5 wave 3).

⭐ THE STEP NAMES THE WRONG FILE, AND THE MEASUREMENT IS WHY THIS MODULE EXISTS.
3.1 says `worker_connections 1024` at the edge is what stops 1,500 sockets.
Measured on the live stack, 1,500 upgraded WebSockets through the real nginx:
the edge carried every one of them at 309-341 descriptors per worker against a
1024 ceiling (3,210 total = 210 idle + two per proxied connection), and the
error log contained ZERO occurrences of `worker_connections are not enough`.

What refused was the app. One gunicorn worker sat at exactly **1024** file
descriptors -- Docker's inherited soft limit, which nothing in this repository
had ever set -- while its sibling held 946. At 2,400 offered sockets: 1,914
upgraded, 335 reset, 151 accepted and closed with no response. `ListenOverflows`
stayed at 0 the whole time, so it was not the accept queue; it was one process
against a number nobody wrote.

⭐ AND THE REFUSAL IS SILENT, which is what makes a guard the right answer
rather than a bigger number. No `Too many open files` line, no metric, no
`ListenDrops`. The client gets a TCP reset, the edge renders it 502, and the
application log says nothing at all -- so the failure this file prevents is not
"the platform is slow", it is "a third of the sockets vanish and every artefact
blames something else".

⭐ THE REPOSITORY HAD ALREADY DIAGNOSED THIS TWICE AND FIXED BOTH ENDS OF THE
WIRE EXCEPT THE MIDDLE. `qdrant` carries `ulimits: nofile: 65536` because the
seed tool died at the SEVENTH collection with `Too many open files (os error
24)`, and the k6 generator carries it with a comment that writes the mechanism
out in full: "`peak` holds 1,500 WebSockets open ... Docker's inherited soft
limit is 1024 -- the generator would shed load at roughly two thirds of the
target and the report would blame the platform." That sentence is equally true
of the platform receiving them, and of the edge proxying them, and neither had
been raised.

⚠️ ONE NUMBER, THREE PLACES, BECAUSE THE THREE PROCESSES RAISE IT DIFFERENTLY.
nginx raises its own with `worker_rlimit_nofile` (it starts as root, and the
hard limit is already 1,048,576), so the edge needs no Compose entry and one
line covers both publishers. gunicorn has no equivalent setting, so the app's
half is `ulimits:` in `docker-compose.yml` on the Compose path and `minfds` in
`supervisord.conf` on the RunPod path. Nothing compares the three, which is
exactly the shape `test_connection_budget.py` was written for one wave earlier.

⚠️ AND `worker_connections` IS A PROMISE, NOT A RESOURCE -- the trap this file's
first test exists to make unrepeatable. Measured on `nginx:1.27` with 16384
connections and no rlimit line:

    [warn] 1#1: 16384 worker_connections exceed open file resource limit: 1024

A warning, and nginx starts anyway. `nginx -t` does not print it at all, because
the check runs at worker init and not at config parse -- so a config promising
sixteen thousand connections against a thousand descriptors passes every syntax
gate this repository owns.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_EDGE = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_EDGE_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"
_RUNPOD_EDGE = _REPO_ROOT / "deploy" / "runpod" / "nginx.conf"
_RUNPOD_SUPERVISOR = _REPO_ROOT / "deploy" / "runpod" / "supervisord.conf"

# `docs/capacity-plan.md` §0, the binding target: 500 concurrent users holding
# up to 3 tabs each. Every floor below is DERIVED from this, never typed twice
# -- the day §0 moves, the failure should name the number that moved.
_WS_TARGET = 1500

# A proxied connection costs the edge TWO of everything: nginx allocates a
# connection structure and a descriptor for the client socket, and another pair
# for the upstream socket. Measured, not assumed -- 1,500 held sockets moved the
# edge's total worker descriptors from 210 to 3,210.
_EDGE_COST_PER_SOCKET = 2

# ⚠️ THE FLOOR IS THE WHOLE POPULATION IN ONE WORKER, and that is the point of
# the number rather than a safety margin. `worker_processes auto` makes the
# edge's aggregate ceiling a function of the HOST'S CORE COUNT: 1024 x 10 on the
# box this was measured on, 1024 x 2 on a small production VM -- a capacity that
# changes with the machine and is written down nowhere. Nothing distributes
# connections evenly either; the app's two gunicorn workers split 1,500 sockets
# 1005/552 under measurement. So the only floor that survives a machine change
# is one a SINGLE worker can meet alone.
_EDGE_FLOOR = _WS_TARGET * _EDGE_COST_PER_SOCKET
_APP_FLOOR = _WS_TARGET


def _directive(text: str, name: str) -> int | None:
    """The value of a bare `name <number>;` directive, ignoring comments.

    Matched at the start of a line on purpose, the same distinction
    `test_ops_slow_queries.py`'s tuning guard had to learn: every one of these
    knobs is DISCUSSED in the comments around it, and a substring search over
    the file would read a paragraph explaining why 1024 was wrong as though it
    were a directive setting 1024.
    """
    match = re.search(rf"^\s*{name}\s+(\d+)\s*;", text, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


def _nofile_soft(service: dict[str, object]) -> int | None:
    ulimits = service.get("ulimits")
    if not isinstance(ulimits, dict):
        return None
    nofile = ulimits.get("nofile")
    if isinstance(nofile, int):
        return nofile
    if isinstance(nofile, dict):
        soft = nofile.get("soft")
        return soft if isinstance(soft, int) else None
    return None


def _edge_files() -> list[tuple[str, str]]:
    """Both publishers' edges. `deploy/runpod/nginx.conf` has no `include`d
    sibling, so it repeats what `app-locations.conf` holds for Compose -- which
    is precisely why it is checked rather than trusted."""
    return [
        ("deploy/nginx/nginx.conf", _EDGE.read_text(encoding="utf-8")),
        ("deploy/runpod/nginx.conf", _RUNPOD_EDGE.read_text(encoding="utf-8")),
    ]


def test_worker_connections_never_promises_more_than_the_descriptor_budget() -> None:
    """The rule nginx itself only warns about, made a failing test.

    nginx compares `worker_connections` against RLIMIT_NOFILE at worker init and
    prints `[warn] N worker_connections exceed open file resource limit: M` --
    then starts anyway, and `nginx -t` never prints it at all. So the config can
    claim any number at all and pass every gate this repository has; the
    descriptor limit is what the kernel enforces, and it is the one that has to
    be at least as large."""
    for name, text in _edge_files():
        connections = _directive(text, "worker_connections")
        rlimit = _directive(text, "worker_rlimit_nofile")

        assert rlimit is not None, (
            f"{name} sets no `worker_rlimit_nofile`, so its workers inherit "
            "Docker's soft limit of 1024 descriptors no matter what "
            "`worker_connections` claims. That is the half of capacity 3.1 that "
            "actually buys capacity."
        )
        assert connections is not None, f"{name} sets no `worker_connections`."
        assert connections <= rlimit, (
            f"{name} promises {connections} worker_connections against a "
            f"{rlimit}-descriptor budget. nginx answers this with a [warn] and "
            "starts regardless -- the promise is not the resource."
        )


def test_one_edge_worker_alone_clears_the_whole_socket_population() -> None:
    """`worker_processes auto` is a multiplier nobody can read off the file.

    The old `worker_connections 1024` was never 1024: on the 10-core box this
    was measured on it meant 10,240 connection slots and carried §0's 1,500
    sockets comfortably. The SAME FILE on a 2-core VM means 2,048 slots ≈ 1,024
    proxied pairs -- under target, with nothing in the repository saying so. The
    only assertion that holds across machines is the one that ignores the
    multiplier."""
    for name, text in _edge_files():
        connections = _directive(text, "worker_connections")
        rlimit = _directive(text, "worker_rlimit_nofile")
        assert connections is not None and rlimit is not None

        assert connections >= _EDGE_FLOOR, (
            f"{name}: one worker must hold §0's {_WS_TARGET} sockets alone "
            f"({_EDGE_COST_PER_SOCKET} connection slots each = {_EDGE_FLOOR}), "
            f"because `worker_processes auto` guarantees nothing about how the "
            f"kernel splits them. Got {connections}."
        )
        assert rlimit >= _EDGE_FLOOR, (
            f"{name}: same floor in descriptors -- {_EDGE_FLOOR}, got {rlimit}."
        )


def test_the_app_declares_a_descriptor_limit_on_both_publishers() -> None:
    """The number the measurement actually found, and the reason it needs two
    homes: nginx raises its own limit and gunicorn cannot, so `app` depends on
    whatever launches it -- Compose on one path, supervisord on the other."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    app_soft = _nofile_soft(compose["services"]["app"])

    assert app_soft is not None, (
        "docker-compose.yml's `app` service declares no `ulimits: nofile:`, so "
        "every gunicorn worker inherits Docker's soft limit of 1024. Measured: "
        "one worker pinned at exactly 1024 descriptors while its sibling held "
        "946, refusing sockets with no log line and no metric."
    )
    assert app_soft >= _APP_FLOOR, (
        f"`app` may open {app_soft} descriptors; §0 asks one replica to carry "
        f"{_APP_FLOOR} sockets plus its connection pools."
    )

    supervisor = _RUNPOD_SUPERVISOR.read_text(encoding="utf-8")
    minfds = re.search(r"^minfds\s*=\s*(\d+)\s*$", supervisor, flags=re.MULTILINE)
    assert minfds is not None, (
        "deploy/runpod/supervisord.conf sets no `minfds`, and supervisord's "
        "default is 1024 -- the RunPod path would keep exactly the wall the "
        "Compose path just lost. 2.3's rule, unchanged: a budget that covers "
        "one publisher and not the other is not a budget."
    )
    assert int(minfds.group(1)) >= _APP_FLOOR


def test_the_two_edges_have_not_drifted() -> None:
    """`deploy/runpod/nginx.conf` cannot `include` `app-locations.conf`, so it
    carries its own copy of the same four numbers. Copies drift -- that is the
    whole argument for the `include` on the Compose side, and this is the only
    protection the side without one can have."""
    compose_edge = _EDGE.read_text(encoding="utf-8")
    compose_locations = _EDGE_LOCATIONS.read_text(encoding="utf-8")
    runpod = _RUNPOD_EDGE.read_text(encoding="utf-8")

    for knob in ("worker_connections", "worker_rlimit_nofile"):
        assert _directive(compose_edge, knob) == _directive(runpod, knob), (
            f"`{knob}` differs between the two edges."
        )

    def rate(text: str) -> str | None:
        match = re.search(r"zone=api_req:\d+m\s+rate=(\d+r/s)", text)
        return match.group(1) if match else None

    def burst(text: str) -> str | None:
        match = re.search(r"limit_req\s+zone=api_req\s+burst=(\d+)", text)
        return match.group(1) if match else None

    def ws_conn(text: str) -> str | None:
        match = re.search(r"limit_conn\s+ws_conn\s+(\d+)", text)
        return match.group(1) if match else None

    assert rate(compose_edge) == rate(runpod)
    assert burst(compose_locations) == burst(runpod)
    assert ws_conn(compose_locations) == ws_conn(runpod)


def test_the_ws_ceiling_clears_a_single_nated_office() -> None:
    """`limit_conn ws_conn` measured exactly, before the change: 150 upgrades
    offered from ONE address were answered 100 x 101 and 50 x 429. The limiter
    is not approximate and it was not wrong -- it was sized for a different
    claim than the one §0 makes, where 500 users behind one office NAT are one
    address holding up to three sockets each."""
    ws_conn = re.search(
        r"limit_conn\s+ws_conn\s+(\d+)", _EDGE_LOCATIONS.read_text(encoding="utf-8")
    )
    assert ws_conn is not None
    assert int(ws_conn.group(1)) * 3 >= _WS_TARGET, (
        "the per-address WebSocket ceiling cannot admit §0's population from a "
        "single NAT even at the 3-tabs-per-user §0 itself assumes."
    )


def test_the_descriptor_guards_can_actually_fail() -> None:
    """A guard that has only ever passed is a guard nobody has tested -- the
    pattern `test_ops_slow_queries.py` established for the wave-2 tuning gate.
    Both halves are exercised against the shapes they exist to reject: the
    config that raises the promise and forgets the resource, and the service
    that declares no limit at all."""
    forgot_the_resource = "events {\n    worker_connections  16384;\n}\n"
    assert _directive(forgot_the_resource, "worker_connections") == 16384
    assert _directive(forgot_the_resource, "worker_rlimit_nofile") is None

    # A knob NAMED in a comment is not a knob SET -- every one of these values
    # is discussed at length in the file that sets it.
    discussed_only = "# worker_rlimit_nofile 65535; would fix this\nevents {}\n"
    assert _directive(discussed_only, "worker_rlimit_nofile") is None

    assert _nofile_soft({"image": "x"}) is None
    assert _nofile_soft({"ulimits": {"nofile": {"soft": 65536, "hard": 1048576}}}) == 65536
