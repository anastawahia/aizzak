"""The API server's own command line -- capacity step 3.3 of
``docs/capacity-plan.md``.

Two publishers build the SAME process out of the same repository, and until
this step they launched it differently: ``deploy/runpod/supervisord.conf``
passed ``--timeout 120 --graceful-timeout 30``, the ``Dockerfile`` passed
nothing after ``--bind``. Neither drift was visible from either file. The tests
here read both command lines and fail on the drift itself, so the next flag
lands on both or on neither.

⚠️ **Two of the three numbers mean something other than what they look like,**
and both were measured rather than read off a man page:

* ``--timeout`` **is not a request timeout** under ``UvicornWorker``. The worker
  heartbeat is a timer (uvicorn's ``on_tick`` calls ``callback_notify`` every
  ``timeout/2`` seconds) and is independent of what any request is doing. A
  300-second SSE response survived intact on the pre-3.3 command line, 300 of
  300 chunks, same worker pid, zero ``WORKER TIMEOUT`` lines. What the flag
  catches is a **blocked event loop**: a 90s blocking call was killed at
  ``--timeout 30`` and survived at ``--timeout 120``. It does not bound worker
  boot either -- a 40s boot at ``--timeout 30`` was never killed, because
  gunicorn's ``WorkerTmp`` starts life with ``mkstemp``'s wall-clock mtime while
  ``murder_workers`` compares it against ``time.monotonic()``.
* ``--graceful-timeout`` is only worth what the process holding the outer
  window allows. Docker's default stop window and supervisord's ``stopwaitsecs``
  default are both **10 seconds** against gunicorn's 30, so the SIGKILL always
  came from the supervisor, which knows nothing about what is in flight.

And one flag from the step's own text is deliberately **absent**;
``test_no_publisher_recycles_workers_faster_than_the_sockets_can_stand``
carries the arithmetic that refuses it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_DOCKERFILE = _REPO_ROOT / "Dockerfile"
_RUNPOD_SUPERVISOR = _REPO_ROOT / "deploy" / "runpod" / "supervisord.conf"

# `docs/capacity-plan.md` §0, the binding target. Derived here, never retyped:
# the day §0 moves, the failure should name the number that moved.
_RPS_PEAK = 300
_WS_SOCKETS = 1_500

# §0's peak factor is x6 over the 50 rps average, and step 3.4 takes the API
# from 2 processes to 12. A per-worker request counter therefore means six
# different things across the load range and six more across the rollout, which
# is the whole reason `--max-requests` is refused below. The floor is expressed
# in TIME, against the worst case for a recycle -- peak rate, fewest processes.
_PROCESSES_TODAY = 2
_MIN_RECYCLE_INTERVAL_S = 3600.0

# What a wedged worker costs while the arbiter waits for it: its share of §0's
# sockets, refused in silence (3.1 measured that silence -- no log line, no
# `ListenOverflows`, no metric). 60s is already twice gunicorn's default and
# 400x the request-path budget `07 §2` gives a person.
_MAX_HANG_DETECTION_S = 60.0


def _command_lines() -> list[tuple[str, str]]:
    """The gunicorn invocation on each publisher, as one flat string.

    The Dockerfile's is a line-continued JSON-form ``CMD``; RunPod's is a single
    ``command=`` line. Both are normalised to `--flag value` text so one parser
    reads them, and both are located by `gunicorn` rather than by line number.
    """
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    start = dockerfile.index('CMD ["gunicorn"')
    raw = dockerfile[start : dockerfile.index("]", start) + 1]
    compose_cmd = " ".join(re.findall(r'"([^"]*)"', raw.replace("\\\n", " ")))

    supervisor = _RUNPOD_SUPERVISOR.read_text(encoding="utf-8")
    match = re.search(r"^command=(\S*gunicorn .*)$", supervisor, flags=re.MULTILINE)
    assert match is not None, "deploy/runpod/supervisord.conf launches no gunicorn"
    return [("Dockerfile", compose_cmd), ("deploy/runpod/supervisord.conf", match.group(1))]


def _flag(command: str, name: str) -> str | None:
    match = re.search(rf"{re.escape(name)}[= ]+(\S+)", command)
    return match.group(1) if match else None


def _compose_service(name: str) -> dict[str, object]:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"][name]
    assert isinstance(service, dict)
    return service


def _outer_clears_inner(outer: float, inner: float) -> bool:
    """Whether the supervisor's stop window leaves gunicorn's graceful window
    room to finish first.

    STRICTLY greater, in both directions: an outer window merely EQUAL to the
    inner one reads like agreement and is a race between two timers started at
    different instants -- the supervisor's starts when it sends the signal, the
    arbiter's when it receives one.
    """
    return outer > inner


def _supervisor_app_setting(key: str) -> str | None:
    """A `[program:app]` setting, read from that section alone.

    Section-scoped rather than file-wide on purpose: `supervisord.conf` declares
    a dozen programs and a file-wide search would happily read `minfds` out of
    `[supervisord]` or a `stopwaitsecs` belonging to `worker`.
    """
    text = _RUNPOD_SUPERVISOR.read_text(encoding="utf-8")
    start = text.index("[program:app]")
    end = text.find("\n[", start + 1)
    section = text[start : end if end != -1 else len(text)]
    match = re.search(rf"^{key}=(\S+)", section, flags=re.MULTILINE)
    return match.group(1) if match else None


def test_both_publishers_launch_the_same_server() -> None:
    """The drift 3.1 found while looking for something else.

    One repository, one process, two command lines that disagreed -- and nothing
    in either file said the other existed. Every flag that governs behaviour is
    compared by VALUE, so the next number to be tuned has to be tuned twice or
    not at all."""
    lines = _command_lines()
    for flag in ("--timeout", "--graceful-timeout", "--keep-alive", "--worker-class"):
        values = {name: _flag(command, flag) for name, command in lines}
        assert None not in values.values(), (
            f"{flag} is missing from {[n for n, v in values.items() if v is None]}. "
            "Capacity 3.3 exists to make these explicit on BOTH publishers; a "
            "default that only one of them falls back to is the drift again."
        )
        assert len(set(values.values())) == 1, (
            f"{flag} differs between publishers: {values}. This is exactly the "
            "shape 3.3 was opened to close -- `--timeout 120` on RunPod against "
            "the image's silent 30."
        )


def test_the_hang_detector_is_not_mistaken_for_a_request_timeout() -> None:
    """`--timeout` is the blocked-event-loop detector and nothing else.

    MEASURED both ways, because the plan's own text reads as though this flag
    were what lets a stream run long: a 300s SSE response survived intact at the
    default 30 (300/300 chunks, same pid, zero `WORKER TIMEOUT`), while a 90s
    blocking call was killed at 30 and survived at 120. Raising it therefore
    buys nothing for streams and costs exactly one thing -- how long a wedged
    worker keeps its share of §0's sockets before the arbiter takes them back.

    `ح-5` in the capacity plan is this failure mode already written down for a
    sibling service ("even its health check freezes"), and `§1` is what makes a
    tight number safe here: every blocking I/O on this path is already off the
    loop via `asyncio.to_thread`."""
    for name, command in _command_lines():
        value = float(_flag(command, "--timeout") or 0)
        assert 0 < value <= _MAX_HANG_DETECTION_S, (
            f"{name} sets `--timeout {value:g}` against a ceiling of "
            f"{_MAX_HANG_DETECTION_S:g}s. A worker whose loop is wedged is "
            f"holding roughly {_WS_SOCKETS // _PROCESSES_TODAY} of §0's "
            f"{_WS_SOCKETS} sockets and refusing them in silence. This flag is "
            "not what keeps a stream alive -- measured, a 300s SSE response "
            "survives at 30 -- so a larger number buys nothing and waits longer."
        )


def test_the_platform_never_kills_the_server_before_the_server_gives_up() -> None:
    """The inversion 3.3 found, and the half that makes its criterion true.

    gunicorn's graceful window is 30s (its own default, so this was already the
    case before 3.3 wrote the flag down) and BOTH supervisors defaulted to 10:
    Docker's -- MEASURED, `docker stop` on a process that ignores SIGTERM took
    10.43s -- and supervisord's, whose source reads
    `integer(get(section, 'stopwaitsecs', 10))`. The process that knows what it
    is still waiting for was given a third of the time of the one that does not,
    and every request in flight was cut mid-write by the outer SIGKILL.

    Strictly greater, in both directions: an outer window merely EQUAL to the
    inner one is a race between two timers started at different instants."""
    inner = {
        name: float(_flag(command, "--graceful-timeout") or 0) for name, command in _command_lines()
    }

    compose_window = _compose_service("app").get("stop_grace_period")
    assert compose_window is not None, (
        "docker-compose.yml's `app` declares no `stop_grace_period`, so it "
        "inherits Docker's 10s (measured: 10.43s) while gunicorn believes it has "
        f"{inner['Dockerfile']:g}s. Every request still in flight on a deploy is "
        "killed mid-write, and nothing logs it."
    )
    compose_seconds = float(str(compose_window).rstrip("s"))
    assert _outer_clears_inner(compose_seconds, inner["Dockerfile"]), (
        f"`stop_grace_period: {compose_window}` is not above gunicorn's "
        f"`--graceful-timeout {inner['Dockerfile']:g}`. Docker's SIGKILL would "
        "land first, and it knows nothing about what is in flight."
    )

    runpod_window = _supervisor_app_setting("stopwaitsecs")
    assert runpod_window is not None, (
        "deploy/runpod/supervisord.conf's `[program:app]` sets no "
        "`stopwaitsecs`, so supervisor's own default of 10 applies -- the same "
        "inversion as Compose, from a different default."
    )
    assert _outer_clears_inner(float(runpod_window), inner["deploy/runpod/supervisord.conf"]), (
        f"`stopwaitsecs={runpod_window}` is not above gunicorn's "
        f"`--graceful-timeout {inner['deploy/runpod/supervisord.conf']:g}`."
    )


def test_no_publisher_recycles_workers_faster_than_the_sockets_can_stand() -> None:
    """`--max-requests` is refused, and this is the arithmetic that refuses it.

    The step's text asks for `--max-requests 2000 --max-requests-jitter 200`
    against a memory leak nobody has measured. The COST is measured: a recycle
    closes every WebSocket the worker holds, with code 1012. Only HTTP responses
    count toward the limit (`total_requests` increments in uvicorn's
    `on_response_complete`; the WebSocket implementation never touches it), so
    the recycle interval is `max_requests / (peak_rps / processes)` -- 13.3s
    today and 80s after step 3.4's twelve processes, each one dropping that
    worker's share of §0's 1,500 sockets.

    This test does not forbid the flag. It forbids a NUMBER that recycles more
    often than once an hour at §0's worst case, which is what turns "recycle
    rarely" from an intention into something the build can check."""
    for name, command in _command_lines():
        raw = _flag(command, "--max-requests")
        if raw is None:
            continue
        per_worker_rps = _RPS_PEAK / _PROCESSES_TODAY
        interval = float(raw) / per_worker_rps
        assert interval >= _MIN_RECYCLE_INTERVAL_S, (
            f"{name} sets `--max-requests {raw}`, which at §0's {_RPS_PEAK} rps "
            f"peak across {_PROCESSES_TODAY} processes recycles every "
            f"{interval:.1f}s. Each recycle closes every WebSocket that worker "
            "holds with code 1012 (measured), i.e. its share of §0's "
            f"{_WS_SOCKETS} sockets, and every one of them reconnects through "
            "the auth path. Raise the number or drop the flag; do not lower "
            "this floor without a measured leak to weigh against it."
        )


def test_the_command_line_guards_can_actually_fail() -> None:
    """Every assertion above reads a file, and a parser that silently finds
    nothing would pass all of them forever. Each check is re-run against a
    deliberately broken input."""
    broken = (
        "gunicorn app:x --worker-class uvicorn.workers.UvicornWorker "
        "--timeout 120 --graceful-timeout 30 --keep-alive 65 --max-requests 2000"
    )
    assert _flag(broken, "--timeout") == "120"
    assert float(_flag(broken, "--timeout") or 0) > _MAX_HANG_DETECTION_S
    recycled_every = float(_flag(broken, "--max-requests") or 0) / (_RPS_PEAK / _PROCESSES_TODAY)
    assert recycled_every < _MIN_RECYCLE_INTERVAL_S
    assert _flag(broken, "--max-requests-jitter") is None

    # And the two shapes the inversion rule has to reject, the second of which
    # is the one that reads like agreement.
    assert not _outer_clears_inner(10.0, 30.0)
    assert not _outer_clears_inner(30.0, 30.0)
    assert _outer_clears_inner(45.0, 30.0)

    # The parsers find the real thing, so a pass above is not an empty read.
    names = [name for name, _ in _command_lines()]
    assert names == ["Dockerfile", "deploy/runpod/supervisord.conf"]
    assert _supervisor_app_setting("stopwaitsecs") is not None
    assert _compose_service("app").get("stop_grace_period") is not None
