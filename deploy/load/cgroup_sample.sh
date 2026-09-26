#!/bin/sh
# One line of THIS container's cgroup counters. `run.sh` execs it inside the
# k6 container every few seconds while a profile runs ("generator health");
# the values are cumulative counters, and `run.sh` differences them.
cd /sys/fs/cgroup || exit 1
printf 't=%s current=%s peak=%s limit=%s' "$(date +%s)" "$(cat memory.current)" \
  "$(cat memory.peak 2>/dev/null || echo 0)" "$(cat memory.max)"
awk '{ printf " %s=%s", $1, $2 }' cpu.stat memory.events
echo
