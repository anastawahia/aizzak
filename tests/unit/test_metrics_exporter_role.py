"""Static guards for the `metrics_exporter` role and its exporter
(monitoring plan phase 2, row 2; docs/delivery/monitoring-host-postgres/).

The role lives in its own initdb script, NOT in `10-roles.sh`, for one reason
that the first test below pins down: a role created there needs its password in
the `postgres` service's environment, a new key there changes that service's
compose config hash, and the next plain `docker compose up -d` would RECREATE
THE DATABASE (design.md §3-ز, Q-6). The password therefore reaches Postgres
only through the script run by hand, and the exporter holds the one credential.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "deploy" / "postgres" / "initdb" / "15-metrics-exporter.sh"
_QUERIES = _REPO_ROOT / "deploy" / "postgres-exporter" / "queries.yaml"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_VAR = "METRICS_EXPORTER_PASSWORD"


def _script() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _sql_only() -> str:
    """The script with shell comment lines and SQL comments dropped."""
    lines = [line for line in _script().splitlines() if not line.lstrip().startswith(("#", "-- "))]
    return "\n".join(lines)


def _services() -> dict[str, dict]:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    services: dict[str, dict] = compose["services"]
    return services


def test_the_role_script_sorts_between_the_roles_and_the_extensions() -> None:
    names = sorted(p.name for p in _SCRIPT.parent.glob("*.sh"))
    assert names.index("10-roles.sh") < names.index(_SCRIPT.name) < names.index("20-extensions.sh")
    # 0644 like its siblings: docker-entrypoint SOURCES non-executable files.
    assert _SCRIPT.stat().st_mode & 0o111 == 0, "an executable file is run, not sourced"


def test_the_role_script_is_safe_to_source() -> None:
    """Sourced at initdb: a top-level `exit` would abort cluster init."""
    for line in _sql_only().splitlines():
        assert not re.match(r"^exit\b", line.strip()), f"top-level exit: {line!r}"


def test_the_role_is_created_inside_an_existence_check() -> None:
    sql = _sql_only()
    assert re.search(
        r"IF NOT EXISTS \(SELECT FROM pg_catalog\.pg_roles WHERE rolname = 'metrics_exporter'\)",
        sql,
    )
    assert "CREATE ROLE metrics_exporter LOGIN NOINHERIT CONNECTION LIMIT 2" in sql


def test_the_password_is_a_psql_variable_never_shell_interpolated() -> None:
    sql = _sql_only()
    assert "\\getenv exporter_password METRICS_EXPORTER_PASSWORD" in sql
    assert "PASSWORD :'exporter_password'" in sql
    assert "PASSWORD '$" not in sql


def test_the_password_is_never_on_a_command_line() -> None:
    """L-1 (CWE-214): `psql --set x=value` puts the value in argv, readable from
    /proc/<pid>/cmdline. `\\getenv` reads it from the environment inside psql."""
    sql = _sql_only()
    assert "--set exporter_password" not in sql
    assert not re.search(r"(-v|--set)\s+exporter_password", sql)


def test_the_literal_is_kept_out_of_pg_stat_statements_and_the_log() -> None:
    """M-1 (CWE-312/532): with `track_utility` on, `ALTER ROLE ... PASSWORD '<literal>'`
    is stored in pg_stat_statements, which `slow_queries top` prints; and a failing
    statement is logged. Both are switched off in the SAME psql session, BEFORE the ALTER."""
    sql = _sql_only()
    alter = sql.index("ALTER ROLE metrics_exporter PASSWORD")
    for setting in (
        "SET pg_stat_statements.track_utility = off;",
        "SET log_min_error_statement = panic;",
    ):
        assert setting in sql, setting
        assert sql.index(setting) < alter, f"{setting} must precede the password ALTER"
    getenv = sql.index("\\getenv exporter_password")
    session_start = sql.rindex("psql -v ON_ERROR_STOP=1", 0, alter)
    assert session_start < sql.index("SET pg_stat_statements.track_utility") < getenv < alter


def test_a_real_cluster_refuses_an_empty_or_placeholder_password() -> None:
    """BUG-1 (AC-9.1): the value `.env.example` publishes must not become the
    role's password; CI alone opts in explicitly."""
    sql = _sql_only()
    assert '"${METRICS_EXPORTER_PASSWORD+set}" = "set"' in sql
    assert 'if [ -z "${METRICS_EXPORTER_PASSWORD}" ]' in sql
    assert "change-me*)" in sql
    assert '"${METRICS_EXPORTER_ALLOW_PLACEHOLDER:-}" != "1"' in sql
    assert sql.count("REFUSED") == 2 and sql.count("return 1 2>/dev/null || exit 1") == 2
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "METRICS_EXPORTER_ALLOW_PLACEHOLDER=1" in ci


def test_pg_monitor_with_inherit_is_the_only_grant() -> None:
    sql = _sql_only()
    assert "GRANT pg_monitor TO metrics_exporter WITH INHERIT TRUE" in sql
    assert not re.search(r"GRANT\s+(SELECT|INSERT|UPDATE|DELETE)", sql, re.IGNORECASE)
    for role in (
        "pg_read_all_data",
        "pg_write_all_data",
        "pg_read_server_files",
        "pg_write_server_files",
        "pg_execute_server_program",
    ):
        assert role not in sql, role
    for attribute in ("SUPERUSER", "BYPASSRLS", "CREATEROLE", "REPLICATION", "CREATEDB"):
        for match in re.finditer(attribute, sql):
            assert sql[match.start() - 2 : match.start()] == "NO", (
                f"{attribute} appears without NO: {sql[match.start() - 20 : match.end()]!r}"
            )


def test_the_role_is_bounded_server_side() -> None:
    sql = _sql_only()
    assert "statement_timeout = '5s'" in sql
    assert "lock_timeout = '1s'" in sql
    assert "default_transaction_read_only = on" in sql
    assert "CONNECTION LIMIT 2" in sql


def test_every_attribute_is_reasserted_so_a_rerun_converges() -> None:
    """The ALTER is outside the DO block, so a role made by hand with other
    attributes is brought back on every run (AC-2.6)."""
    sql = _sql_only()
    do_block = sql[sql.index("DO $$") : sql.index("$$;", sql.index("DO $$") + 5)]
    alter = "ALTER ROLE metrics_exporter LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE"
    assert alter in sql and alter not in do_block
    assert "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2" in sql


def test_the_password_never_enters_the_postgres_service() -> None:
    """Q-6: a new key in `postgres`'s environment changes its config hash, and
    the next plain `docker compose up -d` would recreate the database."""
    environment = _services()["postgres"].get("environment", {})
    flat = (
        environment
        if isinstance(environment, dict)
        else dict(item.split("=", 1) for item in environment)
    )
    assert _VAR not in flat
    assert _VAR not in str(_services()["postgres"]), "referenced somewhere in the postgres block"


def test_the_exporter_alone_requires_the_password() -> None:
    environment = _services()["postgres-exporter"]["environment"]
    assert str(environment["DATA_SOURCE_PASS"]).startswith("${" + _VAR + ":?")
    assert re.search(rf"^{_VAR}=change-me-", _ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE)


def test_the_exporter_connects_directly_as_its_own_role() -> None:
    service = _services()["postgres-exporter"]
    environment = service["environment"]
    assert str(environment["DATA_SOURCE_URI"]).startswith("postgres:5432/")
    assert "pgbouncer" not in str(environment["DATA_SOURCE_URI"])
    assert environment["DATA_SOURCE_USER"] == "metrics_exporter"
    references = set(re.findall(r"\$\{([A-Z_]+)[:}]", str(service["environment"])))
    assert {r for r in references if r.endswith("PASSWORD")} == {_VAR}
    assert service["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_no_per_query_series_can_be_exported() -> None:
    """AC-3.7: no query text, no queryid, no user ever becomes a label."""
    command = _services()["postgres-exporter"]["command"]
    assert "--no-collector.stat_statements" in command
    assert "--collector.stat_statements" not in command
    queries = yaml.safe_load(_QUERIES.read_text(encoding="utf-8"))
    assert queries
    for name, spec in queries.items():
        for metric in spec["metrics"]:
            (column,) = metric.values()
            assert column["usage"] in {"GAUGE", "COUNTER"}, (name, column)
    text = _QUERIES.read_text(encoding="utf-8")
    assert 'usage: "LABEL"' not in text and "pg_stat_statements" not in text


def test_both_new_images_are_pinned_to_a_version() -> None:
    for name in ("node-exporter", "postgres-exporter"):
        tag = str(_services()[name]["image"]).rsplit(":", 1)[1]
        assert re.fullmatch(r"v\d+\.\d+\.\d+", tag), f"{name}: {tag!r} is not a pinned version"
