#!/usr/bin/env bash
# What this stack is allowed to consume, added up, and compared with the host
# it is about to run on -- capacity step 3.4 (docs/capacity-plan.md §5 wave 3).
#
#   deploy/resource-budget.sh          report the budget against THIS host
#   deploy/resource-budget.sh --host-cpus N --host-memory-gb G
#                                      report it against a host you do not have
#
# ⚠️ THIS SCRIPT EXISTS BECAUSE DOCKER WILL NOT TELL YOU. Measured on the
# development host these numbers were taken on (10 vCPU, 12.67 GB):
#
#     deploy: {resources: {limits: {cpus: "6.0", memory: 20G}}}
#     -> HostConfig.Memory=21474836480  NanoCpus=6000000000
#
# Accepted in full. No warning on stdout, no log line, no failure, no clamp. A
# budget written for a host you do not have looks exactly like one that fits,
# and the difference only appears the first time something is actually under
# load -- which is the worst moment to discover it.
#
# ⭐ A CPU LIMIT AND A MEMORY LIMIT ARE NOT THE SAME KIND OF PROMISE, so this
# script judges them differently and that asymmetry is the whole point:
#
#   * `cpus` is a CEILING on a share the scheduler hands out. Several services
#     capped at 2 on a 4-core host still get isolation from each other, because
#     no one of them can take more than its cap however loaded it is. A CPU
#     total above the host is OVERSUBSCRIPTION -- reported as a ratio, never an
#     error.
#   * `memory` is a KILL THRESHOLD on a cgroup, and it isolates NOTHING once
#     the sum passes host RAM: the kernel's OOM killer fires host-wide and
#     picks by RSS, long before any single cgroup reaches its own limit. So the
#     memory total is a HARD requirement and a failure here is a real one.
#
# The one-shot services (`migrate`, the two bootstraps, `nginx-certs`,
# `wal-archive-init`) are capped but NOT counted in the standing total: every
# standing service declares `service_completed_successfully` on them, so they
# cannot be concurrent with the steady state. Same exclusion, and the same
# reason, as the connection ledger's `_PRE_FLIGHT_MODULES` (08 §2-ب).
# Profiled services (`backup`, `cadvisor`, `k6`) are excluded because a default
# `docker compose up -d` never starts them; `--with-profiles` counts them.
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

host_cpus=""
host_memory_gb=""
with_profiles=0

while [ $# -gt 0 ]; do
  case "$1" in
    --host-cpus) host_cpus="$2"; shift 2 ;;
    --host-memory-gb) host_memory_gb="$2"; shift 2 ;;
    --with-profiles) with_profiles=1; shift ;;
    -h | --help)
      sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "usage: $0 [--host-cpus N] [--host-memory-gb G] [--with-profiles]" >&2; exit 2 ;;
  esac
done

if [ -z "$host_cpus" ]; then
  host_cpus="$(nproc)"
fi
if [ -z "$host_memory_gb" ]; then
  # MemTotal is in kB; the ceiling this compares against is physical RAM, not
  # what is free right now -- a limit is a maximum, and the question is whether
  # the maxima can coexist at all.
  host_memory_gb="$(awk '/^MemTotal:/ {printf "%.2f", $2 / 1048576}' /proc/meminfo)"
fi

exec python3 - "$COMPOSE_FILE" "$host_cpus" "$host_memory_gb" "$with_profiles" <<'PY'
import sys

import yaml

compose_file, host_cpus, host_memory_gb, with_profiles = sys.argv[1:5]
host_cpus = float(host_cpus)
host_memory_gb = float(host_memory_gb)
with_profiles = with_profiles == "1"

_SUFFIX = {"b": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def to_bytes(raw: object) -> int:
    """Compose's own size grammar: a bare integer is bytes, otherwise one of
    b/k/m/g (case-insensitive, optional trailing `b`)."""
    text = str(raw).strip().lower().removesuffix("b") or "0"
    if text[-1] in _SUFFIX:
        return int(float(text[:-1]) * _SUFFIX[text[-1]])
    return int(float(text))


compose = yaml.safe_load(open(compose_file, encoding="utf-8"))
services = compose["services"]

standing, oneshot, profiled, missing = [], [], [], []
for name, service in sorted(services.items()):
    limits = ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}
    if "cpus" not in limits or "memory" not in limits:
        missing.append(name)
        continue
    replicas = int((service.get("deploy") or {}).get("replicas", 1))
    row = (name, replicas, float(limits["cpus"]), to_bytes(limits["memory"]))
    if service.get("profiles"):
        profiled.append(row)
    elif service.get("restart") == "no":
        oneshot.append(row)
    else:
        standing.append(row)

counted = standing + (profiled if with_profiles else [])

width = max(len(r[0]) for r in standing + oneshot + profiled)
GB = 1024**3


def show(title: str, rows: list[tuple[str, int, float, int]]) -> None:
    if not rows:
        return
    print(f"\n{title}")
    for name, replicas, cpus, mem in rows:
        tag = f" x{replicas}" if replicas != 1 else "   "
        print(
            f"  {name:<{width}}{tag}  {cpus * replicas:6.2f} vCPU"
            f"  {mem * replicas / GB:7.2f} GB"
        )


show("standing (a default `docker compose up -d`)", standing)
show("one-shot (completes before the steady state; capped, not counted)", oneshot)
show(
    "profiled (never started by a default `up -d`)"
    + ("  [COUNTED: --with-profiles]" if with_profiles else ""),
    profiled,
)

cpu_total = sum(c * r for _, r, c, _ in counted)
mem_total = sum(m * r for _, r, _, m in counted) / GB

print(f"\n{'':<{width}}      ------ ----      ------- --")
print(f"  {'TOTAL':<{width}}     {cpu_total:6.2f} vCPU  {mem_total:7.2f} GB")
print(f"  {'this host':<{width}}     {host_cpus:6.2f} vCPU  {host_memory_gb:7.2f} GB")

status = 0

# CPU: a ratio, never a failure. See the header.
ratio = cpu_total / host_cpus if host_cpus else float("inf")
if cpu_total <= host_cpus:
    print(f"\n  CPU     OK       {cpu_total:.2f} of {host_cpus:.2f} vCPU ({ratio:.0%})")
else:
    print(
        f"\n  CPU     OVERSUBSCRIBED  {cpu_total:.2f} of {host_cpus:.2f} vCPU"
        f" ({ratio:.2f}x). Caps still isolate -- no service can exceed its own --"
        f" but they cannot all be saturated at once."
    )

if mem_total <= host_memory_gb:
    print(
        f"  MEMORY  OK       {mem_total:.2f} of {host_memory_gb:.2f} GB"
        f" ({mem_total / host_memory_gb:.0%})"
    )
else:
    print(
        f"  MEMORY  OVER     {mem_total:.2f} GB of limits on a host with"
        f" {host_memory_gb:.2f} GB. These limits ISOLATE NOTHING: the host OOM"
        f" killer fires by RSS before any cgroup reaches its own ceiling."
    )
    status = 1

if missing:
    print(
        f"\n  NO LIMIT  {', '.join(missing)}"
        "\n            An unbounded service is the one that starves the others"
        " (capacity 3.4)."
    )
    status = 1

sys.exit(status)
PY
