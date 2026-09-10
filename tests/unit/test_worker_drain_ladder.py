"""The shutdown ladder for the three ``worker-*`` processes -- capacity step
5.1's invariant 4 (``docs/capacity-plan.md`` §5 wave 5).

⭐ **THE DEFECT THIS EXISTS FOR IS THE ONE 3.3 FOUND ON `app`, ON THE OTHER
PROCESS.** A worker asked to stop now DRAINS: it stops reading, finishes the
messages it has already taken out of the stream within
``WORKER_DRAIN_TIMEOUT_S``, and only then is cancelled
(``workers/lifecycle.py``). That inner window is worth exactly nothing unless
the process supervisor's outer one is wider than it -- and BOTH publishers
shipped the default. Compose's ``stop_grace_period`` default is 10 s and
supervisord's ``stopwaitsecs`` default is 10 s (read out of supervisor's own
source, ``integer(get(section, 'stopwaitsecs', 10))``), against a drain of 30.
So without this step's two edits the drain could never once have completed:
``SIGKILL`` at ten seconds, mid-handler, on every single restart -- and now
``WORKER_CONCURRENCY`` jobs truncated at a time instead of one, which is
precisely the multiplication the drain was added to prevent.

⚠️ **THE ORDER IS THE ASSERTION, NOT THE NUMBERS.** Every value here is meant
to be tunable: a deployment may raise the drain, and an operator may widen the
grace period. What may never happen is the inner window growing past the outer
one, because that failure is SILENT -- nothing logs, nothing alerts, the
container simply dies a little earlier than it promised and the entries it was
finishing go back to being the sweeper's problem.

Same shape as ``test_gunicorn_flags.py`` (which guards the identical nesting
for gunicorn's ``--graceful-timeout``) and ``test_connection_budget.py``:
several files have to agree, and nothing but a test compares them.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.framework.settings.settings import EventSettings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_SUPERVISORD = _REPO_ROOT / "deploy" / "runpod" / "supervisord.conf"
_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"

# The three Compose services that run `python -m app.workers.main`, i.e. every
# service `workers/lifecycle.run_worker` governs. Named rather than derived so
# that a FOURTH worker added without a `stop_grace_period` fails this file
# instead of quietly not being checked.
_WORKER_SERVICES = ("worker-knowledge", "worker-media", "worker-memory")

# What the drain still has to do after the loop returns and before the process
# may be killed: `deregister` is two Redis round trips per (stream, group), and
# each entrypoint then closes its clients. Seconds, not milliseconds, because
# the machine that matters is a loaded one.
_TEARDOWN_MARGIN_S = 5.0

_DURATION = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>ms|s|m|h)?$")
_SUPERVISOR_PROGRAM = re.compile(
    r"^\[program:(?P<name>[^\]]+)\]\n(?P<body>(?:(?!\[program:).*\n)*)", re.MULTILINE
)


def _seconds(raw: object) -> float:
    """Compose's duration grammar, for the subset this file can meet."""
    match = _DURATION.match(str(raw).strip())
    assert match is not None, f"unparseable duration {raw!r}"
    value = float(match.group("value"))
    return value * {"ms": 0.001, None: 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}[match.group("unit")]


def _compose() -> dict[str, object]:
    loaded = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _services() -> dict[str, dict[str, object]]:
    services = _compose()["services"]
    assert isinstance(services, dict)
    return services


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _drain_default() -> float:
    """The shipped drain, read off the contract rather than off a literal."""
    return EventSettings().worker_drain_timeout_s


def test_the_shipped_drain_is_actually_on() -> None:
    """A `0` here is a legal configuration -- it is the pre-5.1 "cancel where
    it stands" -- but it must be a CHOICE. Shipping 0 by accident would leave
    every one of this file's other assertions vacuously true."""
    assert _drain_default() > 0


def test_every_worker_service_outlives_its_own_drain() -> None:
    """Compose's half of the ladder. The DEFAULT `stop_grace_period` is 10 s;
    each worker service now names 45, which is the drain plus the
    deregistration round trips plus margin."""
    services = _services()

    for name in _WORKER_SERVICES:
        service = services[name]
        grace = service.get("stop_grace_period")
        assert grace is not None, f"{name} has no stop_grace_period: it inherits Docker's 10s"
        assert _seconds(grace) >= _drain_default() + _TEARDOWN_MARGIN_S


def test_the_runpod_worker_outlives_its_own_drain_too() -> None:
    """The other publisher, and the one whose omission was invisible: a
    supervisord `[program:]` block with no `stopwaitsecs` inherits 10, and
    nothing in the file says so."""
    programs = {
        match.group("name"): match.group("body")
        for match in _SUPERVISOR_PROGRAM.finditer(_SUPERVISORD.read_text(encoding="utf-8"))
    }
    body = programs["worker"]
    found = re.search(r"^stopwaitsecs=(?P<value>\d+)", body, re.MULTILINE)

    assert found is not None, "[program:worker] has no stopwaitsecs: it inherits supervisor's 10s"
    assert float(found.group("value")) >= _drain_default() + _TEARDOWN_MARGIN_S


def test_both_publishers_ship_the_same_drain_the_contract_defines() -> None:
    """A drain that differs per publisher is a ladder nobody can reason about.
    Both files interpolate the same key with the same fallback, and both
    fallbacks equal the `EventSettings` default -- so `.env` moves one number
    and the whole ladder moves with it."""
    compose_env = _services()["worker-memory"]["environment"]
    assert isinstance(compose_env, dict)
    rendered = yaml.safe_dump(_compose()["x-app-env"])
    entrypoint = _ENTRYPOINT.read_text(encoding="utf-8")
    shipped = _drain_default()

    assert f"WORKER_DRAIN_TIMEOUT_S: ${{WORKER_DRAIN_TIMEOUT_S:-{shipped:g}}}" in rendered
    assert f'WORKER_DRAIN_TIMEOUT_S="${{WORKER_DRAIN_TIMEOUT_S:-{shipped:g}}}"' in entrypoint
    assert _env_example()["WORKER_DRAIN_TIMEOUT_S"] == f"{shipped:g}"


def test_the_shipped_concurrency_agrees_across_every_file_that_names_it() -> None:
    """`WORKER_CONCURRENCY` decides three things at once (the DB pool, the
    `XREADGROUP` COUNT, and peak RSS), so a publisher that ships a different
    default is shipping a different connection budget -- and
    `test_connection_budget.py` would be recomputing a stack nobody runs."""
    shipped = EventSettings().worker_concurrency
    rendered = yaml.safe_dump(_compose()["x-app-env"])
    entrypoint = _ENTRYPOINT.read_text(encoding="utf-8")

    assert f"WORKER_CONCURRENCY: ${{WORKER_CONCURRENCY:-{shipped}}}" in rendered
    assert f'WORKER_CONCURRENCY="${{WORKER_CONCURRENCY:-{shipped}}}"' in entrypoint
    assert _env_example()["WORKER_CONCURRENCY"] == str(shipped)


def test_a_per_service_override_never_silently_widens_the_fleet() -> None:
    """The per-service keys exist because these three workers do NOT have the
    same facts -- `worker-media`'s handler waits on an external provider with
    no ceiling in front of it until step 6.1. What the overrides may not do is
    exceed the shared default without someone also revisiting that service's
    `memory` limit and the connection ledger, so the guard is one-directional:
    an override may lower, never raise."""
    services = _services()
    shipped = EventSettings().worker_concurrency
    pattern = re.compile(r"^\$\{WORKER_CONCURRENCY_[A-Z]+:-(?P<value>\d+)\}$")

    seen = 0
    for name in _WORKER_SERVICES:
        environment = services[name].get("environment")
        assert isinstance(environment, dict)
        raw = environment.get("WORKER_CONCURRENCY")
        if raw is None:
            continue
        match = pattern.match(str(raw))
        assert match is not None, f"{name} pins WORKER_CONCURRENCY to {raw!r}, not to a key"
        assert int(match.group("value")) <= shipped
        seen += 1
    assert seen == len(_WORKER_SERVICES)
