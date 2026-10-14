#!/usr/bin/env bash
# GPU server pre-flight (server-gpu-plan.md 2.1): is this host ready for
# docker-compose.gpu.yml? It CHECKS and never installs, starts a service or
# changes a setting; it names what is missing and the command that fixes it.
#
# Usage:
#   deploy/gpu-preflight.sh [--smoke]
#   deploy/gpu-preflight.sh --help
#
# --smoke adds one live check: a throwaway container that asks the GPU for its
# name (`docker run --rm --gpus all <SMOKE_IMAGE> nvidia-smi ...`). It needs the
# image, so a first run pulls it (92 MB compressed).
#
# Reads no environment variable and no environment file, and needs no root. Every
# external probe runs under `timeout`, so a hung driver or daemon cannot hang this.
#
# Checks, all run, one line each:
#   driver   nvidia-smi on PATH exits 0 and lists a GPU (every GPU is printed)
#   docker   `docker info` answers
#   toolkit  Docker lists an `nvidia` runtime            (skipped if driver/docker failed)
#   compose  `docker compose version` >= MIN_COMPOSE      (depends_on.required)
#   smoke    --smoke only: the live GPU container above
#
# --smoke decides over a missing `nvidia` runtime (the toolkit check): a CDI-only
# host or Docker Desktop resolves --gpus and Compose's GPU reservation without
# registering that runtime. It runs only when driver, docker and compose passed.
#
# Exit codes (docs/deploy-server.md carries the same table; a test compares them):
#    0  ready for docker-compose.gpu.yml
#    2  bad usage
#   10  no usable NVIDIA GPU (nvidia-smi missing or failing); Docker and Compose are fine,
#       so the stack can still run on CPU
#   11  Docker unusable: no CLI, the server does not answer, or permission denied on the socket
#   12  GPU present but the NVIDIA Container Toolkit is not configured for Docker
#       (no `nvidia` runtime, and no passing --smoke)
#   13  Docker Compose missing or older than MIN_COMPOSE
#   14  --smoke: the static checks passed and the GPU container failed (Docker's message is shown)
#
# With several failures the exit code is the highest-precedence one: 11, then 13,
# then 10, then 12. A platform failure (Docker, Compose) outranks a GPU one because it
# also blocks a CPU run, so 10 means exactly "no GPU, but a CPU run is possible".
# Exit 1 is an unexpected error (set -e).
set -euo pipefail

readonly MIN_COMPOSE="2.20.0"                                 # depends_on.required
# Index digest on 2026-10-14:
# sha256:0f6bfcbf267e65123bcc2287e2153dedfc0f24772fb5ce84afe16ac4b2fada95
readonly SMOKE_IMAGE="nvidia/cuda:12.4.1-base-ubuntu22.04"
readonly PROBE_TIMEOUT_S=5                                    # per external probe
readonly SMOKE_TIMEOUT_S=300                                  # the live check; a first run pulls the image
readonly SMOKE_MESSAGE_LINES=10                               # how much of Docker's message to relay
readonly FIX_ADVICE='sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker'

SMOKE=0
OUT=""
ERR=""
RC=0
ERR_FILE=""
DRIVER_OK=0
DOCKER_OK=0
COMPOSE_OK=0
TOOLKIT_STATE="skipped"       # ok | missing | skipped
SMOKE_STATE="not-run"         # not-run | ok | failed

usage() {
  cat <<'USAGE'
usage: gpu-preflight.sh [--smoke]
       gpu-preflight.sh --help

Checks the NVIDIA driver, Docker, the NVIDIA Container Toolkit and Docker Compose.
Never installs anything. --smoke also runs one throwaway GPU container.
Exit codes: 0 ready, 10 no GPU, 11 Docker, 12 toolkit, 13 Compose, 14 --smoke failed.
USAGE
}

cleanup() {
  if [[ -n $ERR_FILE ]]; then
    rm -f "$ERR_FILE"
  fi
}

row() {
  printf 'gpu-preflight: %-8s %-5s %s\n' "$1" "$2" "$3"
}

say() {
  printf 'gpu-preflight: %s\n' "$*"
}

# run <timeout seconds> <command...>: stdout -> OUT, stderr -> ERR, status -> RC.
# Never trips `set -e`: a failing probe is a result, not an error.
run() {
  local limit="$1"
  shift
  RC=0
  OUT=$(timeout "$limit" "$@" 2>"$ERR_FILE") || RC=$?
  ERR=$(<"$ERR_FILE")
}

first_line() {
  local text="$1"
  printf '%s' "${text%%$'\n'*}"
}

why_failed() {
  if [[ $RC -eq 124 ]]; then
    printf 'timed out after %ss' "$PROBE_TIMEOUT_S"
  else
    printf 'exit %s' "$RC"
  fi
}

check_driver() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    row driver FAIL "nvidia-smi is not on PATH: no NVIDIA driver on this host (docs/deploy-server.md)"
    return 0
  fi
  run "$PROBE_TIMEOUT_S" nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
  if [[ $RC -ne 0 || -z ${OUT//[[:space:]]/} ]]; then
    row driver FAIL "nvidia-smi failed ($(why_failed)): the NVIDIA driver is not working"
    return 0
  fi
  local gpu first=1
  while IFS= read -r gpu; do
    [[ -n $gpu ]] || continue
    if ((first)); then
      row driver OK "$gpu"
      first=0
    else
      row "" "" "$gpu"
    fi
  done <<<"$OUT"
  DRIVER_OK=1
}

RUNTIMES=""
check_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    row docker FAIL "the docker CLI is not on PATH (docs/deploy-server.md)"
    return 0
  fi
  run "$PROBE_TIMEOUT_S" docker info --format '{{.ServerVersion}}|{{json .Runtimes}}'
  if [[ $RC -ne 0 ]]; then
    if [[ ${ERR,,} == *"permission denied"* ]]; then
      row docker FAIL "permission denied on the Docker socket: this user cannot talk to the Docker daemon"
    else
      row docker FAIL "the Docker server does not answer ($(why_failed)) $(first_line "$ERR")"
    fi
    return 0
  fi
  row docker OK "server ${OUT%%|*}"
  RUNTIMES="${OUT#*|}"
  DOCKER_OK=1
}

check_toolkit() {
  if ((!DRIVER_OK || !DOCKER_OK)); then
    row toolkit SKIP "needs a working driver and Docker"
    return 0
  fi
  local nvidia_runtime='"nvidia"[[:space:]]*:'
  if [[ $RUNTIMES =~ $nvidia_runtime ]]; then
    row toolkit OK "Docker has the \`nvidia\` runtime"
    TOOLKIT_STATE="ok"
    return 0
  fi
  TOOLKIT_STATE="missing"
  if ((SMOKE)); then
    row toolkit WARN "Docker has no \`nvidia\` runtime; the live check (--smoke) below decides"
  else
    row toolkit FAIL "Docker has no \`nvidia\` runtime"
  fi
  row "" "" "fix: $FIX_ADVICE (docs/deploy-server.md)"
  if ((!SMOKE)); then
    row "" "" "on a CDI-only or Docker Desktop host, run again with --smoke"
  fi
}

check_compose() {
  if ! command -v docker >/dev/null 2>&1; then
    row compose SKIP "needs the docker CLI"
    return 0
  fi
  run "$PROBE_TIMEOUT_S" docker compose version --short
  local version_re='^v?([0-9]+)\.([0-9]+)\.([0-9]+)'
  local line
  line=$(first_line "$OUT")
  if [[ $RC -ne 0 || ! $line =~ $version_re ]]; then
    row compose FAIL "docker compose version gave no usable answer ($(why_failed)); need >= $MIN_COMPOSE"
    return 0
  fi
  local have_major=$((10#${BASH_REMATCH[1]}))
  local have_minor=$((10#${BASH_REMATCH[2]}))
  local have_patch=$((10#${BASH_REMATCH[3]}))
  local want_major want_minor want_patch
  IFS=. read -r want_major want_minor want_patch <<<"$MIN_COMPOSE"
  local shown="$have_major.$have_minor.$have_patch"
  if ((have_major > want_major)) ||
    ((have_major == want_major && have_minor > want_minor)) ||
    ((have_major == want_major && have_minor == want_minor && have_patch >= want_patch)); then
    row compose OK "$shown (>= $MIN_COMPOSE)"
    COMPOSE_OK=1
  else
    row compose FAIL "$shown is older than $MIN_COMPOSE (depends_on.required needs it)"
  fi
}

check_smoke() {
  if ((!SMOKE)); then
    return 0
  fi
  if ((!DRIVER_OK || !DOCKER_OK || !COMPOSE_OK)); then
    row smoke SKIP "needs a working driver, Docker and Compose"
    return 0
  fi
  run "$SMOKE_TIMEOUT_S" docker run --rm --gpus all "$SMOKE_IMAGE" \
    nvidia-smi --query-gpu=name --format=csv,noheader
  if [[ $RC -eq 0 && -n ${OUT//[[:space:]]/} ]]; then
    local gpu first=1
    while IFS= read -r gpu; do
      [[ -n $gpu ]] || continue
      if ((first)); then
        row smoke OK "$gpu (from inside $SMOKE_IMAGE)"
        first=0
      else
        row "" "" "$gpu"
      fi
    done <<<"$OUT"
    SMOKE_STATE="ok"
    return 0
  fi
  SMOKE_STATE="failed"
  if [[ $RC -eq 124 ]]; then
    row smoke FAIL "the GPU container did not finish within ${SMOKE_TIMEOUT_S}s"
  else
    row smoke FAIL "the GPU container failed (exit $RC); Docker says:"
  fi
  local shown=0 message
  while IFS= read -r message; do
    [[ -n $message ]] || continue
    if ((shown >= SMOKE_MESSAGE_LINES)); then
      break
    fi
    row "" "" "$message"
    shown=$((shown + 1))
  done <<<"$ERR"
}

verdict() {
  if ((!DOCKER_OK)); then
    say "FAIL 11 (Docker)"
    return 11
  fi
  if ((!COMPOSE_OK)); then
    say "FAIL 13 (Docker Compose)"
    return 13
  fi
  if ((!DRIVER_OK)); then
    say "FAIL 10 (no usable NVIDIA GPU: the NVIDIA driver; the stack can still run on CPU)"
    return 10
  fi
  if [[ $TOOLKIT_STATE == "missing" && $SMOKE_STATE != "ok" ]]; then
    say "FAIL 12 (NVIDIA Container Toolkit)"
    return 12
  fi
  if [[ $SMOKE_STATE == "failed" ]]; then
    say "FAIL 14 (--smoke: the GPU container failed)"
    return 14
  fi
  say "OK (ready for docker-compose.gpu.yml)"
  return 0
}

main() {
  while (($#)); do
    case "$1" in
      --smoke) SMOKE=1 ;;
      -h | --help)
        usage
        return 0
        ;;
      *)
        echo "gpu-preflight: ERROR: unknown argument '$1'" >&2
        usage >&2
        return 2
        ;;
    esac
    shift
  done

  ERR_FILE=$(mktemp)
  trap cleanup EXIT

  check_driver
  check_docker
  check_toolkit
  check_compose
  check_smoke
  verdict
}

main "$@"
