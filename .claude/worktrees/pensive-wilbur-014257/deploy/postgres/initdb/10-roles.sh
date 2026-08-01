#!/bin/bash
# Cluster init: create the six application roles (7.1 · 01-data-model §6 ·
# P1-5/p1-hardening-plan.md §3 step 8 added the fourth · P1-3/step 10 added
# the fifth · P1-9/step 12 added the sixth).
#
# Runs ONCE, as the superuser, when the postgres volume is first initialised
# -- which is the only footing that can do this: CREATE ROLE is a
# cluster-wide privilege, and `aizzak_owner` is deliberately NOSUPERUSER and
# not CREATEROLE. It owns every table (enough to GRANT on them and to ALTER
# TABLE ... FORCE ROW LEVEL SECURITY) and nothing more. The GRANTs themselves
# are NOT here: they need the tables to exist, so they belong to
# `app.ops.provision`, which runs after Alembic.
#
# The five roles and why they are five:
#   aizzak_owner       -- migrator and table owner. Runs Alembic. Never
#                         serves a request.
#   app_rw             -- what the API and the Streams workers connect as.
#                         NOINHERIT, no BYPASSRLS, not an owner, so the RLS
#                         policy (01 §3) is what confines it to one
#                         workspace. FORCE ROW LEVEL SECURITY means even the
#                         owner would be subject to policy, but app_rw not
#                         owning the tables is the belt to that suspenders.
#   outbox_relay       -- SELECT/UPDATE on platform.outbox and nothing else,
#                         the mirror image of app_rw's INSERT-only grant
#                         there. A producer that could UPDATE published_at
#                         could make an event vanish unpublished (D-18).
#   retention_sweeper  -- SELECT/DELETE on the three unbounded platform
#                         ledgers (outbox, processed_events, idempotency_keys)
#                         and nothing else -- the age-based retention sweep
#                         (`python -m app.ops.retention`, P1-5). Never a
#                         standing service: an operator runs it by hand, the
#                         same footing `python -m app.ops.dlq` stands on.
#                         MUST still be created here -- `app.ops.provision`'s
#                         `_require_roles` check fails the WHOLE provisioning
#                         run before a single migration if this role is
#                         missing, because `0003_retention_sweep.py` issues
#                         `CREATE POLICY ... TO retention_sweeper`.
#   metrics_reader     -- SELECT-only on platform.outbox and nothing else --
#                         the `/metrics` endpoint's own read (P1-3, `python -m
#                         app.ops.provision`'s `METRICS_GRANTS`). `app_rw`
#                         cannot answer "how old is the oldest unpublished
#                         row" over its own INSERT-only connection at all, so
#                         this is a role of its own rather than a widened
#                         `app_rw` grant -- unlike `retention_sweeper` this
#                         IS a standing consumer (the `app` process, every
#                         scrape), the same footing `outbox_relay` stands on.
#   transit_rotator    -- SELECT plus column-scoped UPDATE (the ciphertext
#                         column alone) on the three Transit-ciphertext-
#                         bearing tables (credentials.credentials,
#                         integrations.connections, integrations.mcp_servers)
#                         and nothing else -- the Transit key-rotation sweep
#                         (`python -m app.ops.rotate_transit`, P1-9). Never a
#                         standing service, the `retention_sweeper` footing.
#                         MUST still be created here for the same reason
#                         retention_sweeper must: `migrations/versions/
#                         credentials/0002_transit_rotator.py` and
#                         `integrations/0002_transit_rotator.py` both issue
#                         `CREATE POLICY ... TO transit_rotator`, which
#                         `app.ops.provision`'s `_require_roles` check fails
#                         fast on BEFORE any migration if the role is absent.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "${POSTGRES_USER}" \
     --dbname "${POSTGRES_DB}" \
     --set db_name="${POSTGRES_DB}" \
     --set owner_password="${AIZZAK_OWNER_PASSWORD}" \
     --set app_password="${APP_RW_PASSWORD}" \
     --set relay_password="${OUTBOX_RELAY_PASSWORD}" \
     --set retention_password="${RETENTION_SWEEPER_PASSWORD}" \
     --set metrics_password="${METRICS_READER_PASSWORD}" \
     --set rotator_password="${TRANSIT_ROTATOR_PASSWORD}" <<-'EOSQL'
    -- CREATE ROLE has no IF NOT EXISTS clause of its own.
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'aizzak_owner') THEN
            CREATE ROLE aizzak_owner LOGIN;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'app_rw') THEN
            CREATE ROLE app_rw LOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'outbox_relay') THEN
            CREATE ROLE outbox_relay LOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'retention_sweeper') THEN
            CREATE ROLE retention_sweeper LOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'metrics_reader') THEN
            CREATE ROLE metrics_reader LOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'transit_rotator') THEN
            CREATE ROLE transit_rotator LOGIN NOINHERIT;
        END IF;
    END
    $$;

    -- Passwords are set separately from creation so this script stays
    -- re-runnable, and passed as psql variables rather than interpolated
    -- into the heredoc by the shell.
    ALTER ROLE aizzak_owner      PASSWORD :'owner_password';
    ALTER ROLE app_rw            PASSWORD :'app_password';
    ALTER ROLE outbox_relay      PASSWORD :'relay_password';
    ALTER ROLE retention_sweeper PASSWORD :'retention_password';
    ALTER ROLE metrics_reader    PASSWORD :'metrics_password';
    ALTER ROLE transit_rotator   PASSWORD :'rotator_password';

    -- The migrator needs to create schemas in this database; nobody else does.
    GRANT CREATE, CONNECT ON DATABASE :"db_name" TO aizzak_owner;
    GRANT CONNECT ON DATABASE :"db_name" TO app_rw, outbox_relay, retention_sweeper, metrics_reader,
        transit_rotator;

    -- PG15+ no longer grants CREATE on public to PUBLIC; make that explicit
    -- rather than relying on the default, and keep app_rw out of it.
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;

    -- ...but the migrator DOES need CREATE on public, and the reason is a
    -- real asymmetry rather than an oversight: the ten module chains each
    -- record their revision in their own version_table_schema (DAT-03), while
    -- the platform BASELINE chain passes no `-x vts=` at all and so lands its
    -- `alembic_version` in `public`. Found the hard way on the first
    -- containerised boot -- "permission denied for schema public" on
    -- CREATE TABLE alembic_version, before a single migration had run. The
    -- grant is deliberately narrow: aizzak_owner only, never app_rw.
    GRANT CREATE, USAGE ON SCHEMA public TO aizzak_owner;
EOSQL

echo "initdb: roles aizzak_owner / app_rw / outbox_relay / retention_sweeper / metrics_reader / transit_rotator ready"
