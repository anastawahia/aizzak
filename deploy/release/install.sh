#!/usr/bin/env bash
# Install AIZZAK on a server from an unpacked release bundle (server-gpu-plan.md 5.2).
#
# Usage, from the directory the bundle was unpacked into (as root: it tunes the kernel and writes
# files that hold secrets):
#   sudo ./install.sh --domain NAME --firebase-project ID [--size auto|small|medium|large]
#                     [--cpu] [--no-monitoring] [--gpu-smoke]
#   ./install.sh --help
#
#   --domain NAME            the public DNS name, ASCII (an IDN as xn--...); no scheme, path or port
#   --firebase-project ID    the Firebase project id; the app does not start without it
#   --size S                 auto (default: computed from this host) or a file name in deploy/sizes/
#   --cpu                    run on the CPU even when a GPU is usable
#   --no-monitoring          do not run the monitoring profile (no alerts)
#   --gpu-smoke              also run gpu-preflight.sh --smoke (a CDI host, docs/deploy-server.md)
#
# No option takes a key, a password or a token, and nothing is asked interactively. The registry
# login is the operator's own: `sudo docker login docker.io -u <user>` with a READ-ONLY access
# token made for this server alone.
#
# The eight steps, each safe to repeat: (1) checks, nothing is written; (2) kernel settings;
# (3) .env; (4) the size and its budget; (5) pull what is missing and verify it against VERSION;
# (6) first boot in a fixed order; (7) health and smoke checks; (8) the report.
# Running it again on a healthy install generates no secret, mints no key and creates no
# container. Moving to another release is upgrade.sh (server-gpu-plan.md 5.3), not this script.
#
# Exit codes (docs/deploy-server.md §3 carries the same table; a test compares them):
#    0  READY: /health and /health/ready answer 200 and stack_smoke.py passes
#    2  bad usage: an unknown or repeated option, --domain or --firebase-project missing or
#       malformed, or an unknown --size
#    3  not an unpacked bundle (no VERSION beside docker-compose.yml, a repository checkout, or a
#       damaged VERSION), or, as root, the bundle, deploy, deploy/postgres/sizes or .env is a
#       symbolic link or not owned by root, or another install.sh is running in this directory
#    4  not root: run sudo ./install.sh ...
#   10  gpu-preflight.sh stopped the install (its own exit code is printed)
#   11  a tool is missing on this host: bash 4, Python 3.10, PyYAML, curl, timeout, flock or df
#   12  too little free space where Docker keeps images (first install only)
#   13  the registry refused the release images: no docker login, a key without access
#   14  the registry cannot be reached: timeout, DNS, TLS or a 5xx
#   20  the kernel settings could not be applied (host-tuning.sh --apply --persist)
#   30  .env refused: a key that contradicts the options, a password still change-me, or damage
#   40  the size is refused: the server is smaller than the smallest size, or over budget
#   41  the size tool failed (the generator, or resource-budget.sh, reported an error)
#   50  pulling an image failed or timed out
#   51  an image does not match VERSION (the Compose list or the pulled digest), or has a dev or
#       latest tag
#   60  a boot step failed or timed out; the message names the step and the service
#   61  VAULT_SECRET_ID: minting failed, the output is not a UUID, or the value in .env is damaged
#   70  /health or /health/ready did not answer 200
#   71  stack_smoke.py failed
# Exit 1 is an unexpected error (set -e).
set -euo pipefail

if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "install: ERROR: bash 4 or newer is required" >&2
  exit 11
fi

# ── constants ───────────────────────────────────────────────────────────────
readonly IMAGE_REPO="anastawahia/aizzak"
# Estimates (NFR-8): the images measured 27 GB (GPU set) and 19.5 GB (CPU set) on the development host.
readonly MIN_FREE_GB_GPU=40 MIN_FREE_GB_CPU=30
readonly SECRET_ID_TTL_H=720            # equals secret_id_ttl in deploy/vault/bootstrap.sh (a test compares)
# Timeouts are estimates, not measurements; a test may lower them after sourcing this file.
PROBE_TIMEOUT_S=30
PULL_TIMEOUT_S=1800
PULL_DEADLINE_S=3600
BOOT_DEADLINE_S=1800
HEALTH_DEADLINE_S=120
POLL_S=2
# Runs INSIDE the vault container: the root token is read there from init.json and never leaves it.
readonly MINT_SCRIPT='export VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN="$(sed -n "s/.*\"root_token\": *\"\([^\"]*\)\".*/\1/p" /vault/init/init.json)"; vault write -f -field=secret_id auth/approle/role/app/secret-id'
readonly PREFIXES=(app embedding embedding-cuda)
# A host path is never a place a variable from the caller's shell may reach Compose through.
readonly KEEP_ENV='^(PATH|HOME|USER|LOGNAME|SHELL|TERM|LANG|LC_[A-Z]+|PWD|OLDPWD|SHLVL|TMPDIR|DOCKER_[A-Z_]+|SYSCTL_ROOT|SYSCTL_D_DIR|SUDO_[A-Z]+|XDG_[A-Z_]+|BASH[A-Z_]*)$'

# ── state ───────────────────────────────────────────────────────────────────
ROOT=""
RELEASE="" COMMIT="" REF_APP="" REF_EMBEDDING="" REF_CUDA=""
DOMAIN="" FIREBASE_PROJECT="" SIZE="auto"
FORCE_CPU=0 MONITORING=1 GPU_SMOKE=0
MODE="cpu" PREFLIGHT_RC=""
FIRST_INSTALL=1
HOST_LINE="" SIZE_DESC=""
BOOT_START=0
declare -A STATE_HASH=()
STATE_INSPECT=""
STATE_SERVICES=""
FAILED_CHECK=""
PULL_LIST=""
PORT=""
BOOT_HINT=""

# ── output ──────────────────────────────────────────────────────────────────
say() { printf '%s\n' "$*"; }

# Tool output with control characters removed, so a hostile name cannot repaint the terminal.
relay() { LC_ALL=C tr -d '\000-\010\013-\037\177'; }

# Vault's stderr may carry a token or an id: shown shortened, with both shapes replaced.
redact() {
  relay | sed -E 's/hv[sbr]\.[A-Za-z0-9._-]+/<redacted>/g;
    s/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/<redacted>/g' | head -n 5
}

die() {
  local code="$1"
  shift
  local line
  for line in "$@"; do
    printf 'install: ERROR: %s\n' "$line" | relay >&2
  done
  exit "$code"
}

usage() {
  cat <<'USAGE'
usage: sudo ./install.sh --domain NAME --firebase-project ID [--size auto|small|medium|large]
                         [--cpu] [--no-monitoring] [--gpu-smoke]
       ./install.sh --help

  --domain NAME           public DNS name, ASCII (an IDN as xn--...); no scheme, path or port
  --firebase-project ID   the Firebase project id (for example aizzak-agent)
  --size S                auto (default) or a name from deploy/sizes/
  --cpu                   run on the CPU even when a GPU is usable
  --no-monitoring         do not run the monitoring profile (no alerts)
  --gpu-smoke             also run gpu-preflight.sh --smoke

Run it from the unpacked release bundle, as root: sudo ./install.sh ...
Before the first run: sudo docker login docker.io -u <user>  (a read-only token for this server).
USAGE
}

# ── prepare: everything that needs no root and writes nothing ───────────────
locate_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  if [[ ! -f $here/docker-compose.yml && -f $here/../../docker-compose.yml ]]; then
    here="$(cd "$here/../.." && pwd -P)"
  fi
  if [[ ! -f $here/docker-compose.yml || ! -f $here/VERSION || -e $here/.git || -d $here/src ]]; then
    die 3 "this is not an unpacked release bundle ($here): run it from the unpacked bundle," \
      "aizzak-<version>/install.sh, not from a checkout of the repository"
  fi
  ROOT="$here"
  cd "$ROOT"
}

read_version() {
  local -a lines=()
  local damaged="$ROOT/VERSION is damaged: unpack the bundle again"
  mapfile -t lines <"$ROOT/VERSION" || die 3 "cannot read $ROOT/VERSION"
  ((${#lines[@]} == 5)) || die 3 "$damaged (it must have 5 lines)"
  RELEASE="${lines[0]}"
  [[ $RELEASE =~ ^20[0-9]{2}\.(1[0-2]|[1-9])\.[1-9][0-9]*$ ]] || die 3 "$damaged (line 1)"
  [[ ${lines[1]} =~ ^commit\ ([0-9a-f]{40})$ ]] || die 3 "$damaged (line 2)"
  COMMIT="${BASH_REMATCH[1]}"
  local i expect
  for i in 0 1 2; do
    expect="$IMAGE_REPO:${PREFIXES[$i]}-$RELEASE@sha256:"
    [[ ${lines[$((i + 2))]:0:${#expect}} == "$expect" && ${lines[$((i + 2))]:${#expect}} =~ ^[0-9a-f]{64}$ ]] \
      || die 3 "$damaged (line $((i + 3)))"
  done
  REF_APP="${lines[2]}"
  REF_EMBEDDING="${lines[3]}"
  REF_CUDA="${lines[4]}"
}

# 0 when S is a DNS name this script accepts (lower-case it first); no output.
valid_domain() {
  local s="$1" label
  local -a labels=()
  [[ -n $s && ${#s} -le 253 && $s =~ ^[a-z0-9.-]+$ ]] || return 1
  [[ $s != .* && $s != *. && $s != *..* ]] || return 1
  IFS=. read -r -a labels <<<"$s"
  ((${#labels[@]} >= 2)) || return 1
  for label in "${labels[@]}"; do
    [[ $label =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || return 1
  done
  label="${labels[${#labels[@]} - 1]}"
  [[ $label =~ ^[a-z]+$ || $label =~ ^xn--[a-z0-9-]+$ ]]
}

valid_firebase() { [[ $1 =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; }

valid_size() {
  [[ $1 == auto ]] && return 0
  [[ $1 =~ ^[a-z0-9-]+$ && -f $ROOT/deploy/sizes/$1.env ]]
}

parse_args() {
  local seen=" " opt
  DOMAIN="" FIREBASE_PROJECT="" SIZE="auto" FORCE_CPU=0 MONITORING=1 GPU_SMOKE=0
  while (($# > 0)); do
    opt="$1"
    case "$opt" in
      --domain | --firebase-project | --size | --cpu | --no-monitoring | --gpu-smoke) ;;
      -*) die 2 "unknown option: $opt (see ./install.sh --help)" ;;
      *) die 2 "unexpected argument: $opt (see ./install.sh --help)" ;;
    esac
    [[ $seen != *" $opt "* ]] || die 2 "$opt is given twice"
    seen+="$opt "
    case "$opt" in
      --cpu) FORCE_CPU=1 ;;
      --no-monitoring) MONITORING=0 ;;
      --gpu-smoke) GPU_SMOKE=1 ;;
      *)
        (($# >= 2)) || die 2 "$opt needs a value"
        case "$opt" in
          --domain) DOMAIN="$(printf '%s' "$2" | LC_ALL=C tr '[:upper:]' '[:lower:]')" ;;
          --firebase-project) FIREBASE_PROJECT="$2" ;;
          --size) SIZE="$2" ;;
        esac
        shift
        ;;
    esac
    shift
  done
  [[ -n $DOMAIN ]] || die 2 "--domain NAME is required (for example example.com)"
  valid_domain "$DOMAIN" \
    || die 2 "--domain must be a DNS name like example.com: ASCII letters, digits, hyphens and dots," \
      "no scheme, path or port; write an international name in its xn-- form (punycode)"
  [[ -n $FIREBASE_PROJECT ]] \
    || die 2 "--firebase-project ID is required: the app does not start without it (for example aizzak-agent)"
  valid_firebase "$FIREBASE_PROJECT" \
    || die 2 "--firebase-project must be a Firebase project id: lower-case letters, digits and hyphens," \
      "6 to 30 characters, starting with a letter; the app does not start without it"
  valid_size "$SIZE" || die 2 "--size '$SIZE' is not auto or a file in deploy/sizes/ ($(size_names))"
}

size_names() {
  local f names=""
  for f in "$ROOT"/deploy/sizes/*.env; do
    [[ -f $f ]] || continue
    f="${f##*/}"
    names+="${f%.env} "
  done
  printf '%s' "${names% }"
}

require_host_tools() {
  local tool rc
  command -v python3 >/dev/null 2>&1 \
    || die 11 "python3 is not installed: install Python 3.10 or newer (sudo apt-get install python3)"
  # one interpreter start for both questions: exit 3 = older than 3.10, 4 = no PyYAML
  rc=0
  python3 -I -c '
import sys
if sys.version_info < (3, 10):
    sys.exit(3)
try:
    import yaml
except ImportError:
    sys.exit(4)
' 2>/dev/null || rc=$?
  case "$rc" in
    0) ;;
    3) die 11 "python3 is older than 3.10: install Python 3.10 or newer" ;;
    4) die 11 "PyYAML is missing for python3: sudo apt-get install python3-yaml" ;;
    *) die 11 "python3 could not run its self-check (exit $rc): reinstall python3 and python3-yaml" ;;
  esac
  for tool in curl timeout flock df; do
    command -v "$tool" >/dev/null 2>&1 || die 11 "$tool is not installed (curl, coreutils and util-linux are needed)"
  done
}

# Compose gives the caller's shell priority over .env, so a stale export would silently win.
scrub_environment() {
  local name removed=""
  local -A known=()
  while read -r name; do
    [[ -n $name ]] && known[$name]=1
  done < <(sed -nE 's/^[#[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' "$ROOT/.env.example")
  # only what the caller exported can reach Compose; the script's own variables are not exported
  while read -r name; do
    [[ $name =~ $KEEP_ENV ]] && continue
    if [[ -n ${known[$name]:-} || $name == COMPOSE_* ]]; then
      unset "$name"
      removed+="$name "
    fi
  done < <(compgen -e | sort -u)
  if [[ -n $removed ]]; then
    say "install: warning: removed from the environment because Compose gives the shell priority over .env: ${removed% }"
  fi
}

prepare() {
  local arg
  for arg in "$@"; do
    if [[ $arg == -h || $arg == --help ]]; then
      usage
      exit 0
    fi
  done
  locate_root
  read_version
  parse_args "$@"
  require_host_tools
  scrub_environment
}

# ── the root-only guards ────────────────────────────────────────────────────
require_root() {
  [[ $EUID -eq 0 ]] || die 4 "this must run as root: sudo ./install.sh ... (it tunes the kernel and writes secrets)"
}

# Each path: not a symbolic link, and owned by UID. Root writes into these, so another user must not own them.
require_trusted() {
  local uid="$1" path
  shift
  for path in "$@"; do
    if [[ -L $path ]]; then
      die 3 "$path is a symbolic link; refusing to write through it as root"
    fi
    [[ -e $path ]] || continue
    [[ "$(stat -c %u "$path")" == "$uid" ]] \
      || die 3 "$path is not owned by uid $uid; unpack the bundle as root (sudo tar -xzf ...)"
  done
}

# A lock on the directory itself: no file is created in the bundle.
lock_bundle() {
  exec 9<"$ROOT"
  flock -n 9 || die 3 "another install.sh is running in $ROOT"
}

# ── calling the outside world ───────────────────────────────────────────────
# `timeout` moves its child into a new process group, a background group for the controlling
# terminal. A docker CLI that reads a terminal stdin there gets SIGTTIN and stops, silently, until
# the timeout kills it (QA-7: `compose exec -T` kept stdin open). So a terminal is never passed on;
# a pipe or a file is (set_metrics_exporter_password sends the password through one).
dk() {
  local secs="$1"
  shift
  if [[ -t 0 ]]; then
    timeout -k 10 "$secs" docker "$@" </dev/null
  else
    timeout -k 10 "$secs" docker "$@"
  fi
}

compose() {
  local secs="$1"
  shift
  dk "$secs" compose --project-directory "$ROOT" "$@"
}

# ── .env: one embedded program, standard library only ───────────────────────
# It reads .env with the rules of deploy/auto_size.py (_ASSIGN, _unquote, _lines) and prints no value
# it read, except `get`. Its messages are key names and line numbers.
env_tool() {
  python3 -I - "$@" <<'PY'
import datetime
import os
import re
import secrets
import stat
import sys
import uuid
from pathlib import Path

INSTALL_START = "# >>> aizzak install >>>"
INSTALL_END = "# <<< aizzak install <<<"
SIZE_START = "# >>> aizzak size >>>"
SIZE_END = "# <<< aizzak size <<<"
AUTO_START = "# >>> aizzak auto-size >>>"
AUTO_END = "# <<< aizzak auto-size <<<"
BLOCKS = {
    "install": (INSTALL_START, INSTALL_END),
    "size": (SIZE_START, SIZE_END),
    "auto": (AUTO_START, AUTO_END),
}
BLOCK_KEYS = (
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "APP_IMAGE_REPO",
    "APP_IMAGE_TAG",
    "EMBEDDING_IMAGE_REPO",
    "EMBEDDING_IMAGE_TAG",
    "EMBEDDING_CUDA_IMAGE_REPO",
    "EMBEDDING_CUDA_IMAGE_TAG",
    "VAULT_SECRET_ID",
)
DOMAIN_KEYS = ("MINIO_PUBLIC_ENDPOINT", "MINIO_PUBLIC_SECURE", "OAUTH_REDIRECT_BASE_URL")
SECRET_WORDS = re.compile(r"PASSWORD|SECRET|TOKEN|_KEY|USER")
# copied from deploy/auto_size.py; keep them in step
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:](.*)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class Refused(Exception):
    """The .env or the request is refused (exit 3). Key names and line numbers only."""


def lines_of(text):
    return re.findall(r"[^\n]*\n|[^\n]+", text)


def bare(line):
    return line.rstrip("\r\n")


def unquote(raw):
    value = raw.lstrip()
    if value[:1] in ("'", '"'):
        end = value.find(value[0], 1)
        if end != -1:
            return value[1:end]
    cut = value.find(" #")
    return (value if cut == -1 else value[:cut]).strip()


def find_block(lines, name):
    start, end = BLOCKS[name]
    starts = [i for i, ln in enumerate(lines) if bare(ln) == start]
    ends = [i for i, ln in enumerate(lines) if bare(ln) == end]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise Refused(f"the {name} block is malformed: {len(starts)} start and {len(ends)} end marker lines")
    return starts[0], ends[0]


def spans(lines):
    return {name: find_block(lines, name) for name in BLOCKS}


def block_of(index, found):
    for name, span in found.items():
        if span is not None and span[0] <= index <= span[1]:
            return name
    return None


def assignments(lines, found):
    """(index, key, value, block name or None) for every active assignment."""
    out = []
    for i, line in enumerate(lines):
        m = ASSIGN.match(bare(line))
        if m:
            out.append((i, m.group(1), unquote(m.group(2)), block_of(i, found)))
    return out


def last_value(rows, key):
    found = [r for r in rows if r[1] == key]
    return found[-1][2] if found else None


def lineno(index):
    return index + 1


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        n = exc.object[: exc.start].count(b"\n") + 1
        raise Refused(f"{Path(path).name} line {n} is not valid UTF-8") from None


def atomic_write(path, text):
    """Temp file next to the target, fsync, rename. A new file takes the mode umask gives it
    (0666 & ~umask); an existing one keeps its mode and owner, set on the open file BEFORE the
    first byte is written, so the secrets are never in a file more open than the one they replace.
    chmod is never run on the target. Temp files a killed run left behind are removed first."""
    target = Path(os.path.realpath(path))
    for stale in target.parent.glob(".install-tmp-*"):
        stale.unlink(missing_ok=True)
    tmp = target.with_name(f".install-tmp-{os.getpid()}")
    existing = os.stat(target) if target.exists() else None
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
    try:
        if existing is not None:
            os.fchmod(fd, stat.S_IMODE(existing.st_mode))
            try:
                os.fchown(fd, existing.st_uid, existing.st_gid)
            except PermissionError:
                if os.geteuid() == 0:
                    raise
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        dfd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def options(argv):
    out = {}
    it = iter(argv)
    for flag in it:
        value = next(it, None)
        if not flag.startswith("--") or value is None:
            raise SystemExit(f"env_tool: bad option {flag}")
        out[flag[2:]] = value
    return out


def template_view(path):
    text = read_text(path)
    lines = lines_of(text)
    rows = assignments(lines, {})
    values = {}
    for _, key, value, _ in rows:
        values[key] = value
    secret_keys = []
    for _, key, value, _ in rows:
        if value.startswith("change-me") and key not in secret_keys:
            secret_keys.append(key)
    return text, values, secret_keys


def build_install_block(o):
    gpu = o["mode"] == "gpu"
    compose_file = "docker-compose.yml:docker-compose.gpu.yml" if gpu else "docker-compose.yml"
    profiles = "local-llm,monitoring" if o["monitoring"] == "on" else "local-llm"
    return {
        "COMPOSE_FILE": compose_file,
        "COMPOSE_PROFILES": profiles,
        "APP_IMAGE_REPO": "anastawahia/aizzak",
        "APP_IMAGE_TAG": o["image-app"].split(":", 1)[1],
        "EMBEDDING_IMAGE_REPO": "anastawahia/aizzak",
        "EMBEDDING_IMAGE_TAG": o["image-embedding"].split(":", 1)[1],
        "EMBEDDING_CUDA_IMAGE_REPO": "anastawahia/aizzak",
        "EMBEDDING_CUDA_IMAGE_TAG": o["image-cuda"].split(":", 1)[1],
    }


def render_install_block(o, want):
    head = f"# release {o['release']} (commit {o['commit'][:12]}) -- written by install.sh; re-install to change it, do not edit"
    body = [f"{k}={v}" for k, v in want.items()]
    return "\n".join([INSTALL_START, head, *body, INSTALL_END]) + "\n"


def check_size_rules(found, lines, size):
    auto, named = found["auto"], found["size"]
    if auto and named:
        raise Refused("size: .env has both an auto-size block and a size block; remove one by hand")
    if auto and size != "auto":
        raise Refused("size: this server was sized with auto and --size asks for a named size; "
                      "changing the size is not what install.sh does")
    if named:
        have = None
        for ln in lines[named[0] : named[1] + 1]:
            m = re.match(r"^# size: ([a-z0-9-]+) ", bare(ln))
            if m:
                have = m.group(1)
        if have != size:
            raise Refused("size: this server was sized with another size than --size asks for; "
                          "changing the size is not what install.sh does")


def cmd_compose(argv):
    env_path, tpl_path, *rest = argv
    o = options(rest)
    for need in ("release", "commit", "mode", "monitoring", "size", "domain", "firebase",
                 "image-app", "image-embedding", "image-cuda"):
        if need not in o:
            raise SystemExit(f"env_tool: --{need} is required")
    tpl_text, tpl_values, secret_keys = template_view(tpl_path)
    want_block = build_install_block(o)
    want_plain = {
        "MINIO_PUBLIC_ENDPOINT": o["domain"],
        "MINIO_PUBLIC_SECURE": "true",
        "OAUTH_REDIRECT_BASE_URL": "https://" + o["domain"],
        "FIREBASE_PROJECT_ID": o["firebase"],
    }
    role_template = tpl_values.get("VAULT_ROLE_ID", "")
    path = Path(env_path)
    if not path.exists():
        updates = {}
        taken = set()
        for key in secret_keys:
            value = secrets.token_hex(24)
            while value in taken:
                value = secrets.token_hex(24)
            taken.add(value)
            updates[key] = value
        updates.update(want_plain)
        updates["VAULT_ROLE_ID"] = str(uuid.uuid4())
        body = set_values(lines_of(tpl_text), updates)
        text = "".join(body)
        if text and not text.endswith("\n"):
            text += "\n"
        text += render_install_block(o, want_block)
        atomic_write(path, text)
        print("created")
        return 0

    text = read_text(path)
    lines = lines_of(text)
    found = spans(lines)
    rows = assignments(lines, found)

    for i, key, value, where in rows:
        if key == "VAULT_TOKEN" and value != "":
            raise Refused(f"VAULT_TOKEN (line {lineno(i)}) must be empty: with a token set the app skips AppRole")
        if key in BLOCK_KEYS and where != "install":
            raise Refused(f"{key} (line {lineno(i)}) is defined outside the install block; "
                          "Compose keeps the last definition, so it would override the block")
    check_size_rules(found, lines, o["size"])

    if found["install"] is None:
        return adopt(path, lines, rows, found, o, tpl_values, secret_keys, want_plain, want_block,
                     role_template)
    return verify_repeat(lines, rows, found, o, want_plain, want_block, secret_keys, role_template)


def set_values(lines, updates, only_existing=False):
    """Lines with every active assignment of a key in `updates` rewritten in place; keys with
    none are appended. Line endings stay as they were."""
    done = set()
    out = []
    for line in lines:
        m = ASSIGN.match(bare(line))
        if m and m.group(1) in updates:
            eol = line[len(bare(line)):] or "\n"
            out.append(f"{m.group(1)}={updates[m.group(1)]}{eol}")
            done.add(m.group(1))
        else:
            out.append(line)
    missing = [k for k in updates if k not in done]
    if missing and not only_existing:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.extend(f"{k}={updates[k]}\n" for k in missing)
    return out


def adopt(path, lines, rows, found, o, tpl_values, secret_keys, want_plain, want_block, role_template):
    updates = {}
    taken = {r[2] for r in rows if r[1] in secret_keys and r[2] and not r[2].startswith("change-me")}
    for key in secret_keys:
        rows_k = [r for r in rows if r[1] == key and r[3] is None]
        have = rows_k[-1][2] if rows_k else None
        if have is None or have == "" or have.startswith("change-me"):
            value = secrets.token_hex(24)
            while value in taken:
                value = secrets.token_hex(24)
            taken.add(value)
            updates[key] = value
        elif key == "METRICS_EXPORTER_PASSWORD" and len(have) < 32:
            raise Refused(f"{key} (line {lineno(rows_k[-1][0])}) is shorter than 32 characters; "
                          "the role script refuses it")
    for key, wanted in want_plain.items():
        rows_k = [r for r in rows if r[1] == key and r[3] is None]
        have = rows_k[-1][2] if rows_k else None
        if have is None or have == tpl_values.get(key):
            updates[key] = wanted
        elif have != wanted:
            raise Refused(f"{key} (line {lineno(rows_k[-1][0])}) differs from what the options ask for; "
                          "install.sh does not overwrite a value an operator set")
    rows_role = [r for r in rows if r[1] == "VAULT_ROLE_ID" and r[3] is None]
    have_role = rows_role[-1][2] if rows_role else None
    if have_role is None or have_role in ("", role_template):
        updates["VAULT_ROLE_ID"] = str(uuid.uuid4())
    text = "".join(set_values(lines, updates))
    if text and not text.endswith("\n"):
        text += "\n"
    relines = lines_of(text)
    refound = spans(relines)
    later = [refound[n][0] for n in ("auto", "size") if refound[n] is not None]
    if later:
        relines[min(later) : min(later)] = [render_install_block(o, want_block)]
        text = "".join(relines)
    else:
        text += render_install_block(o, want_block)
    atomic_write(path, text)
    print("adopted")
    return 0


def verify_repeat(lines, rows, found, o, want_plain, want_block, secret_keys, role_template):
    start, end = found["install"]
    have = {}
    for i, key, value, where in rows:
        if where != "install":
            continue
        if key == "VAULT_SECRET_ID":
            continue
        if key not in want_block:
            raise Refused(f"{key} (line {lineno(i)}) is not expected inside the install block")
        have[key] = (i, value)
    for ln in lines[start : end + 1]:
        m = re.match(r"^# release (\S+) ", bare(ln))
        if m and m.group(1) != o["release"]:
            raise Refused("release: this server was installed from another release; "
                          "upgrading is upgrade.sh (5.3), not install.sh")
    for key, wanted in want_block.items():
        if key not in have:
            raise Refused(f"{key} is missing from the install block; the .env is damaged")
        if have[key][1] != wanted:
            where = lineno(have[key][0])
            hint = ""
            if key == "COMPOSE_FILE":
                hint = " (this server was installed in the other mode; --cpu changes it)"
            elif key == "COMPOSE_PROFILES":
                hint = " (monitoring was chosen differently at the first install)"
            raise Refused(f"{key} (line {where}) differs from what this run asks for{hint}")
    for key, wanted in want_plain.items():
        value = last_value([r for r in rows if r[3] is None], key)
        if value != wanted:
            raise Refused(f"{key} differs from what the options ask for (a different "
                          "domain or Firebase project than the first install)")
    for key in secret_keys:
        value = last_value([r for r in rows if r[3] is None], key)
        if value is None or value == "" or value.startswith("change-me"):
            raise Refused(f"{key} is empty or still change-me on an installed server; "
                          "Postgres and Vault would disagree")
    role = last_value([r for r in rows if r[3] is None], "VAULT_ROLE_ID")
    if role is None or role in ("", role_template):
        raise Refused("VAULT_ROLE_ID is empty or the development default on an installed server")
    print("unchanged")
    return 0


def cmd_size_block(argv):
    env_path, size_file, name = argv
    path = Path(env_path)
    text = read_text(path)
    lines = lines_of(text)
    found = spans(lines)
    if found["auto"] is not None:
        raise Refused("size: .env has an auto-size block; a named size cannot be added next to it")
    template = path.parent / ".env.example"
    tpl_values = {}
    if template.is_file():
        tpl_values = {k: v for _, k, v, _ in assignments(lines_of(read_text(template)), {})}
    block = [SIZE_START, f"# size: {name} (deploy/sizes/{name}.env)"]
    keys = []
    for ln in lines_of(read_text(size_file)):
        s = bare(ln)
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        m = ASSIGN.match(s)
        if not m:
            raise Refused(f"{Path(size_file).name}: a line that is not KEY=VALUE")
        block.append(f"{m.group(1)}={unquote(m.group(2))}")
        keys.append(m.group(1))
    block.append(SIZE_END)
    new_block = "\n".join(block) + "\n"
    span = found["size"]
    for i, key, value, where in assignments(lines, found):
        if key not in keys or where == "size":
            continue
        before = span is None or i < span[0]
        if before and where is None and value == tpl_values.get(key):
            continue
        raise Refused(f"{key} (line {lineno(i)}) is also defined outside the size block; "
                      "Compose keeps the last definition, so one would override the other silently")
    if span is None:
        glue = "" if not text or text.endswith("\n") else "\n"
        new_text = text + glue + new_block
    else:
        new_text = "".join(lines[: span[0]]) + new_block + "".join(lines[span[1] + 1 :])
    if new_text == text:
        print("unchanged")
        return 0
    atomic_write(path, new_text)
    print("written")
    return 0


def secret_rows(env_path):
    lines = lines_of(read_text(env_path))
    found = spans(lines)
    return lines, found, [r for r in assignments(lines, found) if r[1] == "VAULT_SECRET_ID"]


def cmd_secret_id_state(argv):
    _, _, rows = secret_rows(argv[0])
    if not rows:
        return 1
    if len(rows) > 1:
        return 2
    value = rows[0][2]
    if value == "":
        return 1
    return 0 if UUID_RE.match(value) else 2


def cmd_set_secret_id(argv):
    env_path = argv[0]
    o = options(argv[1:])
    sid = os.environ.get("AIZZAK_INSTALL_SECRET_ID", "")
    if not UUID_RE.match(sid):
        raise Refused("the value for VAULT_SECRET_ID is not a UUID")
    lines, found, rows = secret_rows(env_path)
    if found["install"] is None:
        raise Refused("there is no install block to put VAULT_SECRET_ID in")
    if any(r[2] != "" for r in rows):
        raise Refused("VAULT_SECRET_ID is already set")
    ttl = o.get("ttl-hours", "720")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    note = (f"# VAULT_SECRET_ID minted {stamp}; secret_id_ttl {ttl}h (deploy/vault/bootstrap.sh); "
            "renewal is server-gpu-plan.md 6.5\n")
    start, end = found["install"]
    keep = []
    for i, ln in enumerate(lines):
        m = ASSIGN.match(bare(ln))
        if start <= i <= end and m and m.group(1) == "VAULT_SECRET_ID":
            continue  # an empty VAULT_SECRET_ID= line is replaced, not repeated
        keep.append(ln)
    end -= len(lines) - len(keep)
    keep[end:end] = [note, f"VAULT_SECRET_ID={sid}\n"]
    atomic_write(env_path, "".join(keep))
    return 0


def cmd_minted_at(argv):
    lines = lines_of(read_text(argv[0]))
    for ln in lines:
        m = re.match(r"^# VAULT_SECRET_ID minted (\S+?);", bare(ln))
        if m:
            print(m.group(1))
    return 0


def cmd_get(argv):
    env_path, key = argv
    lines = lines_of(read_text(env_path))
    rows = assignments(lines, spans(lines))
    value = last_value(rows, key)
    if value is None:
        return 1
    print(value)
    return 0


def cmd_get_public(argv):
    if SECRET_WORDS.search(argv[1]):
        print(f"env_tool: {argv[1]} looks like a secret; use get through a pipe", file=sys.stderr)
        return 2
    return cmd_get(argv)


COMMANDS = {
    "compose": cmd_compose,
    "size-block": cmd_size_block,
    "secret-id-state": cmd_secret_id_state,
    "set-secret-id": cmd_set_secret_id,
    "get": cmd_get,
    "get-public": cmd_get_public,
    "minted-at": cmd_minted_at,
}


def main(argv):
    if not argv or argv[0] not in COMMANDS:
        print("env_tool: unknown subcommand", file=sys.stderr)
        return 2
    try:
        return COMMANDS[argv[0]](argv[1:])
    except Refused as exc:
        print(f"env_tool: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print(f"env_tool: {type(exc).__name__} while handling .env", file=sys.stderr)
        return 4


sys.exit(main(sys.argv[1:]))
PY
}

# ── step 1: checks, nothing is written ──────────────────────────────────────
step_preflight() {
  local out rc=0 args=()
  ((GPU_SMOKE == 0)) || args+=(--smoke)
  # stdin is /dev/null: under `timeout` a terminal stdin would stop the script (see dk).
  out="$(timeout -k 10 600 bash "$ROOT/deploy/gpu-preflight.sh" "${args[@]}" 2>&1 </dev/null)" || rc=$?
  [[ -z $out ]] || printf '%s\n' "$out" | relay
  PREFLIGHT_RC="$rc"
  case "$rc" in
    0)
      if ((FORCE_CPU == 1)); then MODE="cpu"; else MODE="gpu"; fi
      ;;
    10) MODE="cpu" ;;
    12)
      if ((FORCE_CPU == 1)); then
        MODE="cpu"
      else
        die 10 "gpu-preflight.sh stopped the install (exit 12): the GPU is present but the container toolkit is" \
          "not configured; fix it above, or run with --cpu to install without the GPU"
      fi
      ;;
    *) die 10 "gpu-preflight.sh stopped the install (exit $rc); fix what it names above and run again" ;;
  esac
}

step_disk() {
  local dir kb gb need
  dir="$(dk "$PROBE_TIMEOUT_S" info --format '{{.DockerRootDir}}' 2>/dev/null | head -n 1)" || dir=""
  [[ -n $dir ]] || dir="/var/lib/docker"
  kb="$(df -Pk "$dir" 2>/dev/null | awk 'NR == 2 {print $4}')" || kb=""
  [[ $kb =~ ^[0-9]+$ ]] || die 11 "cannot read the free space of $dir with df"
  gb=$((kb / 1048576))
  need=$MIN_FREE_GB_CPU
  [[ $MODE == gpu ]] && need=$MIN_FREE_GB_GPU
  if ((FIRST_INSTALL == 1)) && ((gb < need)); then
    die 12 "only $gb GB free in $dir; the images of this install need about $need GB" \
      "free space there, or move Docker's data root to a bigger disk, then run again"
  fi
  say "checks: $gb GB free in $dir"
}

step_registry() {
  local out rc=0
  out="$(dk 60 manifest inspect "$IMAGE_REPO@${REF_APP#*@}" 2>&1)" || rc=$?
  ((rc != 0)) || return 0
  if [[ $out =~ (unauthorized|denied|authentication\ required|no\ basic\ auth|manifest\ unknown|not\ found) ]]; then
    die 13 "the registry refused $IMAGE_REPO: log in once as root with a READ-ONLY access token made for this server only:" \
      "  sudo docker login docker.io -u <user>" \
      "(a read-only Docker Hub access token; this script never takes or stores a key)"
  fi
  die 14 "no network: the registry could not be reached (exit $rc): $(printf '%s' "$out" | relay | head -n 1)"
}

step_checks_host() {
  say "step 1/8: checks (nothing is written)"
  # tr: a .env saved with CRLF line endings must still be recognised as an installed server
  if [[ -f $ROOT/.env ]] && tr -d '\r' <"$ROOT/.env" | grep -qxF '# >>> aizzak install >>>'; then
    FIRST_INSTALL=0
  fi
  step_preflight
  step_disk
  step_registry
}

# ── step 2: kernel ──────────────────────────────────────────────────────────
step_kernel() {
  local out rc=0
  say "step 2/8: kernel settings"
  out="$(bash "$ROOT/deploy/host-tuning.sh" --apply --persist 2>&1)" || rc=$?
  printf '%s\n' "$out" | relay | head -n 20
  ((rc == 0)) || die 20 "the kernel settings could not be applied (host-tuning.sh exit $rc): run as root"
}

# ── steps 3 and 4: .env, size, budget ───────────────────────────────────────
profiles_arg() {
  if ((MONITORING == 1)); then printf 'local-llm,monitoring'; else printf 'local-llm'; fi
}

step_env() {
  local out monitoring=off
  say "step 3/8: .env"
  ((MONITORING == 0)) || monitoring=on
  out="$(env_tool compose "$ROOT/.env" "$ROOT/.env.example" \
    --release "$RELEASE" --commit "$COMMIT" --mode "$MODE" \
    --monitoring "$monitoring" --size "$SIZE" \
    --domain "$DOMAIN" --firebase "$FIREBASE_PROJECT" \
    --image-app "$REF_APP" --image-embedding "$REF_EMBEDDING" --image-cuda "$REF_CUDA" 2>&1)" \
    || die 30 ".env refused:" "$out" "nothing was written"
  say "env: $out"
}

# The two tools judge a host, not the caller's shell: pin the inputs they read.
size_tool_env() {
  env COMPOSE_FILE=docker-compose.yml GPU_COMPOSE_FILE=docker-compose.gpu.yml SIZES_DIR=deploy/sizes "$@"
}

budget_rc() {
  case "$1" in
    0) ;;
    1) die 40 "the size is refused: this server is over the budget of the stack (see above)" ;;
    *) die 41 "resource-budget.sh failed (exit $1)" ;;
  esac
}

step_size() {
  local out rc=0 args=() size_file
  if [[ $SIZE != auto ]]; then
    args=(--size "$SIZE")
    ((MONITORING == 1)) || args+=(--without-monitoring)
    out="$(size_tool_env bash "$ROOT/deploy/resource-budget.sh" "${args[@]}" 2>&1)" || rc=$?
    printf '%s\n' "$out" | relay
    budget_rc "$rc"
    size_file="$ROOT/deploy/sizes/$SIZE.env"
    out="$(env_tool size-block "$ROOT/.env" "$size_file" "$SIZE" 2>&1)" || die 30 ".env refused:" "$out"
    SIZE_DESC="$SIZE (deploy/sizes/$SIZE.env)"
    return 0
  fi
  out="$(size_tool_env python3 -I "$ROOT/deploy/auto_size.py" --role server --mode "$MODE" \
    --profiles "$(profiles_arg)" --env-file "$ROOT/.env" 2>&1)" || rc=$?
  printf '%s\n' "$out" | relay
  case "$rc" in
    0) ;;
    1) die 40 "the server is smaller than the stack needs (the figures are above)" ;;
    *) die 41 "the size generator failed (exit $rc)" ;;
  esac
  HOST_LINE="$(printf '%s\n' "$out" | sed -n 's|^host: \([0-9.]*\) vCPU / \([0-9.]*\) GB.*|\1 vCPU, \2 GB|p' | head -n 1)"
  SIZE_DESC="auto (${HOST_LINE:-host unknown})"
}

step_budget() {
  local out rc=0
  [[ $SIZE == auto ]] || return 0
  out="$(size_tool_env bash "$ROOT/deploy/resource-budget.sh" --block "$ROOT/.env" 2>&1)" || rc=$?
  printf '%s\n' "$out" | relay
  budget_rc "$rc"
}

# ── step 5: pull what is missing, verify against VERSION ────────────────────
ref_by_digest() {
  local image="$1" base name
  if [[ $image == *@sha256:* ]]; then
    base="${image%@*}"
    name="${base##*/}"
    [[ $name != *:* ]] || base="${base%:*}"
    printf '%s@%s' "$base" "${image#*@}"
    return 0
  fi
  printf '%s' "$image"
}

release_images() {
  case "$MODE" in
    gpu) printf '%s\n%s\n' "$REF_APP" "$REF_CUDA" ;;
    *) printf '%s\n%s\n' "$REF_APP" "$REF_EMBEDDING" ;;
  esac | sort
}

step_pull() {
  local images image ref deadline
  say "step 5/8: images"
  # stderr stays out of the list: Compose warnings are not images
  images="$(compose 60 config --images 2>/dev/null | sort -u)" \
    || die 30 "Compose refused the project; run, with sudo and in $ROOT: docker compose config --quiet"
  [[ -n $images ]] || die 30 "Compose lists no images"
  PULL_LIST="$images"
  while IFS= read -r image; do
    [[ -n $image ]] || continue
    local tag="${image%@*}"
    tag="${tag##*/}"
    if [[ $tag != *:* ]] || [[ ${tag##*:} == dev || ${tag##*:} == latest ]]; then
      die 51 "image '$image' has no pinned tag, or a dev or latest tag; the release must pin every image"
    fi
  done <<<"$images"
  deadline=$((SECONDS + PULL_DEADLINE_S))
  while IFS= read -r image; do
    [[ -n $image ]] || continue
    ref="$(ref_by_digest "$image")"
    if dk "$PROBE_TIMEOUT_S" image inspect "$ref" >/dev/null 2>&1; then
      continue
    fi
    ((SECONDS < deadline)) || die 50 "the pull deadline passed before $image"
    say "pull: $ref"
    dk "$PULL_TIMEOUT_S" pull "$ref" 2>&1 | relay >/dev/null || true
    dk "$PROBE_TIMEOUT_S" image inspect "$ref" >/dev/null 2>&1 \
      || die 50 "pulling $ref failed or timed out"
  done <<<"$images"
}

step_verify_images() {
  local ours want listed ref local_digests digest ok
  ours="$(printf '%s\n' "$PULL_LIST" | grep -E "^${IMAGE_REPO//./\\.}:" | sort -u || true)"
  want="$(release_images)"
  if [[ $ours != "$want" ]]; then
    die 51 "the release images in the Compose list do not match VERSION" \
      "Compose lists: $(printf '%s' "$ours" | tr '\n' ' ')" \
      "VERSION says:  $(printf '%s' "$want" | tr '\n' ' ')"
  fi
  while IFS= read -r listed; do
    [[ -n $listed ]] || continue
    ref="$IMAGE_REPO@${listed#*@}"
    local_digests="$(dk "$PROBE_TIMEOUT_S" image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" 2>/dev/null || true)"
    ok=0
    while IFS= read -r digest; do
      if [[ $digest == "$ref" || $digest == "docker.io/$ref" ]]; then ok=1; fi
    done <<<"$local_digests"
    if ((ok == 0)); then
      die 51 "$ref: the pulled image does not carry the digest VERSION names" \
        "expected ${listed#*@}, local digests: $(printf '%s' "$local_digests" | tr '\n' ' ')"
    fi
  done <<<"$ours"
  say "images: the release images match VERSION"
}

# ── step 6: first boot, in a fixed order ────────────────────────────────────
# The service list and the config hashes change only when .env does (after the key is written),
# so they are read when the boot starts and again after that write, not on every poll.
refresh_config() {
  local out svc hash
  STATE_HASH=()
  STATE_SERVICES="$(compose 60 config --services)" || die 60 "docker compose config --services failed"
  out="$(compose 60 config --hash '*')" || die 60 "docker compose config --hash failed"
  while read -r svc hash; do
    [[ -n ${svc:-} ]] && STATE_HASH[$svc]="$hash"
  done <<<"$out"
}

# One batch: which containers exist and what state they are in.
stack_state() {
  local ids
  local -a idlist=()
  STATE_INSPECT=""
  ids="$(compose 60 ps -a -q)" || die 60 "docker compose ps failed"
  if [[ -n $ids ]]; then
    mapfile -t idlist <<<"$ids"
    STATE_INSPECT="$(dk 60 inspect --format '{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.config-hash"}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.ExitCode}}|{{.HostConfig.RestartPolicy.Name}}' "${idlist[@]}")" \
      || die 60 "docker inspect failed"
  fi
}

# 0 when every container of SVC carries the current config hash and is where it should be.
service_done() {
  local svc="$1" s h st hl ec rp found=0
  [[ -n ${STATE_HASH[$svc]:-} ]] || return 1
  while IFS='|' read -r s h st hl ec rp; do
    [[ $s == "$svc" ]] || continue
    found=1
    [[ $h == "${STATE_HASH[$svc]}" ]] || return 1
    if [[ $rp == no ]]; then
      [[ $st == exited && $ec == 0 ]] || return 1
    else
      [[ $st == running && (-z $hl || $hl == healthy) ]] || return 1
    fi
  done <<<"$STATE_INSPECT"
  ((found == 1))
}

one_shot_failed() {
  local svc="$1" s h st hl ec rp
  while IFS='|' read -r s h st hl ec rp; do
    [[ $s == "$svc" && $rp == no && $st == exited && $ec != 0 ]] && return 0
  done <<<"$STATE_INSPECT"
  return 1
}

boot_left() {
  printf '%s' $((BOOT_DEADLINE_S - (SECONDS - BOOT_START)))
}

# Waits until every service named in $1 (space separated) is done, or fails by name.
wait_for() {
  local names="$1" secs="$2" left deadline svc still
  left="$(boot_left)"
  ((left > 0)) || die 60 "boot step ${names%% *}: the boot deadline of ${BOOT_DEADLINE_S}s passed" \
    "read: docker compose logs ${names%% *}  (run it with sudo, from $ROOT)" ${BOOT_HINT:+"$BOOT_HINT"}
  ((secs <= left)) || secs="$left"
  deadline=$((SECONDS + secs))
  while :; do
    stack_state
    still=""
    for svc in $names; do
      service_done "$svc" && continue
      if one_shot_failed "$svc"; then
        die 60 "boot step $svc: the container exited with an error" \
          "read: docker compose logs $svc  (run it with sudo, from $ROOT)" ${BOOT_HINT:+"$BOOT_HINT"}
      fi
      still+="$svc "
    done
    [[ -n $still ]] || return 0
    ((SECONDS < deadline)) || die 60 "boot step ${still%% *}: not ready after ${secs}s (still waiting for: ${still% })" \
      "read: docker compose logs ${still%% *}  (run it with sudo, from $ROOT)" ${BOOT_HINT:+"$BOOT_HINT"}
    sleep "$POLL_S"
  done
}

ensure() {
  local svc="$1" secs="$2" out
  stack_state
  if service_done "$svc"; then
    say "boot: $svc is already done"
    return 0
  fi
  say "boot: starting $svc"
  out="$(compose 300 up -d "$svc" 2>&1)" || die 60 "boot step $svc: docker compose up -d $svc failed" \
    "$(printf '%s' "$out" | relay | tail -n 3)" "read: docker compose logs $svc  (run it with sudo, from $ROOT)" \
    ${BOOT_HINT:+"$BOOT_HINT"}
  wait_for "$svc" "$secs"
}

mint_secret_id() {
  local state=0 sid err
  env_tool secret-id-state "$ROOT/.env" || state=$?
  case "$state" in
    0)
      say "boot: VAULT_SECRET_ID is already in .env"
      return 0
      ;;
    1) ;;
    *) die 61 "VAULT_SECRET_ID in .env is damaged (not a UUID, or defined twice); nothing was replaced" ;;
  esac
  err="$(mktemp)"
  if ! sid="$(compose 60 exec -T vault sh -c "$MINT_SCRIPT" 2>"$err")"; then
    # Vault's stderr may name the token: shorten and redact it, and delete the file, before dying.
    sid="$(redact <"$err")"
    rm -f "$err"
    die 61 "minting VAULT_SECRET_ID failed:" "$sid"
  fi
  rm -f "$err"
  sid="$(printf '%s' "$sid" | tr -d '\r\n')"
  if [[ ! $sid =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; then
    die 61 "minting VAULT_SECRET_ID returned something that is not a UUID (length ${#sid}); nothing was written"
  fi
  AIZZAK_INSTALL_SECRET_ID="$sid" env_tool set-secret-id "$ROOT/.env" --ttl-hours "$SECRET_ID_TTL_H" \
    || die 61 "VAULT_SECRET_ID could not be written to .env"
  sid=""
  say "boot: VAULT_SECRET_ID minted and written to .env"
}

set_metrics_exporter_password() {
  local out
  out="$(env_tool get "$ROOT/.env" METRICS_EXPORTER_PASSWORD \
    | compose 120 exec -T postgres bash -c 'read -r METRICS_EXPORTER_PASSWORD; export METRICS_EXPORTER_PASSWORD; bash /docker-entrypoint-initdb.d/15-metrics-exporter.sh' 2>&1)" \
    || die 60 "boot step metrics_exporter: setting the role password in postgres failed" \
      "$(printf '%s' "$out" | redact)" "read: docker compose logs postgres  (run it with sudo, from $ROOT)"
  say "boot: metrics_exporter password set"
}

step_boot() {
  local svc pending="" out
  say "step 6/8: first boot"
  BOOT_START=$SECONDS
  refresh_config
  ensure vault 300
  ensure vault-bootstrap 300
  mint_secret_id
  refresh_config
  env_tool secret-id-state "$ROOT/.env" || die 61 "VAULT_SECRET_ID is not in .env: no service that carries it is started"
  ensure minio-bootstrap 300
  ensure migrate 900
  set_metrics_exporter_password
  if grep -qx 'ollama-pull' <<<"$STATE_SERVICES"; then
    BOOT_HINT="the model was not pulled; chat would answer 502; re-run sudo ./install.sh"
    ensure ollama-pull 900
    BOOT_HINT=""
  fi
  stack_state
  for svc in $STATE_SERVICES; do
    service_done "$svc" || pending+="$svc "
  done
  pending="${pending% }"
  if [[ -z $pending ]]; then
    say "boot: already converged: no container touched"
    return 0
  fi
  say "boot: starting the rest: $pending"
  out="$(compose 900 up -d 2>&1)" || die 60 "boot step up -d failed" "$(printf '%s' "$out" | relay | tail -n 3)" \
    "read: docker compose ps  (run it with sudo, from $ROOT)"
  wait_for "$pending" 900
}

# ── step 7: checks ──────────────────────────────────────────────────────────
http_code() {
  curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$1" 2>/dev/null || printf '000'
}

probe() {
  local url="$1" deadline code
  deadline=$((SECONDS + HEALTH_DEADLINE_S))
  while :; do
    code="$(http_code "$url")"
    [[ $code == 200 ]] && return 0
    ((SECONDS < deadline)) || return 1
    sleep "$POLL_S"
  done
}

step_checks() {
  local bound port out rc=0
  say "step 7/8: health checks"
  bound="$(compose 30 port nginx 80 2>/dev/null | head -n 1)" || bound=""
  port="${bound##*:}"
  if [[ ! $port =~ ^[0-9]+$ ]]; then
    FAILED_CHECK="/health (the published port of nginx could not be read)"
    return 70
  fi
  PORT="$port"
  probe "http://127.0.0.1:$port/health" || { FAILED_CHECK="/health"; return 70; }
  probe "http://127.0.0.1:$port/health/ready" || { FAILED_CHECK="/health/ready"; return 70; }
  out="$(compose 300 exec -T app python /app/deploy/smoke/stack_smoke.py 2>&1)" || rc=$?
  printf '%s\n' "$out" | relay | tail -n 20
  if ((rc != 0)); then
    FAILED_CHECK="stack_smoke"
    return 71
  fi
  return 0
}

# ── step 8: the report, on stdout only ──────────────────────────────────────
vault_volume() {
  local project
  project="$(env_tool get-public "$ROOT/.env" COMPOSE_PROJECT_NAME 2>/dev/null)" || project=""
  [[ -n $project ]] || project="$(sed -n 's/^name:[[:space:]]*\(.*[^[:space:]]\)[[:space:]]*$/\1/p' "$ROOT/docker-compose.yml" | head -n 1)"
  printf '%s_vault-init' "${project:-aizzak}"
}

# One line for the report: the certificate nginx serves on :443 (cloudflare-tls FR-18). Reads
# server.crt only, never the key file, and prints no certificate material. Always returns 0.
tls_state() {
  local crt="$ROOT/deploy/nginx/certs/server.crt" out not_after end_s day label hint=""
  local -a hashes=()
  if [[ -L $crt ]]; then
    printf 'WARNING: deploy/nginx/certs/server.crt is a symbolic link; not read'
    return 0
  fi
  if [[ ! -e $crt ]]; then
    printf 'no certificate yet (deploy/nginx/certs/server.crt is missing)'
    return 0
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    printf 'not checked: openssl is not installed on this host'
    return 0
  fi
  # one call; -checkhost prints last. (-checkend would end the output before it: measured)
  out="$(timeout 10 openssl x509 -in "$crt" -noout -subject_hash -issuer_hash -enddate \
    -issuer -nameopt sep_multiline,lname,utf8 -checkhost "$DOMAIN" 2>/dev/null | relay)" || out=""
  not_after="$(sed -n 's/^notAfter=//p' <<<"$out" | head -n 1)"
  end_s="$(date -u -d "$not_after" +%s 2>/dev/null)" || end_s=""
  if [[ -z $not_after || -z $end_s ]]; then
    printf 'WARNING: deploy/nginx/certs/server.crt could not be read as a certificate'
    return 0
  fi
  day="$(date -u -d "@$end_s" +%Y-%m-%d)"
  mapfile -t hashes < <(head -n 2 <<<"$out")
  if [[ ${#hashes[@]} -eq 2 && ${hashes[0]} == "${hashes[1]}" ]]; then
    printf 'self-signed development certificate -- browsers will not trust https://%s; a real one: docs/deploy-server.md §4' "$DOMAIN"
    return 0
  fi
  label="$(sed -n 's/^ *commonName=//p' <<<"$out" | head -n 1)"
  [[ -n $label ]] || label="$(sed -n 's/^ *organizationalUnitName=//p' <<<"$out" | head -n 1)"
  [[ -n $label ]] || label="$(sed -n 's/^ *organizationName=//p' <<<"$out" | head -n 1)"
  label="${label:-unknown}"
  if ((end_s <= $(date -u +%s))); then
    printf 'WARNING: the certificate expired on %s (issuer %s); docs/deploy-server.md §4' "$day" "$label"
    return 0
  fi
  if [[ $out != *"does match certificate"* ]]; then
    printf 'WARNING: the certificate does not cover %s (issuer %s, expires %s); docs/deploy-server.md §4' \
      "$DOMAIN" "$label" "$day"
    return 0
  fi
  [[ $label != *"CloudFlare Origin"* ]] || hint=' -- trusted by Cloudflare only: keep the DNS record proxied'
  printf 'certificate for %s, issuer %s, expires %s%s' "$DOMAIN" "$label" "$day" "$hint"
}

report() {
  local status="$1" minted expires mode
  if [[ $status == READY ]]; then
    say "install: READY -- AIZZAK $RELEASE at https://$DOMAIN"
    say "  checks      /health 200, /health/ready 200, stack_smoke PASS"
  else
    say "install: NOT READY -- AIZZAK $RELEASE at https://$DOMAIN"
    say "  failed: $FAILED_CHECK"
  fi
  say "  TLS: $(tls_state)"
  say "  size        $SIZE_DESC"
  if [[ $MODE == gpu ]]; then
    say "  GPU: yes    docker-compose.gpu.yml"
  elif ((FORCE_CPU == 1)); then
    say "  GPU: no (--cpu)"
  else
    say "  GPU: no (gpu-preflight exit ${PREFLIGHT_RC:-10})"
  fi
  if ((MONITORING == 1)); then
    say "  monitoring: on"
  else
    say "  monitoring: off -- NO ALERTS: watch https://$DOMAIN/health from outside this server (docs/deploy-server.md §2)"
  fi
  minted="$(env_tool minted-at "$ROOT/.env" 2>/dev/null | head -n 1)" || minted=""
  expires=""
  [[ -z $minted ]] || expires="$(date -u -d "$minted + $SECRET_ID_TTL_H hours" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" || expires=""
  if [[ -n $expires ]]; then
    say "  vault       secret_id expires $expires (${SECRET_ID_TTL_H}h after minting); renewing it is server-gpu-plan.md 6.5"
  else
    say "  vault       secret_id expiry unknown here; renewing it is server-gpu-plan.md 6.5"
  fi
  say "  backup      copy the Vault volume $(vault_volume) off this server (6.4); its contents are not shown"
  mode="$(stat -c %a "$ROOT/.env" 2>/dev/null || echo '?')"
  if [[ $mode == 600 ]]; then
    say "  .env        mode 0600 (chmod 600 .env: done)"
  else
    say "  .env        mode 0$mode -- run: chmod 600 .env  (it holds every password and VAULT_SECRET_ID)"
  fi
  say "  next        run every docker compose command with sudo, from this directory"
}

# ── the whole run ───────────────────────────────────────────────────────────
run_steps() {
  local rc=0
  step_checks_host
  step_kernel
  step_env
  step_size
  step_budget
  step_pull
  step_verify_images
  step_boot
  step_checks || rc=$?
  say "step 8/8: report"
  if ((rc == 0)); then
    report READY
    return 0
  fi
  report "NOT READY"
  exit "$rc"
}

main() {
  local -a trusted
  prepare "$@"
  require_root
  trusted=("$ROOT" "$ROOT/deploy" "$ROOT/deploy/postgres/sizes")
  if [[ -e $ROOT/.env || -L $ROOT/.env ]]; then trusted+=("$ROOT/.env"); fi
  require_trusted 0 "${trusted[@]}"
  lock_bundle
  run_steps
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
