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
set -euo pipefail

SERVICE="${SERVICE:-app}"
# Ask Compose, don't guess: docker-compose.yml pins `name: aizzak`, and the
# directory is `AIZZAK` -- basename(pwd) matched no container at all.
PROJECT="${PROJECT:-$(docker compose config 2>/dev/null | sed -n 's/^name: //p' | head -1)}"
[ -n "$PROJECT" ] || { echo "ERROR: cannot resolve compose project name (set PROJECT=)" >&2; exit 2; }
DRAIN_WINDOW_S="${DRAIN_WINDOW_S:-10}"
DRAIN_GRACE_S="${DRAIN_GRACE_S:-2}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-120}"
APP_PORT="${APP_PORT:-8000}"
DRY_RUN=0

usage() {
  cat <<'USAGE'
usage: deploy/rolling-deploy.sh [options]

  --service NAME        Compose service to roll (default: app)
  --drain-window SECS   how long each replica spreads its WebSocket closes
                        over before it is stopped (default: 10)
  --ready-timeout SECS  how long to wait for a replacement to answer
                        /health/ready before giving up (default: 120)
  --dry-run             print the plan and exit, changing nothing

Environment: SERVICE, PROJECT, DRAIN_WINDOW_S, DRAIN_GRACE_S,
READY_TIMEOUT_S, APP_PORT override the same values.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --service) SERVICE="$2"; shift 2;;
    --drain-window) DRAIN_WINDOW_S="$2"; shift 2;;
    --ready-timeout) READY_TIMEOUT_S="$2"; shift 2;;
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
in_net() { docker exec "$1" "${@:2}"; }

ready_wait() {
  local cid="$1" ip deadline
  ip="$(ip_of "$cid")"
  deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if in_net "$cid" curl -fsS -o /dev/null --max-time 5 \
        "http://127.0.0.1:${APP_PORT}/health/ready" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  die "replica $(name_of "$cid") ($ip) never answered /health/ready within ${READY_TIMEOUT_S}s"
}

mapfile -t OLD < <(replicas)
[ "${#OLD[@]}" -gt 0 ] || die "no running replicas of '${SERVICE}' in project '${PROJECT}'"
TARGET="${#OLD[@]}"

log "rolling ${SERVICE}: ${TARGET} replica(s), drain ${DRAIN_WINDOW_S}s, ready timeout ${READY_TIMEOUT_S}s"
for cid in "${OLD[@]}"; do log "  will replace $(name_of "$cid") ($(ip_of "$cid"))"; done
if [ "$DRY_RUN" -eq 1 ]; then log "dry run: nothing changed"; exit 0; fi

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

for cid in "${OLD[@]}"; do
  name="$(name_of "$cid")"
  log "── ${name} ──"

  # 1. SURGE. The replacement is built and proven ready while the outgoing
  #    replica is still taking traffic.
  before="$(replicas | sort | tr '\n' ' ')"
  docker compose up -d --no-deps --no-recreate --scale "${SERVICE}=$((TARGET + 1))" "${SERVICE}" >/dev/null
  new=""
  for candidate in $(replicas); do
    case " ${before} " in *" ${candidate} "*) ;; *) new="${candidate}";; esac
  done
  [ -n "$new" ] || die "Compose did not create a surge replica for ${SERVICE}"
  log "   surged: $(name_of "$new") ($(ip_of "$new")) — waiting for readiness"
  ready_wait "$new"
  log "   ready: $(name_of "$new")"

  # 2. DRAIN. Readiness goes false and every WebSocket this replica holds is
  #    asked to reconnect, spread over the window with per-socket jitter. The
  #    replica drains ITSELF over loopback: the endpoint must reach exactly one
  #    replica, and every other route to it is load balanced.
  # ⚠️ NOT FATAL, AND NOT SILENT EITHER. The first roll onto an image that
  # introduces this endpoint drains replicas that do not have it yet, and a
  # deploy that refused to proceed there could never deliver the endpoint at
  # all. But a drain that quietly failed would look exactly like a drain that
  # worked and found no sockets -- the first version of this script printed
  # `{}` for both -- so the two are told apart in the log.
  if answer="$(in_net "$cid" curl -fsS -X POST --max-time 10 \
      "http://127.0.0.1:${APP_PORT}/health/drain?window_s=${DRAIN_WINDOW_S}" 2>/dev/null)"; then
    log "   drain: ${answer}"
  else
    log "   drain: UNAVAILABLE on ${name} (pre-7.2 image?) — its sockets will be cut, not spread"
  fi

  # 3. WAIT OUT THE WINDOW. Anything still open when the stop signal lands is
  #    closed by uvicorn in one tick (3.3: 1012 at 0.10s), which is the herd
  #    this whole sequence exists to avoid -- so the wait is not optional and
  #    the grace is what covers the last socket's own close.
  sleep "$(( DRAIN_WINDOW_S + DRAIN_GRACE_S ))"

  # 4. STOP AND REMOVE, which drops the outgoing replica out of Docker's DNS
  #    at once (measured: the address leaves the answer within a second).
  log "   removing ${name}"
  docker rm -f "$cid" >/dev/null

  # 5. RECONCILE back to the declared size, so the next iteration surges from
  #    N again rather than compounding.
  docker compose up -d --no-deps --no-recreate --scale "${SERVICE}=${TARGET}" "${SERVICE}" >/dev/null
done

trap - EXIT
log "done: ${SERVICE} rolled through ${TARGET} replica(s)"
for cid in $(replicas); do log "  $(name_of "$cid") ($(ip_of "$cid"))"; done
