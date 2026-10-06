#!/usr/bin/env bash
# Runs a command with the app's Vault AppRole login in its environment.
#
# supervisord fixes a program's environment when supervisord itself starts,
# which is before Vault exists. aizzak-bootstrap.sh mints the login later, on
# every boot, into a 0600 file; this wrapper reads it at process start, so
# `app` and `worker` (and every autorestart of them) get the current one.
# Only the two AppRole keys are taken from the file, and nothing is printed.
set -euo pipefail

cred_file="${AIZZAK_VAULT_CRED_FILE:-/run/aizzak/vault-approle.env}"
if [ ! -r "$cred_file" ]; then
    printf '[with-vault] ⛔ %s is not readable -- the bootstrap has not minted the Vault login\n' \
        "$cred_file" >&2
    exit 1
fi

while IFS='=' read -r key value; do
    case "$key" in
        VAULT_ROLE_ID|VAULT_SECRET_ID) export "$key=$value" ;;
    esac
done < "$cred_file"

exec "$@"
