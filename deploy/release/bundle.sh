#!/usr/bin/env bash
# Build ONE release bundle, aizzak-<VERSION>.tar.gz (server-gpu-plan.md 5.1): what a server needs to
# run the platform from its release images, and nothing else. No repository, no tests, no secrets.
#
# Usage:
#   deploy/release/bundle.sh --digests FILE [--out DIR] <VERSION>
#   deploy/release/bundle.sh --help
#
# <VERSION> is YYYY.M.N, WITHOUT a leading "v" (2026.10.3), the same shape build-push.sh takes.
# --digests is the `release-digests` artifact that build-push.sh --digest-file wrote: one line per
# image, "anastawahia/aizzak:<prefix>-<VERSION> sha256:<64 hex> <commit>". --out defaults to the
# current directory; the bundle is DIR/aizzak-<VERSION>.tar.gz.
#
# The content is the tree AT THE TAG v<VERSION>, not the working directory: what the list
# deploy/release/bundle-files.txt (read from the tag too) names, plus deploy/release/install.sh at
# the root, plus a VERSION file made from the digests. An untracked or edited file in the working
# tree cannot get in. The archive is deterministic: the same tag and digests give the same bytes.
#
# This script does not check that the tag is on master. build-push.sh refuses to build a commit
# that is not on origin/master, and every line of the digests file must name the tag's commit, so a
# real digests file already carries that guarantee. It does not sign anything either: it prints the
# sha256 so the operator can compare it by hand (docs/deploy-server.md, gap th-12).
#
# Every check runs before anything is written into DIR. The work happens in a temporary directory
# that a trap removes.
#
# Exit codes:
#   0  the bundle was written; its path, sha256 and file count are printed
#   2  bad usage: a missing or malformed version, an unknown option, or no --digests
#   3  the environment: not the repository root, or git, gzip or sha256sum missing, or tar is not
#      GNU tar 1.28 or newer
#   4  the tag v<VERSION> does not exist
#   5  the digests file: unreadable, not three lines, a prefix missing or repeated, digest-unknown,
#      a digest that is not sha256: and 64 hex characters, an image that is not
#      anastawahia/aizzak:<prefix>-<VERSION>, or a commit that is not the tag's commit
#   6  the output: DIR does not exist or is not writable, or aizzak-<VERSION>.tar.gz is already there
#   7  the content: a listed path is absent at the tag, a symbolic link or submodule is under one, a
#      forbidden path is in the staged tree, or the tag is older than bundle-files.txt
#   1  an unexpected error (set -e)
set -euo pipefail
umask 022
export LC_ALL=C

readonly IMAGE_REPO="anastawahia/aizzak"
readonly VERSION_RE='^20[0-9]{2}\.(1[0-2]|[1-9])\.[1-9][0-9]*$'
readonly LIST_PATH="deploy/release/bundle-files.txt"
readonly INSTALL_PATH="deploy/release/install.sh"
readonly PREFIXES=(app embedding embedding-cuda)    # the order of the VERSION file
# A path in the staged tree matching any of these never ships (FR-3). The content comes from the
# tag and the list, so this is the second line of defence, not the first.
readonly FORBIDDEN_RE=(
  '^docker-compose\.(build|test|wsl-gpu)\.yml$'
  '^deploy/(load|postgres/testdb|backup/results)/'
  '^(src|tests)/'
  '(^|/)\.env([^/]*)$'
  '(^|/)init\.json$'
  '^deploy/nginx/certs/'
  '^deploy/postgres/sizes/auto\.conf'
  '\.auto-size-tmp-'
  '(^|/)__pycache__(/|$)'
)

DIGESTS=""
OUT_DIR="."
VERSION=""
STAGE=""
TMP_ARCHIVE=""

usage() {
  cat >&2 <<'USAGE'
usage: bundle.sh --digests FILE [--out DIR] <VERSION>
       bundle.sh --help

  <VERSION>   YYYY.M.N without "v", e.g. 2026.10.3; the tag v<VERSION> must exist
  --digests   the release-digests file build-push.sh wrote (required)
  --out       where to write aizzak-<VERSION>.tar.gz (default: the current directory)
USAGE
}

die() {
  local code="$1"
  shift
  echo "bundle: ERROR: $*" >&2
  exit "$code"
}

usage_die() {
  echo "bundle: ERROR: $*" >&2
  usage
  exit 2
}

cleanup() {
  [[ -z $STAGE ]] || rm -rf -- "$STAGE"
  [[ -z $TMP_ARCHIVE ]] || rm -f -- "$TMP_ARCHIVE"
}
trap cleanup EXIT

parse_args() {
  while (($# > 0)); do
    case "$1" in
      -h | --help)
        usage
        exit 0
        ;;
      --digests)
        (($# >= 2)) || usage_die "--digests needs a path"
        DIGESTS="$2"
        shift 2
        ;;
      --out)
        (($# >= 2)) || usage_die "--out needs a directory"
        OUT_DIR="$2"
        shift 2
        ;;
      -*) usage_die "unknown option: $1" ;;
      *)
        [[ -z $VERSION ]] || usage_die "more than one version: $VERSION and $1"
        VERSION="$1"
        shift
        ;;
    esac
  done
  [[ -n $VERSION ]] || usage_die "a version is required, e.g. 2026.10.3"
  [[ $VERSION =~ $VERSION_RE ]] \
    || usage_die "version '$VERSION' is not YYYY.M.N without a 'v' or an image prefix (2026.10.3)"
  [[ -n $DIGESTS ]] || usage_die "--digests FILE is required: the release-digests artifact of the build"
}

require_tools() {
  local tool version
  for tool in git gzip sha256sum tar; do
    command -v "$tool" >/dev/null 2>&1 || die 3 "$tool is not installed"
  done
  version="$(tar --version 2>/dev/null | head -n 1)" || version=""
  [[ $version == "tar (GNU tar) "* ]] || die 3 "tar is not GNU tar ($version); --sort and --mtime are needed"
  version="${version#tar (GNU tar) }"
  # 1.28 is where --sort=name arrived.
  printf '%s\n1.28\n' "$version" | sort -V -C -r 2>/dev/null \
    || die 3 "GNU tar $version is older than 1.28"
}

require_repo_root() {
  local here top
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
  top="$(git -C "$here" rev-parse --show-toplevel 2>/dev/null)" \
    || die 3 "not inside a git repository: $here"
  top="$(cd "$top" && pwd -P)"
  [[ $top == "$here" ]] || die 3 "$here is not the repository root ($top)"
  ROOT="$here"
}

# ── the tag ────────────────────────────────────────────────────────────────
resolve_tag() {
  COMMIT="$(git -C "$ROOT" rev-parse -q --verify "refs/tags/v$VERSION^{commit}")" \
    || die 4 "the tag v$VERSION does not exist in $ROOT"
  EPOCH="$(git -C "$ROOT" log -1 --format=%ct "$COMMIT")"
}

# ── the digests file ───────────────────────────────────────────────────────
declare -A DIGEST_OF=()

read_digests() {
  local -a lines=()
  local line ref digest sha extra prefix want seen
  [[ -f $DIGESTS && -r $DIGESTS ]] || die 5 "cannot read the digests file: $DIGESTS"
  mapfile -t lines <"$DIGESTS" || die 5 "cannot read the digests file: $DIGESTS"
  lines=("${lines[@]%$'\r'}")  # a file saved with CRLF line endings is read as if with LF
  ((${#lines[@]} == 3)) \
    || die 5 "the digests file must have exactly 3 lines (app, embedding, embedding-cuda); it has ${#lines[@]}"
  for line in "${lines[@]}"; do
    read -r ref digest sha extra <<<"$line"
    [[ -z $extra && -n $sha ]] || die 5 "malformed digests line (want '<image> <digest> <commit>'): $line"
    [[ $digest != digest-unknown ]] || die 5 "the digests file has digest-unknown for $ref: that push is not usable"
    [[ $digest =~ ^sha256:[0-9a-f]{64}$ ]] \
      || die 5 "the digest of $ref is not sha256: followed by 64 hex characters"
    seen=""
    for prefix in "${PREFIXES[@]}"; do
      want="$IMAGE_REPO:$prefix-$VERSION"
      if [[ $ref == "$want" ]]; then
        seen="$prefix"
      fi
    done
    [[ -n $seen ]] || die 5 "unexpected image '$ref' (want $IMAGE_REPO:<app|embedding|embedding-cuda>-$VERSION)"
    [[ -z ${DIGEST_OF[$seen]:-} ]] || die 5 "the digests file names $ref twice"
    [[ $sha == "$COMMIT" ]] \
      || die 5 "$ref was built from commit $sha, but the tag v$VERSION is $COMMIT"
    DIGEST_OF[$seen]="$digest"
  done
  for prefix in "${PREFIXES[@]}"; do
    [[ -n ${DIGEST_OF[$prefix]:-} ]] || die 5 "the digests file has no line for $IMAGE_REPO:$prefix-$VERSION"
  done
}

# ── the output directory ───────────────────────────────────────────────────
check_output() {
  [[ -d $OUT_DIR ]] || die 6 "the output directory does not exist: $OUT_DIR"
  OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"
  [[ -w $OUT_DIR ]] || die 6 "the output directory is not writable: $OUT_DIR"
  OUT_FILE="$OUT_DIR/aizzak-$VERSION.tar.gz"
  if [[ -e $OUT_FILE || -L $OUT_FILE ]]; then
    die 6 "$OUT_FILE already exists; it is never replaced (remove it yourself)"
  fi
}

# ── the content ────────────────────────────────────────────────────────────
declare -a PATHS=()

read_list() {
  local text raw path
  text="$(git -C "$ROOT" show "$COMMIT:$LIST_PATH" 2>/dev/null)" \
    || die 7 "the tag v$VERSION has no $LIST_PATH: it is older than the bundle"
  while IFS= read -r raw; do
    path="${raw#"${raw%%[![:space:]]*}"}"
    path="${path%"${path##*[![:space:]]}"}"
    [[ -n $path && $path != "#"* ]] || continue
    path="${path%/}"
    [[ $path =~ ^[A-Za-z0-9._/-]+$ && $path != /* && $path != *..* ]] \
      || die 7 "$LIST_PATH has a path this script will not handle: $path"
    PATHS+=("$path")
  done <<<"$text"
  ((${#PATHS[@]} > 0)) || die 7 "$LIST_PATH at the tag lists nothing"
  git -C "$ROOT" cat-file -e "$COMMIT:$INSTALL_PATH" 2>/dev/null \
    || die 7 "$INSTALL_PATH is absent at the tag v$VERSION"
}

check_listed_paths() {
  local path bad
  for path in "${PATHS[@]}"; do
    git -C "$ROOT" cat-file -e "$COMMIT:$path" 2>/dev/null \
      || die 7 "$path is listed in $LIST_PATH but is absent at the tag v$VERSION"
  done
  bad="$(git -C "$ROOT" ls-tree -r "$COMMIT" -- "${PATHS[@]}" | grep -E '^(120000|160000) ' || true)"
  [[ -z $bad ]] || die 7 "a symbolic link or submodule is under a listed path: ${bad#*$'\t'}"
}

stage_tree() {
  local top="aizzak-$VERSION" prefix
  STAGE="$(mktemp -d)"
  mkdir -p "$STAGE/$top"
  git -C "$ROOT" archive --format=tar "$COMMIT" -- "${PATHS[@]}" | tar -x -C "$STAGE/$top"
  git -C "$ROOT" show "$COMMIT:$INSTALL_PATH" >"$STAGE/$top/install.sh"
  chmod 0755 "$STAGE/$top/install.sh"
  {
    echo "$VERSION"
    echo "commit $COMMIT"
    for prefix in "${PREFIXES[@]}"; do
      echo "$IMAGE_REPO:$prefix-$VERSION@${DIGEST_OF[$prefix]}"
    done
  } >"$STAGE/$top/VERSION"
  chmod 0644 "$STAGE/$top/VERSION"
}

check_staged() {
  local top="aizzak-$VERSION" file rel re
  if [[ -n "$(find "$STAGE/$top" ! -type f ! -type d -print -quit)" ]]; then
    die 7 "the staged tree has something that is neither a file nor a directory (a link?)"
  fi
  while IFS= read -r file; do
    rel="${file#"$STAGE/$top/"}"
    for re in "${FORBIDDEN_RE[@]}"; do
      if [[ $rel =~ $re && ${rel##*/} != ".env.example" ]]; then
        die 7 "$rel may not be in a bundle (matches $re)"
      fi
    done
  done < <(find "$STAGE/$top" -type f | sort)
  FILE_COUNT="$(find "$STAGE/$top" -type f | wc -l)"
}

write_archive() {
  local top="aizzak-$VERSION"
  TMP_ARCHIVE="$OUT_DIR/.aizzak-$VERSION.tar.gz.tmp.$$"
  tar --sort=name --format=gnu --owner=0 --group=0 --numeric-owner \
    --mtime="@$EPOCH" --mode='u+rwX,go+rX,go-w' -C "$STAGE" -cf - "$top" \
    | gzip -n -9 >"$TMP_ARCHIVE"
}

check_archive() {
  local top="aizzak-$VERSION" name kind
  while IFS= read -r name; do
    [[ $name == "$top/"* || $name == "$top/" ]] || die 7 "the archive has an entry outside $top/: $name"
    [[ $name != /* && $name != *..* ]] || die 7 "the archive has an absolute or '..' path: $name"
  done < <(tar -tzf "$TMP_ARCHIVE")
  while IFS= read -r kind; do
    [[ $kind != l && $kind != h ]] || die 7 "the archive has a link entry"
  done < <(tar -tvzf "$TMP_ARCHIVE" | cut -c1)
}

publish() {
  # ln refuses to replace: the final name is taken atomically or not at all.
  ln -- "$TMP_ARCHIVE" "$OUT_FILE" || die 6 "cannot create $OUT_FILE"
  rm -f -- "$TMP_ARCHIVE"
  TMP_ARCHIVE=""
}

main() {
  local arg
  for arg in "$@"; do  # --help works anywhere, before any check of the repository
    if [[ $arg == -h || $arg == --help ]]; then
      usage
      exit 0
    fi
  done
  require_tools
  require_repo_root
  parse_args "$@"
  resolve_tag
  read_digests
  check_output
  read_list
  check_listed_paths
  stage_tree
  check_staged
  write_archive
  check_archive
  publish
  echo "bundle: wrote $OUT_FILE"
  echo "bundle: sha256 $(sha256sum "$OUT_FILE" | cut -d' ' -f1)"
  echo "bundle: $((FILE_COUNT)) files, $(wc -c <"$OUT_FILE") bytes, version $VERSION, commit $COMMIT"
}

main "$@"
