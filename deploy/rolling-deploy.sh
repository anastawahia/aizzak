#!/usr/bin/env bash
# Rolling deploy for a replicated Compose service — capacity step 7.2
# (docs/capacity-plan.md §5 wave 7). Replaces replicas ONE AT A TIME, draining
# each one's WebSockets before it goes and waiting for its replacement to be
# READY before touching the next.
#
# ⛔ WHY THIS FILE EXISTS AT ALL, MEASURED. `docker compose up -d
# --force-recreate app` on a three-replica service does NOT roll. It stops
# every replica, then starts them:
#
#     19:45:02.02  running=3 of 3
#     19:45:05.10  running=0 of 3     <- all three, at once
#     19:45:09.74  running=1 of 3
#     19:45:11.61  running=3 of 3
#
# 4.64 seconds with nothing serving, and under 50 rps through the edge that
# measured 413 failed requests of 3,750 (11.0%) -- 385 x 502 and 28 read
# timeouts, p95 8.76 s, worst 11.68 s. `replicas: 3` bought nothing: it is
# three containers with one lifecycle. Worse, the output Compose prints while
# doing it ("app-3 Started", "app-2 Starting") reads exactly like a rolling
# deploy, so the thing that makes this necessary is invisible in the log of
# the command that needs it.
#
# ⭐ SURGE, NOT DIP. The obvious shape -- remove one, let Compose recreate it
# -- also measures zero failures, but it runs the fleet at N-1 for the whole
# boot. `--scale N+1` is accepted alongside `deploy.replicas` (verified: no
# warning, no conflict), so the replacement is built and PROVEN READY while
# the outgoing replica is still serving, and capacity never drops below N.
#
# ⚠️ THE ONE THING THIS CANNOT FIX, AND IT IS MEASURED. Docker's embedded DNS
# publishes a container the moment it is `running`, not when it is healthy --
# measured 6-7 s of "in rotation, not ready" -- and gunicorn's master binds the
# listening socket before any worker can serve. So the first request to a
# brand-new replica connects in 0.17 ms and then waits 4.708 s for a byte.
# nginx cannot route around it: from the edge's side that is a perfectly
# healthy accept, with no error for `proxy_next_upstream` to fire on. Zero
# failures, real latency -- see `د-29` in docs/capacity-status.md. What this
# script controls is that only ONE replica is ever in that state at a time.
#
# ⛔ TWO KINDS OF SERVICE, AND THE FIRST VERSION KNEW ONE. Until 2026-10-03
# every service was probed as if it were `app`: `/health/ready` on :8000. A
# worker serves no HTTP at all -- its healthcheck is `python -m
# app.ops.healthcheck <name>`, a heartbeat file -- so `SERVICE=worker-knowledge`
# (documented in 08 §4.15) waited out the timeout on every replica and gave up:
# it had never once worked. The kind is now read off the replica's OWN
# healthcheck, not off its name:
#
#   http    the healthcheck probes /health/ready -> readiness over HTTP, the
#           WebSocket drain, and the surge (request traffic must never see N-1)
#   health  any other healthcheck -> readiness is Docker's own `healthy`, there
#           is no drain endpoint, and the replica is STOPPED, never killed:
#           `docker stop` sends SIGTERM and waits its stop_grace_period (45 s),
#           which is what the workers' drain ladder (5.1) is sized for. And it
#           is replaced IN PLACE by default: a queue sits in front of it, so a
#           brief N-1 costs lag, not errors -- while a surge costs a whole
#           extra process, and on 2026-10-03 one surge worker on a host with
#           its swap full is what tipped the kernel into killing Qdrant.
#
#   (none)  no healthcheck -> refused: nothing could prove the copy ready.
#
# ⚠️ A SURGE NOW CHECKS FOR MEMORY FIRST, and refuses before changing anything:
# MemAvailable must cover what the outgoing copy uses now plus a reserve
# (MEM_RESERVE_MIB, 1024). `--in-place` is the way through on a full host.
set -euo pipefail

SERVICE="${SERVICE:-app}"
# Ask Compose, don't guess: docker-compose.yml pins `name: aizzak`, and the
# directory is `AIZZAK` -- basename(pwd) matched no container at all.
PROJECT="${PROJECT:-$(docker compose config 2>/dev/null | sed -n 's/^name: //p' | head -1)}"
[ -n "$PROJECT" ] || { echo "ERROR: cannot resolve compose project name (set PROJECT=)" >&2; exit 2; }
DRAIN_WINDOW_S="${DRAIN_WINDOW_S:-10}"
DRAIN_GRACE_S="${DRAIN_GRACE_S:-2}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-}"   # 120 for http, 180 for health (below)
APP_PORT="${APP_PORT:-8000}"
STRATEGY="${STRATEGY:-}"                 # surge | in-place; by kind (below)
MEM_RESERVE_MIB="${MEM_RESERVE_MIB:-1024}"
MEMINFO="${MEMINFO:-/proc/meminfo}"
DRY_RUN=0

usage() {
  cat <<'USAGE'
usage: deploy/rolling-deploy.sh [options]

  --service NAME        Compose service to roll (default: app)
  --drain-window SECS   how long each replica spreads its WebSocket closes
                        over before it is stopped (default: 10)
  --ready-timeout SECS  how long to wait for a replacement to be ready
                        (default: 120 for an HTTP service, 180 for a worker)
  --surge               start the replacement BEFORE retiring the old copy
                        (default for an HTTP service; checks memory first)
  --in-place            retire the old copy first, then start the replacement
                        (default for a worker; needs no extra memory)
  --dry-run             print the plan and exit, changing nothing

Environment: SERVICE, PROJECT, DRAIN_WINDOW_S, DRAIN_GRACE_S,
READY_TIMEOUT_S, APP_PORT, STRATEGY, MEM_RESERVE_MIB override the same values.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --service) SERVICE="$2"; shift 2;;
    --drain-window) DRAIN_WINDOW_S="$2"; shift 2;;
    --ready-timeout) READY_TIMEOUT_S="$2"; shift 2;;
    --surge) STRATEGY=surge; shift;;
    --in-place) STRATEGY=in-place; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2;;
  esac
done

log() { printf '%s  %s\n' "$(date +%H:%M:%S)" "$*"; }
die() { printf '%s  ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }

# ⚠️ ADDRESSED BY LABEL, NEVER BY NAME. Compose does not reuse a replica's
# index: after a `rm` + recreate the fleet is `app-4 app-5 app-6`, and the
# number keeps climbing for the life of the project. A script that reached for
# `<project>-<service>-1` would silently no-op on a stack that had ever been
# rolled -- which is exactly what it is for.
replicas() {
  docker ps -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.service=${SERVICE}"
}

ip_of()   { docker inspect "$1" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'; }
name_of() { docker inspect "$1" --format '{{.Name}}' | sed 's|^/||'; }

# Probes run FROM INSIDE a container on the same network, never from the host:
# `/health/ready` and `/health/drain` are only reachable there (the edge
# answers /health/drain with 404 by design), and a replica's address is a
# Compose-network address with no host route.
# ⚠️ BOUNDED. On 2026-10-03, with the host out of memory, one `docker exec`
# hung for minutes and the 120 s deadline -- checked between attempts -- could
# not fire. `timeout` makes every attempt end.
in_net() { timeout 15 docker exec "$1" "${@:2}"; }

# What the replica's own healthcheck runs, as Docker stored it (JSON), or "".
health_test() {
  docker inspect "$1" --format '{{if .Config.Healthcheck}}{{json .Config.Healthcheck.Test}}{{end}}'
}

ready_wait() {
  local cid="$1" ip deadline state
  ip="$(ip_of "$cid")"
  deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ "$KIND" = http ]; then
      if in_net "$cid" curl -fsS -o /dev/null --max-time 5 \
          "http://127.0.0.1:${APP_PORT}/health/ready" 2>/dev/null; then
        return 0
      fi
    else
      # Docker's own verdict on the replica's own healthcheck. `starting`
      # is the normal state inside start_period; `unhealthy` or a container
      # that is no longer running will not get better by waiting.
      state="$(docker inspect "$cid" --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null || echo gone/)"
      case "$state" in
        running/healthy) return 0;;
        running/starting|running/) ;;
        *) die "replica $(name_of "$cid" 2>/dev/null || echo "$cid") is '${state}' — see its log: docker logs $cid";;
      esac
    fi
    sleep 2
  done
  if [ "$KIND" = http ]; then
    die "replica $(name_of "$cid") ($ip) never answered /health/ready within ${READY_TIMEOUT_S}s"
  fi
  die "replica $(name_of "$cid") ($ip) never became healthy within ${READY_TIMEOUT_S}s"
}

# MiB the container uses now, from `docker stats` ("174.1MiB / 2GiB").
mem_used_mib() {
  docker stats --no-stream --format '{{.MemUsage}}' "$1" 2>/dev/null | awk '{
    v = $1; u = $1; sub(/^[0-9.]+/, "", u); sub(/[A-Za-z]+$/, "", v)
    f = (u == "TiB") ? 1048576 : (u == "GiB") ? 1024 : (u == "MiB") ? 1 : (u == "KiB") ? 1 / 1024 : 1 / 1048576
    printf "%d\n", v * f + 0.5 }'
}
mem_available_mib() { awk '/^MemAvailable:/ { printf "%d\n", $2 / 1024 }' "$MEMINFO" 2>/dev/null || true; }

# A surge runs N+1 copies for as long as the replacement takes to boot. Refuse
# it -- before anything changes -- when the host cannot hold one more.
surge_headroom() {
  local cid="$1" used avail need
  avail="$(mem_available_mib)"
  used="$(mem_used_mib "$cid" || true)"
  if [ -z "$avail" ] || [ -z "$used" ]; then
    log "   memory: cannot read MemAvailable or the copy's usage — headroom NOT checked"
    return 0
  fi
  need=$(( used + MEM_RESERVE_MIB ))
  if [ "$avail" -lt "$need" ]; then
    die "not enough memory for a surge: ${avail} MiB available, ${need} MiB needed (${used} for the extra copy + ${MEM_RESERVE_MIB} reserve). Nothing was changed for $(name_of "$cid"). Re-run with --in-place, or free memory first"
  fi
  log "   memory: ${avail} MiB available ≥ ${need} MiB (${used} for the extra copy + ${MEM_RESERVE_MIB} reserve)"
}

mapfile -t OLD < <(replicas)
[ "${#OLD[@]}" -gt 0 ] || die "no running replicas of '${SERVICE}' in project '${PROJECT}'"
TARGET="${#OLD[@]}"

PROBE="$(health_test "${OLD[0]}")"
case "$PROBE" in
  *"/health/ready"*) KIND=http;;
  ""|null|'["NONE"]') die "'${SERVICE}' has no healthcheck: nothing could prove a replacement ready, so it is not rolled";;
  *) KIND=health;;
esac
if [ -z "$STRATEGY" ]; then
  if [ "$KIND" = http ]; then STRATEGY=surge; else STRATEGY=in-place; fi
fi
case "$STRATEGY" in surge|in-place) ;; *) die "STRATEGY must be surge or in-place, not '${STRATEGY}'";; esac
if [ -z "$READY_TIMEOUT_S" ]; then
  if [ "$KIND" = http ]; then READY_TIMEOUT_S=120; else READY_TIMEOUT_S=180; fi
fi

if [ "$KIND" = http ]; then
  log "rolling ${SERVICE}: ${TARGET} replica(s), ${STRATEGY}, drain ${DRAIN_WINDOW_S}s, ready = /health/ready within ${READY_TIMEOUT_S}s"
else
  log "rolling ${SERVICE}: ${TARGET} replica(s), ${STRATEGY}, no HTTP — ready = its own healthcheck within ${READY_TIMEOUT_S}s, stop = SIGTERM + stop_grace_period"
fi
for cid in "${OLD[@]}"; do log "  will replace $(name_of "$cid") ($(ip_of "$cid"))"; done
if [ "$DRY_RUN" -eq 1 ]; then
  # Read-only, and not fatal here: the point of a dry run is to SAY a surge
  # would be refused, not to be refused.
  if [ "$STRATEGY" = surge ]; then ( surge_headroom "${OLD[0]}" ) || true; fi
  log "dry run: nothing changed"
  exit 0
fi

# If anything below dies, put the fleet back to its declared size rather than
# leaving the surge replica behind -- a stack quietly running N+1 is a budget
# (08 §2-ب, §2-ز) that no longer matches the file that declares it.
cleanup() {
  local rc=$?
  [ "$rc" -eq 0 ] && return 0
  log "failed (exit ${rc}); reconciling ${SERVICE} back to ${TARGET}"
  docker compose up -d --no-deps --no-recreate --scale "${SERVICE}=${TARGET}" "${SERVICE}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# The replica that `docker compose up --scale` just added: in the fleet now,
# not in the list taken before.
newest() {
  local before="$1" candidate new=""
  for candidate in $(replicas); do
    case " ${before} " in *" ${candidate} "*) ;; *) new="${candidate}";; esac
  done
  [ -n "$new" ] || die "Compose did not create a replacement replica for ${SERVICE}"
  printf '%s\n' "$new"
}

# 2. DRAIN (http only). Readiness goes false and every WebSocket this replica
#    holds is asked to reconnect, spread over the window with per-socket
#    jitter. The replica drains ITSELF over loopback: the endpoint must reach
#    exactly one replica, and every other route to it is load balanced.
# ⚠️ NOT FATAL, AND NOT SILENT EITHER. The first roll onto an image that
# introduces this endpoint drains replicas that do not have it yet, and a
# deploy that refused to proceed there could never deliver the endpoint at
# all. But a drain that quietly failed would look exactly like a drain that
# worked and found no sockets -- the first version of this script printed
# `{}` for both -- so the two are told apart in the log.
# 3. WAIT OUT THE WINDOW. Anything still open when the stop signal lands is
#    closed by uvicorn in one tick (3.3: 1012 at 0.10s), which is the herd
#    this whole sequence exists to avoid -- so the wait is not optional and
#    the grace is what covers the last socket's own close.
drain() {
  local cid="$1" name="$2" answer
  [ "$KIND" = http ] || return 0
  if answer="$(in_net "$cid" curl -fsS -X POST --max-time 10 \
      "http://127.0.0.1:${APP_PORT}/health/drain?window_s=${DRAIN_WINDOW_S}" 2>/dev/null)"; then
    log "   drain: ${answer}"
  else
    log "   drain: UNAVAILABLE on ${name} (pre-7.2 image?) — its sockets will be cut, not spread"
  fi
  sleep "$(( DRAIN_WINDOW_S + DRAIN_GRACE_S ))"
}

# 4. RETIRE, which drops the outgoing replica out of Docker's DNS (measured:
#    the address leaves the answer within a second).
#    http:   `rm -f`, as 7.2 measured it (0 of 7,500 failed).
#    health: `docker stop` FIRST -- SIGTERM, then up to the container's own
#            stop_grace_period -- so a worker finishes the batch it holds
#            and deregisters its consumer (5.1's drain ladder). `rm -f` is
#            SIGKILL: every job in flight cut, and a consumer tombstone left
#            in the group.
retire() {
  local cid="$1" name="$2"
  if [ "$KIND" = http ]; then
    log "   removing ${name}"
    docker rm -f "$cid" >/dev/null
  else
    log "   stopping ${name} (SIGTERM; it finishes what it holds first)"
    docker stop "$cid" >/dev/null
    docker rm "$cid" >/dev/null
  fi
}

for cid in "${OLD[@]}"; do
  name="$(name_of "$cid")"
  log "── ${name} ──"

  if [ "$STRATEGY" = surge ]; then
    # 1. SURGE. The replacement is built and proven ready while the outgoing
    #    replica is still taking traffic -- if the host has room for it.
    surge_headroom "$cid"
    before="$(replicas | sort | tr '\n' ' ')"
    docker compose up -d --no-deps --no-recreate --scale "${SERVICE}=$((TARGET + 1))" "${SERVICE}" >/dev/null
    new="$(newest "$before")"
    log "   surged: $(name_of "$new") ($(ip_of "$new")) — waiting for readiness"
    ready_wait "$new"
    log "   ready: $(name_of "$new")"
    drain "$cid" "$name"
    retire "$cid" "$name"
    # 5. RECONCILE back to the declared size, so the next iteration surges
    #    from N again rather than compounding.
    docker compose up -d --no-deps --no-recreate --scale "${SERVICE}=${TARGET}" "${SERVICE}" >/dev/null
  else
    # IN PLACE. Retire first, then let Compose bring the fleet back to N with
    # one new copy -- N-1 for the length of one boot, and no extra memory.
    drain "$cid" "$name"
    retire "$cid" "$name"
    before="$(replicas | sort | tr '\n' ' ')"
    docker compose up -d --no-deps --no-recreate --scale "${SERVICE}=${TARGET}" "${SERVICE}" >/dev/null
    new="$(newest "$before")"
    log "   started: $(name_of "$new") ($(ip_of "$new")) — waiting for readiness"
    ready_wait "$new"
    log "   ready: $(name_of "$new")"
  fi
done

trap - EXIT
log "done: ${SERVICE} rolled through ${TARGET} replica(s)"
for cid in $(replicas); do log "  $(name_of "$cid") ($(ip_of "$cid"))"; done
