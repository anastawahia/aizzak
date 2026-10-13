#!/usr/bin/env bash
# Build and push ONE release of the platform images (server-gpu-plan.md 1.3).
#
# This script is the ONLY release logic: the release workflow
# (.github/workflows/release.yml) runs it, and an operator runs it for an
# emergency. Nothing else in the repository builds or pushes a release image.
#
# Usage:
#   deploy/release/build-push.sh [--gates-checked-by-workflow] [--digest-file PATH] <VERSION>
#   deploy/release/build-push.sh --preflight <VERSION>    # tag, tree, master ancestry only
#   deploy/release/build-push.sh --check-tag <VERSION>    # version format only
#   deploy/release/build-push.sh --list-images
#   deploy/release/build-push.sh --help
#
# <VERSION> is YYYY.M.N, WITHOUT a leading "v" and WITHOUT an image prefix
# (2026.10.1, never v2026.10.1 or app-2026.10.1). The script derives every
# image tag from it.
#
# ONE private Docker Hub repository, anastawahia/aizzak; the tag prefix tells
# the images apart:
#
#   prefix          built from                      reference for 2026.10.1
#   app             Dockerfile                      anastawahia/aizzak:app-2026.10.1
#   embedding       services/embedding/Dockerfile   anastawahia/aizzak:embedding-2026.10.1
#   embedding-cuda  RESERVED for plan step 2.4, NOT built here
#
# There is no "dev", no "latest" and no "<prefix>-latest" tag.
#
# Prerequisites: a registry session already exists (this script never logs in
# and takes no credential option), `gh auth status` is healthy, buildx is
# available with linux/amd64, and the repository anastawahia/aizzak exists
# and is PRIVATE. Docker Hub creates a missing repository on first push with
# the account's default visibility, which is public -- so the script refuses
# to run while the repository is public, and fails loudly if it turns public.
#
# Order and exit codes (the first failing check decides):
#   2  bad usage: missing or malformed version, unknown option,
#      --gates-checked-by-workflow outside GitHub Actions, or a --digest-file
#      that cannot be written (checked before anything is built or pushed)
#   3  not the repository root, or the working tree is dirty
#   4  HEAD is not an ancestor of origin/master (or the fetch failed)
#   5  CI for this commit is not completed/success (skipped with
#      --gates-checked-by-workflow: the workflow's own needs: is the check)
#   6  no docker, no buildx, or no linux/amd64 builder
#   7  the repository is public / unverifiable, or a tag of this version exists
#   8  an image failed to BUILD (nothing has been pushed yet: all images are
#      built before the first push)
#   9  after `docker push` started: a push failed; a pushed tag's digest could not
#      be read or is malformed; the --digest-file could not be written after all (backstop); or the
#      repository did not answer exactly 404 (private) after a push -- public,
#      rate limited, 5xx and timeouts all abort (fail closed). Every tag that
#      reached the registry is listed with the ABANDONED message
#
# A release that failed after pushing something is ABANDONED, never resumed:
# the tag-exists check refuses to push over it. Release the next number
# (YYYY.M.(N+1)) instead. The script prints what it already pushed.
#
# CI is the normal route. Run this by hand only in an emergency: a push from a
# home connection moves about 150 KB/s, roughly seven hours for the embedding
# image.
set -euo pipefail

readonly IMAGE_REPO="anastawahia/aizzak"         # ONE private repository (owner, 2026-10-13)
readonly GITHUB_REPO="anastawahia/aizzak"
readonly CI_WORKFLOW="ci.yml"
readonly PLATFORM="linux/amd64"
readonly HUB_API_URL="https://hub.docker.com/v2/repositories/anastawahia/aizzak/"
readonly VERSION_RE='^20[0-9]{2}\.(1[0-2]|[1-9])\.[1-9][0-9]*$'
readonly IMAGES=(                                 # "<tag prefix> <dockerfile>"
  "app Dockerfile"
  "embedding services/embedding/Dockerfile"
)
readonly RESERVED_IMAGES=(                        # "<tag prefix> <plan step>" -- NOT built here
  "embedding-cuda 2.4"                            # server-gpu-plan.md 2.4 adds requirements-cuda.txt
)

MODE="release"
GATES_BY_WORKFLOW=0
DIGEST_FILE=""
VERSION=""
COMMIT=""
PUSHED=()        # "<repo>:<tag>@sha256:<hex>" for every image already pushed
LAST_REF=""      # set by push_image on success

usage() {
  cat >&2 <<'USAGE'
usage: build-push.sh [--gates-checked-by-workflow] [--digest-file PATH] <VERSION>
       build-push.sh --preflight <VERSION>
       build-push.sh --check-tag <VERSION>
       build-push.sh --list-images
       build-push.sh --help

  <VERSION>  YYYY.M.N without "v" and without an image prefix, e.g. 2026.10.1
USAGE
}

die() {
  local code="$1"
  shift
  echo "release: ERROR: $*" >&2
  exit "$code"
}

usage_die() {
  echo "release: ERROR: $*" >&2
  usage
  exit 2
}

valid_version() {
  [[ $1 =~ $VERSION_RE ]]
}

image_tag() {
  echo "$1-$2"
}

list_images() {
  local entry
  for entry in "${IMAGES[@]}"; do
    echo "$entry"
  done
  for entry in "${RESERVED_IMAGES[@]}"; do
    echo "${entry% *} reserved:${entry#* }"
  done
}

require_repo_root() {
  local here top
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
  cd "$here"
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || die 3 "not inside a git repository: $here"
  top="$(cd "$top" && pwd -P)"
  [[ $top == "$here" ]] || die 3 "$here is not the repository root ($top)"
}

require_clean_tree() {
  local dirty
  dirty="$(git status --porcelain --untracked-files=normal)" || die 3 "git status failed"
  if [[ -n $dirty ]]; then
    echo "$dirty" >&2
    die 3 "working tree is dirty: commit or remove the changes above"
  fi
}

require_on_master() {
  git fetch --quiet origin master || die 4 "cannot fetch origin/master"
  git merge-base --is-ancestor HEAD FETCH_HEAD || die 4 "HEAD is not on origin/master"
}

require_ci_green() {
  local sha="$1" state
  command -v gh >/dev/null 2>&1 || die 5 "gh is not installed: cannot verify CI for $sha"
  state="$(gh run list --repo "$GITHUB_REPO" --workflow "$CI_WORKFLOW" --commit "$sha" \
    --limit 20 --json status,conclusion,createdAt \
    --jq 'if length==0 then "none" else (sort_by(.createdAt)|last|"\(.status)/\(.conclusion)") end')" \
    || die 5 "gh could not list CI runs for $sha"
  [[ $state == "completed/success" ]] || die 5 "CI for $sha is '$state', not completed/success"
}

require_buildx_amd64() {
  command -v docker >/dev/null 2>&1 || die 6 "docker is not installed"
  docker buildx version >/dev/null 2>&1 || die 6 "docker buildx is not available"
  local builders
  builders="$(docker buildx inspect 2>/dev/null)" || die 6 "docker buildx inspect failed"
  [[ $builders == *"$PLATFORM"* ]] || die 6 "the buildx builder does not list $PLATFORM"
}

# Prints the HTTP status of the repository page, asked WITHOUT credentials.
hub_status() {
  local code
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$HUB_API_URL" 2>/dev/null)" \
    || code="000"
  echo "$code"
}

require_repo_private() {
  local code
  code="$(hub_status)"
  case "$code" in
    404)
      # Unauthenticated, a private repository and a missing one look the same.
      echo "release: note: HTTP 404 means private OR not created yet; $IMAGE_REPO must exist" \
        "and be PRIVATE before the first push" >&2
      ;;
    200) die 7 "$IMAGE_REPO is PUBLIC: make it private before releasing" ;;
    *)
      die 7 "cannot verify that $IMAGE_REPO is private (HTTP $code; only 404 is accepted," \
        "and 404 also means the repository does not exist: create it PRIVATE first)"
      ;;
  esac
}

require_tag_absent() {
  local tag="$1" out
  if out="$(docker buildx imagetools inspect "$IMAGE_REPO:$tag" 2>&1)"; then
    die 7 "$IMAGE_REPO:$tag already exists: release the next version"
  fi
  if ! grep -qi "not found" <<<"$out"; then
    die 7 "cannot tell whether $IMAGE_REPO:$tag exists: $out"
  fi
}

build_image() {
  local prefix="$1" dockerfile="$2" version="$3" sha="$4"
  docker buildx build --platform "$PLATFORM" --pull --load \
    --provenance=false --sbom=false \
    --file "$dockerfile" --tag "$IMAGE_REPO:$(image_tag "$prefix" "$version")" \
    --label "org.opencontainers.image.revision=$sha" \
    --label "org.opencontainers.image.version=$version" \
    --label "org.opencontainers.image.title=aizzak-$prefix" \
    --label "org.opencontainers.image.source=https://github.com/$GITHUB_REPO" \
    .
}

# Pushes one image. Returns 0 on success (LAST_REF = "<repo>:<tag>@sha256:<hex>");
# 1 if `docker push` itself failed; 2 if the push succeeded but the digest could not be
# read or is malformed; 3 if the digest file could not be written. From the moment
# `docker push` succeeds the tag EXISTS in the registry, so it is recorded in PUSHED
# (and in the digest file) before anything else can fail.
push_image() {
  local prefix="$1" version="$2" sha="$3" tag digest="digest-unknown" rc=0 slot
  tag="$(image_tag "$prefix" "$version")"
  docker push "$IMAGE_REPO:$tag" || return 1
  slot=${#PUSHED[@]}
  PUSHED+=("$IMAGE_REPO:$tag@$digest")
  if ! digest="$(docker buildx imagetools inspect --format '{{.Manifest.Digest}}' \
    "$IMAGE_REPO:$tag")" || [[ ! $digest =~ ^sha256:[0-9a-f]{64}$ ]]; then
    digest="digest-unknown"
    rc=2
  fi
  PUSHED[slot]="$IMAGE_REPO:$tag@$digest"
  LAST_REF="${PUSHED[slot]}"
  if [[ -n $DIGEST_FILE ]]; then
    echo "$IMAGE_REPO:$tag $digest $sha" >>"$DIGEST_FILE" || return 3
  fi
  return "$rc"
}

abandon() {
  local what="$1" next ref
  echo "release: ERROR: $what" >&2
  if ((${#PUSHED[@]} > 0)); then
    next="${VERSION%.*}.$((${VERSION##*.} + 1))"
    echo "release: already pushed (version $VERSION is ABANDONED -- release $next):" >&2
    for ref in "${PUSHED[@]}"; do
      echo "$ref" >&2
    done
  fi
  exit 9
}

parse_args() {
  local mode_seen=0
  while (($# > 0)); do
    case "$1" in
      --help | -h)
        usage
        exit 0
        ;;
      --list-images | --preflight | --check-tag)
        ((mode_seen == 0)) || usage_die "only one of --list-images, --preflight, --check-tag"
        mode_seen=1
        MODE="${1#--}"
        ;;
      --gates-checked-by-workflow)
        GATES_BY_WORKFLOW=1
        ;;
      --digest-file)
        (($# >= 2)) || usage_die "--digest-file needs a path"
        DIGEST_FILE="$2"
        [[ $DIGEST_FILE == /* ]] || DIGEST_FILE="$PWD/$DIGEST_FILE"
        shift
        ;;
      -*)
        usage_die "unknown option: $1"
        ;;
      *)
        [[ -z $VERSION ]] || usage_die "more than one version given"
        VERSION="$1"
        ;;
    esac
    shift
  done
}

main() {
  parse_args "$@"

  if [[ $MODE == "list-images" ]]; then
    list_images
    exit 0
  fi

  [[ -n $VERSION ]] || usage_die "missing version (YYYY.M.N)"
  valid_version "$VERSION" \
    || usage_die "'$VERSION' is not a version: expected YYYY.M.N without a leading v or a prefix"
  if [[ $MODE == "check-tag" ]]; then
    exit 0
  fi
  if ((GATES_BY_WORKFLOW == 1)) && [[ ${GITHUB_ACTIONS:-} != "true" ]]; then
    usage_die "--gates-checked-by-workflow is only accepted inside GitHub Actions"
  fi

  # Fail before any build or push: a bad path found after the first push would
  # burn a version number. The check in push_image stays as a backstop.
  if [[ $MODE == "release" && -n $DIGEST_FILE ]]; then
    # A probe file must not outlive the probe: it would dirty the tree checked next.
    local existed=0
    [[ -e $DIGEST_FILE ]] && existed=1
    : >>"$DIGEST_FILE" 2>/dev/null || usage_die "cannot write --digest-file $DIGEST_FILE"
    ((existed == 1)) || rm -f "$DIGEST_FILE"
  fi

  require_repo_root
  require_clean_tree
  require_on_master
  COMMIT="$(git rev-parse HEAD)"
  if [[ $MODE == "preflight" ]]; then
    echo "release: preflight OK $VERSION ($COMMIT)"
    exit 0
  fi

  if ((GATES_BY_WORKFLOW == 0)); then
    require_ci_green "$COMMIT"
  fi
  require_buildx_amd64
  require_repo_private
  local entry prefix dockerfile ref code
  for entry in "${IMAGES[@]}"; do
    prefix="${entry%% *}"
    require_tag_absent "$(image_tag "$prefix" "$VERSION")"
  done

  echo "release: commit $COMMIT"
  echo "release: version $VERSION"

  # Build everything first: a failed build (a model download, say) must happen
  # before any push, so it leaves nothing in the registry.
  for entry in "${IMAGES[@]}"; do
    prefix="${entry%% *}"
    dockerfile="${entry#* }"
    build_image "$prefix" "$dockerfile" "$VERSION" "$COMMIT" \
      || die 8 "build failed: $IMAGE_REPO:$(image_tag "$prefix" "$VERSION") (nothing was pushed)"
  done

  for entry in "${IMAGES[@]}"; do
    prefix="${entry%% *}"
    ref="$IMAGE_REPO:$(image_tag "$prefix" "$VERSION")"
    if push_image "$prefix" "$VERSION" "$COMMIT"; then
      :
    else
      case "$?" in
        1) abandon "push failed: $ref" ;;
        2) abandon "pushed $ref but its digest could not be read or is malformed" ;;
        *) abandon "pushed $ref but the digest file could not be written: $DIGEST_FILE" ;;
      esac
    fi
    # Fail closed: only an exact 404 (private) is acceptable after a push.
    code="$(hub_status)"
    if [[ $code == "200" ]]; then
      abandon "$IMAGE_REPO turned PUBLIC after pushing $ref"
    elif [[ $code != "404" ]]; then
      abandon "cannot confirm $IMAGE_REPO is still private after pushing $ref (HTTP $code)"
    fi
  done

  local ref
  for ref in "${PUSHED[@]}"; do
    echo "$ref"
  done
  echo "release: OK $VERSION (${#PUSHED[@]} images)"
}

main "$@"
