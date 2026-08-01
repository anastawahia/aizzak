# Vault policy for the application's AppRole (7.3 · D-22 ·
# 05-rbac-config-secrets §3.1/§3.2/§3.3).
#
# This is the ENTIRE Vault surface `src/` touches. It was derived from the
# call sites, not from the catalog: `infrastructure/secrets/vault_secrets.py`
# is the sole module that speaks to Vault (05 §3.3 -- "SecretsProvider is the
# only interface to secrets"), and it makes exactly four kinds of request.
#
# The dev root token this replaces could do everything, including seal the
# vault and rewrite this policy. That difference IS the deliverable: an
# application compromise now leaks the tenant Transit key's USE, not its
# material, and cannot read another mount, mint a token, or change an ACL.

# ── KV v2 reads (05 §3.1) ────────────────────────────────────────────────
# `VaultSecrets.get_secret` -> hvac `read_secret_version(mount_point=...,
# path=...)` -> GET /v1/secret/data/<path>. The `/data/` infix is hvac's own
# (KV v2's wire shape), which is why the policy path carries it too.
#
# `read` only. Nothing in `src/` ever writes a KV secret -- seeding is an
# operator step (deploy/vault/bootstrap.sh), and an application that cannot
# overwrite its own database password cannot lock itself out of the cluster.
path "secret/data/*" {
  capabilities = ["read"]
}

# ── Transit (05 §3.2, SEC-07) ────────────────────────────────────────────
# `VaultSecrets.encrypt`/`decrypt` -> hvac `encrypt_data`/`decrypt_data` ->
# POST /v1/transit/{encrypt,decrypt}/<key>. Vault spells POST "update".
#
# Named per-key rather than `transit/encrypt/*`: SEC-07 fixes ONE unified key
# (`tenant-secrets`) for both tenant secret sites, so a wildcard would grant
# use of keys that do not exist yet and whose purpose has not been decided.
path "transit/encrypt/tenant-secrets" {
  capabilities = ["update"]
}

path "transit/decrypt/tenant-secrets" {
  capabilities = ["update"]
}

# `VaultSecrets.rewrap` -> hvac `rewrap_data` -> POST /v1/transit/rewrap/
# <key>. P1-9 (docs/p1-hardening-plan.md §3 step 12, "مسار تدوير مفتاح
# Transit"): 05 §3.3 names rewrap as the rotation path, and 7.1/7.3 withheld
# this grant deliberately UNTIL a caller existed -- "a privilege nobody
# exercises is a free privilege. It belongs to whichever step implements
# rotation, added together with its first caller" (§3.75 decision (b) -- this
# comment used to BE that promise, in the "Deliberately NOT granted" section
# below, before this grant existed). The caller is now `app.ops.rotate_transit`
# (the ONLY thing in `src/` that calls it):
# rewrap never returns plaintext (hvac's own docstring), so this grant lets
# the sweep re-encrypt every stored ciphertext under the CURRENT key version
# without ever handing the app -- or an attacker who compromises its token --
# a path to read one.
path "transit/rewrap/tenant-secrets" {
  capabilities = ["update"]
}

# ── Deliberately NOT granted ─────────────────────────────────────────────
# `transit/keys/tenant-secrets/rotate` and every other `transit/keys/*`
# path -- minting a NEW key version, changing `min_decryption_version`, or
# any other key-ADMINISTRATION action is a Vault-operator step
# (08-local-runbook §4.5 gives the exact command), never something the
# application authenticates to do. `rewrap` re-encrypts under whatever
# version an operator already minted; it cannot mint one itself. Granting
# `transit/keys/*` here would let a compromised AppRole token rotate or
# retire the tenant key on its own initiative -- exactly the "sys/auth
# administration" boundary the next paragraph already draws, just one
# mount deeper.
#
# `sys/*`, `auth/*` -- the application never administers Vault. Token
# self-renewal (`auth/token/renew-self`) is absent too, and deliberately:
# `create_vault_client` logs in once and never renews, so the capability has
# no reachable caller. See 08-local-runbook §3.1's TTL note for what that
# costs and how to detect it.
#
# ⚠️ This file is only the whole surface because the role is created with
# `token_no_default_policy=true` (deploy/vault/bootstrap.sh). Vault otherwise
# adds its built-in `default` policy -- cubbyhole, `lookup-self`,
# `renew-self` -- on top of whatever `token_policies` names, and the
# paragraph above would be false.
