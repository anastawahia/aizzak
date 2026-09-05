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

────────────────────────────────────────────────────────────────────────────
CAPACITY 3.2 ADDED THE SECOND HALF: THE EDGE IS A CLIENT TOO.

3.1 counted the descriptors nginx needs to ACCEPT §0's sockets. 3.2 counted the
source ports it needs to OPEN them, and found the same shape one layer over.
`app-locations.conf` proxies to a variable so the name is re-resolved per
request, which means no `upstream` block, which means no keepalive pool at all:
every proxied request is a fresh TCP connection from an ephemeral port that is
then held sixty seconds in TIME_WAIT. Measured at §0's 300 rps through the real
edge: 15,782 TIME_WAIT sockets and ZERO established -- 55.9% of Docker's default
28,232-port range spent to serve the target rate, implying a ceiling near 537
rps. It was then reached deliberately at 700 rps, and the edge answered

    [crit] connect() to ...:8000 failed (99: Cannot assign requested address)

4,090 times -- 5.84% of 70,001 requests -- with 94.1% of the range in TIME_WAIT.

⭐ AND AGAIN THE REPOSITORY HAD ALREADY FIXED IT ON THE OTHER SIDE OF THE WIRE.
The `k6` service carries `net.ipv4.ip_local_port_range` with a comment naming
this exact error from a real run ("the generator running out of SOURCE PORTS in
its own network namespace"). The thing that measures the platform was hardened;
the thing measured, which opens one connection per REQUEST rather than one per
iteration, was not.

⚠️ AND THE PLAN'S OWN REMEDY POINTED AT THE WRONG MACHINE. 3.2 option (أ) and
step 3.5 put `ip_local_port_range`/`tcp_tw_reuse` in `deploy/host-tuning.sh`.
Both are NETWORK-NAMESPACED: the host's range on the measured box is 4,096
ports, six times narrower than the container's default, and setting it there
changes nothing inside while reading like a fix. Same lesson as 3.1's `nofile`
-- the number belongs where the process lives.

⚠️ THE THIRD FINDING WAS NOT ABOUT PORTS AT ALL. `proxy_set_header` obeys the
same array-inheritance rule this repository documented at length for
`add_header`: a level that declares one replaces the whole inherited set. Both
publishers' `/api/v1/ws` locations declared two (Upgrade, Connection), so every
WebSocket handshake reached the app with no X-Real-IP, no X-Forwarded-For, no
X-Forwarded-Proto, no X-Correlation-Id, no X-Request-Id and `Host` defaulted to
`$proxy_host`. Measured against the repository's own files, unmodified. That
defeats capacity 0.6 precisely where 0.6 is worth most -- the edge line and the
app line for the same 1,500-socket population could not be joined -- and
`test_the_edge_passes_both_ids_upstream` passed the whole time, because it
asserts the DIRECTIVE IS IN THE FILE and nginx applying it is a different
claim.
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

# §0's other target, and the one 3.2 is about.
_RPS_TARGET = 300

# Linux holds a closed socket in TIME_WAIT for `TCP_TIMEWAIT_LEN`, a
# COMPILE-TIME constant of 60 seconds. MEASURED rather than looked up, because
# the `k6` service's own comment implies otherwise: one client-closed socket
# watched out of /proc/net/tcp lived 60.8s at `tcp_fin_timeout=5` and 60.7s at
# `tcp_fin_timeout=60`. No sysctl shortens it -- `tcp_fin_timeout` bounds
# FIN_WAIT_2 -- so the port range is the only knob that buys headroom outright.
_TIME_WAIT_S = 60

# One proxied request = one source port, held for the interval above. With no
# keepalive pool that is the edge's steady-state port bill at the target rate,
# and it is the floor the range has to clear on its own. `tcp_tw_reuse` raises
# the ceiling far past this by recycling sockets older than a second, but it is
# the SECOND line: a budget that only works because the kernel is recycling
# under pressure is a budget nobody has sized.
_SOURCE_PORT_FLOOR = _RPS_TARGET * _TIME_WAIT_S

# gunicorn's default when no `--keep-alive` is passed, which is the situation on
# both publishers until capacity 3.3. An upstream keepalive pool that holds a
# socket LONGER than the app will is a 502 waiting for a request nginx is not
# allowed to retry.
_GUNICORN_DEFAULT_KEEPALIVE_S = 2


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


def _sysctl(service: dict[str, object], key: str) -> str | None:
    sysctls = service.get("sysctls")
    if not isinstance(sysctls, dict):
        return None
    value = sysctls.get(key)
    return str(value) if value is not None else None


def _seconds(value: str) -> float:
    """nginx's time suffixes, for the two directives this module reads."""
    units = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    for suffix, scale in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * scale
    return float(value)


def _proxy_headers(text: str) -> set[str]:
    """The header NAMES a chunk of nginx config sets, lower-cased.

    Names, not values: the two publishers legitimately differ on
    `X-Forwarded-Proto` ($scheme behind Compose's two server blocks, pinned
    `https` behind RunPod's single one, which sits behind RunPod's own TLS
    proxy). What may never differ is WHICH headers are sent."""
    return {
        m.group(1).lower()
        for m in re.finditer(r"^\s*proxy_set_header\s+([A-Za-z0-9-]+)", text, flags=re.MULTILINE)
    }


def _ws_location(text: str) -> str:
    """The body of `location /api/v1/ws { ... }`, brace-matched.

    Brace-matched rather than regex-bounded because the block contains an
    `if`-less but comment-heavy body, and a lazy `.*?}` would stop at the first
    closing brace inside a comment."""
    start = text.index("location /api/v1/ws")
    depth = 0
    for i in range(text.index("{", start), len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("unterminated `location /api/v1/ws` block")


def _gunicorn_keepalive_s() -> float:
    """What the app will actually hold an idle upstream socket for.

    Read from the command lines rather than assumed, so that the day capacity
    3.3 adds `--keep-alive 65` this guard loosens itself instead of having to
    be edited."""
    sources = [
        (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"),
        _RUNPOD_SUPERVISOR.read_text(encoding="utf-8"),
        (_REPO_ROOT / "deploy" / "gunicorn.conf.py").read_text(encoding="utf-8"),
    ]
    found = [
        float(m.group(1))
        for text in sources
        for m in re.finditer(r"--keep-alive[= ](\d+)|^keepalive\s*=\s*(\d+)", text)
        if m.group(1)
    ]
    return min(found) if found else float(_GUNICORN_DEFAULT_KEEPALIVE_S)


def test_the_edge_declares_a_source_port_budget() -> None:
    """The half of the edge nobody had counted: nginx as a CLIENT.

    With no upstream keepalive pool -- and `app-locations.conf` gives up the
    pool on purpose, to keep per-request DNS resolution -- every proxied request
    burns one ephemeral port for sixty seconds. Measured at 300 rps: 15,782 of
    Docker's default 28,232 in TIME_WAIT, and at 700 rps the edge answered
    `(99: Cannot assign requested address)` 4,090 times. Both settings are
    net-namespaced, so `deploy/host-tuning.sh` cannot reach them."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    nginx = compose["services"]["nginx"]

    port_range = _sysctl(nginx, "net.ipv4.ip_local_port_range")
    assert port_range is not None, (
        "docker-compose.yml's `nginx` service declares no "
        "`net.ipv4.ip_local_port_range`, so it inherits Docker's 32768-60999. "
        "The `k6` service already carries this setting for the SAME failure "
        "seen from the other end of the wire."
    )
    low, high = (int(part) for part in port_range.split())
    assert high - low + 1 >= _SOURCE_PORT_FLOOR, (
        f"{high - low + 1} source ports against a floor of {_SOURCE_PORT_FLOOR} "
        f"({_RPS_TARGET} rps x {_TIME_WAIT_S}s of TIME_WAIT). Linux fixes the "
        "TIME_WAIT interval at 60s in `TCP_TIMEWAIT_LEN`; no sysctl shortens it."
    )

    reuse = _sysctl(nginx, "net.ipv4.tcp_tw_reuse")
    assert reuse == "1", (
        f"`net.ipv4.tcp_tw_reuse` is {reuse!r}. The kernel's default of 2 means "
        "'loopback only', which is why `deploy/runpod/nginx.conf` -- proxying "
        "to a literal 127.0.0.1:8000 -- was never exposed and the Compose "
        "bridge always was. MEASURED, paced 300 conn/s into a 1,000-port range: "
        "tw_reuse=2 to a bridge peer connected 999 of 6,000; tw_reuse=1 "
        "connected 6,000 of 6,000."
    )


def test_only_the_publisher_with_nothing_to_resolve_pools_upstream() -> None:
    """An anti-regression, because `keepalive` is the obvious thing to add here
    and it is a 502 on one of the two paths.

    RE-CONFIRMED LIVE in 3.2 rather than inherited from 7.1: with the app forced
    onto a new address and no edge restarted, the variable edge answered 200 and
    an otherwise identical `upstream`-block edge answered 502 permanently. The
    trap is that a plain `docker compose up -d --force-recreate app` left the
    address unchanged and BOTH answered 200 -- so the failure passes every test
    this repository runs and waits for a deploy nobody is watching."""
    compose_edge = _EDGE.read_text(encoding="utf-8")
    compose_locations = _EDGE_LOCATIONS.read_text(encoding="utf-8")
    for name, text in (
        ("deploy/nginx/nginx.conf", compose_edge),
        ("deploy/nginx/app-locations.conf", compose_locations),
    ):
        assert not re.search(r"^\s*upstream\s+\w+\s*\{", text, flags=re.MULTILINE), (
            f"{name} declares an `upstream` block. nginx resolves its server "
            "ONCE at config load and caches it for the process lifetime; on "
            "Compose the app's address moves and every request 502s until nginx "
            "is restarted, while `docker compose ps` reports it healthy. The "
            "keepalive this buys is paid for in `sysctls:` instead."
        )
    assert "$aizzak_app" in compose_locations, (
        "the variable destination is what forces per-request resolution; "
        "without it the `resolver` in nginx.conf has nothing to do."
    )


def test_the_runpod_pool_never_outlives_the_app_that_feeds_it() -> None:
    """RunPod CAN pool -- it proxies to a literal loopback address, so there is
    no name to go stale -- and the pool has exactly one way to hurt: holding a
    socket the app has already closed. nginx does not replay a POST by default,
    so that lands as a 502 on a request nobody can retry.

    The bound is read from the gunicorn command lines, not hard-coded, so
    capacity 3.3's `--keep-alive 65` loosens this guard by itself."""
    runpod = _RUNPOD_EDGE.read_text(encoding="utf-8")
    pool = re.search(r"^\s*keepalive\s+(\d+)\s*;", runpod, flags=re.MULTILINE)
    if pool is None:
        return

    idle = re.search(r"^\s*keepalive_timeout\s+(\S+)\s*;", runpod, flags=re.MULTILINE)
    assert idle is not None, (
        "deploy/runpod/nginx.conf pools upstream connections but sets no "
        "`keepalive_timeout`, so it keeps them for nginx's 60s default while "
        "gunicorn drops them after its own `--keep-alive`."
    )
    app_side = _gunicorn_keepalive_s()
    assert _seconds(idle.group(1)) < app_side, (
        f"the pool holds an idle socket for {idle.group(1)} while the app closes "
        f"it after {app_side}s. Raise gunicorn's `--keep-alive` (capacity 3.3) "
        "in the same change, never this alone."
    )
    assert 'proxy_set_header Connection       "";' in runpod, (
        "`keepalive` without an empty `Connection` header pools nothing at all "
        "-- measured: 0 established upstream sockets with `close` left in place."
    )


def test_the_websocket_handshake_still_carries_the_shared_proxy_headers() -> None:
    """The `add_header` trap, one directive over, found live in 3.2.

    `proxy_set_header` is an inherited ARRAY: a level that declares one replaces
    all of it. Both `/api/v1/ws` locations declared Upgrade and Connection, and
    therefore sent NONE of the six the server level declares. Measured against
    these files unmodified: `host=app:8000 | xff= | corr=` on the WebSocket
    path against `host=aizzak.test | xff=127.0.0.1 | corr=<minted>` on `/`.

    Asserted as a SUPERSET of whatever the shared level carries, so a seventh
    shared header added later cannot quietly skip this location."""
    for name, shared_text, whole_text in (
        (
            "deploy/nginx/app-locations.conf",
            _EDGE_LOCATIONS.read_text(encoding="utf-8"),
            _EDGE_LOCATIONS.read_text(encoding="utf-8"),
        ),
        (
            "deploy/runpod/nginx.conf",
            _RUNPOD_EDGE.read_text(encoding="utf-8"),
            _RUNPOD_EDGE.read_text(encoding="utf-8"),
        ),
    ):
        ws = _ws_location(whole_text)
        shared = _proxy_headers(shared_text.replace(ws, ""))
        assert shared, f"{name}: found no shared `proxy_set_header` directives."
        missing = shared - _proxy_headers(ws)
        assert not missing, (
            f"{name}: `location /api/v1/ws` declares `proxy_set_header` of its "
            f"own and therefore inherits NOTHING, so it drops {sorted(missing)}. "
            "nginx offers no way to add to an inherited set from a nested level "
            "-- the list has to be repeated in full."
        )
        assert "upgrade" in _proxy_headers(ws)


def test_the_websocket_header_set_is_the_same_on_both_publishers() -> None:
    """Names only. The two legitimately differ on `X-Forwarded-Proto`'s VALUE
    ($scheme behind Compose's two server blocks, pinned `https` behind RunPod's
    single one), and on `Connection` at the shared level, which is half of
    RunPod's keepalive pool and measurably wrong on Compose."""
    compose_ws = _proxy_headers(_ws_location(_EDGE_LOCATIONS.read_text(encoding="utf-8")))
    runpod_ws = _proxy_headers(_ws_location(_RUNPOD_EDGE.read_text(encoding="utf-8")))
    assert compose_ws == runpod_ws, (
        f"the two WebSocket locations send different headers: "
        f"only Compose {sorted(compose_ws - runpod_ws)}, "
        f"only RunPod {sorted(runpod_ws - compose_ws)}."
    )


def test_the_source_port_guards_can_actually_fail() -> None:
    """Exercised against the shapes they exist to reject, the pattern
    `test_ops_slow_queries.py` set for a tuning gate."""
    assert _sysctl({"image": "x"}, "net.ipv4.tcp_tw_reuse") is None
    assert _sysctl({"sysctls": {"net.ipv4.tcp_tw_reuse": 1}}, "net.ipv4.tcp_tw_reuse") == "1"

    assert _seconds("1s") == 1.0
    assert _seconds("500ms") == 0.5
    assert _seconds("1m") == 60.0
    assert _seconds("65") == 65.0

    # The exact shape that was shipping: a location declaring two headers of its
    # own, which silently discards the six above it.
    broken = (
        "proxy_set_header Host $host;\n"
        "proxy_set_header X-Correlation-Id $id;\n"
        "location /api/v1/ws {\n"
        "    proxy_set_header Upgrade $http_upgrade;\n"
        "}\n"
    )
    ws = _ws_location(broken)
    assert _proxy_headers(broken.replace(ws, "")) == {"host", "x-correlation-id"}
    assert _proxy_headers(ws) == {"upgrade"}

    # And a comment mentioning a directive is not the directive.
    assert _proxy_headers("# proxy_set_header Host $host;\n") == set()
