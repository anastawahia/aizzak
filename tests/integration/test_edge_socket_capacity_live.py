"""The per-address WebSocket ceiling, measured against the RUNNING edge --
capacity step 3.1 (docs/capacity-plan.md §5 wave 3).

`tests/unit/test_edge_capacity.py` reads the config files and compares the
numbers in them. This file asks the edge that is actually running, because the
two are different claims and step 3.1 was built out of the gap between them:
`worker_connections 1024` looked like the binding number in the file and was
not the binding number on the wire.

⭐ WHY THIS CAN BE MEASURED AT ALL WITH NO TOKEN POOL (blocker د-9).
`api/v1/websocket/streaming.py` verifies a `?token=` BEFORE `accept`, so k6's
`ws_hold` scenario -- which always sends one -- gets a 403 and holds nothing
without real Firebase credentials. A TOKENLESS handshake takes the other
branch: it is accepted, and `_AUTH_WINDOW_S = 10s` is how long it may sit there
before the socket is closed 1008. For those ten seconds it is a fully upgraded,
proxied WebSocket -- two descriptors and two connection slots in an nginx
worker, `proxy_read_timeout 3600s` -- which is exactly the shape §0's 1,500
sockets have at this layer. What stays unmeasurable without real tokens is what
happens AFTER authentication; the edge's carrying capacity is not that.

⭐ WHAT THIS WOULD HAVE CAUGHT, and did: offered 150 simultaneous upgrades from
one address, the edge answered **100 x 101 and 50 x 429** -- `limit_conn ws_conn
100`, doing exactly what it said. It was not a wrong limiter, it was a limiter
sized for a different claim than §0's: 500 users behind one office NAT are ONE
address holding up to three sockets each, and 100 of them is roughly 33 users
before the 34th is refused. After 3.1 the identical run answers 150 x 101.

⚠️ One address on purpose, and the whole point. Everything else in the load
harness deliberately spreads itself over 32 source addresses
(`deploy/load/entrypoint.sh`, blocker د-8) so that a generator stops measuring
the edge's per-address limiter instead of the platform. This file measures that
limiter, so it does the opposite.

Gated behind the same opt-in as its `live_` siblings: it holds real sockets
against a real stack for the length of the auth window, which is not something
the bare gate-5 `pytest` run should do.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
from collections import Counter

import pytest

# Module level, not a decorator: `test_ci_workflow_truthfulness.py` requires a
# `pytestmark` naming a `live_*` marker on every module under this directory,
# so that a module whose service is absent SKIPS on a runner rather than
# failing on it.
pytestmark = pytest.mark.live_edge

_ENABLE_VAR = "RUN_EDGE_CAPACITY_TEST"
_HOST_VAR = "LOAD_TEST_NGINX_HOST"
_PORT_VAR = "LOAD_TEST_NGINX_PORT_HTTP"
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 80

# Comfortably above the OLD ceiling of 100 and comfortably below the new one, so
# a pass means "the limiter moved" and not "the offer happened to be small".
_OFFERED = 150

_PROBE_TIMEOUT_S = 1.5
_SOCKET_TIMEOUT_S = 15.0

# :80 rather than :443 — this measures an admission decision nginx makes before
# it proxies anything, and a TLS handshake per socket would put 150 of them on
# the measurement's own critical path. `app-locations.conf` is `include`d by
# both server blocks, so `limit_conn ws_conn` is the same directive on either.
_UPGRADE = (
    b"GET /api/v1/ws HTTP/1.1\r\n"
    b"Host: localhost\r\n"
    b"Upgrade: websocket\r\n"
    b"Connection: Upgrade\r\n"
    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
    b"Sec-WebSocket-Version: 13\r\n"
    b"\r\n"
)


def _tcp_reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def _skip_reason() -> str | None:
    if not os.environ.get(_ENABLE_VAR):
        return f"set {_ENABLE_VAR}=1 to measure the live edge's socket ceiling (capacity 3.1)"
    host = os.environ.get(_HOST_VAR, _DEFAULT_HOST)
    port = int(os.environ.get(_PORT_VAR, str(_DEFAULT_PORT)))
    if not _tcp_reachable(host, port):
        return f"nginx not reachable at {host}:{port} — bring the live stack up first"
    return None


def _offer_upgrades(host: str, port: int, count: int) -> Counter[str]:
    """`count` upgrades held OPEN together, then all released.

    The barrier is the measurement. `limit_conn` counts sockets currently open,
    so a loop that opens and closes one at a time never has more than one in the
    zone and would report a clean pass against any ceiling at all, including
    zero. Every socket has to be established before any of them asks."""
    codes: Counter[str] = Counter()
    lock = threading.Lock()
    ready = threading.Barrier(count, timeout=60)
    held: list[socket.socket] = []

    def one() -> None:
        sock: socket.socket | None = None
        try:
            sock = socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S)
            with lock:
                held.append(sock)
            ready.wait()
            sock.sendall(_UPGRADE)
            status = sock.recv(128).decode("latin-1").split("\r\n")[0]
            code = status.split(" ")[1] if " " in status else (status or "<empty>")
        # Deliberately broad: a refusal at this layer arrives as a reset, a
        # timeout or an empty read depending on WHERE it was refused, and every
        # one of those is a datum this test wants counted rather than raised.
        except Exception as exc:
            code = type(exc).__name__
        with lock:
            codes[code] += 1

    threads = [threading.Thread(target=one) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=90)

    for sock in held:
        with contextlib.suppress(OSError):
            sock.close()
    return codes


def test_one_address_may_hold_a_nated_office_worth_of_sockets() -> None:
    """`limit_conn ws_conn`, on the edge that is running rather than the edge
    that is written down."""
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)

    host = os.environ.get(_HOST_VAR, _DEFAULT_HOST)
    port = int(os.environ.get(_PORT_VAR, str(_DEFAULT_PORT)))
    codes = _offer_upgrades(host, port, _OFFERED)

    assert codes["429"] == 0, (
        f"the edge refused {codes['429']} of {_OFFERED} simultaneous upgrades from a "
        f"single address with 429 — `limit_conn ws_conn` in app-locations.conf is "
        f"below what §0's NATed office needs. Full tally: {dict(codes)}"
    )
    assert codes["101"] == _OFFERED, (
        f"only {codes['101']} of {_OFFERED} upgrades completed. A non-429 shortfall is "
        f"NOT this limiter — check the app's descriptor budget (`ulimits: nofile:` on "
        f"the `app` service), which refuses silently. Full tally: {dict(codes)}"
    )
