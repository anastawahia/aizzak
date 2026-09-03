#!/usr/bin/env bash
# The wrapper that makes a k6 run REPRODUCIBLE -- §0.1's acceptance criterion
# asks for the commit SHA, the image digests and the seed size alongside the
# percentiles, and k6 can see none of those three from inside a script.
#
#   deploy/load/run.sh peak
#   deploy/load/run.sh average
#
# Environment the OPERATOR must supply (there are no defaults, on purpose --
# see `lib/config.js` on why an unstated seed makes two runs incomparable):
#
#   LOAD_SEED_ID          a name for the corpus this ran against
#   LOAD_SEED_MESSAGES    row counts, as seeded
#   LOAD_SEED_FILES
#   LOAD_SEED_VECTORS
#   LOAD_SEED_WORKSPACES
#
# Optional: LOAD_BASE_URL (default https://localhost, or https://nginx in a
# container) · LOAD_TOKEN_FILE · LOAD_DURATION_S · LOAD_WS_VUS · LOAD_OUT ·
# LOAD_K6 (auto|host|docker) · LOAD_SRC_IPS (how many source addresses the
# container claims; see `entrypoint.sh` and capacity blocker د‑8).
set -euo pipefail

profile="${1:-peak}"
case "$profile" in
  peak | average) ;;
  *)
    echo "usage: $0 {peak|average}" >&2
    exit 2
    ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

# ── Where k6 comes from (capacity blocker د‑3) ────────────────────────────
# The plan's acceptance criterion is a k6 run, and k6 is not a library this
# project can vendor -- it is a Go binary that has to exist somewhere. It did
# not exist on the machine that wrote the harness, which held ALL of 0.5 and
# the acceptance of 0.1 and 0.4 behind an `apt` line nobody had run.
#
# So the profile is also a pinned Compose service (`--profile load`), and this
# script decides between the two:
#
#   auto   (default)  a host binary if there is one, else the container
#   host              refuse rather than silently containerise
#   docker            the pinned image even where a host k6 exists
#
# `host` is preferred when available for one reason only: one less network
# hop and one less scheduler between the generator and the edge. Both paths
# run the SAME scripts against the SAME edge, and both stamp the k6 version
# they used into the archived result, so a report never has to be trusted
# about which one produced it.
k6_mode="${LOAD_K6:-auto}"
case "$k6_mode" in
  auto)
    if command -v k6 >/dev/null 2>&1; then k6_mode=host; else k6_mode=docker; fi
    ;;
  host)
    command -v k6 >/dev/null 2>&1 || {
      echo "LOAD_K6=host but k6 is not installed." >&2
      echo "https://grafana.com/docs/k6/latest/set-up/install-k6/ — or unset LOAD_K6 to use the container." >&2
      exit 127
    }
    ;;
  docker) ;;
  *)
    echo "LOAD_K6 must be auto, host or docker (got '$k6_mode')" >&2
    exit 2
    ;;
esac

if [ "$k6_mode" = docker ]; then
  docker compose version >/dev/null 2>&1 || {
    echo "no host k6 and no usable \`docker compose\`: this run has no load generator." >&2
    echo "Install k6 (https://grafana.com/docs/k6/latest/set-up/install-k6/) or Docker Compose." >&2
    exit 127
  }
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
results_dir="deploy/load/results"
mkdir -p "$results_dir"

export RUN_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo '')"
# A dirty tree does not block the run -- it blocks the CLAIM that the run
# describes the commit. Recorded rather than refused, and `0.5`'s baseline
# should never be taken from a dirty tree.
if [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then
  export RUN_DIRTY=1
  echo "⚠️  working tree is dirty; this run is not attributable to ${RUN_COMMIT:0:12}" >&2
else
  export RUN_DIRTY=0
fi

# Image digests, best effort: `docker compose images` is the only view that
# names what is ACTUALLY running rather than what the file asks for, which is
# the whole distinction ح‑20 is about.
export RUN_IMAGES="$(
  docker compose images --format json 2>/dev/null |
    python3 -c 'import json,sys
try:
    rows = json.load(sys.stdin)
except Exception:
    print("{}"); raise SystemExit
if isinstance(rows, dict):
    rows = [rows]
print(json.dumps({r.get("Service", "?"): (r.get("ID") or r.get("Digest") or "") for r in rows}))' 2>/dev/null || echo '{}'
)"

export RUN_HOST="$(hostname 2>/dev/null || echo '')"

# ── Paths, and the one thing the container changes about them ─────────────
# The container sees exactly `deploy/load/` (bind-mounted at /load) and
# nothing else of this repository. An operator-supplied path outside that
# directory is REFUSED rather than quietly redirected: a run whose result
# went somewhere other than where the operator asked is worse than a run that
# did not start.
out_name="$profile-$stamp.json"

_to_container_path() {
  case "$1" in
    /load/*) echo "$1" ;;
    deploy/load/*) echo "/load/${1#deploy/load/}" ;;
    "$repo_root"/deploy/load/*) echo "/load/${1#"$repo_root"/deploy/load/}" ;;
    *)
      echo "LOAD_K6=docker: '$1' is outside deploy/load/, which is the only directory the k6 container can see." >&2
      return 1
      ;;
  esac
}

if [ "$k6_mode" = docker ]; then
  host_out="${LOAD_OUT:-$results_dir/$out_name}"
  LOAD_OUT="$(_to_container_path "$host_out")" || exit 2
  export LOAD_OUT
  if [ -n "${LOAD_TOKEN_FILE:-}" ]; then
    LOAD_TOKEN_FILE="$(_to_container_path "$LOAD_TOKEN_FILE")" || exit 2
    export LOAD_TOKEN_FILE
  fi

  # `https://localhost` inside the generator's own container is the
  # generator's own loopback -- a connection refused that reads like a dead
  # edge. Inside this network the edge is `nginx`, which is the same nginx,
  # reached without the published-port hop.
  if [ -z "${LOAD_BASE_URL:-}" ]; then
    export LOAD_BASE_URL="https://nginx"
  else
    case "$LOAD_BASE_URL" in
      *localhost* | *127.0.0.1*)
        echo "LOAD_BASE_URL=$LOAD_BASE_URL names the k6 CONTAINER's loopback, not the edge." >&2
        echo "Inside the Compose network the edge is https://nginx (leave LOAD_BASE_URL unset)." >&2
        exit 2
        ;;
    esac
  fi

  # `LOAD_SRC_IPS=0`: a version probe has no reason to claim 32 addresses.
  export RUN_K6_VERSION="$(
    LOAD_SRC_IPS=0 docker compose --profile load run --rm --no-deps -T k6 version 2>/dev/null | head -1 || echo ''
  )"

  # ── The generator's source addresses (capacity blocker د‑8) ─────────────
  # The edge meters per source address, so one container is one client and a
  # 300 rps run is answered 429 for 92.7% of it. The generator claims a block
  # of addresses instead and spreads its VUs across them, which changes
  # nothing at all about the system under test -- see `entrypoint.sh` for the
  # measurement, and for why the alternative (loosening `limit_req`) is
  # forbidden here by `م‑8`.
  export LOAD_SRC_IPS="${LOAD_SRC_IPS:-32}"
  # The container runs k6 as root to get CAP_NET_ADMIN; these say who should
  # own the results it leaves behind.
  export LOAD_UID="$(id -u)"
  export LOAD_GID="$(id -g)"
else
  export LOAD_OUT="${LOAD_OUT:-$results_dir/$out_name}"
  host_out="$LOAD_OUT"
  export RUN_K6_VERSION="$(k6 version 2>/dev/null | head -1 || echo '')"
  # A host k6 reaches the edge from ONE address too, and this script has no
  # business adding addresses to the operator's own machine behind their back.
  # So host mode still meets `limit_req`'s 20 r/s per address unless the
  # operator supplies the addresses themselves -- said out loud, because a
  # peak run that silently measured the limiter is exactly how د‑8 was missed.
  export LOAD_SRC_IPS=0
  if [ "$profile" = peak ]; then
    echo "⚠️  LOAD_K6=host: the edge meters per source address (20 r/s, burst 40)." >&2
    echo "    A single-address peak run measures nginx's limiter, not the platform." >&2
    echo "    Use the container (unset LOAD_K6), or pass k6 your own --local-ips." >&2
  fi
fi

echo "profile   : $profile"
echo "generator : $k6_mode  ${RUN_K6_VERSION:-<unknown>}  src-addrs=${LOAD_SRC_IPS:-0}"
echo "commit    : ${RUN_COMMIT:-<none>}${RUN_DIRTY:+ (dirty=$RUN_DIRTY)}"
echo "edge      : ${LOAD_BASE_URL:-https://localhost}"
echo "seed      : ${LOAD_SEED_ID:-<UNSTATED — this run cannot be a baseline>}"
echo "out       : $host_out"
echo

set +e
if [ "$k6_mode" = host ]; then
  k6 run "deploy/load/$profile.js"
else
  # No `--user`: the entrypoint needs root for CAP_NET_ADMIN (د‑8) and hands
  # `results/` back to LOAD_UID:LOAD_GID when the run ends. The image's own uid
  # is 12345, so without one of the two the whole 30-minute run would end in a
  # permission denied writing its own archive.
  docker compose --profile load run --rm -T \
    k6 run "/load/$profile.js"
fi
k6_status=$?
set -e

if [ -f "$host_out" ]; then
  valid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["valid"])' "$host_out" 2>/dev/null || echo '?')"
  echo
  echo "archived  : $host_out"
  echo "valid     : $valid"
  if [ "$valid" != "True" ]; then
    echo "            ⚠️  one of §0.1's three conditions was not met — see .validity in the file." >&2
  fi
fi

# k6's own exit code is the threshold verdict (§7's PASS/FAIL), and it is
# passed through unchanged: a wrapper that swallows it turns a gate into a
# report.
exit "$k6_status"
