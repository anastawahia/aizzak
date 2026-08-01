#!/bin/sh
# Vault startup wrapper -- persistent `file` storage + auto-unseal
# (release-blockers-plan.md §3 step 1; rationale + upgrade path in
# docs/quickstart.md §5).
#
# Runs as this container's ENTRYPOINT, REPLACING hashicorp/vault's own
# docker-entrypoint.sh. That script `exec`s straight into `vault server` and
# never returns, which leaves no room to run `operator init`/`operator
# unseal` afterward in the SAME process -- and that is exactly what a
# healthcheck-driven `depends_on: condition: service_healthy` needs here:
# the healthcheck IS `vault status`, which only reports success (exit 0)
# once Vault is unsealed. So THIS script starts the server itself, drives it
# through init/unseal, and only then sits on it (`wait`) so the container's
# lifecycle stays tied to the server process and signals still forward.
#
# ⚠️ SINGLE LOCAL HOST ONLY -- NOT ACCEPTABLE FOR REAL PRODUCTION. There is
# no KMS here to auto-unseal against, so the unseal key AND the root token
# are written to a plain file on disk (`chmod 600`). Anyone who can read
# this container's volumes can unseal Vault and read every tenant secret it
# guards -- and that includes any operator or backup process with host
# access, not just an attacker. The real upgrade path is auto-unseal via a
# cloud KMS (AWS KMS / GCP CKMS / Azure Key Vault -- Vault has a `seal "..."`
# stanza for each) or an external Vault used purely as a Transit-unseal
# provider; either one needs that KMS/Vault to already exist, so it cannot
# be turned on by editing this file alone. See docs/quickstart.md §5 before
# this stack ever holds one real tenant credential.
#
# Runs entirely as root -- this container's default user, and the ONLY
# process in it. No attempt is made to drop to the vault image's own
# unprivileged `vault` user: doing so would only isolate this process from a
# root that already owns every volume mounted here, which is not a boundary
# this single-host deployment has. `disable_mlock` in server.hcl is the same
# trade, explained there.
set -eu

VAULT_DATA_DIR="${VAULT_DATA_DIR:-/vault/data}"
VAULT_INIT_DIR="${VAULT_INIT_DIR:-/vault/init}"
VAULT_LISTEN_ADDR="${VAULT_LISTEN_ADDR:-0.0.0.0}"
VAULT_CONFIG_TEMPLATE="${VAULT_CONFIG_TEMPLATE:-/vault/config/server.hcl}"
VAULT_CONFIG_RENDERED="/tmp/vault-server-rendered.hcl"
INIT_FILE="$VAULT_INIT_DIR/init.json"

log() { printf 'vault-start: %s\n' "$*"; }
die() { printf 'vault-start: \xe2\x9b\x94 %s\n' "$*" >&2; exit 1; }

# Extracts the first (and, with -key-shares=1, only) base64 unseal key out of
# a `vault operator init -format=json` document. No `jq` in this image --
# busybox grep/sed cover it: the array spans two lines when pretty-printed,
# so isolate the `unseal_keys_b64` block first, then pull the quoted value.
extract_unseal_key() {
    sed -n '/unseal_keys_b64/,/\]/p' "$1" | grep -oE '"[A-Za-z0-9+/=]{20,}"' | head -1 | tr -d '"'
}

mkdir -p "$VAULT_DATA_DIR" "$VAULT_INIT_DIR"

sed -e "s#__VAULT_DATA_DIR__#${VAULT_DATA_DIR}#" \
    -e "s#__VAULT_LISTEN_ADDR__#${VAULT_LISTEN_ADDR}#" \
    "$VAULT_CONFIG_TEMPLATE" > "$VAULT_CONFIG_RENDERED"

export VAULT_ADDR="http://127.0.0.1:8200"

log "launching vault server (storage=file path=$VAULT_DATA_DIR listen=$VAULT_LISTEN_ADDR:8200)"
vault server -config="$VAULT_CONFIG_RENDERED" &
VAULT_PID=$!

# Forward a graceful shutdown to the server instead of letting the container
# runtime SIGKILL it -- file storage does not want an unclean stop.
term() {
    log "forwarding shutdown to vault (pid $VAULT_PID)"
    kill -TERM "$VAULT_PID" 2>/dev/null || true
    wait "$VAULT_PID" 2>/dev/null || true
    exit 0
}
trap term TERM INT

# ── wait for the HTTP listener ────────────────────────────────────────────
# `vault status` exits 1 on a connection error (not listening yet), 2 when
# sealed (listening, just not usable yet) and 0 when unsealed -- measured
# live against this exact image (1.18.5), because the exit codes an older
# Vault CLI used are NOT what this one does. Only exit 1 means "keep
# waiting"; 0 and 2 both mean the process is up.
tries=0
rc=1
while :; do
    if vault status >/dev/null 2>&1; then
        rc=0
    else
        rc=$?
    fi
    if [ "$rc" -ne 1 ]; then
        break
    fi
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
        die "the HTTP listener never came up (60s)"
    fi
    sleep 1
done

# ── initialize (first boot only) or unseal (every boot after) ────────────
if [ "$rc" -eq 0 ]; then
    log "already unsealed (unexpected for a freshly started process, harmless)"
elif [ -f "$INIT_FILE" ]; then
    log "sealed; unsealing from the recorded init material at $INIT_FILE"
    UNSEAL_KEY="$(extract_unseal_key "$INIT_FILE")"
    if [ -z "$UNSEAL_KEY" ]; then
        die "$INIT_FILE exists but no unseal key could be parsed out of it"
    fi
    if ! vault operator unseal "$UNSEAL_KEY" >/dev/null; then
        die "unseal FAILED with the recorded key -- see the output above"
    fi
else
    log "sealed with no init material on record -- attempting first-time initialization"
    if ! INIT_OUT="$(vault operator init -key-shares=1 -key-threshold=1 -format=json 2>&1)"; then
        die "'operator init' failed, and $INIT_FILE does not exist: $INIT_OUT
    If the message above says Vault is ALREADY initialized: the data
    directory ($VAULT_DATA_DIR) holds a previously-initialized store, but its
    unseal key/root token (normally recorded at $INIT_FILE) are missing --
    lost, or never written by this wrapper in the first place. There is NO
    way to recover access without that key: a fresh 'operator init' would
    only create a SEPARATE keyring that cannot open THIS data, which is
    exactly why this script refuses to try it automatically. Restore
    $INIT_FILE from a backup, or treat this data as permanently unreachable."
    fi
    printf '%s' "$INIT_OUT" > "$INIT_FILE"
    chmod 600 "$INIT_FILE"
    log "initialized; unseal key + root token recorded at $INIT_FILE (chmod 600)"
    UNSEAL_KEY="$(extract_unseal_key "$INIT_FILE")"
    if [ -z "$UNSEAL_KEY" ]; then
        die "initialized but could not parse the unseal key back out of $INIT_FILE"
    fi
    vault operator unseal "$UNSEAL_KEY" >/dev/null
fi

log "unsealed and ready"
wait "$VAULT_PID"
