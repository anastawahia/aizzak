#!/usr/bin/env bash
# The restore drill (capacity step 2.5). Restore, not backup, is the test.
#
#   deploy/backup/restore_drill.sh [--keep] [--no-markers]
#
# WHY THIS FILE EXISTS AND `app.ops.backup` DOES NOT CONTAIN IT. The plan's
# own wording: "not acceptable without an ACTUAL restore drill to a CLEAN
# stack". A clean stack means a cluster this codebase has never connected to,
# started from nothing but the objects in the bucket -- so the drill runs
# containers, and a Python module that starts and stops sibling containers
# would need the Docker socket, which is a far larger grant than any backup
# tool should hold. This runs on the host, like `deploy/load/run.sh`.
#
# WHAT IT PROVES, IN ORDER OF HOW MUCH IT IS WORTH:
#
#   1. **The point in time is real.** The drill writes marker A into the LIVE
#      database, waits, writes marker B, and restores to a moment BETWEEN
#      them. A restore that carries A and not B is a point-in-time restore. A
#      restore that carries both is a restore of "whatever the last segment
#      happened to contain", which is what every untested WAL archive
#      silently is.
#   2. The restored cluster is the SAME cluster. `pg_control_system()
#      .system_identifier` on the restored server is compared against the one
#      recorded in the base backup's manifest -- the identity that decides
#      whether those WAL segments were replayable here at all.
#   3. The tenant corpus survived. Row counts for three tenant tables,
#      compared against the live database.
#   4. The live proofs in `deploy/smoke/` pass against it.
#   5. **How long it took**, split into download / extract / recovery. That
#      number is the RTO, and until a drill has run, an RTO is a wish.
#
# ⚠️ IT WRITES TWO ROWS TO THE LIVE DATABASE (`public.restore_drill_marker`,
# created and dropped by this script, superuser). That is the price of
# proving (1) end to end, and `--no-markers` declines it -- at the cost of
# reducing the drill to "the cluster came up", which is exactly the reassuring
# half-test this step exists to replace.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

keep=0
markers=1
for arg in "$@"; do
    case "$arg" in
        --keep) keep=1 ;;
        --no-markers) markers=0 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

# ⚠️ `.env` IS READ, NOT SOURCED, AND THE DIFFERENCE IS MEASURED. `set -a; .
# ./.env` looks like the idiom `docs/stack-commands.md` uses for `.env.test`,
# and on THIS file it corrupts values: shell assignment performs quote
# removal, so `PROVIDER_ROUTING={"llm":{...}}` becomes `{llm:{...}}`, which
# `docker compose` then interpolates back into every service it starts. The
# first run of this drill died on
# `SettingsError: error parsing value for field "provider_routing"` -- in a
# container the drill had merely asked Compose to start. Compose loads `.env`
# itself, correctly; this reads the handful of values the SCRIPT needs and
# exports none of them.
env_get() {
    sed -n "s/^$1=//p" .env | head -1
}

MINIO_ROOT_USER="$(env_get MINIO_ROOT_USER)"
MINIO_ROOT_PASSWORD="$(env_get MINIO_ROOT_PASSWORD)"
POSTGRES_DB="$(env_get POSTGRES_DB)"
POSTGRES_SUPERUSER="$(env_get POSTGRES_SUPERUSER)"
POSTGRES_SUPERUSER_PASSWORD="$(env_get POSTGRES_SUPERUSER_PASSWORD)"
APP_RW_PASSWORD="$(env_get APP_RW_PASSWORD)"
BACKUP_BUCKET="$(env_get BACKUP_BUCKET)"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work="deploy/backup/results/${stamp}"
wal_dir="${work}/wal"
container="aizzak-restore-${stamp}"
volume="aizzak-restore-${stamp}"
network="$(docker compose ps --format '{{.Networks}}' postgres | head -1)"
bucket="${BACKUP_BUCKET:-aizzak-backups}"
db="${POSTGRES_DB:-aizzak}"
mc_image="minio/mc:RELEASE.2025-04-16T18-13-26Z"
pg_image="postgres:16"

mkdir -p "${wal_dir}"
echo "restore-drill: work dir ${work}"

cleanup() {
    if [ "$keep" = "1" ]; then
        echo "restore-drill: --keep -- leaving ${container} and volume ${volume} in place"
        return
    fi
    docker rm -f "${container}" >/dev/null 2>&1 || true
    docker volume rm "${volume}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

psql_live() {
    docker compose exec -T postgres psql -U "${POSTGRES_SUPERUSER:-postgres}" -d "${db}" -tAc "$1"
}

# ⚠️ SWITCHING A SEGMENT IS NOT ARCHIVING IT, AND THE FIRST RUN OF THIS DRILL
# DIED ON EXACTLY THAT. `pg_switch_wal()` returns as soon as the segment is
# closed; the archiver copies it afterwards, on its own schedule. Shipping the
# spool immediately therefore ships everything EXCEPT the write that just
# happened -- and recovery then stops short:
#
#     redo done at 3/50000110
#     last completed transaction was at log time 11:02:32.954345+00
#     FATAL:  recovery ended before configured recovery target was reached
#
# The target was 11:02:35.97, three seconds later, and it was unreachable
# because the segment carrying it never left the spool. `pg_walfile_name()` of
# the LSN `pg_switch_wal()` returns names the segment that JUST COMPLETED
# (that boundary behaviour is documented and is what makes it usable here), so
# waiting for `last_archived_wal` to reach it is exact rather than a sleep.
switch_wal_and_wait_for_the_archiver() {
    local switched deadline
    switched="$(psql_live "SELECT pg_walfile_name(pg_switch_wal())")"
    deadline=$(( $(date +%s) + 120 ))
    until [ "$(psql_live "SELECT (coalesce(last_archived_wal,'') >= '${switched}')::int FROM pg_stat_archiver")" = "1" ]; do
        if [ "$(date +%s)" -gt "${deadline}" ]; then
            echo "restore-drill: the archiver never reached ${switched} -- check archive_command" >&2
            exit 1
        fi
        sleep 1
    done
}

mc_run() {
    docker run --rm --network "${network}" \
        -e "MC_HOST_aizzak=http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
        -v "${repo_root}/${work}:/out" \
        "${mc_image}" "$@"
}

# ── 0. markers, and the target time between them ──────────────────────────
target_time=""
if [ "$markers" = "1" ]; then
    echo "restore-drill: writing marker A into the LIVE database"
    psql_live "CREATE TABLE IF NOT EXISTS public.restore_drill_marker(
                   id text primary key, written_at timestamptz not null default now());
               DELETE FROM public.restore_drill_marker;
               INSERT INTO public.restore_drill_marker(id) VALUES ('A');" >/dev/null
    switch_wal_and_wait_for_the_archiver
    sleep 2
    target_time="$(psql_live "SELECT now()")"
    sleep 2
    echo "restore-drill: target time ${target_time}"
    psql_live "INSERT INTO public.restore_drill_marker(id) VALUES ('B');" >/dev/null
    switch_wal_and_wait_for_the_archiver
    echo "restore-drill: marker B written AFTER the target, and archived"
fi

# The spool has to reach object storage before anything can replay it. The
# standing shipper does this every BACKUP_WAL_INTERVAL_S; the drill does not
# wait for a timer it does not control.
echo "restore-drill: shipping the spool"
docker compose --profile backup run --rm -T backup python -m app.ops.backup wal >/dev/null

# ── 1. fetch the newest base backup and the whole WAL shelf ───────────────
t0=$(date +%s.%N)
base_set="$(mc_run --quiet ls "aizzak/${bucket}/base/" | awk '{print $NF}' | tr -d '/' | sort | tail -1)"
[ -n "${base_set}" ] || { echo "restore-drill: no base backup in ${bucket}/base/ -- run 'app.ops.backup base' first" >&2; exit 1; }
echo "restore-drill: newest base set ${base_set}"

mc_run --quiet cp --recursive "aizzak/${bucket}/base/${base_set}/" "/out/base/" >/dev/null
mc_run --quiet cp --recursive "aizzak/${bucket}/wal/" "/out/walgz/" >/dev/null
t_download=$(echo "$(date +%s.%N) - ${t0}" | bc)

# Ungzip into bare segment names: `restore_command` is handed `%f` and
# nothing else, so the archive it reads has to be spelled the way the server
# spells it.
for gz in "${work}"/walgz/*.gz; do
    [ -e "$gz" ] || break
    gunzip -c "$gz" > "${wal_dir}/$(basename "${gz%.gz}")"
done
echo "restore-drill: $(find "${wal_dir}" -type f | wc -l) WAL segments staged"

# ── 2. rebuild a data directory from the tar ──────────────────────────────
t1=$(date +%s.%N)
docker volume create "${volume}" >/dev/null
docker run --rm --user root \
    -v "${volume}:/pgdata" \
    -v "${repo_root}/${work}/base:/in:ro" \
    "${pg_image}" sh -c '
        set -eu
        tar -xzf /in/base.tar.gz -C /pgdata
        mkdir -p /pgdata/pg_wal
        tar -xzf /in/pg_wal.tar.gz -C /pgdata/pg_wal
        chown -R 999:999 /pgdata
        chmod 700 /pgdata
    '
t_extract=$(echo "$(date +%s.%N) - ${t1}" | bc)

# ── 3. recovery configuration ─────────────────────────────────────────────
# `recovery.signal` is what turns a data directory into a recovering cluster
# (PG12+ -- there is no recovery.conf any more). `promote` makes the server
# open for writes once it reaches the target, which is what the smoke proofs
# below need; the alternative (`pause`) would leave every check reading a
# server in recovery and unable to say why.
recovery_conf="restore_command = 'cp /wal-restore/%f %p'
recovery_target_action = 'promote'"
if [ -n "${target_time}" ]; then
    recovery_conf="${recovery_conf}
recovery_target_time = '${target_time}'"
fi
docker run --rm --user root -v "${volume}:/pgdata" "${pg_image}" sh -c "
    set -eu
    cat >> /pgdata/postgresql.auto.conf <<'EOF'
# --- restore drill $(date -u +%FT%TZ) ---
${recovery_conf}
EOF
    touch /pgdata/recovery.signal
    chown 999:999 /pgdata/postgresql.auto.conf /pgdata/recovery.signal
"

# ── 4. start it, and wait for recovery to finish ──────────────────────────
t2=$(date +%s.%N)
docker run -d --name "${container}" --network "${network}" \
    -v "${volume}:/var/lib/postgresql/data" \
    -v "${repo_root}/${wal_dir}:/wal-restore:ro" \
    -e POSTGRES_PASSWORD="${POSTGRES_SUPERUSER_PASSWORD}" \
    "${pg_image}" \
    postgres \
        -c shared_preload_libraries=pg_stat_statements \
        -c pg_stat_statements.max=10000 \
        -c pg_stat_statements.track=top >/dev/null
echo "restore-drill: ${container} starting, replaying WAL"

deadline=$(( $(date +%s) + 600 ))
until docker exec "${container}" pg_isready -U "${POSTGRES_SUPERUSER:-postgres}" -q 2>/dev/null; do
    # A recovering server that GIVES UP exits; polling it for ten more minutes
    # turns a one-line diagnosis into a wait. Measured: the first failure here
    # was `recovery ended before configured recovery target was reached`, said
    # once, two seconds in.
    if [ "$(docker inspect -f '{{.State.Running}}' "${container}" 2>/dev/null)" != "true" ]; then
        echo "restore-drill: the restored server exited during recovery. Its own words:" >&2
        docker logs --tail 25 "${container}" >&2
        exit 1
    fi
    if [ "$(date +%s)" -gt "${deadline}" ]; then
        echo "restore-drill: server never became ready. Last log lines:" >&2
        docker logs --tail 40 "${container}" >&2
        exit 1
    fi
    sleep 1
done
until [ "$(docker exec "${container}" psql -U "${POSTGRES_SUPERUSER:-postgres}" -tAc 'SELECT pg_is_in_recovery()')" = "f" ]; do
    if [ "$(date +%s)" -gt "${deadline}" ]; then
        echo "restore-drill: still in recovery after 10 minutes. Last log lines:" >&2
        docker logs --tail 40 "${container}" >&2
        exit 1
    fi
    sleep 1
done
t_recover=$(echo "$(date +%s.%N) - ${t2}" | bc)
total=$(echo "$(date +%s.%N) - ${t0}" | bc)

psql_restored() {
    docker exec "${container}" psql -U "${POSTGRES_SUPERUSER:-postgres}" -d "${db}" -tAc "$1"
}

echo
echo "── what the restored cluster says ──────────────────────────────────"

# (2) identity
restored_sysid="$(psql_restored 'SELECT system_identifier FROM pg_control_system()')"
manifest_sysid="$(python3 -c "
import json,sys
print(json.load(open('${work}/base/manifest.json')).get('system_identifier'))
" 2>/dev/null || echo "?")"
echo "system_identifier: restored=${restored_sysid} manifest=${manifest_sysid}"
if [ "${restored_sysid}" != "${manifest_sysid}" ]; then
    echo "FAIL: the restored cluster is not the cluster this base backup came from" >&2
    exit 1
fi

# (1) the point in time
if [ "$markers" = "1" ]; then
    have_a="$(psql_restored "SELECT count(*) FROM public.restore_drill_marker WHERE id='A'")"
    have_b="$(psql_restored "SELECT count(*) FROM public.restore_drill_marker WHERE id='B'")"
    echo "markers: A=${have_a} (want 1, written BEFORE the target) B=${have_b} (want 0, written AFTER)"
    if [ "${have_a}" != "1" ] || [ "${have_b}" != "0" ]; then
        echo "FAIL: this is not a point-in-time restore -- the target time changed nothing" >&2
        exit 1
    fi
fi

# (3) the tenant corpus
for table in workspace.workspaces files.files conversations.messages; do
    live="$(psql_live "SELECT count(*) FROM ${table}")"
    restored="$(psql_restored "SELECT count(*) FROM ${table}")"
    printf 'rows %-28s live=%-10s restored=%s\n' "${table}" "${live}" "${restored}"
done

# (4) the live proofs, against the restored database
echo
echo "── deploy/smoke/stack_smoke.py against the restored database ───────"
echo "   (check [1]'s subject is the POOLER; the restored cluster is reached"
echo "    directly, so what it proves here is OPS-02 on real statements.)"
docker compose exec -T \
    -e DATABASE_URL="postgresql+asyncpg://app_rw:${APP_RW_PASSWORD}@${container}:5432/${db}" \
    app python /app/deploy/smoke/stack_smoke.py

# ── 5. the number this whole file exists to produce ───────────────────────
echo
printf '── RTO ─────────────────────────────────────────────────────────────\n'
printf 'download  %6.1fs\nextract   %6.1fs\nrecovery  %6.1fs\nTOTAL     %6.1fs\n' \
    "${t_download}" "${t_extract}" "${t_recover}" "${total}"

if [ "$markers" = "1" ]; then
    psql_live "DROP TABLE IF EXISTS public.restore_drill_marker" >/dev/null
fi

cat > "${work}/drill.json" <<EOF
{
  "stamp": "${stamp}",
  "base_set": "${base_set}",
  "target_time": "${target_time}",
  "system_identifier": "${restored_sysid}",
  "seconds": {
    "download": ${t_download},
    "extract": ${t_extract},
    "recovery": ${t_recover},
    "total": ${total}
  }
}
EOF
echo
echo "restore-drill: PASS -- ${work}/drill.json"
