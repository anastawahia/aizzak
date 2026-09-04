"""Every bound on one database call's life, laid end to end and compared with
the bounds underneath it -- capacity step 2.6 (docs/capacity-plan.md §5 wave 2).
The sibling of `test_connection_budget.py`: that one adds the pools up, this one
orders their timeouts.

A DATABASE CALL PASSES THROUGH FIVE PHASES AND EACH HAS ITS OWN CLOCK. Waiting
for a slot in this process's pool (`pool_timeout`); the age of the connection it
gets (`pool_recycle`); waiting for a free SERVER connection behind the pooler
(`query_wait_timeout`); executing on a backend (`statement_timeout`); and
holding that backend with nothing running on it
(`idle_in_transaction_session_timeout`). Before 2.6 exactly one of the five was
set anywhere, and it was set by SQLAlchemy's own undeclared default.

⭐ THE ORDERING IS THE POINT, NOT THE VALUES. Every client that reaches Postgres
here carries its own per-path budget; PgBouncer carries a second, looser set for
clients that do not. Those two sets must not cross, and the reason is
diagnostic rather than arithmetic: a per-path bound fails with a SQLSTATE that
names the phase (`57014`, `25P03`), while the pooler ends a wait by closing the
client socket -- measured, asyncpg reports "connection was closed in the middle
of operation", which is true and tells an operator nothing. Whenever a pooler
ceiling drops below a client budget, every incident of that kind gets diagnosed
as a network fault. These tests are what stop the two sets from crossing.

⭐ AND THERE ARE TWO DEPLOYMENTS, exactly as in the budget ledger.
`deploy/runpod/` runs the same processes with NO POOLER AT ALL (`08 §2`), so
none of the backstops exist there and the per-path budgets are the whole of it.
That makes the RunPod exports load-bearing rather than decorative, which is what
`test_runpod_exports_the_same_four_timeouts` is for.

Same shape as `test_connection_budget.py` and `test_deploy_worker_default.py`:
several files have to agree and nothing compared them. The `.env.example` half
is not optional for the reason those record -- Compose auto-loads `.env`, every
guide's first step is `cp .env.example .env`, and a value there quietly beating
an inline `${VAR:-...}` fallback has bitten this repository before.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.framework.settings.settings import DatabaseSettings
from app.infrastructure.config.env_settings import _EnvSettings
from app.infrastructure.persistence.database import _budget_sql
from app.workers.bootstrap import (
    _BACKGROUND_IDLE_IN_TRANSACTION_TIMEOUT_MS,
    _BACKGROUND_POOL_TIMEOUT_S,
    _BACKGROUND_STATEMENT_TIMEOUT_MS,
    _worker_db,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_RUNPOD_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_NGINX_LOCATIONS = _REPO_ROOT / "deploy" / "nginx" / "app-locations.conf"

# The four keys the request path is configured with, and the one of them that
# is shared with every other process (it answers to the pooler, not to the
# process -- see the `x-app-env` comment beside it).
_REQUEST_KEYS = (
    "DB_POOL_TIMEOUT_S",
    "DB_POOL_RECYCLE_S",
    "DB_STATEMENT_TIMEOUT_MS",
    "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
)
_SHARED_KEY = "DB_POOL_RECYCLE_S"

# Read off `_EnvSettings`' own field defaults rather than by calling
# `load_settings()`: this file compares what the REPOSITORY ships, and a
# developer's `.env` overriding a key locally must not turn a drift check into
# a machine-specific failure.
_ENV_FIELD_OF = {
    "DB_POOL_TIMEOUT_S": "db_pool_timeout_s",
    "DB_POOL_RECYCLE_S": "db_pool_recycle_s",
    "DB_STATEMENT_TIMEOUT_MS": "db_statement_timeout_ms",
    "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS": "db_idle_in_transaction_timeout_ms",
}

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:-|:\?)([^}]*))?\}")
_EXPORT_PATTERN = re.compile(
    r'^export (?P<name>[A-Z_][A-Z0-9_]*)="\$\{(?P=name):-(?P<value>[^}"]*)\}"',
    re.MULTILINE,
)
# `location / { ... proxy_read_timeout 300s; }` -- the edge hop a hung database
# call is stuck behind. Deliberately NOT the `/api/v1/ws` block above it, which
# is an hour long because a socket that stays open is what it is for.
_PROXY_READ_TIMEOUT = re.compile(
    r"location / \{(?:[^}]*?)proxy_read_timeout\s+(?P<seconds>\d+)s;", re.DOTALL
)


# ------------------------------------------------------------- the parsing --


def _shipped_defaults() -> dict[str, float]:
    """What a process gets with nothing set -- the loader's own field defaults."""
    return {
        key: float(_EnvSettings.model_fields[field].default) for key, field in _ENV_FIELD_OF.items()
    }


def _env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _resolve(raw: str) -> str:
    """Compose's `${VAR}` / `${VAR:-default}` rules with `.env` unset -- the
    INLINE fallback alone, which is what a deployment that never copied
    `.env.example` actually runs on."""

    def one(match: re.Match[str]) -> str:
        return match.group(3) if match.group(2) == ":-" else ""

    return _VAR_PATTERN.sub(one, raw)


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _app_env() -> dict[str, str]:
    """Everything the `app` service is handed, shared block included."""
    compose = _compose()
    env = dict(compose["x-app-env"])
    env.update({k: v for k, v in compose["services"]["app"]["environment"].items() if k != "<<"})
    return {k: str(v) for k, v in env.items()}


def _pgbouncer_env() -> dict[str, str]:
    return {k: str(v) for k, v in _compose()["services"]["pgbouncer"]["environment"].items()}


def _runpod_exports() -> dict[str, str]:
    text = _RUNPOD_ENTRYPOINT.read_text(encoding="utf-8")
    return {m.group("name"): m.group("value") for m in _EXPORT_PATTERN.finditer(text)}


# ------------------------------------------------- the four keys, one value --


def test_every_request_path_timeout_is_declared_in_all_three_places() -> None:
    """`.env.example`, the Compose fallback and the code default are three
    independent copies of one number, and this is the only thing that has ever
    compared them."""
    defaults = _env_example()
    app_env = _app_env()
    code = _shipped_defaults()
    for key in _REQUEST_KEYS:
        assert key in defaults, f"{key} is not in .env.example"
        assert key in app_env, f"{key} never reaches the app container"
        inline = float(_resolve(app_env[key]))
        assert inline == float(defaults[key]), (
            f"{key}: docker-compose.yml falls back to {inline}, .env.example says "
            f"{defaults[key]} -- and .env wins, so the fallback is the lie"
        )
        assert inline == float(code[key]), (
            f"{key}: the deployment says {inline}, the code default says {code[key]}"
        )


def test_only_pool_recycle_is_shared_with_the_other_processes() -> None:
    """The three request-path budgets belong to the API server alone (the
    `DB_POOL_SIZE` precedent); `DB_POOL_RECYCLE_S` is shared because it is the
    only one that answers to something outside the process -- the pooler's own
    `client_idle_timeout`, which is the same pooler for all of them."""
    shared = {k: str(v) for k, v in _compose()["x-app-env"].items()}
    assert _SHARED_KEY in shared
    for key in _REQUEST_KEYS:
        if key != _SHARED_KEY:
            assert key not in shared, (
                f"{key} is in x-app-env, so the relay and the three workers now read "
                "the API server's budget instead of the background one"
            )


def test_runpod_exports_the_same_four_timeouts() -> None:
    """The second deployment (`08 §2`) has NO POOLER, so every backstop below
    is absent there and these four are the whole of its protection."""
    exports = _runpod_exports()
    defaults = _env_example()
    for key in _REQUEST_KEYS:
        assert key in exports, f"deploy/runpod/entrypoint.sh never exports {key}"
        assert float(exports[key]) == float(defaults[key]), (
            f"{key}: RunPod exports {exports[key]}, Compose deploys {defaults[key]}"
        )


# --------------------------------------------------------------- the ladder --


def test_the_pooler_backstops_all_sit_above_the_per_path_budgets() -> None:
    """The crossing this module exists to prevent. Each per-path bound must
    fire BEFORE the pooler-wide one covering the same phase, or every incident
    gets diagnosed as "connection was closed in the middle of operation"."""
    pooler = _pgbouncer_env()
    shipped = _shipped_defaults()

    client_idle_s = int(pooler["CLIENT_IDLE_TIMEOUT"])
    assert shipped["DB_POOL_RECYCLE_S"] < client_idle_s, (
        f"pool_recycle {shipped['DB_POOL_RECYCLE_S']}s is not under client_idle_timeout "
        f"{client_idle_s}s -- the pool would hand out connections PgBouncer "
        "has already dropped"
    )

    idle_txn_s = int(pooler["IDLE_TRANSACTION_TIMEOUT"])
    assert shipped["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"] / 1000 < idle_txn_s
    assert idle_txn_s > _BACKGROUND_IDLE_IN_TRANSACTION_TIMEOUT_MS / 1000, (
        f"the background idle budget ({_BACKGROUND_IDLE_IN_TRANSACTION_TIMEOUT_MS}ms) "
        f"is not under the pooler's {idle_txn_s}s backstop"
    )

    # `query_wait_timeout` bounds a phase NO client-side setting reaches: the
    # wait for a free server slot, before any backend has seen the statement.
    # Measured on the live stack -- with all 25 `app_rw` slots busy, a 26th
    # client carrying a 5s statement budget waited 7.85s and then SUCCEEDED.
    # It must stay ABOVE every execution budget so it never pre-empts a guard
    # that could name the cause.
    query_wait_s = int(pooler["QUERY_WAIT_TIMEOUT"])
    assert query_wait_s > shipped["DB_STATEMENT_TIMEOUT_MS"] / 1000
    assert query_wait_s < int(pooler["IDLE_TRANSACTION_TIMEOUT"])


def test_the_pooler_never_gives_up_after_the_edge_already_has() -> None:
    """A hung call must surface as a database error, not as the edge's 504.
    `location /` carries `proxy_read_timeout 300s` because SSE streams share
    it (`ح-9`), so 300s is the ceiling every DB-side bound has to beat."""
    match = _PROXY_READ_TIMEOUT.search(_NGINX_LOCATIONS.read_text(encoding="utf-8"))
    assert match is not None, "no proxy_read_timeout on `location /` any more"
    edge_s = int(match.group("seconds"))
    pooler = _pgbouncer_env()
    assert int(pooler["QUERY_WAIT_TIMEOUT"]) < edge_s
    assert _shipped_defaults()["DB_STATEMENT_TIMEOUT_MS"] / 1000 < edge_s


def test_query_timeout_stays_off_on_the_pooler() -> None:
    """Its default is `0` and it must stay there: it would duplicate
    `statement_timeout` one layer higher and worse, killing the client
    connection where Postgres returns a `57014` every adapter's `_translate`
    already understands."""
    assert "QUERY_TIMEOUT" not in _pgbouncer_env()


# ----------------------------------------------------- the per-path budgets --


def test_the_background_budget_is_looser_than_the_request_one() -> None:
    """Nothing interactive waits on the relay or the three workers, and a
    request that has spent five seconds in Postgres has no caller left."""
    shipped = _shipped_defaults()
    assert shipped["DB_STATEMENT_TIMEOUT_MS"] < _BACKGROUND_STATEMENT_TIMEOUT_MS
    assert shipped["DB_IDLE_IN_TRANSACTION_TIMEOUT_MS"] < _BACKGROUND_IDLE_IN_TRANSACTION_TIMEOUT_MS
    assert shipped["DB_POOL_TIMEOUT_S"] < _BACKGROUND_POOL_TIMEOUT_S


def test_worker_engines_carry_the_background_budget_and_the_shared_recycle() -> None:
    """`_worker_db` overrides three of the four and inherits the fourth --
    `pool_recycle_s` is the one that answers to the pooler, so it has to be the
    same number in every process that talks to it."""
    worker = _worker_db(DatabaseSettings(url="postgresql+asyncpg://x@y/z", pool_recycle_s=777))
    assert worker.statement_timeout_ms == _BACKGROUND_STATEMENT_TIMEOUT_MS
    assert worker.idle_in_transaction_timeout_ms == _BACKGROUND_IDLE_IN_TRANSACTION_TIMEOUT_MS
    assert worker.pool_timeout_s == _BACKGROUND_POOL_TIMEOUT_S
    assert worker.pool_recycle_s == 777


def test_a_bare_database_settings_leaves_the_ops_tools_unbounded() -> None:
    """Load-bearing, not an oversight. `app.ops.*` build exactly this and
    legitimately run statements for minutes -- `load_seed`'s corpus took 591
    seconds and `explain_hot_paths` runs `EXPLAIN (ANALYZE)` over a million
    rows. A contract default of 5s would have failed both, and a default that
    breaks the tools gets deleted rather than fixed."""
    bare = DatabaseSettings(url="postgresql+asyncpg://x@y/z")
    assert bare.statement_timeout_ms == 0
    assert bare.idle_in_transaction_timeout_ms == 0
    assert _budget_sql(bare) is None


def test_the_budget_is_transaction_local_in_every_form_it_is_issued() -> None:
    """The measured hazard this whole mechanism exists to avoid: under
    transaction pooling a non-LOCAL `SET` leaks onto a SHARED server
    connection. One client issued `SET statement_timeout = '1234ms'` on the
    live stack and the next SIX unrelated clients read it back. Both calls
    must therefore stay `is_local => true`, and there must be exactly one
    statement -- asyncpg refuses multi-statement text."""
    sql = _budget_sql(DatabaseSettings(url="x", statement_timeout_ms=5000))
    assert sql is not None
    assert sql.count("set_config") == 2
    assert sql.count(", true)") == 2
    assert ";" not in sql
    assert sql.lstrip().upper().startswith("SELECT ")


def test_half_a_budget_still_installs_the_listener() -> None:
    """Either GUC alone is enough to be worth a round trip; only both at zero
    means "this path opted out"."""
    assert _budget_sql(DatabaseSettings(url="x", statement_timeout_ms=1)) is not None
    assert _budget_sql(DatabaseSettings(url="x", idle_in_transaction_timeout_ms=1)) is not None
