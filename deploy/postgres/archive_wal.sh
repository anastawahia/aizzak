#!/bin/sh
# `archive_command` for the `postgres` service (capacity step 2.5).
#
#   archive_command = '/usr/local/bin/archive_wal.sh %p %f'
#
# WHY A SPOOL AND NOT A DIRECT UPLOAD. This runs inside the postgres:16
# container, as the server's own archiver process. That container carries no
# S3 client, no Python and none of this repository's code, and it is not
# going to start carrying them: the image is pinned deliberately, and an
# `archive_command` that reaches the network is an `archive_command` that
# hangs when the network does -- with the whole cluster's WAL piling up in
# pg_wal behind it. So the archiver does the one thing it can do
# synchronously and locally, and `python -m app.ops.backup wal --follow`
# ships the spool from a container that does have those things.
#
# THREE PROPERTIES, EACH OF WHICH POSTGRES ACTUALLY RELIES ON:
#
#   1. It NEVER overwrites a different file under an existing name. The
#      archiver may re-run a command for a segment it already archived (a
#      crash between the copy and recording the success); the documented
#      contract is that archiving an identical file again is success and
#      archiving a DIFFERENT file under an existing name must fail. Silently
#      overwriting is how a recoverable archive becomes an unrecoverable one.
#   2. It is atomic. A partially written segment that a shipper picks up is a
#      corrupt segment in object storage with a perfectly good name. Write to
#      a temp name in the same directory, fsync, then rename.
#   3. It fails loudly (non-zero). Postgres retries a failed archive forever
#      and keeps the segment; that is the correct behaviour and the reason
#      this script must never "succeed" on a copy it could not complete.
#
# THE FAILURE MODE THIS PAIR HAS, STATED RATHER THAN DISCOVERED. If the
# shipper stops, this script keeps succeeding and the spool grows without
# limit until the volume fills. `python -m app.ops.backup status` prints the
# spool depth and the age of its oldest file for exactly that reason, and the
# shipper runs as a standing Compose service rather than as an operator
# habit.
set -eu

source_path="$1"   # %p -- path relative to PGDATA
segment_name="$2"  # %f -- bare file name

: "${WAL_ARCHIVE_DIR:=/var/lib/postgresql/wal-archive}"

target="${WAL_ARCHIVE_DIR}/${segment_name}"
staging="${WAL_ARCHIVE_DIR}/.${segment_name}.$$"

if [ ! -d "${WAL_ARCHIVE_DIR}" ]; then
    echo "archive_wal: ${WAL_ARCHIVE_DIR} does not exist or is not a directory" >&2
    exit 1
fi

# (1) An existing name is success ONLY if it is byte-for-byte the same file.
if [ -f "${target}" ]; then
    if cmp -s "${source_path}" "${target}"; then
        exit 0
    fi
    echo "archive_wal: ${segment_name} already archived with DIFFERENT contents" >&2
    exit 1
fi

# (2) Temp file in the SAME directory (rename is only atomic within a
# filesystem), fsync of the data and of the directory entry, then rename.
trap 'rm -f "${staging}"' EXIT INT TERM
cp "${source_path}" "${staging}"
# `sync` on the file itself where the coreutils build supports it; the
# directory sync below is what makes the rename durable either way.
sync "${staging}" 2>/dev/null || sync
mv "${staging}" "${target}"
sync "${WAL_ARCHIVE_DIR}" 2>/dev/null || sync
trap - EXIT INT TERM

# (3) Group-readable and group-writable, and the reason is the shipper: it
# runs in a different container and must be able to DELETE from this
# directory once a segment is safely in object storage. Deleting needs write
# on the directory, not on the file -- which is why docker-compose.yml runs
# the backup service as uid/gid 999 rather than loosening the mode here.
chmod 0640 "${target}"
exit 0
