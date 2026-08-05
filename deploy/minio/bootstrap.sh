#!/bin/sh
# MinIO bootstrap (7.1 · 08-local-runbook §3 step 4): create the object
# bucket. Runs from the pinned `minio/mc` image -- a deploy artifact does not
# curl an unversioned binary off the internet at boot.
#
# Idempotent: `mb --ignore-existing` is a no-op on an existing bucket, and the
# optional test account below is created-or-updated rather than added blindly.
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

# ── the live test harness's bucket + scoped account (docs/log/3.99.md) ───
# `tests/integration/test_minio_storage.py` runs against a REAL MinIO through a
# service account that can see ONE bucket and nothing else -- that blast-radius
# limit is itself asserted by `test_out_of_scope_bucket_is_denied_not_leaked`.
# Until now the account was provisioned by hand, outside the repo (docs/log/
# 3.19.md), against a native `minio.service` that no longer exists; every
# rebuild of this container therefore came up without it and the whole
# `live_minio` suite failed on `InvalidAccessKeyId`.
#
# OFF BY DEFAULT. A deployment has no business minting test credentials, so
# this half runs only when the operator sets MINIO_TEST_ACCOUNT_ENABLED=true.
# Where that happens is worth stating exactly, because the comment that stood
# here until plan step أ-1 claimed the local Compose stack already did it
# "through `.env`" -- a description of an intention, not of the tree: no `.env`
# and no `.env.example` carried the variable at all, `docker-compose.yml`
# therefore substituted its `false` default, and this half had never once run
# from a checkout. The live account existed only inside a volume some earlier
# session had provisioned by hand, so a `down -v` would have taken the whole
# live_minio suite with it. Today `.env.example` ships all four MINIO_TEST_*
# lines with the switch at `false`, and a developer flips it to `true` in their
# own git-ignored `.env`. No deployment path sets it anywhere.
if [ "${MINIO_TEST_ACCOUNT_ENABLED:-false}" != "true" ]; then
    exit 0
fi

test_bucket="${MINIO_TEST_BUCKET:-aizzak-test}"
test_access_key="${MINIO_TEST_ACCESS_KEY:-aizzak_test}"
test_secret_key="${MINIO_TEST_SECRET_KEY:-aizzak-test-secret}"

mc mb --ignore-existing "aizzak/${test_bucket}" >/dev/null

# Scoped to the one bucket, by ARN. `ListBucket`/`GetBucketLocation` on the
# bucket itself, object verbs under it -- and NOTHING at the server level, so
# `mc ls` on this account lists exactly one bucket and a read of any other
# fails with AccessDenied.
policy_file=/tmp/aizzak-test-policy.json
cat >"${policy_file}" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${test_bucket}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::${test_bucket}/*"]
    }
  ]
}
EOF

# Created-or-updated: `svcacct add` fails on an existing access key, so an
# existing account is re-pointed at the same secret/policy instead. That makes
# a re-run converge on the intended state rather than aborting the bootstrap.
if mc admin user svcacct info aizzak "${test_access_key}" >/dev/null 2>&1; then
    mc admin user svcacct edit \
        --secret-key "${test_secret_key}" \
        --policy "${policy_file}" \
        aizzak "${test_access_key}" >/dev/null
    echo "minio-bootstrap: test account ${test_access_key} updated"
else
    mc admin user svcacct add \
        --access-key "${test_access_key}" \
        --secret-key "${test_secret_key}" \
        --policy "${policy_file}" \
        aizzak "${MINIO_ROOT_USER}" >/dev/null
    echo "minio-bootstrap: test account ${test_access_key} created"
fi

rm -f "${policy_file}"
echo "minio-bootstrap: test bucket ${test_bucket} ready"
