#!/bin/bash
# Cluster init: create the eight application roles (7.1 · 01-data-model §6 ·
# P1-5/p1-hardening-plan.md §3 step 8 added the fourth · P1-3/step 10 added
# the fifth · P1-9/step 12 added the sixth · BE-ADM-014 added the seventh ·
# capacity step 2.5 added the eighth).
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
#   workspace_purger   -- SELECT/DELETE on every tenant table a purged
#                         workspace's content lives in, plus column-scoped
#                         SELECT/UPDATE on workspace.workspaces/workspace.users
#                         and SELECT/INSERT on platform.admin_audit_log --
#                         the workspace content-purge sweep (`python -m
#                         app.ops.purge`, BE-ADM-014). Never a standing
#                         service, the `retention_sweeper`/`transit_rotator`
#                         footing. MUST still be created here for the same
#                         reason those two must: `migrations/versions/
#                         workspace/0009_workspace_purge.py` issues
#                         `CREATE POLICY ... TO workspace_purger`, which
#                         `app.ops.provision`'s `_require_roles` check fails
#                         fast on BEFORE any migration if the role is absent.
#   backup_operator    -- the ONLY role that may read every tenant's rows, and
#                         the only one with REPLICATION (`python -m
#                         app.ops.backup`, capacity step 2.5). Three
#                         attributes, each earning its place separately:
#                           * REPLICATION -- `pg_basebackup` opens a physical
#                             replication connection; without it there is no
#                             base backup, and without a base backup there is
#                             no point in time to recover to.
#                           * BYPASSRLS -- every tenant table is under FORCE
#                             ROW LEVEL SECURITY, which subjects the OWNER to
#                             the policy too. MEASURED on this stack: pg_dump
#                             as `aizzak_owner` fails on the first tenant
#                             table, and with `--enable-row-security` it exits
#                             0 having written 202 workspaces and ZERO users.
#                             A backup that succeeds and contains nothing.
#                           * pg_read_all_data -- SELECT on every table,
#                             including tables whose migrations are not
#                             written yet. A backup role built from a
#                             hand-maintained grant list stops covering new
#                             tables silently: the same failure with a slower
#                             fuse.
#                         ⚠️ AND IT IS THE ONE ROLE HERE THAT IS *INHERIT*.
#                         Every role above is NOINHERIT deliberately -- a role
#                         must not silently wield privileges granted to a role
#                         it merely belongs to. But `pg_read_all_data` IS a
#                         membership, so under NOINHERIT it is inert until the
#                         session runs `SET ROLE`, and MEASURED here that is
#                         not a subtle degradation: `SELECT count(*) FROM
#                         workspace.users` answered "permission denied for
#                         schema workspace" and pg_dump would have failed the
#                         same way. Nothing is widened by the change -- the
#                         membership is the privilege this role was created
#                         to have, and it already holds BYPASSRLS outright.
#                         It holds no INSERT/UPDATE/DELETE anywhere. Unlike
#                         the six above it is deliberately NOT in
#                         `_require_roles`: no migration issues a policy
#                         naming it and `provision` grants it nothing, so a
#                         cluster without it migrates perfectly well and only
#                         the backup tool refuses (`app.ops.backup.preflight`
#                         says so by name instead of failing at pg_dump).
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
     --set rotator_password="${TRANSIT_ROTATOR_PASSWORD}" \
     --set purger_password="${WORKSPACE_PURGER_PASSWORD}" \
     --set backup_password="${BACKUP_OPERATOR_PASSWORD}" <<-'EOSQL'
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
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'workspace_purger') THEN
            CREATE ROLE workspace_purger LOGIN NOINHERIT;
        END IF;
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'backup_operator') THEN
            CREATE ROLE backup_operator LOGIN INHERIT REPLICATION BYPASSRLS;
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
    ALTER ROLE workspace_purger  PASSWORD :'purger_password';
    ALTER ROLE backup_operator   PASSWORD :'backup_password';

    -- Re-asserted on every run rather than only at CREATE. An operator
    -- adding this role by hand to an EXISTING cluster (08 §3.3-ب) may
    -- create it without either attribute, and a backup role missing
    -- BYPASSRLS is the empty-dump failure described above.
    ALTER ROLE backup_operator   INHERIT REPLICATION BYPASSRLS;

    -- The migrator needs to create schemas in this database; nobody else does.
    GRANT CREATE, CONNECT ON DATABASE :"db_name" TO aizzak_owner;
    GRANT CONNECT ON DATABASE :"db_name" TO app_rw, outbox_relay, retention_sweeper, metrics_reader,
        transit_rotator, workspace_purger, backup_operator;

    -- SELECT on every table, present and future, with no grant list to
    -- maintain. A PREDEFINED role, and it deliberately does NOT bypass
    -- row-level security -- which is why the BYPASSRLS attribute above is
    -- a separate thing and not a duplicate of this line.
    --
    -- ⚠️ `WITH INHERIT TRUE` IS LOAD-BEARING ON POSTGRESQL 16, and this is
    -- the second half of the NOINHERIT lesson above. Since 16 the inherit
    -- option is recorded PER MEMBERSHIP (`pg_auth_members.inherit_option`)
    -- and defaults to the member's `rolinherit` AS IT WAS AT GRANT TIME --
    -- so a later `ALTER ROLE backup_operator INHERIT` does not revive an
    -- existing grant. MEASURED here: the attribute read INHERIT, the
    -- membership read `inherit_option = f`, and every read still answered
    -- "permission denied for schema workspace". Stating it explicitly makes
    -- this line correct on a fresh cluster and on one an operator repaired
    -- by hand, in either order.
    GRANT pg_read_all_data TO backup_operator WITH INHERIT TRUE;

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

echo "initdb: roles aizzak_owner / app_rw / outbox_relay / retention_sweeper / metrics_reader / transit_rotator / workspace_purger / backup_operator ready"
