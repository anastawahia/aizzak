#!/bin/sh
# Vault bootstrap (7.1 · 08-local-runbook §3 step 3 · release-blockers-plan.md
# §3 step 1).
#
# Enables KV v2 and Transit and seeds the secrets the app reads at startup.
# Idempotent throughout: Vault now persists (`file` storage, not `-dev`), so
# this script runs on EVERY `up` against SURVIVING state, not a wiped one --
# re-running must be a no-op, not an error, and MUST NEVER destroy or rotate
# the Transit key an earlier run already created (a regenerated key cannot
# decrypt ciphertext produced under the old one -- `CipherRef`/INV-C2/INV-I1).
#
# ⭐ `vault write -f transit/keys/tenant-secrets` on an ALREADY-EXISTING key
# is safe by Vault's own design, not by an `|| true` guard here: measured
# live against this exact image (hashicorp/vault:1.18) -- re-running it left
# `latest_version` unchanged and a ciphertext produced before the re-run
# still decrypted correctly afterward. Vault's "create key" endpoint is a
# no-op when the name already exists; it does NOT rotate. Rotation is a
# separate, unreachable call (`transit/keys/<name>/rotate`) that nothing in
# this script or in `src/` ever makes.
#
# The values seeded here also live in .env (gitignored). That duplication is
# the design's own model -- 08 §3 seeds Vault from the same values Compose
# uses to start Postgres and MinIO, because a container cannot read its own
# password out of Vault before it exists. `secret/data/*` is the ONLY path
# the application itself reads; nothing in src/ ever sees the .env copies.
#
# ⚠️ Nothing here is echoed. Tokens and keys are passed as arguments to
# `vault` and never printed (10 §10).
set -eu

# ── root token (release-blockers-plan.md §3 step 1 requirement 5) ─────────
# The dev root token no longer exists. `VAULT_TOKEN` arrives BLANK in the
# normal case (docker-compose.yml's own comment on this service explains
# why): read the real one out of the vault-init volume `start.sh` wrote at
# `operator init` time, mounted here read-only at /vault-init. An explicit
# `VAULT_TOKEN` (a manual operator override) always wins over the file.
if [ -z "${VAULT_TOKEN:-}" ]; then
    INIT_FILE="/vault-init/init.json"
    if [ ! -r "$INIT_FILE" ]; then
        echo "vault-bootstrap: no VAULT_TOKEN set and $INIT_FILE is not readable -- cannot authenticate to Vault" >&2
        exit 1
    fi
    VAULT_TOKEN="$(sed -n '/root_token/p' "$INIT_FILE" | sed 's/.*"root_token": *"\([^"]*\)".*/\1/')"
    if [ -z "$VAULT_TOKEN" ]; then
        echo "vault-bootstrap: $INIT_FILE exists but no root_token could be parsed out of it" >&2
        exit 1
    fi
    export VAULT_TOKEN
fi

echo "vault-bootstrap: waiting for vault"
until vault status >/dev/null 2>&1; do sleep 1; done

echo "vault-bootstrap: enabling secrets engines"
vault secrets enable -path=secret kv-v2 2>/dev/null || true
vault secrets enable transit 2>/dev/null || true

# SEC-07: ONE Transit key for both tenant secret sites (credentials + the
# integrations connectors), so they share a pattern and a rotation cycle.
vault write -f transit/keys/tenant-secrets >/dev/null 2>&1 || true

echo "vault-bootstrap: seeding secrets (05 §3.1)"
vault kv put secret/db \
    password="${AIZZAK_OWNER_PASSWORD}" \
    app_password="${APP_RW_PASSWORD}" >/dev/null
vault kv put secret/minio \
    access_key="${MINIO_ROOT_USER}" \
    secret_key="${MINIO_ROOT_PASSWORD}" >/dev/null

# ── AppRole (7.3 · D-22 · 08 §3.1) ──────────────────────────────────────
# Provisioned on EVERY environment including this one. Two reasons, both
# learned rather than assumed:
#   1. Vault's storage persists now (release-blockers-plan.md §3 step 1),
#      but this step still has to be the ONE idempotent place that can
#      re-create the role from nothing -- a wiped vault-data volume, a
#      brand-new host, or the very first boot all look identical to this
#      script, and all of them need the role created exactly once more.
#   2. An auth path that is only ever provisioned in production is an auth
#      path that is first exercised in production.
echo "vault-bootstrap: provisioning AppRole (D-22)"
vault auth enable approle 2>/dev/null || true

# The policy lives in its own file (deploy/vault/app-policy.hcl) rather than
# a heredoc here: it is the artifact a security review reads, and it is
# derived from the call sites in vault_secrets.py, not from this script.
vault policy write app /app-policy.hcl >/dev/null

# TTLs verbatim from 08 §3.1. ⚠️ `token_ttl` is load-bearing and short:
# `create_vault_client` logs in ONCE at composition and never renews, so the
# app's Vault access ends when this token expires -- see 08 §3.1's TTL note
# for the measurement and the operational consequence.
#
# `token_no_default_policy=true` is NOT in 08 §3.1 and is added deliberately.
# Vault attaches its built-in `default` policy on top of `token_policies`
# unless told otherwise, and `default` grants cubbyhole plus the token's own
# `lookup-self`/`renew-self`. None of it is reachable from `src/` -- so
# without this flag, app-policy.hcl's claim to be "the ENTIRE Vault surface"
# would simply be untrue, and the file would be documentation rather than a
# boundary.
# ⚠️ `secret_id_ttl` was 24h through P1 step 14 and that number caused a real
# local outage (ن-10, p1-hardening-plan.md §5-ب): NOTHING renews the secret_id,
# so a stack older than the TTL cannot re-login. The failure is silent -- the
# health check never touches Vault, so `docker compose ps` keeps saying
# `healthy` -- and it detonates on the next container restart, when the app
# crash-loops on an expired credential. Worse, that crash loop then trips
# Vault's user-lockout (`core: login attempts exceeded`), after which even a
# freshly minted, perfectly valid secret_id is refused with a bare
# `403 permission denied` -- so the obvious remedy appears not to work either.
# 720h (30d) is the deliberate trade for this local stack: a stolen secret_id
# stays valid longer, which is acceptable when Vault is bound to loopback and
# is not acceptable in a real deployment. A real deployment should renew the
# secret_id instead (option (أ) in that note) and keep this short.
vault write auth/approle/role/app \
    token_policies=app \
    token_no_default_policy=true \
    token_ttl=1h \
    token_max_ttl=4h \
    secret_id_ttl=720h >/dev/null

# Pinning the role_id is a LOCAL convenience and is opt-in. Vault generates a
# fresh role_id whenever the role is FIRST created; persistent storage means
# it now survives every later restart on its own, but a wiped vault-data
# volume (or the first-ever boot on a new host) still creates the role anew
# with a random id -- which would silently stale the VAULT_ROLE_ID sitting in
# .env and produce an authentication failure with no obvious cause. Setting
# AIZZAK_APPROLE_ROLE_ID makes the two agree by construction, on every boot,
# unconditionally on whether this happens to be a fresh role or not. It is
# safe to choose the value: 05 §2 classifies role_id as NON-SECRET (only
# secret_id is sensitive). Leave it unset in any real deployment and read the
# generated id once, as 08 §3.1 describes.
if [ -n "${AIZZAK_APPROLE_ROLE_ID:-}" ]; then
    vault write auth/approle/role/app/role-id \
        role_id="${AIZZAK_APPROLE_ROLE_ID}" >/dev/null
fi

# ⚠️ No secret_id is minted here, and none is printed. A secret_id is a
# credential (05 §3.3); minting one is an operator action whose output goes
# to a terminal, never to a container log this stack's json-file driver would
# capture and retain. 08 §3.1 gives the command.

echo "vault-bootstrap: complete"
