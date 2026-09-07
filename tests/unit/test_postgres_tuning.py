"""Step 2.1's tuning, and the switch that let it ship while `م-8` was open --
capacity step 2.1 (docs/capacity-plan.md §5 wave 2).

⭐ WHY THERE IS A SWITCH AT ALL. `م-8` is "tuning starts before the baseline
exists", and it was recorded as a BLOCKER on this step. Read literally that is
a scheduling constraint nobody can lift by writing code: the 0.5 baseline is
blocked by `د-9` (no real Firebase token pool -- an owner action), so 2.1 would
wait on a wait. Read for its MECHANISM it is a reversibility constraint: what
makes a tuned server unable to answer "did the tuning help?" is that you cannot
un-tune it. `POSTGRES_CONFIG_FILE` un-tunes it, in one variable, and the
baseline can still be taken on the artifact that ships. That is the shape every
other `م-8` subject in this repository already had -- `API_RATE_LIMIT_ENABLED`,
`AUTH_PRINCIPAL_CACHE_TTL_S=0`, `MAX_IN_FLIGHT_REQUESTS=0` -- and 2.1 is the
one that had no equivalent until now.

⚠️ THREE CLASSES OF SETTING REACH THIS SERVER AND ONLY ONE OF THEM IS `م-8`'s.
0.4's instrument (`shared_preload_libraries`, `pg_stat_statements.*`) and 2.5's
durability (`archive_mode`, `archive_command`, `hba_file`) must NOT be
reversible: a baseline that cannot be read, or that was taken on a cluster
which does not archive its WAL, is not a baseline of this deployment. Step
2.1's own text asked the config file to "take the place of that `command`",
which would have put all three behind one switch. It did not, and the tests
below are what keep the separation from eroding -- in both directions.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_TUNING_CONF = _REPO_ROOT / "deploy" / "postgres" / "postgresql.conf"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_MOUNTED_AT = "/etc/postgresql/postgresql.conf"
_PGDATA_CONF = "/var/lib/postgresql/data/postgresql.conf"

# The eight knobs step 2.1 names, and nothing else. Written out rather than
# read from the file, because "the file sets what the plan asked for" is the
# claim -- a test that derived the list from the file would assert that the
# file agrees with itself.
_DECLARED = {
    "max_connections": "300",
    "shared_buffers": "8GB",
    "effective_cache_size": "24GB",
    "work_mem": "16MB",
    "maintenance_work_mem": "1GB",
    "random_page_cost": "1.1",
    "max_wal_size": "4GB",
    "checkpoint_completion_target": "0.9",
}

# `shared_preload_libraries` is 0.4's and stays a `-c` argument; the other two
# are 2.5's. None of the three may move into the file.
_NOT_THE_TUNINGS = ("shared_preload_libraries", "archive_mode", "archive_command", "hba_file")


def _postgres_service() -> dict[str, object]:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    return compose["services"]["postgres"]


def _command() -> list[str]:
    return [str(token) for token in _postgres_service().get("command", [])]


def _settings() -> dict[str, str]:
    """Every `key = value` in the tuning file, ignoring comments and the
    include."""
    found: dict[str, str] = {}
    for line in _TUNING_CONF.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([a-z_]+)\s*=\s*(\S+)", line)
        if match is not None:
            found[match.group(1)] = match.group(2)
    return found


# --------------------------------------------------------- the file itself --


def test_the_file_sets_every_knob_step_2_1_names_and_only_those() -> None:
    """Both halves. A missing knob is the step half-done; an extra one is a
    setting that entered production without the argument the other eight got,
    and `م-8`'s whole subject is settings arriving unexamined."""
    assert _settings() == _DECLARED, (
        "deploy/postgres/postgresql.conf no longer matches the eight knobs capacity "
        f"step 2.1 names. Found: {_settings()}"
    )


def test_the_include_comes_first_and_names_the_data_directorys_own_file() -> None:
    """LOAD-BEARING, and it fails silently without this test.

    `config_file` REPLACES the data directory's postgresql.conf rather than
    layering over it, and that file holds what initdb decided for this cluster
    -- `listen_addresses`, `lc_messages`, `dynamic_shared_memory_type`, the
    collation. Without the include they revert to compiled-in defaults and
    nothing says so; the server starts. And the include must come FIRST,
    because Postgres takes the LAST assignment: an include at the bottom would
    put the data directory's `shared_buffers = 128MB` back on top of this
    file's 8GB, silently undoing the step while every test that reads the file
    still passed.
    """
    body = [
        line.strip()
        for line in _TUNING_CONF.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert body[0] == f"include '{_PGDATA_CONF}'", (
        f"the first non-comment line of the tuning file is {body[0]!r}. It must be the "
        f"include of {_PGDATA_CONF}, and it must be first -- later assignments win."
    )


# ----------------------------------------------------------- the switch --


def test_the_config_file_argument_is_the_m8_switch_and_defaults_to_the_tuning() -> None:
    """One variable, two positions, and the default is the tuned one -- the
    step ships turned ON and the baseline run is what asks for the other
    position."""
    command = _command()
    selectors = [token for token in command if token.startswith("config_file=")]

    assert len(selectors) == 1, f"expected exactly one `config_file=` argument, got {selectors}"
    assert selectors[0] == f"config_file=${{POSTGRES_CONFIG_FILE:-{_MOUNTED_AT}}}", (
        f"the `م-8` switch is not interpolated from POSTGRES_CONFIG_FILE: {selectors[0]}. "
        "A hard-coded path is a tuning nobody can turn off for a 0.5 baseline, which is "
        "the risk itself."
    )


def test_env_example_documents_the_off_position_not_just_the_variable() -> None:
    """A switch whose OTHER position is undocumented is a switch nobody will
    find on the day the baseline is finally runnable. `.env.example` is where
    every guide's first step points (`cp .env.example .env`)."""
    env = _ENV_EXAMPLE.read_text(encoding="utf-8")

    assert re.search(rf"^POSTGRES_CONFIG_FILE={re.escape(_MOUNTED_AT)}$", env, re.M), (
        "POSTGRES_CONFIG_FILE is not set to the tuned default in .env.example. Compose "
        "auto-loads `.env`, so a value there beats the inline `${VAR:-...}` fallback -- "
        "this repository has been bitten by exactly that before."
    )
    assert _PGDATA_CONF in env, (
        "`.env.example` names the switch but not its OFF position "
        f"({_PGDATA_CONF}), which is the only position `م-8` cares about."
    )


def test_the_tuning_file_is_mounted_read_only_and_unconditionally() -> None:
    """Read-only for pg_hba.conf's reason -- a server that can rewrite its own
    configuration is a server that can grant itself anything. Unconditional
    because the switch changes which file is READ, and a switch that also had
    to remove a mount would be two things to remember and one of them silent.
    """
    volumes = [str(v) for v in _postgres_service()["volumes"]]  # type: ignore[index]
    expected = f"./deploy/postgres/postgresql.conf:{_MOUNTED_AT}:ro"

    assert expected in volumes, f"the tuning file is not mounted read-only: {volumes}"


# ------------------------------------------- the separation, both directions --


def test_the_instrument_and_the_durability_stay_on_the_command_line() -> None:
    """The direction `م-8` must NOT reach. These are 0.4's and 2.5's, a `-c`
    argument outranks the config file, and that is why they are still
    arguments: the switch cannot turn them off, and it must not be able to."""
    command = " ".join(_command())
    missing = [knob for knob in _NOT_THE_TUNINGS if f"{knob}=" not in command]

    assert not missing, (
        f"{missing} left the postgres `command:`. If they moved into "
        "deploy/postgres/postgresql.conf they are now behind POSTGRES_CONFIG_FILE, and "
        "turning off step 2.1's tuning for a 0.5 baseline would also turn off WAL "
        "archiving (2.5) and pg_stat_statements (0.4). A baseline taken like that is a "
        "baseline of a deployment nobody would operate."
    )


def test_the_tuning_never_appears_as_a_command_line_argument() -> None:
    """And the direction the tuning must not reach. Same rule read the other
    way: a `-c work_mem=16MB` would survive the off switch, so the knobs must
    exist in exactly one place. `test_ops_slow_queries` guards the same
    boundary from its own side, against the whole compose file rather than
    this service's parsed command."""
    command = " ".join(_command())
    leaked = [knob for knob in _DECLARED if f"{knob}=" in command]

    assert not leaked, (
        f"{leaked} are passed to the server as `-c` arguments as well as set in the "
        "tuning file. Command-line settings outrank the file, so these would survive "
        "POSTGRES_CONFIG_FILE and the step would be irreversible after all."
    )


# -------------------------------------------- the numbers the budgets share --


def test_the_memory_limit_covers_what_these_knobs_can_actually_ask_for() -> None:
    """⚠️ THE TERM EVERY EARLIER SUM MISSED IS AUTOVACUUM. `autovacuum_work_mem`
    is -1 on this server, which means "use `maintenance_work_mem`", and
    `autovacuum_max_workers` is 3 -- so 2.1's 1 GB is 3 GB standing before an
    operator's own VACUUM asks for its. §6 budgeted 20 GB without that term and
    3.4 derived 14 GB with it counted once.

    ⚠️ AND `max_connections x work_mem` IS A FLOOR, NOT A CEILING. The grant is
    per memory-consuming NODE and again per parallel worker: measured, four
    simultaneous grants in one ordinary query and six once parallelism was on.
    What bounds it is `max_worker_processes`, not `max_connections`.

    This test does not re-derive either number -- it asserts the limit is above
    the part that is unambiguous (`shared_buffers` + three autovacuum workers +
    one manual one), which is 12 GB and which 3.4's 14 GB cleared only by two.
    """
    limits = _postgres_service()["deploy"]["resources"]["limits"]  # type: ignore[index]
    limit_gb = int(str(limits["memory"]).removesuffix("g"))  # type: ignore[index]

    settings = _settings()
    shared_buffers_gb = int(settings["shared_buffers"].removesuffix("GB"))
    maintenance_gb = int(settings["maintenance_work_mem"].removesuffix("GB"))
    autovacuum_workers = 3  # Postgres's default, and this file does not change it
    unambiguous = shared_buffers_gb + maintenance_gb * (autovacuum_workers + 1)

    assert limit_gb > unambiguous, (
        f"the postgres container is limited to {limit_gb}g, and shared_buffers "
        f"({shared_buffers_gb}) plus maintenance_work_mem ({maintenance_gb}) times "
        f"{autovacuum_workers} autovacuum workers plus one manual VACUUM already asks "
        f"for {unambiguous}g -- before a single work_mem grant. `autovacuum_work_mem` "
        "is -1, which means autovacuum takes maintenance_work_mem PER WORKER."
    )
