#!/usr/bin/env bash
# What this stack is allowed to consume, added up, and compared with the host
# it is about to run on -- capacity step 3.4 (docs/capacity-plan.md §5 wave 3).
#
#   deploy/resource-budget.sh          report the budget against THIS host
#   deploy/resource-budget.sh --host-cpus N --host-memory-gb G
#                                      report it against a host you do not have
#   deploy/resource-budget.sh --size NAME
#                                      apply deploy/sizes/NAME.env (its mode, its
#                                      limits) and judge it with a 10% memory margin
#   deploy/resource-budget.sh --block FILE
#                                      judge the marked "aizzak auto-size" block of FILE
#                                      (deploy/auto_size.py writes it): role server like
#                                      --size, role dev as a warning only
#   deploy/resource-budget.sh --without-monitoring [--size NAME]
#                                      judge a host that does not run profile `monitoring`
#                                      (counted by default, decision 7). No alerts without it
#   env: COMPOSE_FILE, GPU_COMPOSE_FILE, SIZES_DIR   exit: 0 fits, 1 over, 2 usage
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
# `monitoring` is the exception: counted by default (decision 7); `--without-monitoring`
# judges a host without it; `--block` follows the block's `# profiles:` line, as Compose does.
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
GPU_COMPOSE_FILE="${GPU_COMPOSE_FILE:-docker-compose.gpu.yml}"
SIZES_DIR="${SIZES_DIR:-deploy/sizes}"

host_cpus=""
host_memory_gb=""
with_profiles=0
without_monitoring=0
size=""
size_given=0
block=""
block_given=0

while [ $# -gt 0 ]; do
  case "$1" in
    --host-cpus) host_cpus="$2"; shift 2 ;;
    --host-memory-gb) host_memory_gb="$2"; shift 2 ;;
    --with-profiles) with_profiles=1; shift ;;
    --without-monitoring) without_monitoring=1; shift ;;
    -h | --help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --size)
      if [ $# -lt 2 ] || [[ "$2" == --* ]]; then
        echo "--size needs a value (a name from $SIZES_DIR)" >&2
        exit 2
      fi
      size="$2"; size_given=1; shift 2 ;;
    --block)
      if [ $# -lt 2 ] || [[ "$2" == --* ]]; then
        echo "--block needs a value (an env file holding the auto-size block)" >&2
        exit 2
      fi
      block="$2"; block_given=1; shift 2 ;;
    *) echo "usage: $0 [--host-cpus N] [--host-memory-gb G] [--with-profiles | --without-monitoring] [--size NAME | --block FILE]" >&2; exit 2 ;;
  esac
done

if [ "$without_monitoring" = 1 ] && [ "$block_given" = 1 ]; then
  echo "--without-monitoring and --block are mutually exclusive: the block's '# profiles:' line already says whether monitoring runs" >&2
  exit 2
fi
if [ "$without_monitoring" = 1 ] && [ "$with_profiles" = 1 ]; then
  echo "--without-monitoring and --with-profiles are mutually exclusive: one removes a profile, the other counts them all" >&2
  exit 2
fi

if [ "$size_given" = 1 ] && [ "$block_given" = 1 ]; then
  echo "--size and --block are mutually exclusive" >&2
  exit 2
fi

if [ "$size_given" = 1 ] && ! [[ "$size" =~ ^[a-z0-9-]+$ ]]; then
  echo "unknown size '$size'; available: $(cd "$SIZES_DIR" 2>/dev/null && ls ./*.env 2>/dev/null | sed 's|^\./||; s|\.env$||' | sort | tr '\n' ' ')" >&2
  exit 2
fi

if [ -z "$host_cpus" ]; then
  host_cpus="$(nproc)"
fi
if [ -z "$host_memory_gb" ]; then
  # MemTotal is in kB; the ceiling this compares against is physical RAM, not
  # what is free right now -- a limit is a maximum, and the question is whether
  # the maxima can coexist at all.
  host_memory_gb="$(awk '/^MemTotal:/ {printf "%.2f", $2 / 1048576}' /proc/meminfo)"
fi

exec python3 - "$COMPOSE_FILE" "$host_cpus" "$host_memory_gb" "$with_profiles" \
  "$size" "$SIZES_DIR" "$GPU_COMPOSE_FILE" "$block" "$without_monitoring" <<'PY'
import copy
import re
import sys
from pathlib import Path

import yaml

BLOCK_START = "# >>> aizzak auto-size >>>"
BLOCK_END = "# <<< aizzak auto-size <<<"
compose_file, host_cpus, host_memory_gb, with_profiles, size, sizes_dir, gpu_file, block = (
    sys.argv[1:9]
)
without_monitoring = sys.argv[9] == "1"
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


_INTERP = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}$]*)\}")


def usage_error(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


def resolve(raw: object, values: dict) -> str:
    """Whole-value `${NAME:-default}` only; empty == unset. Never reads the
    environment or `.env`. Mirrors tests/unit/support_compose.py::resolve."""
    text = str(raw)
    if "$" not in text:
        return text
    match = _INTERP.fullmatch(text)
    if match is None:
        usage_error(f"unsupported interpolation: {text!r}")
    return values.get(match.group(1)) or match.group(2)


def merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


values: dict = {}
MONITORING = "monitoring"  # decision 7 (server-gpu-plan.md §6): on by default
enabled: set = set() if (block or without_monitoring) else {MONITORING}
size_line = ""
compose = yaml.safe_load(open(compose_file, encoding="utf-8"))
if size:
    available = sorted(p.stem for p in Path(sizes_dir).glob("*.env"))
    if size not in available:
        usage_error(f"unknown size '{size}'; available: {' '.join(available)}")
    path = Path(sizes_dir) / f"{size}.env"
    host = source = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.fullmatch(r"# host: cpus=([0-9.]+) memory_gb=([0-9.]+) mode=(cpu|gpu)", line)
        if m and host is None:
            host = m.groups()
        elif line.startswith("# source: ") and source is None:
            source = line[len("# source: "):]
        elif line.startswith("#") or not line.strip():
            continue
        else:
            key, _, val = line.partition("=")
            values[key] = val
    if host is None:
        usage_error(f"{path}: no valid '# host: cpus=N memory_gb=G mode=cpu|gpu' line")
    size_cpus, size_gb, mode = host
    if mode == "gpu":
        merge(compose, yaml.safe_load(open(gpu_file, encoding="utf-8")) or {})
        enabled.add("local-llm")
    size_line = (
        f"size {size} ({path}): mode {mode}, written for {float(size_cpus):g} vCPU /"
        f" {float(size_gb):g} GB, source: {source}"
    )
block_role = ""
if block:
    block_path = Path(block)
    try:
        block_lines = block_path.read_bytes().decode("utf-8").splitlines()
    except OSError as exc:
        usage_error(f"{block}: cannot read ({exc.strerror})")
    except UnicodeDecodeError as exc:
        bad = exc.object[: exc.start].count(b"\n") + 1
        usage_error(f"{block}: line {bad} is not valid UTF-8")
    starts = [i for i, ln in enumerate(block_lines) if ln == BLOCK_START]
    ends = [i for i, ln in enumerate(block_lines) if ln == BLOCK_END]
    if not starts and not ends:
        usage_error(f"{block}: no '{BLOCK_START}' block; run deploy/auto_size.py first")
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        usage_error(f"{block}: the auto-size block is malformed (one start and one end marker)")
    host = source = None
    block_profiles: list[str] = []
    for line in block_lines[starts[0] + 1 : ends[0]]:
        m = re.fullmatch(r"# host: cpus=([0-9.]+) memory_gb=([0-9.]+) mode=(cpu|gpu)", line)
        r = re.fullmatch(r"# role: (server|dev)", line)
        if m and host is None:
            host = m.groups()
        elif r and not block_role:
            block_role = r.group(1)
        elif line.startswith("# profiles:"):
            block_profiles = [p.strip() for p in line[len("# profiles:"):].split(",") if p.strip()]
        elif line.startswith("# source: ") and source is None:
            source = line[len("# source: "):]
        elif line.startswith("#") or not line.strip():
            continue
        else:
            key, _, val = line.partition("=")
            values[key] = val
    if host is None:
        usage_error(f"{block}: no valid '# host: cpus=N memory_gb=G mode=cpu|gpu' line")
    if not block_role:
        usage_error(f"{block}: no '# role: server|dev' line")
    block_cpus, block_gb, mode = host
    if mode == "gpu":
        merge(compose, yaml.safe_load(open(gpu_file, encoding="utf-8")) or {})
        enabled.add("local-llm")
    enabled.update(block_profiles)
    size_line = (
        f"block {block}: role {block_role}, mode {mode},"
        f" profiles {','.join(block_profiles) or 'none'},"
        f" written for {float(block_cpus):g} vCPU / {float(block_gb):g} GB,"
        f" source: {source}"
    )
services = compose["services"]

standing, oneshot, profiled, missing = [], [], [], []
for name, service in sorted(services.items()):
    limits = ((service.get("deploy") or {}).get("resources") or {}).get("limits") or {}
    if "cpus" not in limits or "memory" not in limits:
        missing.append(name)
        continue
    try:
        replicas = int(resolve((service.get("deploy") or {}).get("replicas", 1), values))
        row = (
            name,
            replicas,
            float(resolve(limits["cpus"], values)),
            to_bytes(resolve(limits["memory"], values)),
        )
    except ValueError as exc:
        usage_error(f"{name}: a limit or replica count is not a number ({exc})")
    if set(service.get("profiles") or ()) - enabled:
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


if size_line:
    print(size_line)
if block:
    if MONITORING in enabled:
        print("monitoring: counted (in the block's profiles)")
    else:
        print(
            "monitoring: NOT counted (not in the block's profiles). Without it there are no"
            " alerts at all: check /health from outside the host."
        )
elif without_monitoring:
    print(
        "monitoring: NOT counted (--without-monitoring). Without it there are no alerts"
        " at all: check /health from outside the host."
    )
else:
    print("monitoring: counted (on by default, decision 7)")
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

allowed = host_memory_gb * 0.9 if (size or block_role == "server") else host_memory_gb
if block_role == "dev":
    allowed = host_memory_gb * 0.9
    if mem_total > allowed:
        print(
            f"  MEMORY  OVER     role dev: {mem_total:.2f} GB of limits on a"
            f" {host_memory_gb:.2f} GB host ({mem_total / host_memory_gb:.2f}x;"
            f" 90% = {allowed:.2f} GB). Warning only (owner decision Q-1)."
        )
    else:
        print(
            f"  MEMORY  OK       {mem_total:.2f} of {host_memory_gb:.2f} GB"
            f" ({mem_total / host_memory_gb:.0%})"
        )
elif block_role == "server" and mem_total > allowed:
    print(
        f"  MEMORY  OVER     block (role server): {mem_total:.2f} GB of limits,"
        f" {mem_total - allowed:.2f} GB over the {allowed:.2f} GB allowed on a"
        f" {host_memory_gb:.2f} GB host (10% margin). Re-run deploy/auto_size.py."
    )
    status = 1
elif size and mem_total > allowed:
    print(
        f"  MEMORY  OVER     size {size}: {mem_total:.2f} GB of limits,"
        f" {mem_total - allowed:.2f} GB over the {allowed:.2f} GB allowed on a"
        f" {host_memory_gb:.2f} GB host (10% margin). Lower a limit in {path}."
    )
    status = 1
elif mem_total <= allowed:
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

conf_value = values.get("POSTGRES_CONFIG_FILE", "") if block else ""
if conf_value.startswith("/etc/postgresql/sizes/"):
    conf_name = conf_value.rsplit("/", 1)[1]
    if not (block_path.absolute().parent / "deploy/postgres/sizes" / conf_name).is_file():
        print(
            f"\n  NO CONF   {conf_name} is missing beside {block}."
            "\n            Postgres would not start; re-run deploy/auto_size.py."
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
