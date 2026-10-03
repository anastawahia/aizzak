"""Rolling deploy without downtime — capacity step 7.2 (docs/capacity-plan.md
§5 wave 7). Three files have to agree and nothing compared them: the endpoint
that takes a replica out of rotation, the edge that must NOT expose it, and the
script that drives the two.

⛔ WHAT THIS STEP FOUND FIRST, AND IT IS THE REASON THE SCRIPT EXISTS. Step 7.2
opens with "with `replicas: 3` it became possible". MEASURED: `docker compose
up -d --force-recreate app` on a three-replica service stops all three and then
starts them --

    19:45:02.02  running=3 of 3
    19:45:05.10  running=0 of 3
    19:45:09.74  running=1 of 3
    19:45:11.61  running=3 of 3

-- 4.64 s with nothing serving, and under 50 rps through the edge that measured
413 failed requests of 3,750 (11.0 %). `replicas: 3` on its own bought no
availability at all: it is three containers with one lifecycle. The Compose
output while it happens ("app-3 Started", "app-2 Starting") reads like a
rolling deploy, which is why nothing had noticed.

After `deploy/rolling-deploy.sh`, the same load through the same edge across a
full three-replica roll: **7,500 requests, 7,500 x 200, zero failures**, p95
5.62 ms against 5.57 ms measured on an idle stack.

⚠️ THE WEBSOCKET HALF OF THE ACCEPTANCE CRITERION CANNOT BE MEASURED LIVE HERE,
and the blocker is `د-9`, not this step. Registering a session requires a real
Firebase token (a tokenless handshake is closed 1008 before it ever reaches the
hub), so a 1,500-socket drain against the running stack is the same wall that
holds the load halves of `1.1` and `1.2`. What IS asserted here is the property
the criterion actually names -- that the reconnect rate a drain produces stays
under the accept capacity `3.5` wrote down -- as arithmetic over the shipped
numbers, plus the mechanism itself in `test_streaming_hub.py`.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from app.api.health import _DEFAULT_DRAIN_WINDOW_S, _MAX_DRAIN_WINDOW_S
from app.framework.streaming import ConnectionHub
from tests.unit.support_streaming import InMemoryWsConnectionRegistry
from tests.unit.test_api_app_shell import _make_app

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_SCRIPT = _REPO_ROOT / "deploy" / "rolling-deploy.sh"
_NGINX_CONF = _REPO_ROOT / "deploy" / "nginx" / "nginx.conf"
_APP_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"

# §0's WebSocket target, and `3.5`'s shipped accept queue. Both restated here
# rather than imported because they live in a plan and a compose file
# respectively, and the point of the assertion is that the drain window is
# chosen against them.
_TARGET_SOCKETS = 1500
_ACCEPT_QUEUE = 4096


# ------------------------------------------------------- the drain endpoint --


def _app_with_hub(hub: ConnectionHub | None = None):
    app = _make_app()
    app.state.hub = hub
    return app


def test_drain_takes_the_replica_out_of_rotation_before_anything_closes() -> None:
    """Readiness first. The socket closes are what the client sees, but the
    503 is what stops new work arriving at a replica that is leaving -- and it
    has to be true from the instant the request is answered, not when the
    window ends."""
    app = _app_with_hub()
    client = TestClient(app)
    app.state.ready = True

    assert client.post("/health/drain").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_draining_twice_does_not_schedule_a_second_round_of_closes() -> None:
    """Idempotence is not tidiness here: a retried deploy step that closed
    every socket twice would double the reconnect rate at exactly the moment
    the rate is the thing under control."""
    hub = ConnectionHub(max_connections_per_user=5, registry=InMemoryWsConnectionRegistry())
    client = TestClient(_app_with_hub(hub))

    first = client.post("/health/drain").json()
    second = client.post("/health/drain").json()

    assert first["repeat"] is False
    assert second["repeat"] is True
    assert second["sessions"] == first["sessions"]


def test_a_replica_with_no_hub_still_drains_successfully() -> None:
    """ "Nothing to drain" is a successful drain. A caller that had to tell that
    apart from a failure would need to know whether this replica happens to
    serve WebSockets."""
    body = TestClient(_app_with_hub(None)).post("/health/drain").json()

    assert body["status"] == "draining"
    assert body["sessions"] == 0


def test_an_oversized_window_is_clamped_and_says_so() -> None:
    """A mistyped window silently converts a rolling deploy into a stall. The
    clamp reports itself so the script's log shows the number that was used
    rather than the number that was asked for."""
    body = TestClient(_app_with_hub()).post("/health/drain?window_s=600").json()

    assert body["window_s"] == _MAX_DRAIN_WINDOW_S
    assert body["clamped"] is True


def test_the_drain_window_fits_inside_the_shutdown_budget_that_follows_it() -> None:
    """⚠️ THE ORDERING THAT MAKES THE WHOLE MECHANISM WORTH ANYTHING. Whatever
    is still open when `SIGTERM` lands is closed by uvicorn in one tick (3.3
    measured 1012 at 0.10 s) -- the exact burst this endpoint exists to spread.
    So the window must end before the stop signal, which means it must fit
    inside gunicorn's graceful window, which 3.3 shipped at 30 s inside a 45 s
    external stop window."""
    graceful_timeout_s = 30

    assert graceful_timeout_s > _DEFAULT_DRAIN_WINDOW_S
    assert graceful_timeout_s > _MAX_DRAIN_WINDOW_S, (
        f"a caller may ask for {_MAX_DRAIN_WINDOW_S}s of draining inside a {graceful_timeout_s}s "
        "graceful window, so the tail of the drain would be cut by the very burst it spreads"
    )


def test_the_reconnect_rate_a_full_drain_produces_fits_the_accept_queue() -> None:
    """Step 7.2's second acceptance criterion, as arithmetic over the shipped
    numbers: §0's 1,500 sockets spread over the default window against the
    accept queue `3.5` wrote onto this listener.

    ⚠️ The comparison is deliberately against a SINGLE replica's queue, not the
    fleet's: a drained replica's clients do not come back to it, they arrive at
    the siblings, and in the worst case they all pick the same one.
    """
    reconnects_per_second = _TARGET_SOCKETS / _DEFAULT_DRAIN_WINDOW_S

    assert reconnects_per_second <= _ACCEPT_QUEUE, (
        f"draining {_TARGET_SOCKETS} sockets over {_DEFAULT_DRAIN_WINDOW_S}s offers "
        f"{reconnects_per_second:.0f} reconnects/s to a listener whose queue is {_ACCEPT_QUEUE}. "
        "3.5 measured that an exceeded accept queue does not refuse -- it silently drops the "
        "final ACK -- so the peak would surface as unattributable latency."
    )


# ------------------------------------------------------------- the edge --


def test_the_edge_answers_the_drain_path_itself_and_never_proxies_it() -> None:
    """⚠️ THE SHARPEST TRUST BOUNDARY IN THIS FILE, because unlike `/metrics`
    this endpoint is a VERB: unauthenticated, it takes a replica out of
    rotation and closes every socket on it. 404 rather than 403 so a probe
    cannot learn the path exists."""
    conf = _APP_LOCATIONS.read_text(encoding="utf-8")
    block = re.search(r"location /health/drain \{(.*?)\}", conf, flags=re.DOTALL)

    assert block is not None, "deploy/nginx/app-locations.conf no longer intercepts /health/drain"
    assert "return 404" in block.group(1)
    assert "proxy_pass" not in block.group(1), (
        "the edge proxies /health/drain. Even authenticated that would be wrong: the edge load "
        "balances, and this endpoint must address exactly ONE replica."
    )


def test_the_drain_location_outranks_the_health_location_it_sits_under() -> None:
    """nginx picks the LONGEST matching prefix, not the first one written --
    so `/health/drain` wins over `/health` regardless of order. Asserted rather
    than trusted: if `/health/drain` ever became a regex location or `/health`
    an exact match, the 404 above would quietly stop applying and the verb
    would be exposed through the edge."""
    conf = _APP_LOCATIONS.read_text(encoding="utf-8")

    assert re.search(r"^location /health \{", conf, flags=re.MULTILINE)
    assert re.search(r"^location /health/drain \{", conf, flags=re.MULTILINE)
    assert len("/health/drain") > len("/health")


def test_the_edge_gives_up_on_a_dead_replica_in_seconds_not_a_minute() -> None:
    """⛔ MEASURED, AND IT IS THE DIFFERENCE BETWEEN 14 FAILURES AND ZERO. A
    removed container's address is a BLACK HOLE, not a refusal -- packets go
    nowhere -- so nginx waits out `proxy_connect_timeout`, whose default is 60
    s and which nothing here had written down:

        [error] upstream timed out (110: Connection timed out) while connecting
                to upstream, upstream: "http://172.18.0.25:8000/health"

    When Docker happens to recycle the address to another container the SYN is
    refused in microseconds and `proxy_next_upstream` moves on before anyone
    notices. So the same deploy either costs nothing or costs a minute per
    request, decided by IPAM -- the same "fails only sometimes" shape the
    `upstream`-block decision was made to avoid.
    """
    conf = _NGINX_CONF.read_text(encoding="utf-8")
    connect = re.search(r"^\s*proxy_connect_timeout\s+(\d+)s;", conf, flags=re.MULTILINE)

    assert connect is not None, (
        "proxy_connect_timeout is unset, so nginx's 60s default applies and one removed replica "
        "can hold a request for a minute"
    )
    assert int(connect.group(1)) <= 5, (
        f"proxy_connect_timeout is {connect.group(1)}s. A connect on the container bridge is "
        "sub-millisecond; this bounds how long a dead replica costs, and nothing else."
    )
    tries = re.search(r"^\s*proxy_next_upstream_tries\s+(\d+);", conf, flags=re.MULTILINE)
    assert tries is not None and int(tries.group(1)) >= 2, (
        "without a retry there is nothing for proxy_connect_timeout to hand off TO"
    )


def test_the_app_healthcheck_probes_readiness_not_liveness() -> None:
    """`api/health.py` had said so since it was written -- "readiness here is
    'startup done', which is the signal a rolling deploy actually needs" --
    while the compose healthcheck asked for the other one. `/health` answers
    200 from a process that has bound its socket and finished nothing else."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    probe = " ".join(str(part) for part in compose["services"]["app"]["healthcheck"]["test"])

    assert "/health/ready" in probe, (
        f"the app healthcheck probes {probe!r}. /health is LIVENESS: it is 200 from a replica "
        "that cannot yet serve a request, which is the state a rolling deploy is about."
    )


# ------------------------------------------------------------ the script --


def test_the_script_exists_and_is_executable() -> None:
    assert _SCRIPT.is_file(), "deploy/rolling-deploy.sh is missing"
    assert os.access(_SCRIPT, os.X_OK), (
        "deploy/rolling-deploy.sh is not executable. This repository has been bitten by a "
        "mode-0644 script once already -- 2.5's archive_command failed with `exit 126` and "
        "pg_stat_archiver reported nothing."
    )


@pytest.mark.parametrize(
    ("needle", "why"),
    [
        (
            "com.docker.compose.service=",
            "replicas must be found by LABEL. Compose does not reuse a replica index: after one "
            "roll the fleet is app-4/5/6 and the number keeps climbing, so a script reaching for "
            "`<project>-app-1` would silently no-op on any stack that had ever been rolled.",
        ),
        (
            "/health/ready",
            "an HTTP service's transition to the next replica must be gated on READINESS -- "
            "step 7.2 names that endpoint specifically, and `docker` health is not it there.",
        ),
        (
            "/health/drain",
            "each replica must be drained before it is stopped, or its sockets are cut in one "
            "tick by uvicorn and the reconnect herd is exactly what the step forbids.",
        ),
        (
            "--scale",
            "the replacement is built and proven ready while the outgoing replica still serves, "
            "so capacity never drops below the declared count.",
        ),
    ],
)
def test_the_script_does_the_four_things_the_step_requires(needle: str, why: str) -> None:
    assert needle in _SCRIPT.read_text(encoding="utf-8"), why


def test_the_script_puts_the_fleet_back_if_it_dies_mid_roll() -> None:
    """A surge replica left behind is a stack quietly running N+1 -- a
    connection budget (08 §2-ب) and a resource budget (§2-ز) that no longer
    match the file that declares them, with nothing to say so."""
    body = _SCRIPT.read_text(encoding="utf-8")

    assert "trap cleanup EXIT" in body
    assert re.search(r"cleanup\(\)\s*\{", body)


# ------------------------------------------------- the script, actually run --
#
# ⛔ WHY THESE EXIST. Every test above reads the script as TEXT, and all of
# them passed for a script that could not roll a single worker: it probed
# `/health/ready` on :8000 in a container that serves no HTTP, waited out the
# timeout on every replica, and gave up (2026-10-03, capacity-plan 6.2 (7)).
# Text cannot say what a script DOES with a given replica. So the script now
# runs here against a fake `docker` on PATH that keeps a small fleet in a JSON
# file and records every call -- what is asserted is the sequence of calls.

_FAKE_DOCKER = r"""
import json, os, sys

state_path = os.environ["FAKE_DOCKER_STATE"]
with open(state_path) as f:
    st = json.load(f)
with open(os.environ["FAKE_DOCKER_CALLS"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")

def save():
    with open(state_path, "w") as f:
        json.dump(st, f)

def find(cid):
    for c in st["containers"]:
        if c["id"] == cid:
            return c
    sys.exit(1)

def running(service):
    return [c for c in st["containers"] if c["service"] == service and c["status"] == "running"]

a = sys.argv[1:]
if a[:2] == ["compose", "up"]:
    service = a[-1]
    want = int(a[a.index("--scale") + 1].split("=")[1])
    while len(running(service)) < want:
        st["next"] += 1
        n = st["next"]
        st["containers"].append({
            "id": f"c{n}", "name": f"aizzak-{service}-{n}", "service": service,
            "status": "running", "health": st["new_health"], "test": st["test"],
            "ip": f"10.0.0.{n}",
        })
    while len(running(service)) > want:
        st["containers"].remove(running(service)[-1])
    save()
elif a[0] == "ps":
    label = "label=com.docker.compose.service="
    service = [x[len(label):] for x in a if x.startswith(label)][0]
    for c in running(service):
        print(c["id"])
elif a[0] == "inspect":
    c, fmt = find(a[1]), a[a.index("--format") + 1]
    if "IPAddress" in fmt:
        print(c["ip"])
    elif "Healthcheck" in fmt:
        print(json.dumps(c["test"]) if c["test"] else "")
    elif "State.Status" in fmt:
        print(f"{c['status']}/{c['health']}")
    else:
        print("/" + c["name"])
elif a[0] == "exec":
    find(a[1])
    if "/health/drain" in " ".join(a):
        print('{"status":"draining"}')
elif a[0] == "stats":
    find(a[-1])
    print(st["mem_usage"])
elif a[0] == "stop":
    find(a[1])["status"] = "exited"
    save()
elif a[0] == "rm":
    st["containers"].remove(find(a[-1]))
    save()
else:
    sys.exit(f"fake docker: unexpected call {a}")
"""

_APP_TEST = ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/health/ready"]
_WORKER_TEST = ["CMD", "python", "-m", "app.ops.healthcheck", "knowledge"]


def _roll(
    tmp_path: Path,
    *,
    service: str,
    replicas: int,
    test: list[str] | None,
    new_health: str = "healthy",
    mem_available_mib: int = 8192,
    mem_usage: str = "600MiB / 2GiB",
    args: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], list[list[str]], list[dict[str, str]]]:
    """Run the real script against a fleet of ``replicas`` copies of
    ``service``; return its result, every docker call, and the final fleet."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(f"#!{sys.executable}\n{_FAKE_DOCKER}", encoding="utf-8")
    fake.chmod(0o755)
    state = tmp_path / "state.json"
    old = [
        {
            "id": f"old{i}",
            "name": f"aizzak-{service}-{i}",
            "service": service,
            "status": "running",
            "health": "healthy",
            "test": test,
            "ip": f"10.0.1.{i}",
        }
        for i in range(1, replicas + 1)
    ]
    state.write_text(
        json.dumps(
            {
                "containers": old,
                "next": 100,
                "new_health": new_health,
                "test": test,
                "mem_usage": mem_usage,
            }
        ),
        encoding="utf-8",
    )
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal: 13280000 kB\nMemAvailable: {mem_available_mib * 1024} kB\n")
    calls = tmp_path / "calls.jsonl"
    calls.touch()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state),
        "FAKE_DOCKER_CALLS": str(calls),
        "PROJECT": "aizzak",
        "SERVICE": service,
        "MEMINFO": str(meminfo),
        "DRAIN_WINDOW_S": "0",
        "DRAIN_GRACE_S": "0",
        "READY_TIMEOUT_S": "6",
    }
    result = subprocess.run(
        ["bash", str(_SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    logged = [json.loads(line) for line in calls.read_text().splitlines()]
    fleet = json.loads(state.read_text())["containers"]
    return result, logged, fleet


def _scales(calls: list[list[str]]) -> list[str]:
    return [c[c.index("--scale") + 1] for c in calls if c[:2] == ["compose", "up"]]


def test_a_worker_is_rolled_in_place_and_stopped_not_killed(tmp_path: Path) -> None:
    """The case that never worked. A worker serves no HTTP, so readiness is its
    own healthcheck; it is STOPPED (SIGTERM, then its 45 s grace -- 5.1's
    drain ladder) rather than `rm -f`'d; and by default it is replaced in
    place, never running N+1 -- one extra worker on a full host is what had
    the kernel kill Qdrant on 2026-10-03."""
    result, calls, fleet = _roll(
        tmp_path, service="worker-knowledge", replicas=2, test=_WORKER_TEST
    )

    assert result.returncode == 0, result.stderr
    assert ["stop", "old1"] in calls and ["stop", "old2"] in calls
    assert not any(c[:2] == ["rm", "-f"] for c in calls), "a worker was SIGKILLed"
    assert not any(c[0] == "exec" for c in calls), "the script spoke HTTP to a worker"
    assert "worker-knowledge=3" not in _scales(calls), "a worker was surged by default"
    assert sorted(c["id"] for c in fleet) == ["c101", "c102"]


def test_the_app_still_surges_drains_and_waits_for_http_readiness(tmp_path: Path) -> None:
    """7.2's measured sequence, unchanged for an HTTP service: N+1 first, ready
    over HTTP, drained over loopback, then removed."""
    result, calls, fleet = _roll(tmp_path, service="app", replicas=3, test=_APP_TEST)

    assert result.returncode == 0, result.stderr
    assert _scales(calls).count("app=4") == 3
    probes = [" ".join(c) for c in calls if c[0] == "exec"]
    assert sum("/health/ready" in p for p in probes) == 3
    assert sum("/health/drain" in p for p in probes) == 3
    assert ["rm", "-f", "old1"] in calls
    assert len(fleet) == 3 and not any(c["id"].startswith("old") for c in fleet)


def test_a_surge_the_host_cannot_hold_is_refused_before_anything_changes(tmp_path: Path) -> None:
    """600 MiB for the extra copy + the 1,024 MiB reserve does not fit in 1,000
    available: refused, with the way through named, and the fleet untouched."""
    result, calls, fleet = _roll(
        tmp_path, service="app", replicas=3, test=_APP_TEST, mem_available_mib=1000
    )

    assert result.returncode != 0
    assert "--in-place" in result.stderr
    assert "app=4" not in _scales(calls)
    assert not any(c[0] in ("rm", "stop", "exec") for c in calls)
    assert sorted(c["id"] for c in fleet) == ["old1", "old2", "old3"]


def test_in_place_needs_no_memory_check_and_still_drains_the_app(tmp_path: Path) -> None:
    """On the same full host, `--in-place` is the way through: drained, removed,
    replaced -- never N+1, and `docker stats` is not even asked."""
    result, calls, _ = _roll(
        tmp_path,
        service="app",
        replicas=3,
        test=_APP_TEST,
        mem_available_mib=1000,
        args=("--in-place",),
    )

    assert result.returncode == 0, result.stderr
    assert "app=4" not in _scales(calls)
    assert not any(c[0] == "stats" for c in calls)
    assert sum("/health/drain" in " ".join(c) for c in calls if c[0] == "exec") == 3


def test_an_unhealthy_replacement_stops_the_roll_at_once(tmp_path: Path) -> None:
    """`unhealthy` is past start_period and will not improve by waiting: the
    roll stops on the first replica instead of spending the timeout on each."""
    result, calls, _ = _roll(
        tmp_path, service="worker-knowledge", replicas=2, test=_WORKER_TEST, new_health="unhealthy"
    )

    assert result.returncode != 0
    assert "unhealthy" in result.stderr
    assert ["stop", "old2"] not in calls, "the roll went on to the next replica"


def test_a_service_with_no_healthcheck_is_refused(tmp_path: Path) -> None:
    result, calls, _ = _roll(tmp_path, service="thing", replicas=1, test=None)

    assert result.returncode != 0
    assert "no healthcheck" in result.stderr
    assert not any(c[:2] == ["compose", "up"] for c in calls)
