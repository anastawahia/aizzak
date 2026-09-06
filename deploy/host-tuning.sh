#!/usr/bin/env bash
# Kernel settings this stack needs from the HOST -- capacity step 3.5
# (docs/capacity-plan.md §5 wave 3). Idempotent, and it prints what it changed.
#
#   deploy/host-tuning.sh            apply (needs root)
#   deploy/host-tuning.sh --check    report only; exit 1 if anything drifts
#
# ⚠️ FOUR OF THE FIVE KNOBS STEP 3.5 NAMES DO NOT LIVE ON THE HOST, and this
# script's job is as much to refuse them as to set the one that does. The
# kernel draws the line itself, and it is visible: inside a container's network
# namespace `/proc/sys/net/core/` holds exactly SEVEN entries --
#
#     rps_default_mask  somaxconn  txrehash  xfrm_acq_expires
#     xfrm_aevent_etime  xfrm_aevent_rseqth  xfrm_larval_drop
#
# -- and `netdev_max_backlog` is not among them. What a container can own a
# private copy of appears there; what it cannot does not exist there at all.
# Docker enforces the same line at the other end, in so many words:
#
#     $ docker run --sysctl net.core.somaxconn=1024 alpine:3 true      # accepted
#     $ docker run --sysctl vm.overcommit_memory=1  alpine:3 true
#     invalid argument "vm.overcommit_memory=1" for "--sysctl" flag:
#     sysctl 'vm.overcommit_memory=1' is not allowed
#
# So the rule this file follows: SET ONLY WHAT A CONTAINER CANNOT SET FOR
# ITSELF. Everything else is named in the closing report with the file it
# actually belongs in, because a host script that writes a namespaced knob
# changes nothing while reading exactly like a fix -- which is the third time
# this repository has met that shape (3.1's `nofile`, 3.2's port range, and
# `somaxconn` here).
set -euo pipefail

# `/proc/sys` on a real host. Overridable so the guard in
# `tests/unit/test_host_tuning.py` can run this script twice against a
# throwaway tree and PROVE the second run is a no-op, rather than asserting it.
SYSCTL_ROOT="${SYSCTL_ROOT:-/proc/sys}"

mode="apply"
case "${1:-}" in
  "") ;;
  --check) mode="check" ;;
  --apply) ;;
  -h | --help)
    sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "usage: $0 [--check|--apply]" >&2
    exit 2
    ;;
esac

# ── What this host owes the stack ────────────────────────────────────────────
# One line per knob: `name<TAB>value<TAB>why`. A knob earns a line here by
# having a MEASURED consequence in this repository, not by appearing on a
# tuning listicle -- see the closing report for the two that were measured and
# deliberately left alone.
#
# `vm.overcommit_memory` is the only one so far, and it is not theoretical:
# redis has been printing this on EVERY boot of this stack, and the oldest line
# still in the log ring is 2026-09-02.
#
#   1:C 02 Sep 2026 16:29:30.080 # WARNING Memory overcommit must be enabled!
#   Without it, a background save or replication may fail under low memory
#   condition. Being disabled, it can also cause failures without low memory
#   condition [...] add 'vm.overcommit_memory = 1' to /etc/sysctl.conf
#
# The failure it describes is a `fork()` for BGSAVE/AOF-rewrite refused because
# the kernel will not promise a copy of a page table it is nearly certain never
# to need. `1` is what redis, and every redis operations guide, asks for; the
# process that pays for `0` is the one holding this platform's sessions, rate
# limit buckets and stream state (`2.5` counts what is in there).
KNOBS=$(
  cat <<'TABLE'
vm.overcommit_memory	1	redis BGSAVE/AOF fork -- warns on every boot of this stack
TABLE
)

read_knob() {
  local path="${SYSCTL_ROOT}/${1//.//}"
  [[ -r "$path" ]] || return 1
  tr -s ' \t' ' ' <"$path" | sed 's/^ *//;s/ *$//'
}

write_knob() {
  local path="${SYSCTL_ROOT}/${1//.//}"
  printf '%s\n' "$2" >"$path"
}

changed=0
drift=0
missing=0

while IFS=$'\t' read -r name want why; do
  [[ -n "$name" ]] || continue
  if ! have="$(read_knob "$name")"; then
    printf 'absent  %-24s (no %s -- wrong kernel, or not the host)\n' "$name" "${SYSCTL_ROOT}/${name//.//}"
    missing=$((missing + 1))
    continue
  fi
  if [[ "$have" == "$want" ]]; then
    printf 'ok      %-24s %s\n' "$name" "$have"
    continue
  fi
  if [[ "$mode" == "check" ]]; then
    printf 'DRIFT   %-24s %s -> %s   (%s)\n' "$name" "$have" "$want" "$why"
    drift=$((drift + 1))
    continue
  fi
  if ! write_knob "$name" "$want" 2>/dev/null; then
    printf 'FAILED  %-24s %s -> %s   (need root: sudo %s)\n' "$name" "$have" "$want" "$0" >&2
    exit 3
  fi
  printf 'changed %-24s %s -> %s   (%s)\n' "$name" "$have" "$want" "$why"
  changed=$((changed + 1))
done <<<"$KNOBS"

# ── Measured, and deliberately NOT set ───────────────────────────────────────
# `netdev_max_backlog` is the one other knob from step 3.5's list that really
# is host-only (it is absent from a container's `/proc/sys/net/core`, see the
# header). It bounds the per-CPU queue between the driver's softirq and the
# protocol stack, and column 2 of `/proc/net/softnet_stat` counts every packet
# dropped for its being full. Two measurements decided to leave it alone, and
# they are printed rather than argued because they point opposite ways:
#
#   6,000 TCP connections through the real edge   88,812 processed   0 dropped
#   one process, 4,097 loopback connections       25,112 processed   270 dropped
#
# The first is the shape §0 describes -- a mass login against nginx -- and the
# default 1000 holds it with nothing to show. The second is a tight loopback
# loop in a single process, which overruns the queue immediately and is how the
# counter below first became non-zero on the measured machine. So the number
# printed is CUMULATIVE SINCE BOOT and says little on its own; what decides
# this knob is whether it GROWS during a load run. Take it before and after.
report_softnet() {
  local stat=/proc/net/softnet_stat processed=0 dropped=0
  [[ -r "$stat" ]] || return 0
  read -r processed dropped <<<"$(awk '{p += strtonum("0x" $1); d += strtonum("0x" $2)}
    END {printf "%d %d", p, d}' "$stat")"
  printf 'left    %-24s %s   (softnet since boot: %s processed, %s dropped -- watch the DELTA over a load run)\n' \
    "net.core.netdev_max_backlog" "$(read_knob net.core.netdev_max_backlog || echo '?')" \
    "$processed" "$dropped"
}
report_softnet

# ── Named here so nobody puts them here ──────────────────────────────────────
# Each of these was measured to be settable per container, which is exactly why
# writing it on the host would be inert. The file named is where the value that
# the process actually sees is written.
cat <<'ELSEWHERE'
elsewhere net.core.somaxconn           docker-compose.yml (nginx, app) -- namespaced; a container's own copy
                                       clamps listen(): measured min(listen, somaxconn), listen(2048) queued
                                       129 at somaxconn=128 while the host stayed 4096
elsewhere net.ipv4.ip_local_port_range docker-compose.yml (nginx, k6) -- namespaced; capacity 3.2 measured the
                                       HOST range at 4,096 ports, six times narrower than the container default
elsewhere net.ipv4.tcp_tw_reuse        docker-compose.yml (nginx) -- namespaced; capacity 3.2
elsewhere nofile (RLIMIT_NOFILE)       docker-compose.yml `ulimits:` and deploy/runpod/supervisord.conf
                                       `minfds` -- not a sysctl at all. Docker hands every container the
                                       daemon's soft limit; capacity 3.1 measured a gunicorn worker pinned at
                                       exactly 1024 with the host's own limit at 1,048,576
ELSEWHERE

if [[ "$mode" == "check" ]]; then
  printf '\n%s: %d drifting, %d absent\n' "$(basename "$0")" "$drift" "$missing"
  [[ "$drift" -eq 0 ]]
else
  printf '\n%s: %d changed, %d absent\n' "$(basename "$0")" "$changed" "$missing"
fi
