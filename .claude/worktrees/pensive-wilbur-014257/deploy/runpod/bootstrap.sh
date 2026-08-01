#!/usr/bin/env bash
# One-shot boot sequence, run by supervisord AFTER the data plane is up and
# BEFORE the application plane starts.
#
# This script is the RunPod stand-in for what docker-compose.yml expresses
# declaratively -- `depends_on: {condition: service_healthy}` and the three
# one-shot services. Supervisor has no notion of either, so the ordering that
# Compose encodes in YAML is written out here as waits and explicit starts.
# The steps and their order are 08-local-runbook §3's, unchanged:
#
#   roles -> Vault -> MinIO bucket -> migrations+grants -> workers -> relay
#
# IDEMPOTENT throughout: it runs on every Pod start, and a second run must be
# a no-op, not an error. That is what makes "restart the Pod" a valid repair.
set -euo pipefail

log()  { printf '[bootstrap] %s\n' "$*"; }
fail() { printf '[bootstrap] ⛔ %s\n' "$*" >&2; exit 1; }

wait_for() {
    local what="$1" tries="$2"; shift 2
    log "waiting for ${what}"
    for _ in $(seq 1 "$tries"); do
        if "$@" >/dev/null 2>&1; then log "${what} is up"; return 0; fi
        sleep 2
    done
    fail "${what} did not come up within $(( tries * 2 ))s"
}

export PGHOST=127.0.0.1
export PGPORT=5432
export PGPASSWORD="$POSTGRES_SUPERUSER_PASSWORD"

# ── 1. Postgres: database + the six roles ─────────────────────────────────
wait_for "PostgreSQL" 60 pg_isready -h 127.0.0.1 -p 5432 -U "$POSTGRES_SUPERUSER"

if ! psql -U "$POSTGRES_SUPERUSER" -d postgres -tAc \
        "SELECT 1 FROM pg_database WHERE datname='${POSTGRES_DB}'" | grep -q 1; then
    log "creating database ${POSTGRES_DB}"
    createdb -U "$POSTGRES_SUPERUSER" -O "$POSTGRES_SUPERUSER" "$POSTGRES_DB"
fi

# The repository's own script, verbatim. CREATE ROLE is a cluster-wide
# privilege that aizzak_owner deliberately does not hold, which is why role
# creation is a superuser step here and the GRANTs are a separate step below
# (they need the tables to exist -- 01 §6).
log "creating roles aizzak_owner / app_rw / outbox_relay / retention_sweeper / metrics_reader / transit_rotator"
POSTGRES_USER="$POSTGRES_SUPERUSER" bash /app/deploy/postgres/initdb/10-roles.sh

# ── 2. Vault: engines, seeded secrets, AppRole ────────────────────────────
# Vault persists now (release-blockers-plan.md §3 step 1): `[program:vault]`
# runs `deploy/vault/start.sh`, which drives it through `operator init`
# (first boot) or `operator unseal` (every boot after) BEFORE `vault status`
# can ever report success -- so `wait_for` below only returns once Vault is
# genuinely unsealed and usable, not merely listening.
export VAULT_ADDR="http://127.0.0.1:8200"
wait_for "Vault" 60 vault status

# There is no dev root token anymore. start.sh already wrote the real one,
# with the unseal key, to ${VAULT_INIT_DIR}/init.json (0600) -- read it back
# rather than inventing a second place that knows Vault's root token.
VAULT_TOKEN="$(sed -n '/root_token/p' "${VAULT_INIT_DIR}/init.json" | sed 's/.*"root_token": *"\([^"]*\)".*/\1/')"
[ -n "$VAULT_TOKEN" ] || fail "could not read a root token out of ${VAULT_INIT_DIR}/init.json"
export VAULT_TOKEN

log "seeding Vault (KV v2 + Transit + AppRole)"
AIZZAK_APPROLE_ROLE_ID="${VAULT_ROLE_ID:-}" \
    sh /app/deploy/vault/bootstrap.sh

# ── 3. MinIO: the object bucket ───────────────────────────────────────────
wait_for "MinIO" 60 curl -fsS "http://127.0.0.1:9000/minio/health/live"

log "creating bucket ${MINIO_BUCKET}"
# Resolves `minio` via the /etc/hosts alias the entrypoint installed.
sh /app/deploy/minio/bootstrap.sh

# ── 4. Migrations and grants ──────────────────────────────────────────────
# ⚠️ NOT `alembic upgrade head`. v1 runs ELEVEN independent chains (one
# version_table_schema per module), so `head` is ambiguous and Alembic
# refuses it outright. app.ops.provision encodes the real sequence AND the
# grants that follow it -- without the grants, app_rw holds no privilege on
# any table and the first request that touches one answers "permission
# denied".
#
# Runs as aizzak_owner, DIRECTLY on 5432: the migrator is the table owner and
# never serves a request.
log "running migrations + grants (app.ops.provision)"
DATABASE_URL="postgresql+asyncpg://aizzak_owner:${AIZZAK_OWNER_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}" \
    /opt/venv/bin/python -m app.ops.provision \
    || fail "provisioning failed -- see the traceback above"

# ── 5. Ollama: the model the routing table names ──────────────────────────
# PROVIDER_ROUTING names ${OLLAMA_MODEL}; if it is not pulled, every LLM call
# fails at the provider with a 404 that looks like a routing bug.
wait_for "Ollama" 90 curl -fsS "http://127.0.0.1:11434/api/tags"

if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "${OLLAMA_MODEL}"; then
    log "pulling ${OLLAMA_MODEL} (first boot only -- this can take minutes)"
    ollama pull "${OLLAMA_MODEL}" || log "⚠️  pull failed; LLM calls will 404 until it succeeds"
else
    log "${OLLAMA_MODEL} already present"
fi

# ── 6. Embedding service ──────────────────────────────────────────────────
# It loads a 470 MB model into memory on first request path; the app's
# POST /search answers 503 until it is ready, so wait rather than race.
wait_for "embedding service" 90 curl -fsS "http://127.0.0.1:8080/health"

# ── 7. The application plane, in the ONE order that is safe ───────────────
# ⚠️ WORKERS BEFORE THE RELAY, and this is not stylistic. Redis consumer
# groups are created at `$` (the stream tail), so a group created AFTER the
# relay has published to a brand-new stream will never see those entries --
# they are gone, and the outbox row is already marked published. On an
# existing stream the order does not matter; on a first boot it is the
# difference between a working pipeline and silently dropped events.
log "starting workers"
supervisorctl -c /etc/supervisor/supervisord.conf start worker
sleep 3

log "starting outbox relay"
supervisorctl -c /etc/supervisor/supervisord.conf start outbox-relay

log "starting the API"
supervisorctl -c /etc/supervisor/supervisord.conf start app

wait_for "the API" 60 curl -fsS "http://127.0.0.1:8000/health"

log "starting nginx"
supervisorctl -c /etc/supervisor/supervisord.conf start nginx

log "✅ boot complete -- ${PUBLIC_BASE_URL:-http://localhost}/health"
