#!/bin/sh
# TLS material for the local edge (7.3 · 08-local-runbook §2/§3.2).
#
# Runs as a one-shot Compose service on the SAME pinned nginx image that
# consumes the output -- the vault-bootstrap/minio-bootstrap pattern. That is
# deliberate: `openssl` ships in nginx:1.27, so generating here needs nothing
# installed on the host and pins the tool to a version the stack already
# declares, instead of depending on whatever `openssl` a developer happens to
# have (or, on Windows hosts, does not).
#
# IDEMPOTENT: existing material is left alone. Regenerating on every `up`
# would invalidate any certificate an operator deliberately dropped in --
# which is exactly how a real deployment supplies its own.
set -eu

CERT=/certs/server.crt
KEY=/certs/server.key

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
    echo "nginx-certs: certificate already present, leaving it alone"
    exit 0
fi

echo "nginx-certs: generating a self-signed certificate for local TLS"

# SAN is not optional cosmetics: every current client ignores the legacy CN
# for hostname verification and reads subjectAltName only. Without it the
# certificate is rejected by name even when explicitly trusted. Both
# `localhost` and 127.0.0.1 are listed because the healthcheck probes the
# literal address from inside the container while a developer's browser uses
# the name.
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout "$KEY" -out "$CERT" \
    -subj "/CN=localhost/O=AIZZAK local development" \
    -addext "subjectAltName=DNS:localhost,DNS:app,IP:127.0.0.1" >/dev/null 2>&1

# The key is readable only by its owner. nginx reads it as root in the master
# process before dropping to the `nginx` user, so 600 is sufficient and is
# the tightest mode that still works.
chmod 600 "$KEY"
chmod 644 "$CERT"

echo "nginx-certs: wrote server.crt / server.key (self-signed, 825 days)"
echo "nginx-certs: ⚠️  DEVELOPMENT ONLY -- a real deployment mounts its own"
