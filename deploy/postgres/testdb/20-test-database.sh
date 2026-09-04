#!/bin/sh
# The integration-suite database: `aizzak_test`, owned by `aizzak_owner`
# (containerised-test-harness-plan-v2.md step أ-2).
#
# ⚠️ THIS SCRIPT DOES NOT RUN BY ITSELF, AND THAT IS THE DESIGN.
# `docker-compose.test.yml` mounts it at /opt/aizzak/testdb -- deliberately NOT
# under /docker-entrypoint-initdb.d, which the postgres entrypoint executes.
# An operator runs it by hand:
#
#   docker compose exec -T postgres sh /opt/aizzak/testdb/20-test-database.sh
#
# That is not a workaround, it is the plan's 🔒 fixed rule holding: "no test
# database in a production cluster", and every test provision entering through
# an explicit override file or a flag that is off by default. Two gates stand
# between a deployment and this file -- the override must be named with `-f`
# for the mount to exist at all, and then the command above must be typed.
#
# The plan (أ-2) wrote that mount as /docker-entrypoint-initdb.d/testdb, one
# level INSIDE the initdb directory, and both halves of that failed on
# measurement. It cannot run: the entrypoint globs one level
# (`docker_process_init_files /docker-entrypoint-initdb.d/*`) and its case arm
# for anything that is not *.sh / *.sql / *.sql.{gz,xz,zst} is `ignoring`, with
# no recursion -- so the plan's own claim in أ-3 that "the أ-2 script applies to
# any NEW cluster" was never true. And it cannot even start: the parent is
# mounted `:ro`, so runc has nowhere to create the nested mountpoint and the
# container dies before Postgres is reached --
#   error mounting … at "/docker-entrypoint-initdb.d/testdb":
#   create mountpoint … read-only file system
# Making it work would have meant committing an empty decoy `initdb/testdb/`
# directory for the mount to land on -- a tracked placeholder that reads like a
# script directory and holds no script, the very thing this repository deleted
# `deploy/pgbouncer/pgbouncer.ini` for (docker-compose.yml:166-169). Moving one
# directory out costs a path in أ-3's command and buys a file that CANNOT be
# auto-executed by any mount ordering or future loss of that `:ro`, rather than
# one kept harmless by a glob's behaviour. Recorded as ن-ي.
#
# Hence `#!/bin/sh` and `set -eu` rather than 10-roles.sh's bash and `-o
# pipefail`: the invocation above says `sh`, and /bin/sh in this image is dash,
# which has no `pipefail`. Nothing here pipes. The script is still valid as an
# initdb script -- the entrypoint would source it -- should it ever be placed
# where one is looked for.
#
# Re-runnable by construction. `CREATE DATABASE` has no `IF NOT EXISTS`, and
# unlike the `DO $$ ... $$` guard that 10-roles.sh wraps its `CREATE ROLE`s in,
# it cannot be put inside one at all: CREATE DATABASE runs outside any
# transaction block, which a DO body is. `\gexec` is the idiom that remains --
# a query that RETURNS the DDL text, executed only if the row exists.
#
# The names are hardcoded on purpose. `tests/integration/conftest.py:97-120`
# hardcodes `aizzak_test` in all seven DSN defaults; a variable here would be a
# knob whose other half does not exist, which is exactly the trap step أ-1
# recorded as ن-ح. If the name ever moves, both sides move together.
set -eu

psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER}" \
     --dbname "${POSTGRES_DB}" <<-'EOSQL'
    SELECT 'CREATE DATABASE aizzak_test OWNER aizzak_owner'
    WHERE NOT EXISTS (SELECT FROM pg_catalog.pg_database WHERE datname = 'aizzak_test')
\gexec

    -- The six non-migrator roles. `aizzak_owner` is absent deliberately, and
    -- the asymmetry with 10-roles.sh:108 is real rather than an oversight:
    -- there it must be granted CREATE/CONNECT because `aizzak` is owned by the
    -- superuser (measured: datdba=postgres), while here it OWNS the database
    -- and holds every database-level privilege implicitly.
    -- `backup_operator` (capacity step 2.5) joins them: it is cluster-wide by
    -- nature (REPLICATION, BYPASSRLS, pg_read_all_data), but CONNECT is
    -- per-database like every other privilege here, and
    -- `tests/integration/test_backup_live.py` opens its own connection as
    -- this role to prove the thing no catalogue read can -- that it SEES
    -- tenant rows a non-bypassing role cannot.
    GRANT CONNECT ON DATABASE aizzak_test
        TO app_rw, outbox_relay, retention_sweeper, metrics_reader, transit_rotator,
           workspace_purger, backup_operator;
EOSQL

# Schema privileges are per-database and are NOT inherited from `aizzak`, so
# this second connection is not tidiness -- it is the only place these can be
# said. Without the grant the FIRST thing to fail is the migration itself: the
# `platform` baseline chain passes no `-x vts=` (provision.py:79) and so lands
# `alembic_version` in `public`, which is the "permission denied for schema
# public" that 10-roles.sh:116-124 documents having been found the hard way.
#
# The REVOKE is a no-op on PG15+ -- PUBLIC no longer gets CREATE by default --
# and is kept for the same reason 10-roles.sh:112 keeps it: to state the
# intention rather than to inherit it silently from a version default. The pair
# reproduces the ACL measured in `aizzak` exactly:
#   {pg_database_owner=UC/…, =U/…, aizzak_owner=UC/…}
#
# `pg_stat_statements` is created here for the same reason it is created in
# `aizzak` by initdb/20-extensions.sh (capacity step 0.4): the extension's
# VIEW is per-database even though its statistics are one cluster-wide hash
# table, so a connection to `aizzak_test` cannot read it unless it exists
# here too -- and `tests/integration/test_slow_queries_ops_live.py` proves
# `app.ops.slow_queries` against real Postgres over exactly that connection.
# `pg_stat_statements_reset` is deliberately NOT granted here (unlike
# 20-extensions.sh): the reset is cluster-wide, so a test suite allowed to
# call it could erase an operator's in-flight measurement on the same server.
psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER}" \
     --dbname aizzak_test <<-'EOSQL'
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    GRANT CREATE, USAGE ON SCHEMA public TO aizzak_owner;
    CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
EOSQL

echo "testdb: database aizzak_test ready (owner aizzak_owner)"
