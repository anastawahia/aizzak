#!/bin/bash
# Cluster init AND by-hand: the ninth role, `metrics_exporter`, the identity
# postgres-exporter scrapes Postgres with (monitoring plan phase 2, row 2;
# docs/delivery/monitoring-host-postgres/design.md).
#
# Why its own file and not a line in 10-roles.sh: a role created there needs
# its password in the `postgres` service's environment, and a new key there
# changes that service's compose config hash -- the next plain
# `docker compose up -d` would then RECREATE THE DATABASE. This file is both
# what `initdb` runs on a fresh volume and what a human runs on an existing
# cluster, so the SQL exists once:
#
#   docker compose exec -T -e METRICS_EXPORTER_PASSWORD postgres \
#     bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh
#
# Why the password is NOT in the postgres service's environment: see above.
# At initdb it is absent, so the role is created WITHOUT a password (it cannot
# log in until a human sets one: 08-local-runbook §3.3-ج step ①). When the
# variable is present (the by-hand run, or CI) the password is set. It reaches
# psql through `\getenv` (psql >= 15; the cluster is 16) so it is never in
# psql's argv (CWE-214), and as a psql variable, so the shell never
# interpolates it into SQL.
#
# Before the ALTER the same session turns off the two places that would keep
# the literal (CWE-312/532): `pg_stat_statements.track_utility` (the ALTER ROLE
# text, literal included, would sit in pg_stat_statements, readable by
# pg_read_all_stats and printed by `app.ops.slow_queries top`) and
# `log_min_error_statement` (a failing ALTER would log the statement). Both
# are superuser-only settings and this script runs as the superuser. (The
# statement is still in pg_stat_activity.query for the instant it runs.)
#
# A real cluster refuses an empty or `change-me*` password (that value is
# published in .env.example and the role reads other sessions' query text).
# CI alone opts in with METRICS_EXPORTER_ALLOW_PLACEHOLDER=1.
#
# Why no top-level `exit`: docker-entrypoint.sh `source`s every 0644 *.sh in
# /docker-entrypoint-initdb.d, so an `exit` here would abort cluster init.
#
# The role is pg_monitor and nothing else, NOINHERIT with ONE membership that
# is explicitly live (PG16 records the inherit option per membership -- the
# backup_operator lesson in 10-roles.sh). It has no USAGE on any tenant
# schema and no SELECT on any table, and it is not BYPASSRLS. It can read the
# text of other sessions' queries via pg_read_all_stats (part of pg_monitor);
# the exporter never exports that text as a label.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER:-postgres}" \
     --dbname "${POSTGRES_DB:-postgres}" \
     --set db_name="${POSTGRES_DB:-postgres}" <<-'EOSQL'
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metrics_exporter') THEN
            CREATE ROLE metrics_exporter LOGIN NOINHERIT CONNECTION LIMIT 2;
        END IF;
    END
    $$;

    -- Re-asserted on every run: a role an operator made by hand may lack any of these.
    ALTER ROLE metrics_exporter LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
        NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
    ALTER ROLE metrics_exporter SET statement_timeout = '5s';
    ALTER ROLE metrics_exporter SET lock_timeout = '1s';
    ALTER ROLE metrics_exporter SET default_transaction_read_only = on;

    GRANT CONNECT ON DATABASE :"db_name" TO metrics_exporter;
    -- PG16: the inherit option is recorded PER MEMBERSHIP (10-roles.sh's
    -- backup_operator lesson). NOINHERIT on the role keeps every FUTURE grant
    -- inert; this one membership is explicitly live.
    GRANT pg_monitor TO metrics_exporter WITH INHERIT TRUE;
EOSQL

if [ "${METRICS_EXPORTER_PASSWORD+set}" = "set" ]; then
    # `return` when sourced by docker-entrypoint, `exit` when run by hand.
    if [ -z "${METRICS_EXPORTER_PASSWORD}" ]; then
        echo "15-metrics-exporter: REFUSED: METRICS_EXPORTER_PASSWORD is empty -- fix .env first" >&2
        return 1 2>/dev/null || exit 1
    fi
    case "${METRICS_EXPORTER_PASSWORD}" in
        change-me*)
            if [ "${METRICS_EXPORTER_ALLOW_PLACEHOLDER:-}" != "1" ]; then
                echo "15-metrics-exporter: REFUSED: placeholder password (change-me-*) -- fix .env first" >&2
                return 1 2>/dev/null || exit 1
            fi
            echo "15-metrics-exporter: WARNING: placeholder password allowed (CI only)" >&2
            ;;
    esac
    psql -v ON_ERROR_STOP=1 \
         --username "${POSTGRES_USER:-postgres}" \
         --dbname "${POSTGRES_DB:-postgres}" <<-'EOSQL'
        SET pg_stat_statements.track_utility = off;
        SET log_min_error_statement = panic;
        \getenv exporter_password METRICS_EXPORTER_PASSWORD
        ALTER ROLE metrics_exporter PASSWORD :'exporter_password';
EOSQL
    echo "15-metrics-exporter: metrics_exporter ready (password set)"
else
    echo "15-metrics-exporter: metrics_exporter created WITHOUT a password -- set it with 08 §3.3-ج step ①" >&2
fi
