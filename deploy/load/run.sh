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
# Optional: LOAD_BASE_URL (default https://localhost) · LOAD_TOKEN_FILE ·
# LOAD_DURATION_S · LOAD_WS_VUS · LOAD_OUT.
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

command -v k6 >/dev/null 2>&1 || {
  echo "k6 is not installed. https://grafana.com/docs/k6/latest/set-up/install-k6/" >&2
  exit 127
}

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

export RUN_K6_VERSION="$(k6 version 2>/dev/null | head -1 || echo '')"
export RUN_HOST="$(hostname 2>/dev/null || echo '')"
export LOAD_OUT="${LOAD_OUT:-$results_dir/$profile-$stamp.json}"

echo "profile   : $profile"
echo "commit    : ${RUN_COMMIT:-<none>}${RUN_DIRTY:+ (dirty=$RUN_DIRTY)}"
echo "edge      : ${LOAD_BASE_URL:-https://localhost}"
echo "seed      : ${LOAD_SEED_ID:-<UNSTATED — this run cannot be a baseline>}"
echo "out       : $LOAD_OUT"
echo

set +e
k6 run "deploy/load/$profile.js"
k6_status=$?
set -e

if [ -f "$LOAD_OUT" ]; then
  valid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["valid"])' "$LOAD_OUT" 2>/dev/null || echo '?')"
  echo
  echo "archived  : $LOAD_OUT"
  echo "valid     : $valid"
  if [ "$valid" != "True" ]; then
    echo "            ⚠️  one of §0.1's three conditions was not met — see .validity in the file." >&2
  fi
fi

# k6's own exit code is the threshold verdict (§7's PASS/FAIL), and it is
# passed through unchanged: a wrapper that swallows it turns a gate into a
# report.
exit "$k6_status"
