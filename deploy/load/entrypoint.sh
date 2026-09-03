#!/bin/sh
# The generator's source addresses -- capacity blocker د‑8.
#
# The edge meters per SOURCE ADDRESS, and it is right to:
#
#   limit_req_zone  $binary_remote_addr zone=api_req:10m rate=20r/s;   (burst 40)
#   limit_conn_zone $binary_remote_addr zone=ws_conn:10m;              (limit 100)
#
# §0's target is 300 rps and 1,500 WebSockets from HUNDREDS OF USERS, which in
# production arrive from hundreds of addresses. A k6 container is one address,
# so the first honest peak run offered 300.1 rps and the edge admitted 22.0 --
# 92.7% answered 429 by `limit_req`, exactly as configured. That number
# measured nginx's limiter, not this platform.
#
# Two ways out, and only one of them is honest:
#
#   * raise or exempt the limit at the edge -- that is TUNING `ح‑9`, it belongs
#     to wave 3, and `م‑8` forbids it before a baseline exists. A baseline taken
#     on an already-loosened edge can never answer "did loosening it help?".
#   * make the generator look like what it is simulating: many clients, many
#     addresses. Nothing about the system under test changes.
#
# This script is the second. It adds N secondary addresses to the container's
# own interface and hands k6 `--local-ips`, which spreads VUs across them
# round-robin. MEASURED on this stack, 10s at the offered rate, /api/v1 through
# the real edge:
#
#     1 address  · 150 rps offered ->  1,260 of 1,500 rejected (84.0%)
#    16 addresses· 150 rps offered ->      0 rejected
#    32 addresses· 300 rps offered ->      0 rejected  (3,001 of 3,001 admitted)
#
# ⚠️ THIS DOES NOT RAISE THE PLATFORM'S REAL CEILING, and no report may read it
# that way. A single NATed office still gets 20 r/s from this edge. That is a
# genuine design question -- per-IP metering versus the per-USER limiter that
# step `1.2` is supposed to build -- and it is recorded as such, not silently
# fixed here.
#
# Why root, and why NET_ADMIN: `ip addr add` needs CAP_NET_ADMIN, and no file
# capability on /sbin/ip grants it to an unprivileged uid. The container is a
# throwaway on a dev machine, built from a pinned upstream image, on the same
# bridge as the stack. Results are chowned back to the operator on the way out
# so the bind mount is not left full of root-owned files.
#
#   LOAD_SRC_IPS   how many addresses to claim (0 disables the whole mechanism
#                  and this becomes a plain `exec k6`)
#   LOAD_UID/GID   who owns the files this run writes
set -e

k6_bin=k6

if [ "${LOAD_SRC_IPS:-0}" -gt 0 ] 2>/dev/null; then
  dev="$(ip -4 -o route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p')"
  cidr="$(ip -4 -o addr show dev "$dev" scope global 2>/dev/null | sed -n '1s/.* inet \([0-9./]*\).*/\1/p')"
  addr="${cidr%%/*}"
  prefix="${cidr##*/}"

  if [ -z "$dev" ] || [ -z "$addr" ] || [ -z "$prefix" ]; then
    echo "entrypoint: could not read this container's address; refusing to guess." >&2
    exit 2
  fi

  # The block is taken from the TOP of the container's own subnet, so it
  # follows whatever subnet Docker picked on this machine instead of hardcoding
  # 172.18/16. Docker's IPAM hands out addresses from the bottom, so on the
  # default /16 the two ends are ~65,000 containers apart.
  ip2int() {
    IFS=. read -r a b c d <<EOF
$1
EOF
    echo $(((a << 24) + (b << 16) + (c << 8) + d))
  }
  int2ip() {
    echo "$((($1 >> 24) & 255)).$((($1 >> 16) & 255)).$((($1 >> 8) & 255)).$(($1 & 255))"
  }

  n="$LOAD_SRC_IPS"
  self="$(ip2int "$addr")"
  hostbits=$((32 - prefix))
  mask=$((4294967295 ^ ((1 << hostbits) - 1)))
  network=$((self & mask))
  broadcast=$((network | ((1 << hostbits) - 1)))
  first=$((broadcast - n))
  last=$((broadcast - 1))

  # A subnet too small to hold the block would put these addresses on top of
  # the ones Docker is still handing out -- two containers on one address is a
  # debugging session nobody asked for. Refuse rather than overlap.
  if [ "$first" -le "$((network + 1))" ] || [ "$first" -le "$self" ]; then
    echo "entrypoint: /$prefix around $addr is too small to carve $n addresses off its top." >&2
    exit 2
  fi

  i="$first"
  while [ "$i" -le "$last" ]; do
    ip addr add "$(int2ip "$i")/$prefix" dev "$dev" || {
      echo "entrypoint: could not add $(int2ip "$i") -- is NET_ADMIN granted?" >&2
      exit 2
    }
    i=$((i + 1))
  done

  range="$(int2ip "$first")-$(int2ip "$last")"
  echo "entrypoint: $n source addresses on $dev  ($range)" >&2

  # Injected rather than passed through the environment: `--local-ips` on the
  # command line is visible in the run's own log, and a reader should never
  # have to take on trust which addresses a reported number came from.
  if [ "${1:-}" = run ]; then
    shift
    set -- run --local-ips="$range" "$@"
  fi
fi

# `set +e` is not optional here: k6's normal SUCCESSFUL-but-failing-a-threshold
# exit is 99, and `set -e` would abandon the run's own results directory to
# root ownership on every real gate failure.
set +e
"$k6_bin" "$@"
status=$?
set -e

# k6 runs as root here (see above); without this the operator inherits a
# results directory they cannot delete.
if [ -n "${LOAD_UID:-}" ] && [ -d /load/results ]; then
  chown -R "$LOAD_UID:${LOAD_GID:-$LOAD_UID}" /load/results 2>/dev/null || true
fi

exit "$status"
