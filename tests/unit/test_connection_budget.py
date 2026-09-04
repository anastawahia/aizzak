"""Every Postgres connection these deployments can open, added up and compared
with what the servers underneath will accept -- capacity step 2.3
(docs/capacity-plan.md §5 wave 2). The ledger this computes is written out in
prose in `docs/design/08-local-runbook.md` §2-ب, and the last test here is what
stops the two from drifting apart.

THE ARITHMETIC WAS ALWAYS THERE; NOBODY HAD DONE IT. Four numbers decide it and
they live in four different files: `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` and
`WEB_CONCURRENCY` in `docker-compose.yml` and `.env.example`, `_WORKER_POOL_SIZE`
and `_RELAY_POOL_SIZE` in `app/workers/bootstrap.py`, `_METRICS_POOL_SIZE` in
the composition root, and `MAX_CLIENT_CONN`/`DEFAULT_POOL_SIZE` on the pooler.
Nothing multiplied them together, so `ح-3` ("25 concurrent transactions for
`app_rw` across all processes") was an assertion in a planning document rather
than a fact anyone could check. This module multiplies them.

⭐ THERE ARE TWO CEILINGS, NOT ONE, AND STEP 2.3'S OWN WORDING NAMES ONLY THE
FIRST. `MAX_CLIENT_CONN` bounds how many clients may connect TO the pooler;
`max_connections` bounds how many backends the pooler may open BEHIND it. They
are independent, they are set in different files by different people, and today
they are wrong in opposite directions -- the client ceiling is roughly six times
the demand while `DEFAULT_POOL_SIZE` is a quarter of it. A guard that checked
only the first would pass a stack whose real constraint it never looked at.

⭐ AND THERE ARE TWO DEPLOYMENTS. `deploy/runpod/` runs the same three processes
with NO POOLER AT ALL (`08 §2`: "اتّصالٌ مباشرٌ على 127.0.0.1:5432"), so every
pool slot there is a real Postgres backend and the multiplexing that gives the
Compose stack its slack does not exist. That is the tighter of the two by a wide
margin -- see `test_raising_web_concurrency_bites_five_turns_sooner_on_runpod`,
which is the concrete answer to a question `deploy/runpod/entrypoint.sh:139`
invites an operator to ask and nothing anywhere answered.

WHAT IS DELIBERATELY NOT COUNTED, and why it is a modelled fact rather than an
omission: `app.ops.provision` (the `migrate` service, and RunPod's bootstrap)
opens a `NullPool` connection, but it runs to COMPLETION before any standing
service starts -- every one of them declares `migrate:
service_completed_successfully`. Its connection never coexists with the steady
state, so adding it would inflate the sum with a term that cannot be
simultaneous. `test_the_pre_flight_migrator_really_does_finish_first` is what
keeps that true; the day someone relaxes that condition, the exclusion stops
being free and the test says so. The other one-shot tools (`app.ops.retention`,
`rotate_transit`, `purge`, `slow_queries`, `load_seed`) are `NullPool` too and
have no standing process at all -- 08 §4's invocations are manual.

Same shape as `test_deploy_worker_default.py` and
`test_role_provisioning_wiring.py`: several files had to agree and nothing
compared them. And the `.env.example` half is not optional for the reason those
two both record -- Compose auto-loads `.env`, every guide's first step is `cp
.env.example .env`, and this repository has already been bitten once by a value
there quietly beating an inline `${VAR:-...}` fallback.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from app.framework.di.composition_root import _METRICS_MAX_OVERFLOW, _METRICS_POOL_SIZE
from app.workers.bootstrap import (
    _RELAY_MAX_OVERFLOW,
    _RELAY_POOL_SIZE,
    _WORKER_MAX_OVERFLOW,
    _WORKER_POOL_SIZE,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_RUNPOD_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_RUNPOD_SUPERVISORD = _REPO_ROOT / "deploy" / "runpod" / "supervisord.conf"
_RUNBOOK = _REPO_ROOT / "docs" / "design" / "08-local-runbook.md"

# Postgres's own defaults, restated here because NEITHER deployment's Compose
# service sets them and a ceiling nobody wrote down is still a ceiling. If 2.1
# ever adds `max_connections` to the `postgres` command, `_pg_max_connections`
# below reads it from there instead and this constant stops being consulted.
_PG_DEFAULT_MAX_CONNECTIONS = 100
# `superuser_reserved_connections`, also a default. It matters to the sum: the
# three application roles are NOT superusers, so the slots they may compete for
# is `max_connections` MINUS this, and budgeting against the raw number
# overstates the headroom by exactly three every time.
_PG_SUPERUSER_RESERVED = 3

# Two standing client sessions into the pooler that no `DATABASE_URL`
# describes, so no parser can find them:
#
#   * `pgbouncer-exporter` holds one against the VIRTUAL `pgbouncer` admin
#     database. It is a client connection (it counts against MAX_CLIENT_CONN)
#     and it is NOT a server one -- SHOW POOLS is answered by the pooler and
#     never reaches Postgres, as that service's own comment says.
#   * the pooler's healthcheck runs `psql -p 6432` on the REAL database as the
#     superuser, so it is both: one client, and one backend in a fourth
#     (user, database) pool that the three application roles do not share.
#
# `test_the_admin_sessions_this_ledger_counts_still_exist` is what keeps these
# two literals honest.
_EXPORTER_ADMIN_CLIENTS = 1
_POOLER_HEALTHCHECK_CLIENTS = 1

_POOLER_ENDPOINT = "pgbouncer:6432"

# The pool each entrypoint opens for its `DATABASE_URL`. Keyed by the module
# the process RUNS, never by the service name: `worker-memory`,
# `worker-knowledge` and `worker-media` are three services and one program, and
# a name-keyed table would have to be edited on the day a fourth is added while
# this one already knows the answer.
_ENTRYPOINT_POOLS: Mapping[str, tuple[int, int]] = {
    "app.workers.main": (_WORKER_POOL_SIZE, _WORKER_MAX_OVERFLOW),
    "app.workers.outbox_relay": (_RELAY_POOL_SIZE, _RELAY_MAX_OVERFLOW),
}

# Runs to completion before anything standing starts -- see the module
# docstring's "WHAT IS DELIBERATELY NOT COUNTED".
_PRE_FLIGHT_MODULES = frozenset({"app.ops.provision"})

# Operator commands that happen to have a Compose service (capacity step 2.5).
# `app.ops.retention`, `rotate_transit`, `purge`, `slow_queries` and
# `load_seed` are the same class and never appeared here, simply because none
# of them has a service definition -- 08 §4 invokes them with `exec`.
# `app.ops.backup` is the first that does, so the ledger needs the category
# rather than an exception.
#
# THREE PROPERTIES EARN THE EXCLUSION, AND `test_the_manual_tools_really_are_
# out_of_the_standing_budget` ASSERTS ALL THREE rather than trusting this
# comment: the service sits behind a `profiles:` key (so `docker compose up
# -d` never starts it), the module opens `NullPool` engines (one backend at a
# time, from the thirty this ledger already reserves for administration), and
# its DSN goes DIRECT to Postgres -- so it occupies no MAX_CLIENT_CONN seat in
# front of the pooler at all.
_MANUAL_MODULES = frozenset({"app.ops.backup"})

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|:\?)([^}]*))?\}")
_DSN_PATTERN = re.compile(
    r"^postgresql\+asyncpg://(?P<role>[^:]+):[^@]*@(?P<endpoint>[^/]+)/",
)
_EXPORT_PATTERN = re.compile(
    r'^export (?P<name>[A-Z_][A-Z0-9_]*)="\$\{(?P=name):-(?P<value>[^}"]*)\}"',
    re.MULTILINE,
)
_PROGRAM_PATTERN = re.compile(
    r"^\[program:(?P<name>[^\]]+)\]\n(?P<body>(?:(?!\[program:).*\n)*)",
    re.MULTILINE,
)
_COMMAND_PATTERN = re.compile(r"^command=(?P<command>.+)$", re.MULTILINE)
_RUNPOD_MAX_CONNECTIONS = re.compile(r'echo "max_connections = (?P<value>\d+)"')

# RunPod's own virtualenv for the application. `embedding` runs out of
# `/opt/venv-emb` and the bootstrap out of `/usr/local/bin`, so this prefix is
# exactly "the processes that can hold a SQLAlchemy pool" and nothing else.
_RUNPOD_APP_VENV = "/opt/venv/bin/"


# --------------------------------------------------------------- the model --


@dataclass(frozen=True)
class _Pool:
    """One SQLAlchemy engine inside one process."""

    role: str
    endpoint: str
    size: int
    overflow: int

    @property
    def connections(self) -> int:
        """The most connections this engine can hold open at once."""
        return self.size + self.overflow


@dataclass(frozen=True)
class _Runner:
    """One kind of process, and how many of it run."""

    name: str
    processes: int
    pools: tuple[_Pool, ...]

    def clients(self, endpoint: str) -> int:
        return self.processes * sum(p.connections for p in self.pools if p.endpoint == endpoint)


@dataclass(frozen=True)
class _Topology:
    runners: tuple[_Runner, ...]
    unaccounted: tuple[str, ...]

    def with_web_concurrency(self, processes: int) -> _Topology:
        """The same stack with the API server's sibling count changed -- what
        `test_raising_web_concurrency_*` needs in order to prove the guard
        BITES rather than merely passing on today's values."""
        return replace(
            self,
            runners=tuple(
                replace(r, processes=processes) if r.pools and _is_api(r) else r
                for r in self.runners
            ),
        )


def _is_api(runner: _Runner) -> bool:
    """The API server is the runner with more than one pool -- it is the only
    process that opens a second engine (`METRICS_DATABASE_URL`)."""
    return len(runner.pools) > 1


# ------------------------------------------------------------- the parsing --


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _resolve(raw: str, env: Mapping[str, str]) -> str:
    """Compose's own `${VAR}` / `${VAR:-default}` / `${VAR:?msg}` rules, with
    `.env` supplied by `.env.example` -- the file every deployment guide copies."""

    def one(match: re.Match[str]) -> str:
        name, operator, tail = match.group(1), match.group(2), match.group(3)
        if env.get(name):
            return env[name]
        return tail if operator == ":-" else ""

    return _VAR_PATTERN.sub(one, raw)


def _pool_from_dsn(raw: str, size: int, overflow: int, env: Mapping[str, str]) -> _Pool:
    match = _DSN_PATTERN.match(_resolve(raw, env))
    assert match is not None, f"not an asyncpg DSN: {raw}"
    return _Pool(
        role=match.group("role"),
        endpoint=match.group("endpoint"),
        size=size,
        overflow=overflow,
    )


def _module_of(command: object) -> str | None:
    """The module a `python -m ...` entrypoint runs, or None for anything else
    (in both files that means the gunicorn API server)."""
    parts = command if isinstance(command, list) else str(command or "").split()
    if "-m" not in parts:
        return None
    return parts[parts.index("-m") + 1]


def _api_pools(
    dsn_keys: list[str], env: Mapping[str, str], defaults: Mapping[str, str]
) -> tuple[_Pool, ...]:
    """The API server's two engines: `app_rw` at the deployment's own
    `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`, and `metrics_reader` at the composition
    root's own constants."""
    size = int(_resolve(str(env["DB_POOL_SIZE"]), defaults))
    overflow = int(_resolve(str(env["DB_MAX_OVERFLOW"]), defaults))
    pools = [_pool_from_dsn(str(env["DATABASE_URL"]), size, overflow, defaults)]
    for key in dsn_keys:
        if key == "DATABASE_URL":
            continue
        pools.append(
            _pool_from_dsn(str(env[key]), _METRICS_POOL_SIZE, _METRICS_MAX_OVERFLOW, defaults)
        )
    return tuple(pools)


def _compose_topology() -> _Topology:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    defaults = _env_example()
    runners: list[_Runner] = []
    unaccounted: list[str] = []

    for name, service in sorted(compose["services"].items()):
        env = service.get("environment") or {}
        if not isinstance(env, dict):
            continue
        dsn_keys = sorted(key for key in env if key.endswith("DATABASE_URL"))
        if not dsn_keys:
            continue
        module = _module_of(service.get("command"))
        if module in _PRE_FLIGHT_MODULES or module in _MANUAL_MODULES:
            continue
        replicas = int((service.get("deploy") or {}).get("replicas", 1))
        if module is None:
            processes = int(_resolve(str(env["WEB_CONCURRENCY"]), defaults))
            pools = _api_pools(dsn_keys, env, defaults)
        elif module in _ENTRYPOINT_POOLS:
            size, overflow = _ENTRYPOINT_POOLS[module]
            processes = 1
            pools = (_pool_from_dsn(str(env["DATABASE_URL"]), size, overflow, defaults),)
        else:
            unaccounted.append(f"{name} ({module})")
            continue
        runners.append(_Runner(name=name, processes=processes * replicas, pools=pools))

    return _Topology(runners=tuple(runners), unaccounted=tuple(unaccounted))


def _runpod_exports() -> dict[str, str]:
    text = _RUNPOD_ENTRYPOINT.read_text(encoding="utf-8")
    return {m.group("name"): m.group("value") for m in _EXPORT_PATTERN.finditer(text)}


def _runpod_topology() -> _Topology:
    supervisord = _RUNPOD_SUPERVISORD.read_text(encoding="utf-8")
    exports = _runpod_exports()
    # RunPod exports one DSN per role rather than naming them per program, so
    # the endpoint is read from the exports and the role from the entrypoint
    # each program runs -- the same rule as Compose, different file.
    entrypoint = _RUNPOD_ENTRYPOINT.read_text(encoding="utf-8")
    dsns = dict(re.findall(r'^export ([A-Z_]*DATABASE_URL)="([^"]+)"', entrypoint, re.MULTILINE))
    runners: list[_Runner] = []
    unaccounted: list[str] = []

    for match in _PROGRAM_PATTERN.finditer(supervisord):
        command_match = _COMMAND_PATTERN.search(match.group("body"))
        if command_match is None:
            continue
        command = command_match.group("command")
        if not command.startswith(_RUNPOD_APP_VENV):
            continue
        module = _module_of(command)
        if module in _PRE_FLIGHT_MODULES:
            continue
        if module is None:
            processes = int(exports["WEB_CONCURRENCY"])
            env = {
                "DB_POOL_SIZE": exports["DB_POOL_SIZE"],
                "DB_MAX_OVERFLOW": exports["DB_MAX_OVERFLOW"],
                "DATABASE_URL": dsns["DATABASE_URL"],
                "METRICS_DATABASE_URL": dsns["METRICS_DATABASE_URL"],
            }
            dsn_keys = sorted(key for key in env if key.endswith("DATABASE_URL"))
            pools = _api_pools(dsn_keys, env, exports)
        elif module in _ENTRYPOINT_POOLS:
            size, overflow = _ENTRYPOINT_POOLS[module]
            processes = 1
            key = "RELAY_DATABASE_URL" if "relay" in module else "DATABASE_URL"
            pools = (_pool_from_dsn(dsns[key], size, overflow, exports),)
        else:
            unaccounted.append(f"{match.group('name')} ({module})")
            continue
        runners.append(_Runner(name=match.group("name"), processes=processes, pools=pools))

    return _Topology(runners=tuple(runners), unaccounted=tuple(unaccounted))


def _pgbouncer_env() -> dict[str, str]:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return {k: str(v) for k, v in compose["services"]["pgbouncer"]["environment"].items()}


def _pg_max_connections() -> int:
    """Read from the `postgres` service's own command if 2.1 has set it there,
    and from Postgres's default if not -- so this ledger follows that step
    rather than having to be edited by it."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    for token in compose["services"]["postgres"].get("command", []):
        if str(token).startswith("max_connections="):
            return int(str(token).split("=", 1)[1])
    return _PG_DEFAULT_MAX_CONNECTIONS


# ---------------------------------------------------------------- the sums --


def _pooler_clients(topology: _Topology) -> int:
    """Everything holding a client connection INTO the pooler."""
    return (
        sum(runner.clients(_POOLER_ENDPOINT) for runner in topology.runners)
        + _EXPORTER_ADMIN_CLIENTS
        + _POOLER_HEALTHCHECK_CLIENTS
    )


def _demand_by_role(topology: _Topology) -> dict[str, int]:
    demand: dict[str, int] = {}
    for runner in topology.runners:
        for pool in runner.pools:
            if pool.endpoint != _POOLER_ENDPOINT:
                continue
            demand[pool.role] = demand.get(pool.role, 0) + runner.processes * pool.connections
    return demand


def _server_backends(topology: _Topology, default_pool_size: int) -> int:
    """The backends the pooler can have open at once.

    ⚠️ `min(demand, DEFAULT_POOL_SIZE)` per (role, database) pool, NOT the sum
    of the pool maxima. Under transaction pooling a server connection exists
    only for a client inside a transaction, so a pool never opens more backends
    than it has clients -- summing the maxima would report 175 against a
    ceiling of 97 and declare a stack broken that cannot reach a quarter of
    that. The maxima matter to a DIFFERENT question (what an unbounded
    `MAX_DB_CONNECTIONS` would permit if demand ever rose), which is step 2.2's
    to answer and which 08 §2-ب states rather than asserts.
    """
    return (
        sum(min(demand, default_pool_size) for demand in _demand_by_role(topology).values())
        + _POOLER_HEALTHCHECK_CLIENTS
    )


def _direct_backends(topology: _Topology) -> int:
    """RunPod has no pooler, so every pool slot is a backend."""
    return sum(
        runner.processes * sum(pool.connections for pool in runner.pools)
        for runner in topology.runners
    )


def _highest_fitting_web_concurrency(
    topology: _Topology, ceiling: int, measure: Callable[[_Topology], int]
) -> int:
    """The largest sibling count that still fits. A loop rather than algebra
    because the API server is not the only term in either sum, and an operator
    raising the knob wants the number rather than the formula."""
    fitting = 0
    for candidate in range(1, 129):
        if measure(topology.with_web_concurrency(candidate)) <= ceiling:
            fitting = candidate
        else:
            break
    return fitting


# ---------------------------------------------------------- what is counted --


def test_the_manual_tools_really_are_out_of_the_standing_budget() -> None:
    """`_MANUAL_MODULES` is an exclusion, and an exclusion nobody checks is a
    hole. Each of its services must be behind a profile (so `up -d` never
    starts it), and must reach Postgres DIRECTLY -- a manual tool pointed at
    the pooler would take a MAX_CLIENT_CONN seat this ledger did not count."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    excluded: set[str] = set()
    for name, service in sorted(compose["services"].items()):
        module = _module_of(service.get("command"))
        env = service.get("environment") or {}
        dsn_keys = [key for key in env if key.endswith("DATABASE_URL")]
        # Only a service that actually carries a DSN is excluded BY this set;
        # `wal-shipper` runs the same module and holds no database credential
        # at all, so the topology walk drops it one line earlier.
        if module not in _MANUAL_MODULES or not dsn_keys:
            continue
        excluded.add(module)
        assert service.get("profiles"), f"{name} must sit behind a profile to be excluded"
        for key in dsn_keys:
            assert _POOLER_ENDPOINT not in str(env[key]), (
                f"{name}'s {key} goes through the pooler, so it DOES take a client seat"
            )

    assert excluded == set(_MANUAL_MODULES)


def test_every_database_touching_service_is_accounted_for() -> None:
    """The ledger is only worth its ceiling if nothing escapes it. A service
    that grows a `DATABASE_URL` under an entrypoint this module does not know
    fails HERE, on the day it is written, rather than being quietly absent from
    a sum that keeps reading as green."""
    compose = _compose_topology()
    runpod = _runpod_topology()

    assert compose.unaccounted == (), (
        f"Compose service(s) {compose.unaccounted} open a database connection under an "
        "entrypoint the connection budget does not model. Add it to `_ENTRYPOINT_POOLS` "
        "(a standing pool) or `_PRE_FLIGHT_MODULES` (runs to completion first), and "
        "update docs/design/08-local-runbook.md §2-ب to match."
    )
    assert runpod.unaccounted == (), f"RunPod program(s) {runpod.unaccounted} are unmodelled"


def test_the_pre_flight_migrator_really_does_finish_first() -> None:
    """`app.ops.provision` is left out of both sums because it CANNOT be
    concurrent with the steady state -- and that is true only while every
    standing database service waits for it to complete. This is the condition
    the exclusion rests on, checked rather than assumed."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    standing = {runner.name for runner in _compose_topology().runners}
    assert standing, "no standing services found -- the parser matched nothing"

    for name in sorted(standing):
        depends = compose["services"][name].get("depends_on") or {}
        assert depends.get("migrate", {}).get("condition") == "service_completed_successfully", (
            f"`{name}` no longer waits for `migrate` to COMPLETE, so the migrator's "
            "connection can now coexist with the steady state and the connection budget "
            "must start counting it (tests/unit/test_connection_budget.py)."
        )


def test_the_admin_sessions_this_ledger_counts_still_exist() -> None:
    """Two client sessions no `DATABASE_URL` describes, carried as literals.
    Both are load-bearing terms, so both are pinned to the thing that creates
    them."""
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    exporter = compose["services"]["pgbouncer-exporter"]["environment"]
    connection_string = str(exporter["PGBOUNCER_EXPORTER_CONNECTION_STRING"])
    assert f"@{_POOLER_ENDPOINT}/pgbouncer" in connection_string, (
        "the exporter no longer holds a session on the pooler's admin database -- "
        "`_EXPORTER_ADMIN_CLIENTS` is now wrong"
    )

    probe = " ".join(str(part) for part in compose["services"]["pgbouncer"]["healthcheck"]["test"])
    assert "-p 6432" in probe, (
        "the pooler's healthcheck no longer crosses the pool, so it no longer holds a "
        "client connection or a backend -- `_POOLER_HEALTHCHECK_CLIENTS` is now wrong"
    )


# --------------------------------------------- ceiling one: pooler clients --


def test_the_compose_stack_stays_inside_the_poolers_client_ceiling() -> None:
    topology = _compose_topology()
    ceiling = int(_pgbouncer_env()["MAX_CLIENT_CONN"])
    demand = _pooler_clients(topology)

    assert demand <= ceiling, (
        f"the Compose stack can open {demand} client connections into a pooler that "
        f"accepts {ceiling}. Raise MAX_CLIENT_CONN or lower the pools; the arithmetic "
        "is in docs/design/08-local-runbook.md §2-ب."
    )


def test_raising_web_concurrency_alone_eventually_breaks_the_client_ceiling() -> None:
    """Step 2.3's acceptance criterion, stated as the plan states it: the guard
    must FAIL when `WEB_CONCURRENCY` rises without the pooler's ceiling rising
    with it. A guard that only passes on today's values has never been shown to
    be able to fail."""
    topology = _compose_topology()
    ceiling = int(_pgbouncer_env()["MAX_CLIENT_CONN"])
    highest = _highest_fitting_web_concurrency(topology, ceiling, _pooler_clients)

    assert _pooler_clients(topology.with_web_concurrency(highest)) <= ceiling
    assert _pooler_clients(topology.with_web_concurrency(highest + 1)) > ceiling, (
        "raising WEB_CONCURRENCY past the ledger's own maximum did not exceed "
        "MAX_CLIENT_CONN -- the guard cannot fail, so it is not guarding anything"
    )


# --------------------------------------------- ceiling two: postgres backends --


def test_the_pooler_cannot_ask_postgres_for_more_backends_than_it_has() -> None:
    """The ceiling step 2.3's own wording does not name. `MAX_CLIENT_CONN` says
    nothing about it: the pooler will happily accept five hundred clients and
    then discover that Postgres takes ninety-seven."""
    topology = _compose_topology()
    default_pool_size = int(_pgbouncer_env()["DEFAULT_POOL_SIZE"])
    ceiling = _pg_max_connections() - _PG_SUPERUSER_RESERVED
    backends = _server_backends(topology, default_pool_size)

    assert backends <= ceiling, (
        f"the pooler can open {backends} backends against a server that allows {ceiling} "
        f"to non-superusers ({_pg_max_connections()} minus {_PG_SUPERUSER_RESERVED} "
        "reserved). Step 2.2's MAX_DB_CONNECTIONS is the knob that bounds this "
        "regardless of demand."
    )


def test_app_rw_is_the_role_the_pooler_actually_throttles() -> None:
    """`ح-3` as a measured number rather than a claim: `app_rw`'s clients want
    far more concurrent transactions than `DEFAULT_POOL_SIZE` grants, and every
    one over the line waits. This is what makes 2.2 a real step and not a
    round-number bump -- and what makes `QUERY_WAIT_TIMEOUT` matter, since the
    waiting is silent today."""
    topology = _compose_topology()
    default_pool_size = int(_pgbouncer_env()["DEFAULT_POOL_SIZE"])
    demand = _demand_by_role(topology)

    assert set(demand) == {"app_rw", "metrics_reader", "outbox_relay"}, (
        f"the set of roles reaching Postgres through the pooler changed: {sorted(demand)}"
    )
    assert demand["app_rw"] > default_pool_size, (
        "app_rw no longer over-subscribes its pool -- ح-3 has stopped being the "
        "binding constraint and 08 §2-ب's ledger needs rewriting, not patching"
    )
    for role in ("metrics_reader", "outbox_relay"):
        assert demand[role] <= default_pool_size, (
            f"`{role}` now over-subscribes the pool too; the ledger names app_rw as the "
            "only over-subscribed role"
        )


# ------------------------------------------ ceiling three: RunPod, no pooler --


def test_the_runpod_image_stays_inside_its_own_max_connections() -> None:
    topology = _runpod_topology()
    declared = _RUNPOD_MAX_CONNECTIONS.search(_RUNPOD_ENTRYPOINT.read_text(encoding="utf-8"))
    assert declared is not None, "deploy/runpod/entrypoint.sh no longer sets max_connections"
    ceiling = int(declared.group("value")) - _PG_SUPERUSER_RESERVED
    backends = _direct_backends(topology)

    assert backends <= ceiling, (
        f"the RunPod image opens {backends} direct backends against {ceiling} available. "
        "There is no pooler in that image to multiplex them."
    )


def test_raising_web_concurrency_bites_five_turns_sooner_on_runpod() -> None:
    """⭐ The finding this step exists to make visible. The same knob, in the
    same repository, has an order of magnitude more room on one deployment than
    the other -- because Compose puts a pooler between the app and Postgres and
    the RunPod image does not. `deploy/runpod/entrypoint.sh` invites an
    operator to override `WEB_CONCURRENCY` and states no ceiling; this is the
    ceiling, and 08 §2-ب writes both numbers down."""
    compose = _compose_topology()
    runpod = _runpod_topology()
    declared = _RUNPOD_MAX_CONNECTIONS.search(_RUNPOD_ENTRYPOINT.read_text(encoding="utf-8"))
    assert declared is not None

    compose_room = _highest_fitting_web_concurrency(
        compose, int(_pgbouncer_env()["MAX_CLIENT_CONN"]), _pooler_clients
    )
    runpod_room = _highest_fitting_web_concurrency(
        runpod, int(declared.group("value")) - _PG_SUPERUSER_RESERVED, _direct_backends
    )

    assert runpod_room < compose_room, (
        "the RunPod image is no longer the tighter of the two deployments; 08 §2-ب "
        "presents it as the one an operator must budget against"
    )
    assert (
        _direct_backends(runpod.with_web_concurrency(runpod_room + 1))
        > int(declared.group("value")) - _PG_SUPERUSER_RESERVED
    )


# ------------------------------------------------------ the written ledger --


# The heading is spelled with a NON-BREAKING hyphen (U+2011), because
# 08-local-runbook.md spells every one of its Arabic sub-headings with one and
# an ASCII hyphen here would find nothing. Written as an escape rather than the
# character itself: ruff's RUF001 flags the literal, correctly -- the two are
# indistinguishable on screen, which is the whole reason this comment exists.
_LEDGER_HEADING = "### 2\u2011ب"
_LEDGER_LINE = re.compile(r"^(?P<key>[a-z_]+\.[a-z_]+)\s*=\s*(?P<value>\d+)", re.MULTILINE)


def _written_ledger() -> dict[str, int]:
    """The `key = value` block inside 08 §2-ب. A parsed block rather than a
    substring search over the prose: looking for "5" in a page that also says
    "5432" passes for the wrong reason, and a guard that can pass vacuously is
    the failure mode this whole module exists to remove."""
    text = _RUNBOOK.read_text(encoding="utf-8")
    start = text.index(_LEDGER_HEADING)
    section = text[start : text.index("\n## ", start)]
    return {m.group("key"): int(m.group("value")) for m in _LEDGER_LINE.finditer(section)}


def test_the_runbook_ledger_carries_the_numbers_this_module_computes() -> None:
    """Step 2.3 is "the arithmetic is WRITTEN DOWN and guarded". A ledger whose
    numbers no longer match the files it describes is worse than none -- it
    reads as authoritative. So every figure in §2-ب is recomputed from the
    deployment files here and compared with what the document claims."""
    compose = _compose_topology()
    runpod = _runpod_topology()
    declared = _RUNPOD_MAX_CONNECTIONS.search(_RUNPOD_ENTRYPOINT.read_text(encoding="utf-8"))
    assert declared is not None
    runpod_ceiling = int(declared.group("value")) - _PG_SUPERUSER_RESERVED
    client_ceiling = int(_pgbouncer_env()["MAX_CLIENT_CONN"])

    computed = {
        "compose.pooler_clients": _pooler_clients(compose),
        "compose.postgres_backends": _server_backends(
            compose, int(_pgbouncer_env()["DEFAULT_POOL_SIZE"])
        ),
        "compose.app_rw_demand": _demand_by_role(compose)["app_rw"],
        "compose.web_concurrency_max": _highest_fitting_web_concurrency(
            compose, client_ceiling, _pooler_clients
        ),
        "runpod.postgres_backends": _direct_backends(runpod),
        "runpod.web_concurrency_max": _highest_fitting_web_concurrency(
            runpod, runpod_ceiling, _direct_backends
        ),
    }

    assert _written_ledger() == computed, (
        "docs/design/08-local-runbook.md §2-ب's ledger has drifted from the files it "
        f"describes. Computed: {computed}"
    )


def test_env_example_states_the_ceiling_next_to_the_knob() -> None:
    """The operator who raises `WEB_CONCURRENCY` reads `.env.example`, not the
    runbook. The same reasoning as 1.2 and 1.3 put their guards on the route
    rather than in a document: the fact belongs where the mistake is made."""
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    index = text.index("WEB_CONCURRENCY=")
    preamble = text[max(0, index - 1200) : index]
    assert "08-local-runbook" in preamble and _LEDGER_HEADING[4:] in preamble, (
        "`.env.example`'s WEB_CONCURRENCY has no comment pointing at the connection "
        "budget -- raising it is exactly the edit that can exhaust Postgres"
    )


def test_the_patterns_actually_find_something() -> None:
    """Every parser here can silently match nothing and leave a green sum of
    zero. The `test_deploy_worker_default.py` precedent, applied to five
    patterns rather than one."""
    compose = _compose_topology()
    runpod = _runpod_topology()

    assert len(compose.runners) == 5, [r.name for r in compose.runners]
    assert len(runpod.runners) == 3, [r.name for r in runpod.runners]
    assert _pooler_clients(compose) > 0
    assert _direct_backends(runpod) > 0
    # The KEY, not its value: the value is what the ceiling tests above judge,
    # and asserting it here would make this sentinel fail for the wrong reason
    # the day someone legitimately changes the default.
    assert int(_runpod_exports()["WEB_CONCURRENCY"]) >= 1
    assert set(_ENTRYPOINT_POOLS) == {"app.workers.main", "app.workers.outbox_relay"}
