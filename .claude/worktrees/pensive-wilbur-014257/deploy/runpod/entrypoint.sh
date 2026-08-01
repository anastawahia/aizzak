#!/usr/bin/env bash
# AIZZAK RunPod entrypoint -- everything that must happen BEFORE any daemon
# starts, and nothing that can be done after.
#
# Three jobs, in order:
#   1. Resolve configuration (file on the volume, then environment, then
#      RunPod-derived defaults) and FAIL FAST on anything missing. A stack
#      that boots half-configured and dies twenty seconds later inside a
#      supervisor log is the single worst thing to debug on a remote Pod.
#   2. Lay out the persistent tree under /workspace and initialise the
#      Postgres cluster if this is the first boot.
#   3. Hand over to supervisord, which owns every process from here on.
set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*"; }
die() { printf '[entrypoint] ⛔ %s\n' "$*" >&2; exit 1; }

# ──────────────────────────────────────────────────────────────────────────
# 1. Configuration
# ──────────────────────────────────────────────────────────────────────────

# An optional .env ON THE PERSISTENT VOLUME. This exists so an operator can
# keep twenty settings in one file they edit over SSH instead of twenty boxes
# in a web form -- and so a Pod recreated from the same volume comes back
# configured. Values already present in the environment WIN over the file:
# RunPod template variables are the more explicit source, and a stale file
# silently overriding them would be a trap.
ENV_FILE="${AIZZAK_ENV_FILE:-/workspace/.env}"
if [ -f "$ENV_FILE" ]; then
    log "reading $ENV_FILE (environment takes precedence)"
    while IFS='=' read -r key value; do
        # ⚠️ An `if`, NOT `[ -z … ] && export …`. Under `set -e` an AND-list
        # that ends false IS a failing last command in the loop body, so the
        # short form kills the entrypoint the moment it meets a key already
        # present in the environment -- which, since template variables
        # deliberately win, is the ORDINARY case, not the edge one.
        if [ -z "${!key:-}" ]; then
            # Strip one layer of surrounding quotes: KEY="value" and
            # KEY='value' are both things people write in a .env, and neither
            # means the quotes are part of the value.
            case "$value" in
                \"*\") value="${value#\"}"; value="${value%\"}" ;;
                \'*\') value="${value#\'}"; value="${value%\'}" ;;
            esac
            export "$key=$value"
        fi
    done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" || true)
fi

# ── secrets with no safe default ──────────────────────────────────────────
# These are the container BOOTSTRAP passwords, and they are an exception by
# necessity, not by oversight (.env.example's own note): Postgres and MinIO
# must start with a password before anything can read a password out of
# Vault. deploy/vault/bootstrap.sh then seeds the very same values into
# Vault, which is the only copy src/ ever reads.
for required in \
    POSTGRES_SUPERUSER_PASSWORD \
    AIZZAK_OWNER_PASSWORD \
    APP_RW_PASSWORD \
    OUTBOX_RELAY_PASSWORD \
    RETENTION_SWEEPER_PASSWORD \
    METRICS_READER_PASSWORD \
    TRANSIT_ROTATOR_PASSWORD \
    MINIO_ROOT_USER \
    MINIO_ROOT_PASSWORD \
    FIREBASE_PROJECT_ID
do
    [ -n "${!required:-}" ] || die "$required is not set. \
It has no default and the stack cannot boot without it. \
Set it in the RunPod template's environment variables, or in $ENV_FILE."
done

# FIREBASE_PROJECT_ID deserves its own word: FirebaseAuth fails fast at
# CONSTRUCTION on an empty value (_guard_project_id), so the symptom of
# forgetting it is not a 401 later -- it is an app that never starts.

export APP_ENV="${APP_ENV:-production}"
export APP_HOST=0.0.0.0
export APP_PORT=8000
export API_PREFIX="${API_PREFIX:-/api/v1}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

export POSTGRES_DB="${POSTGRES_DB:-aizzak}"
export POSTGRES_SUPERUSER="${POSTGRES_SUPERUSER:-postgres}"
export MINIO_BUCKET="${MINIO_BUCKET:-workspace-files}"

# ── internal addresses ────────────────────────────────────────────────────
# Every one of these is a loopback address, and that is the whole security
# posture of this image: only nginx (:80) and MinIO (:9000/:9001) bind a
# routable interface. Postgres, Redis, Qdrant, Vault, the embedding service,
# Ollama and gunicorn are unreachable from outside the Pod by construction --
# the same trust boundary the Compose network draws, drawn with interfaces
# instead of with a network.
export DATABASE_URL="postgresql+asyncpg://app_rw:${APP_RW_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
# The relay's OWN role: SELECT/UPDATE on platform.outbox and nothing else --
# the mirror image of app_rw's INSERT-only grant there. A producer that could
# UPDATE published_at could make an event vanish unpublished (D-18). Composed
# HERE rather than in supervisord.conf so no password is ever written into a
# config file that supervisor runs `%`-expansion over.
export RELAY_DATABASE_URL="postgresql+asyncpg://outbox_relay:${OUTBOX_RELAY_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
# No RETENTION_DATABASE_URL export here, deliberately -- unlike outbox_relay
# (a supervisord-managed standing service, RELAY_DATABASE_URL above), nothing
# in this image runs `python -m app.ops.retention` automatically: it is a
# manually-invoked one-shot tool (P1-5, no periodic scheduling in scope), so
# there is no standing consumer to compose this URL for. An operator running
# it by hand builds the DSN inline from RETENTION_SWEEPER_PASSWORD (already
# required above, so always present) at invocation time -- see
# 08-local-runbook.md §4.3.
# METRICS_DATABASE_URL, by contrast, DOES need exporting here -- unlike
# retention_sweeper, `metrics_reader` (P1-3, p1-hardening-plan.md §3 step 10)
# IS a standing consumer: the `app` program below (every gunicorn worker
# sibling) reads it on every `/metrics` scrape, the same footing
# RELAY_DATABASE_URL stands on.
export METRICS_DATABASE_URL="postgresql+asyncpg://metrics_reader:${METRICS_READER_PASSWORD}@127.0.0.1:5432/${POSTGRES_DB}"
# No TRANSIT_ROTATOR_DATABASE_URL export here, deliberately -- the
# retention_sweeper precedent above: nothing in this image runs
# `python -m app.ops.rotate_transit` automatically, it is a manually-invoked
# one-shot tool (P1-9, no periodic scheduling in scope), so there is no
# standing consumer to compose this URL for. An operator running it by hand
# builds the DSN inline from TRANSIT_ROTATOR_PASSWORD (already required
# above, so always present) at invocation time -- see
# 08-local-runbook.md §4.5.
export REDIS_URL="redis://127.0.0.1:6379/0"
export MINIO_ENDPOINT="127.0.0.1:9000"
export MINIO_SECURE=false
export QDRANT_URL="http://127.0.0.1:6333"
export EMBEDDING_SERVICE_URL="http://127.0.0.1:8080"
export VAULT_ADDR="http://127.0.0.1:8200"
export OLLAMA_BASE_URL="http://127.0.0.1:11434"

export DB_POOL_SIZE="${DB_POOL_SIZE:-10}"
export DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-20}"
export WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"

# ── PUBLIC addresses, derived from the Pod's own identity ─────────────────
# RunPod injects RUNPOD_POD_ID into every Pod, and its HTTP proxy answers at
# https://<POD_ID>-<INTERNAL_PORT>.proxy.runpod.net. Deriving these means the
# operator does not have to create the Pod, read its id, edit the template
# and restart -- which is otherwise the actual first-boot experience.
#
# ⚠️ MINIO_PUBLIC_ENDPOINT is not cosmetic. Presigned URLs are signed with
# SigV4, which covers the HOST; a URL signed against 127.0.0.1:9000 cannot be
# repointed at the browser afterwards -- not by nginx, not by string surgery.
if [ -n "${RUNPOD_POD_ID:-}" ]; then
    export MINIO_PUBLIC_ENDPOINT="${MINIO_PUBLIC_ENDPOINT:-${RUNPOD_POD_ID}-9000.proxy.runpod.net}"
    export MINIO_PUBLIC_SECURE="${MINIO_PUBLIC_SECURE:-true}"
    export OAUTH_REDIRECT_BASE_URL="${OAUTH_REDIRECT_BASE_URL:-https://${RUNPOD_POD_ID}-80.proxy.runpod.net}"
    export PUBLIC_BASE_URL="https://${RUNPOD_POD_ID}-80.proxy.runpod.net"
else
    log "⚠️  RUNPOD_POD_ID is unset -- not running on RunPod? Falling back to localhost."
    export MINIO_PUBLIC_ENDPOINT="${MINIO_PUBLIC_ENDPOINT:-localhost:9000}"
    export MINIO_PUBLIC_SECURE="${MINIO_PUBLIC_SECURE:-false}"
    export OAUTH_REDIRECT_BASE_URL="${OAUTH_REDIRECT_BASE_URL:-http://localhost}"
    export PUBLIC_BASE_URL="http://localhost"
fi

export FIREBASE_JWKS_CACHE_TTL="${FIREBASE_JWKS_CACHE_TTL:-3600}"
export EVENT_STREAM_PREFIX="${EVENT_STREAM_PREFIX:-stream.}"
export OUTBOX_POLL_INTERVAL_MS="${OUTBOX_POLL_INTERVAL_MS:-500}"
export CONSUMER_BLOCK_MS="${CONSUMER_BLOCK_MS:-5000}"
export MAX_RETRIES_BEFORE_DLQ="${MAX_RETRIES_BEFORE_DLQ:-5}"
export OUTBOX_RELAY_BATCH_SIZE="${OUTBOX_RELAY_BATCH_SIZE:-256}"
export CONSUMER_BATCH_COUNT="${CONSUMER_BATCH_COUNT:-16}"
export STREAM_MAXLEN="${STREAM_MAXLEN:-100000}"
export MCP_ALLOWED_TRANSPORTS="${MCP_ALLOWED_TRANSPORTS:-http,sse}"
export OAUTH_REFRESH_SKEW_S="${OAUTH_REFRESH_SKEW_S:-60}"
export USAGE_ROLLUP_PERIODS="${USAGE_ROLLUP_PERIODS:-day,month}"
export USAGE_DEFAULT_LIMITS="${USAGE_DEFAULT_LIMITS:-{\"tokens\":{\"month\":5000000},\"cost_micros\":{\"month\":50000000}}}"

# ⚠️ memory is the ONLY worker that boots today. knowledge needs
# DocumentContentResolver and media needs MediaGenerator -- both are tracked
# debt, and either value here produces a process that crash-loops forever and
# makes a healthy stack look broken.
export WORKER="${WORKER:-memory}"

# The model the routing table names below must be the model pulled at
# bootstrap, or every LLM call 404s at Ollama.
export OLLAMA_MODEL="${OLLAMA_MODEL:-gemma3:1b}"
export PROVIDER_ROUTING="${PROVIDER_ROUTING:-{\"llm\":{\"default\":{\"provider\":\"ollama\",\"model\":\"${OLLAMA_MODEL}\"}},\"embedding\":{\"default\":{\"provider\":\"embedding-local\",\"model\":\"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2\"}}}}"

export OLLAMA_MODELS=/workspace/data/ollama
export OLLAMA_HOST=127.0.0.1:11434

# ── the Compose service names, resolved to loopback ───────────────────────
# `minio`, `postgres`, `redis`, ... all mean 127.0.0.1 inside this Pod. This
# is not decoration: it lets deploy/minio/bootstrap.sh -- which names
# `http://minio:9000` literally -- run UNCHANGED here, so the RunPod path
# keeps using the repository's own bootstrap scripts instead of forked copies
# that drift. Anything else in the tree that speaks Compose service names
# keeps working for the same reason.
if ! grep -q 'aizzak-runpod-aliases' /etc/hosts 2>/dev/null; then
    printf '127.0.0.1 postgres redis minio qdrant vault embedding app # aizzak-runpod-aliases\n' >> /etc/hosts
fi

# ──────────────────────────────────────────────────────────────────────────
# 2. The persistent tree
# ──────────────────────────────────────────────────────────────────────────
# EVERYTHING stateful lives under /workspace, which is where RunPod mounts a
# volume. The container filesystem is discarded when the Pod is stopped;
# anything written outside /workspace is written to be lost.
DATA=/workspace/data
export PGDATA="$DATA/postgres"

# Vault persists now (release-blockers-plan.md §3 step 1): no dev root token
# exists anymore. `deploy/vault/start.sh` (run by supervisord as
# [program:vault]) mints the real one via `operator init` on first boot and
# records it, with the unseal key, under $DATA/vault-init -- see that
# script's header and docs/deploy-runpod.md §8.1 for what this trades away
# (no KMS on a Pod, so both live in a plain file, 0600) and the upgrade path.
# VAULT_TOKEN stays UNSET: AppRole (below) is the mode this image uses, and
# aizzak-bootstrap.sh reads the real root token out of $DATA/vault-init
# itself for the one bootstrap step that still needs it. `vault` and
# `vault-init` are kept as TWO directories, not one, so the data directory
# stays exactly what Vault itself owns (same reasoning as docker-compose.yml's
# separate `vault-data`/`vault-init` volumes).
export VAULT_DATA_DIR="$DATA/vault"
export VAULT_INIT_DIR="$DATA/vault-init"
export VAULT_LISTEN_ADDR=127.0.0.1

if ! mountpoint -q /workspace 2>/dev/null; then
    log "⚠️  /workspace is NOT a mounted volume. All data will be LOST when this"
    log "⚠️  Pod stops. Attach a volume or network volume at /workspace."
fi

mkdir -p "$DATA"/{postgres,redis,minio,qdrant,ollama,logs,vault,vault-init} \
         /var/run/postgresql /var/lib/nginx /var/log/nginx /run
chown -R postgres:postgres "$DATA/postgres" /var/run/postgresql
chown -R aizzak:aizzak "$DATA/redis" "$DATA/minio" "$DATA/qdrant" "$DATA/ollama" "$DATA/logs" "$DATA/vault" "$DATA/vault-init"
chmod 700 "$DATA/postgres"

# ── first boot: initialise the Postgres cluster ───────────────────────────
if [ ! -s "$PGDATA/PG_VERSION" ]; then
    log "initialising a new PostgreSQL 16 cluster at $PGDATA"
    PWFILE="$(mktemp)"
    printf '%s' "$POSTGRES_SUPERUSER_PASSWORD" > "$PWFILE"
    chown postgres:postgres "$PWFILE"
    gosu postgres /usr/lib/postgresql/16/bin/initdb \
        --pgdata="$PGDATA" \
        --username="$POSTGRES_SUPERUSER" \
        --pwfile="$PWFILE" \
        --encoding=UTF8 --locale=C.UTF-8 \
        --auth-local=peer --auth-host=scram-sha-256 >/dev/null
    rm -f "$PWFILE"

    # Loopback only. Nothing outside this Pod may speak to Postgres, and the
    # absence of a listener is a stronger statement than a firewall rule.
    {
        echo "listen_addresses = '127.0.0.1'"
        echo "password_encryption = scram-sha-256"
        echo "max_connections = 200"
    } >> "$PGDATA/postgresql.conf"
    echo "host all all 127.0.0.1/32 scram-sha-256" >> "$PGDATA/pg_hba.conf"
    log "cluster initialised"
else
    log "existing PostgreSQL cluster found at $PGDATA -- leaving it alone"
fi

# ── SSH, so there is a way in when something goes wrong ───────────────────
# RunPod's official templates ship sshd; a CUSTOM image does not get it for
# free, and a Pod you cannot open a shell on is a Pod you cannot diagnose.
# RunPod injects the account's public key as PUBLIC_KEY. No key, no sshd --
# password authentication is never enabled, so an unset PUBLIC_KEY simply
# means no remote shell rather than an open one.
mkdir -p /run/sshd /root/.ssh
chmod 700 /root/.ssh
ssh-keygen -A >/dev/null 2>&1 || true
if [ -n "${PUBLIC_KEY:-}" ]; then
    printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    log "SSH authorised key installed"
else
    log "PUBLIC_KEY unset -- sshd will accept no logins"
fi

# ──────────────────────────────────────────────────────────────────────────
# 3. Hand over
# ──────────────────────────────────────────────────────────────────────────
log "public base URL: ${PUBLIC_BASE_URL}"
log "starting supervisord"
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf -n
