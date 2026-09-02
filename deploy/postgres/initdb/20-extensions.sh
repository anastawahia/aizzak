#!/bin/bash
# Cluster init, part two: the extensions the application database needs
# (capacity step 0.4, docs/capacity-plan.md §5 Wave 0).
#
# Runs ONCE, as the superuser, when the postgres volume is first initialised
# -- the same footing 10-roles.sh stands on, and for the same reason:
# `CREATE EXTENSION pg_stat_statements` is superuser-only (the extension is
# not marked trusted), and `aizzak_owner` is deliberately NOSUPERUSER. It
# cannot be a migration either: Alembic runs as `aizzak_owner`, so a
# migration issuing this would fail on every fresh cluster.
#
# ⚠️ NUMBERED 20- SO IT RUNS AFTER 10-roles.sh, AND THAT ORDER IS LOAD-
# BEARING: the GRANT at the bottom names `aizzak_owner`, which does not exist
# until 10-roles.sh has created it. The postgres entrypoint globs
# /docker-entrypoint-initdb.d/* and sources what it finds in sort order.
#
# ⚠️ AND IT RUNS ONLY ON A FRESH VOLUME. A cluster initialised before this
# file existed never sees it -- the same asymmetry 08-local-runbook §3
# records for the roles added after the first deployment. `python -m
# app.ops.slow_queries` detects the missing extension by name and prints the
# one-off psql command rather than failing with `UndefinedTable`.
#
# The extension collects nothing unless the server was ALSO started with
# `shared_preload_libraries=pg_stat_statements` (docker-compose.yml's
# `postgres` command). Creating it without that preload succeeds and then
# raises on every read, which is why the tool checks both and distinguishes
# them: the remedies are a psql command and a container restart respectively.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER}" \
     --dbname "${POSTGRES_DB}" <<-'EOSQL'
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

    -- Without this membership the report is a ranked list of anonymous rows.
    -- pg_stat_statements shows every role's ROWS to everyone but shows the
    -- query TEXT only for statements the reading role ran itself -- and every
    -- request-path statement in this system runs as `app_rw`, never as the
    -- role reading the report. `<insufficient privilege>` in place of the SQL
    -- has the exact shape of a working report and says nothing.
    --
    -- Granted to `aizzak_owner` rather than to an eighth login role, and the
    -- asymmetry with retention_sweeper/transit_rotator/workspace_purger is
    -- deliberate: those exist because their tools WRITE and app_rw's grants
    -- must not be widened to let them. This one only reads, and it reads
    -- statistics ABOUT tables whose every row aizzak_owner already owns -- a
    -- role that can ALTER TABLE ... NO FORCE ROW LEVEL SECURITY on all of
    -- them gains nothing from being allowed to see a normalised query string.
    -- Reasoned at length in src/app/ops/slow_queries.py's docstring.
    GRANT pg_read_all_stats TO aizzak_owner;

    -- `pg_stat_statements_reset()` is superuser-only by default. The report
    -- is only attributable to ONE load run if the counters can be zeroed
    -- before it starts (`slow_queries reset --yes`), and that is the whole
    -- reason this grant exists -- it destroys statistics and nothing else.
    -- The three-argument signature is PostgreSQL 16's, which is the version
    -- this compose file pins; PG17 adds a fourth parameter and would need
    -- this line updated with the image tag.
    GRANT EXECUTE ON FUNCTION pg_stat_statements_reset(oid, oid, bigint) TO aizzak_owner;
EOSQL

echo "initdb: extension pg_stat_statements ready (aizzak_owner may read all stats and reset them)"
