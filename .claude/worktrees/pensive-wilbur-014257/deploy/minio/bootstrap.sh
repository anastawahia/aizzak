#!/bin/sh
# MinIO bootstrap (7.1 · 08-local-runbook §3 step 4): create the object
# bucket. Runs from the pinned `minio/mc` image -- a deploy artifact does not
# curl an unversioned binary off the internet at boot.
#
# Idempotent: `mb --ignore-existing` is a no-op on an existing bucket.
#
# ⚠️ Credentials arrive as environment variables and are never echoed.
set -eu

echo "minio-bootstrap: waiting for minio"
until mc alias set aizzak http://minio:9000 \
        "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null 2>&1; do
    sleep 1
done

mc mb --ignore-existing "aizzak/${MINIO_BUCKET}" >/dev/null
echo "minio-bootstrap: bucket ${MINIO_BUCKET} ready"
