#!/usr/bin/env bash
# The wrapper that makes a k6 run REPRODUCIBLE -- §0.1's acceptance criterion
# asks for the commit SHA, the image digests and the seed size alongside the
# percentiles, and k6 can see none of those three from inside a script.
#
#   deploy/load/run.sh peak
#   deploy/load/run.sh average
#   deploy/load/run.sh step
#   deploy/load/run.sh backlog   (capacity 5.5's load; 08 §4.21 stops the worker around it)
#   deploy/load/run.sh abuse     (1.2: one abusive tenant against its neighbours, 3 phases)
#   deploy/load/run.sh rag       (4.3: the RAG scenario alone at §0's peak question rate)
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
  peak | average | step | backlog | abuse | rag) ;;
  *)
    echo "usage: $0 {peak|average|step|backlog|abuse|rag}" >&2
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

# ── The platform under test is the one the files describe (م‑8, ح‑20) ─────
# `.env` is where the baseline's kill switches live, and a switch flipped in
# `.env` reaches a container only when that container is RECREATED. So a run
# can read `AUTH_PRINCIPAL_CACHE_TTL_S=0` in the file and measure a platform
# still caching principals -- nothing in k6 can tell, and the archived result
# would swear to a configuration it never ran. Compose stamps every container
# with a hash of the config it was created from; comparing that with the
# hash the files produce NOW is the whole check. Refused, not recorded: a
# dirty tree is a caveat on the claim, a stale container is a different
# platform.
if docker compose version >/dev/null 2>&1; then
  drift="$(
    hashes="$(docker compose config --hash '*' 2>/dev/null)"
    docker compose ps --format '{{.Name}} {{.Service}}' 2>/dev/null |
      while read -r name service; do
        [ -n "$name" ] || continue
        want="$(printf '%s\n' "$hashes" | awk -v s="$service" '$1 == s { print $2 }')"
        [ -n "$want" ] || continue
        have="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$name" 2>/dev/null)"
        [ "$want" = "$have" ] || echo "$service"
      done | sort -u | tr '\n' ' '
  )"
  if [ -n "$drift" ]; then
    echo "⚠️  running containers were created from an older docker-compose.yml/.env: ${drift}" >&2
    echo "    The switches the files set are not the switches the platform runs. Recreate first:" >&2
    echo "      docker compose up -d --build" >&2
    exit 2
  fi
fi

# Image IDs, one per Compose service, best effort. The image a CONTAINER was
# created from is what is ACTUALLY running -- not what the file asks for, and
# not what `:dev` points at after a rebuild nobody recreated -- which is the
# whole distinction ح‑20 is about. The service comes from Compose's own label,
# the way `rolling-deploy.sh` finds replicas: until 2026-09-28 this read a
# `Service` field from `docker compose images --format json`, which Compose v5
# does not print, so every row landed under "?" and only the last one survived
# (د‑36). Replicas that disagree -- a rollout caught halfway -- are kept as a
# list, never collapsed into whichever came last.
export RUN_IMAGES="$(
  {
    containers="$(docker compose ps -aq 2>/dev/null || true)"
    [ -z "$containers" ] ||
      docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}} {{.Image}}' \
        $containers 2>/dev/null ||
      true
  } | python3 -c 'import json,sys
ids = {}
for line in sys.stdin:
    fields = line.split()
    if fields:
        ids.setdefault(fields[0] if len(fields) == 2 else "?", set()).add(fields[-1])
out = {}
for service, images in sorted(ids.items()):
    images = sorted(images)
    out[service] = images[0] if len(images) == 1 else images
print(json.dumps(out))' 2>/dev/null || echo '{}'
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

# ── The generator's own health ───────────────────────────────────────────────
# §0.1's conditions say the PLATFORM was set up right; none said the generator
# kept up, and on 2026-09-26 it did not: k6 thrashed against its 2 GiB limit
# for minutes -- 957 MiB swapped out, 5.97M major faults -- before the kernel
# killed it at 17m29s, and everything it timed in that stretch timed itself.
# So its cgroup is read from OUTSIDE while it runs (`cgroup_sample.sh`, every
# 10s): hitting the memory limit and being CPU-throttled are the two ways a
# container starves, and a run where either happened is marked invalid below.
# Docker mode only -- a host k6 has no container to read.
gen_log=""
sampler=""
# And the machine's own paging, which the platform shares with the generator:
# on this WSL VM the stack alone leaves ~3 GiB free, so a run can push the
# PLATFORM into swap, and a query waiting on a page-in is timed as a slow
# query. Recorded, not gated -- the idle VM already pages a little.
swap_before="$(awk '/^pswp(in|out) /{printf "%s ", $2}' /proc/vmstat 2>/dev/null || true)"
if [ "$k6_mode" = docker ]; then
  gen_log="${host_out%.json}.generator.log"
  (
    while sleep 10; do
      id="$(docker ps -q --filter label=com.docker.compose.service=k6 \
        --filter label=com.docker.compose.oneoff=True | head -1)"
      [ -n "$id" ] && docker exec "$id" sh /load/cgroup_sample.sh 2>/dev/null
    done
  ) >"$gen_log" &
  sampler=$!
fi

# ── The query-vector cache's own count (4.3 · `aizzak_embedding_cache_total`) ─
# Read from every app replica just before and just after the run, directly and
# not through Prometheus, whose 15 s scrape would smear both edges of the
# window. One line per container and result, so a replica that restarted
# mid-run -- its count starts again at zero -- is caught below rather than
# subtracted into a smaller number. A replica that has served no search has
# no line at all (a labelled counter appears on first use), so each one that
# answered also prints `answered` -- otherwise its first search would read as
# a replica that joined mid-run. Every lookup in the window counts, from any
# caller: in the five-part mix that includes the chat scenario's one fixed
# prompt, which is why 4.3's hit rate is read from the `rag` profile.
cache_snapshot() {
  for id in $(docker compose ps -q app 2>/dev/null); do
    docker exec "$id" python -c 'import urllib.request
for line in urllib.request.urlopen("http://127.0.0.1:8000/metrics", timeout=5).read().decode().splitlines():
    if line.startswith("aizzak_embedding_cache_total{"):
        print(line)
print("answered")' 2>/dev/null |
      sed -nE -e "s/^aizzak_embedding_cache_total\{result=\"([a-z_]+)\"\} ([0-9.e+]+)$/$id \1 \2/p" \
        -e "s/^answered$/$id answered 1/p"
  done
}
# And the TTL those replicas run, for `rag.js`'s replay of how often its
# stream repeated inside that window. From a running replica rather than
# `.env`: the drift check above makes the two agree, and this is the value in
# force. Unset in the container means the platform's default.
RUN_EMBEDDING_CACHE_TTL_S="$(docker compose exec -T app printenv EMBEDDING_CACHE_TTL_S 2>/dev/null | tr -d '\r' || true)"
export RUN_EMBEDDING_CACHE_TTL_S="${RUN_EMBEDDING_CACHE_TTL_S:-600}"
cache_before="$(cache_snapshot || true)"

# The run's window, for the cost block below: the ledger is read for exactly
# the charges written while k6 ran.
run_started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
set +e
if [ "$k6_mode" = host ]; then
  k6 run "deploy/load/$profile.js"
else
  # No `--user`: the entrypoint needs root for CAP_NET_ADMIN (د‑8) and hands
  # `results/` back to LOAD_UID:LOAD_GID when the run ends. The image's own uid
  # is 12345, so without one of the two the whole 30-minute run would end in a
  # permission denied writing its own archive.
  # `--no-deps`, and not as an optimisation. Without it `compose run` brings
  # the generator's dependency chain (k6 -> nginx -> app -> ...) "up", and
  # "up" includes RECREATING any container whose config no longer matches
  # `docker-compose.yml` + `.env`. The 2026-09-20 peak run did exactly that:
  # five seconds after it started, run.sh had replaced all three app replicas
  # with fresh ones, and the fresh ones could not verify a single token for
  # the whole thirty minutes. The platform under test is whatever is already
  # running -- the drift check above is what guarantees that matches the
  # files -- and this script never changes it.
  docker compose --profile load run --rm --no-deps -T \
    k6 run "/load/$profile.js"
fi
k6_status=$?
set -e
run_ended="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cache_after="$(cache_snapshot || true)"

if [ -n "$sampler" ]; then
  kill "$sampler" 2>/dev/null || true
  wait "$sampler" 2>/dev/null || true
fi
swap_after="$(awk '/^pswp(in|out) /{printf "%s ", $2}' /proc/vmstat 2>/dev/null || true)"

if [ ! -f "$host_out" ] && [ "$k6_status" = 137 ]; then
  echo
  echo "⚠️  k6 was KILLED (exit 137) and wrote nothing — almost always its memory limit:" >&2
  echo "      dmesg | grep -i 'killed process'" >&2
fi

# The verdict on the samples, folded into the archived file so it travels with
# the numbers. Invalid when the generator hit its memory limit at all, or was
# throttled in more than 1% of CPU periods -- a throttled period freezes every
# VU for up to 100ms, which is the size of the budgets being measured. The
# first 30s are skipped: that is thousands of VUs compiling the same script,
# before any request is timed.
if [ -f "$host_out" ] && [ -n "$gen_log" ]; then
  python3 - "$host_out" "$gen_log" "$swap_before" "$swap_after" <<'PY' || echo "⚠️  could not evaluate the generator samples in $gen_log" >&2
import json, os, sys

out, log = sys.argv[1], sys.argv[2]
rows = []
for line in open(log):
    kv = dict(p.split("=", 1) for p in line.split() if "=" in p)
    if "t" in kv:
        rows.append(kv)


def num(row, key):
    return int(row.get(key) or 0)


if rows:
    base = next((r for r in rows[:-1] if num(r, "t") >= num(rows[0], "t") + 30), rows[0])
    last = rows[-1]
    periods = num(last, "nr_periods") - num(base, "nr_periods")
    throttled = num(last, "nr_throttled") - num(base, "nr_throttled")
    gen = {
        "samples": len(rows),
        "memory_limit_bytes": None if last.get("limit") == "max" else num(last, "limit"),
        "memory_peak_bytes": max(num(r, "peak") for r in rows),
        "memory_limit_hits": num(last, "max"),
        "oom_kills": num(last, "oom_kill"),
        "cpu_throttled_pct": round(100.0 * throttled / periods, 2) if periods else 0.0,
    }
    kept_up = gen["memory_limit_hits"] == 0 and gen["oom_kills"] == 0 and gen["cpu_throttled_pct"] <= 1.0
else:
    # Nothing sampled is not evidence that nothing went wrong.
    gen, kept_up = {"samples": 0}, False

swap = [s.split() for s in sys.argv[3:5]]
if all(len(x) == 2 for x in swap):
    page = os.sysconf("SC_PAGE_SIZE")
    (in0, out0), (in1, out1) = ([int(v) for v in x] for x in swap)
    gen["host_swapped_in_mib"] = round((in1 - in0) * page / 2**20, 1)
    gen["host_swapped_out_mib"] = round((out1 - out0) * page / 2**20, 1)

doc = json.load(open(out))
doc["generator"] = gen
doc["validity"]["generator_kept_up"] = kept_up
doc["valid"] = doc["validity"]["valid"] = bool(doc["valid"]) and kept_up
with open(out, "w") as f:
    json.dump(doc, f, indent=2)

peak = gen.get("memory_peak_bytes")
limit = gen.get("memory_limit_bytes")
print(
    "generator : "
    + (f"memory peak {peak / 2**20:.0f} MiB of {limit / 2**20:.0f} MiB · " if peak and limit else "")
    + f"limit hits {gen.get('memory_limit_hits', '?')} · CPU throttled {gen.get('cpu_throttled_pct', '?')}% of periods"
)
if "host_swapped_in_mib" in gen:
    print(f"host      : swapped in {gen['host_swapped_in_mib']} MiB, out {gen['host_swapped_out_mib']} MiB during the run")
PY
fi

# ── What the run cost (capacity-plan 6.5 · §7 item 11) ───────────────────────
# §7 asks the gate report for "the cost per 1,000 requests and per million
# tokens". Read from the usage LEDGER, not from a metric: it is the very rows
# the workspace budget decides on, so the report and the 429s cannot disagree,
# and it is the only place a summary build's charge is visible at all (the
# workers expose no metrics). Superuser inside the postgres container, the
# backlog-check precedent: the ledger is RLS-forced per workspace, and this
# question is about all of them. Every charge in the window counts -- on a
# stack that serves anyone else during a run, so does their spend.
# `cost_micros / tokens` IS dollars per million tokens (the units are chosen
# so; `framework/providers/pricing.py`).
if [ -f "$host_out" ]; then
  cost_row="$(printf "select count(*), coalesce(sum(tokens), 0), coalesce(sum(cost_micros), 0), count(distinct workspace_id), coalesce(sum(cost_micros) filter (where agent_key = 'summarize'), 0), coalesce((select max(c) from (select sum(cost_micros) c from usage.usage_records where created_at >= '%s' and created_at < '%s' group by workspace_id) w), 0) from usage.usage_records where created_at >= '%s' and created_at < '%s';\n" \
    "$run_started" "$run_ended" "$run_started" "$run_ended" \
    | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAt -F " "' 2>/dev/null || true)"
  python3 - "$host_out" "$run_started" "$run_ended" "$cost_row" <<'PY' || echo "⚠️  could not record the run's cost" >&2
import json, sys

out, started, ended, row = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split()
doc = json.load(open(out))
requests = doc.get("counters", {}).get("http_reqs") or 0
if len(row) != 6:
    doc["cost"] = None
    print("cost      : ⚠️  the usage ledger could not be read -- no cost recorded for this run")
else:
    charges, tokens, micros, workspaces, summary_micros, top_micros = (int(v) for v in row)
    doc["cost"] = {
        "window": {"from": started, "to": ended},
        "charges": charges,
        "tokens": tokens,
        "cost_micros": micros,
        "cost_usd": micros / 1e6,
        "requests": requests,
        "usd_per_1k_requests": round(micros / 1e6 / requests * 1000, 6) if requests else None,
        "usd_per_1m_tokens": round(micros / tokens, 6) if tokens else None,
        "usd_per_1k_llm_charges": round(micros / 1e6 / charges * 1000, 6) if charges else None,
        "workspaces_charged": workspaces,
        "summary_cost_micros": summary_micros,
        "top_workspace_cost_micros": top_micros,
    }
    c = doc["cost"]
    print(
        f"cost      : ${c['cost_usd']:.4f} for {tokens:,} tokens in {charges:,} charges "
        f"({workspaces} workspaces)"
    )
    print(
        "            "
        + (f"${c['usd_per_1k_requests']:.6f} per 1,000 requests · " if requests else "no requests · ")
        + (f"${c['usd_per_1m_tokens']:.4f} per 1M tokens" if tokens else "no tokens")
    )
with open(out, "w") as f:
    json.dump(doc, f, indent=2)
PY
fi

# The cache count over the window (the snapshots above). `hit_rate` is hits
# over all three results, `08 §2-ط`'s formula: `unavailable` is a lookup Redis
# did not answer, and leaving it out would make a broken cache look like a
# quiet one. `replicas_stable` false means a container came, went or
# restarted in between, and the delta undercounts.
if [ -f "$host_out" ]; then
  python3 - "$host_out" "$cache_before" "$cache_after" <<'PY' || echo "⚠️  could not record the cache count" >&2
import json, sys

out, before, after = sys.argv[1], sys.argv[2], sys.argv[3]


def parse(text):
    rows = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3:
            rows[(parts[0], parts[1])] = float(parts[2])
    return rows


b, a = parse(before), parse(after)
doc = json.load(open(out))
if not a:
    doc["embedding_cache"] = None
    print("cache     : ⚠️  no replica answered /metrics -- no cache count recorded for this run")
else:
    containers = lambda rows: {c for c, _ in rows}
    stable = containers(b) == containers(a) and all(a.get(k, 0) >= v for k, v in b.items())
    lookups = {}
    for (container, result), value in a.items():
        if result != "answered":
            lookups[result] = lookups.get(result, 0) + int(value - b.get((container, result), 0))
    total = sum(lookups.values())
    doc["embedding_cache"] = {
        "lookups": lookups,
        "hit_rate": lookups.get("hit", 0) / total if total else None,
        "replicas": len(containers(a)),
        "replicas_stable": stable,
    }
    c = doc["embedding_cache"]
    print(
        f"cache     : {lookups.get('hit', 0):,} hits of {total:,} lookups"
        + (f" ({c['hit_rate'] * 100:.1f}%)" if total else "")
        + f" · unavailable {lookups.get('unavailable', 0):,}"
        + ("" if stable else " · ⚠️  a replica restarted or changed during the run; the count undercounts")
    )
with open(out, "w") as f:
    json.dump(doc, f, indent=2)
PY
fi

if [ -f "$host_out" ]; then
  valid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["valid"])' "$host_out" 2>/dev/null || echo '?')"
  echo
  answered="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["validity"]["platform_answered"])' "$host_out" 2>/dev/null || echo '?')"
  attempts="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["counters"]["aizzak_failed_requests"]["total"])' "$host_out" 2>/dev/null || echo '?')"
  echo "archived  : $host_out"
  echo "valid     : $valid"
  # k6 writes the summary even for a run that never started (`setup()` threw:
  # an expired pool, a failed pre-flight probe), so the file exists and says
  # so; the message here says which of the two it was.
  if [ "$attempts" = "0" ]; then
    echo "            ⚠️  no load was generated — setup() refused (see the k6 error above); nothing here is a measurement." >&2
  elif [ "$answered" = "False" ]; then
    echo "            ⚠️  the platform refused more than it answered — this run measured an outage, not a capacity." >&2
    echo "                see .counters.aizzak_failed_requests, then the app log for the refusal's cause." >&2
  elif [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["validity"].get("generator_kept_up"))' "$host_out" 2>/dev/null)" = "False" ]; then
    echo "            ⚠️  the GENERATOR ran short (memory limit or CPU throttling) — these latencies partly time k6." >&2
    echo "                see .generator in the file." >&2
  elif [ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["validity"].get("rate_delivered"))' "$host_out" 2>/dev/null)" = "False" ]; then
    echo "            ⚠️  k6 DROPPED arrivals (ran out of VUs) — the profile's rate was never offered; this was a closed-loop run." >&2
    echo "                see .counters.dropped_iterations against .counters.iterations." >&2
  elif [ "$valid" != "True" ]; then
    echo "            ⚠️  one of §0.1's conditions was not met — see .validity in the file." >&2
  fi
fi

# The abuse profile's verdict is a comparison between its phases, which no k6
# threshold can express, so `abuse.js` writes it into the archive and it is
# turned into an exit code HERE -- 99, k6's own code for a crossed threshold,
# so a run whose neighbours felt the abuser fails exactly like a missed budget.
if [ "$profile" = abuse ] && [ -f "$host_out" ] && [ "$k6_status" = 0 ]; then
  held="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["isolation"]["held"])' "$host_out" 2>/dev/null || echo '?')"
  if [ "$held" != "True" ]; then
    echo "isolation : NOT HELD — see .isolation.checks in the file." >&2
    k6_status=99
  fi
fi

# k6's own exit code is the threshold verdict (§7's PASS/FAIL), and it is
# passed through unchanged: a wrapper that swallows it turns a gate into a
# report.
exit "$k6_status"
