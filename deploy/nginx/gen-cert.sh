#!/bin/sh
# TLS material for the edge (7.3 · 08-local-runbook §3.2 · server-gpu-plan.md 6.1).
#
# Runs as a one-shot Compose service on the SAME pinned nginx image that
# consumes the output -- the vault-bootstrap/minio-bootstrap pattern. That is
# deliberate: `openssl` ships in nginx:1.27, so generating here needs nothing
# installed on the host and pins the tool to a version the stack already
# declares, instead of depending on whatever `openssl` a developer happens to
# have (or, on Windows hosts, does not).
#
# PRESENCE, NEVER CONTENT: what is in the directory decides, and nothing here
# opens a file that is already there.
#   both server.crt and server.key -> left alone. This is how a real server
#     supplies its own pair (a Cloudflare origin certificate, 6.1:
#     docs/deploy-server.md §4), and how the dev box keeps its own.
#   exactly one -> stop, name the missing one, write nothing. The old rule
#     regenerated BOTH here, which silently replaced an operator's
#     certificate whose key had not been copied in yet.
#   neither -> a self-signed development pair, written under temporary names
#     and renamed into place, so a failed run leaves neither file instead of
#     the half pair the rule above would then refuse.
# A dangling symbolic link counts as present: it is the operator's, not ours.
set -eu

# /certs is where docker-compose.yml mounts deploy/nginx/certs. The variable
# exists for tests/unit/test_gen_cert.py; Compose does not set it.
CERT_DIR="${AIZZAK_CERT_DIR:-/certs}"
CERT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"

present() { [ -e "$1" ] || [ -L "$1" ]; }

if present "$CERT" && present "$KEY"; then
    echo "nginx-certs: certificate already present, leaving it alone"
    exit 0
fi
if present "$CERT" || present "$KEY"; then
    if present "$CERT"; then
        have=server.crt missing=server.key
    else
        have=server.key missing=server.crt
    fi
    echo "nginx-certs: ERROR: $have is present but $missing is missing (deploy/nginx/certs); nothing was written" >&2
    echo "nginx-certs: put $missing beside it, or move $have aside to get a development certificate" >&2
    exit 1
fi

echo "nginx-certs: generating a self-signed certificate for local TLS"
NEW_KEY="$CERT_DIR/.server.key.new"
NEW_CERT="$CERT_DIR/.server.crt.new"
trap 'rm -f "$NEW_KEY" "$NEW_CERT"' EXIT
umask 077

# SAN is not optional cosmetics: every current client ignores the legacy CN
# for hostname verification and reads subjectAltName only. Without it the
# certificate is rejected by name even when explicitly trusted. Both
# `localhost` and 127.0.0.1 are listed because the healthcheck probes the
# literal address from inside the container while a developer's browser uses
# the name.
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout "$NEW_KEY" -out "$NEW_CERT" \
    -subj "/CN=localhost/O=AIZZAK local development" \
    -addext "subjectAltName=DNS:localhost,DNS:app,IP:127.0.0.1" >/dev/null 2>&1

# The key is readable only by its owner. nginx reads it as root in the master
# process before dropping to the `nginx` user, so 600 is sufficient and is
# the tightest mode that still works.
chmod 600 "$NEW_KEY"
chmod 644 "$NEW_CERT"
mv "$NEW_KEY" "$KEY"
mv "$NEW_CERT" "$CERT"

echo "nginx-certs: wrote server.crt / server.key (self-signed, 825 days)"
echo "nginx-certs: ⚠️  DEVELOPMENT ONLY -- a real deployment mounts its own"
