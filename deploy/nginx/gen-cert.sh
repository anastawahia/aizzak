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
#   neither -> a self-signed development pair, written under random temporary
#     names and published with `ln -T` (which never replaces), so a failed run
#     leaves neither file instead of the half pair the rule above would then
#     refuse, and a pair that appears meanwhile is never overwritten.
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
umask 077

# The temporary files get random names made with O_EXCL (mktemp), never a fixed
# name: a fixed `.server.key.new` that someone planted as a symbolic link in the
# folder would be written through. They are published with `ln -T`, which
# fails when the target already exists (a file, a symlink, even a dangling
# one) and, with -T, also when it is a directory or a symlink to one (without
# it `ln` would put the link INSIDE that directory and succeed), where `mv`
# would silently replace a pair that appeared while we generated.
TMP_KEY="" TMP_CERT=""
cleanup() {
    for f in "$TMP_KEY" "$TMP_CERT"; do
        [ -z "$f" ] || rm -f -- "$f"
    done
}
trap cleanup EXIT
# dash does not run the EXIT trap when a signal kills it (docker stop sends
# TERM): turn the signals into an exit so the temporary files are removed. The
# trap runs once the foreground command (openssl) returns.
trap 'exit 1' HUP INT TERM

fail() {
    echo "nginx-certs: ERROR: $1" >&2
    exit 1
}

TMP_KEY="$(mktemp -p "$CERT_DIR" .server.key.XXXXXX 2>/dev/null)" ||
    fail "cannot create files in $CERT_DIR (is deploy/nginx/certs read-only or not writable?); nothing was written"
TMP_CERT="$(mktemp -p "$CERT_DIR" .server.crt.XXXXXX 2>/dev/null)" ||
    fail "cannot create files in $CERT_DIR (is deploy/nginx/certs read-only or not writable?); nothing was written"

# SAN is not optional cosmetics: every current client ignores the legacy CN
# for hostname verification and reads subjectAltName only. Without it the
# certificate is rejected by name even when explicitly trusted. Both
# `localhost` and 127.0.0.1 are listed because the healthcheck probes the
# literal address from inside the container while a developer's browser uses
# the name.
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout "$TMP_KEY" -out "$TMP_CERT" \
    -subj "/CN=localhost/O=AIZZAK local development" \
    -addext "subjectAltName=DNS:localhost,DNS:app,IP:127.0.0.1" >/dev/null 2>&1 ||
    fail "openssl could not generate the development certificate; nothing was written"

# The key is readable only by its owner. nginx reads it as root in the master
# process before dropping to the `nginx` user, so 600 is sufficient and is
# the tightest mode that still works. (A hard link shares the mode.)
chmod 600 "$TMP_KEY"
chmod 644 "$TMP_CERT"
# `ln` can fail for another reason than a file that appeared (a filesystem
# without hard links, a permission): say which one it was.
publish_failed() {
    if present "$1"; then
        fail "$2 appeared while the certificate was being generated; left untouched, nothing was written"
    fi
    fail "could not publish $2 in $CERT_DIR (a filesystem without hard links?); nothing was written"
}

ln -T "$TMP_KEY" "$KEY" 2>/dev/null || publish_failed "$KEY" server.key
if ! ln -T "$TMP_CERT" "$CERT" 2>/dev/null; then
    # take back the key this run just published, so no half pair is left
    rm -f -- "$KEY"
    publish_failed "$CERT" server.crt
fi

echo "nginx-certs: wrote server.crt / server.key (self-signed, 825 days)"
echo "nginx-certs: ⚠️  DEVELOPMENT ONLY -- a real deployment mounts its own"
