"""The `WORKER` default must not drift between the two deployment paths, nor
between a file and the auto-loaded `.env` that actually overrides it
(release-blockers-plan.md §3, step 3 · pre-release-review.md P0-4).

The defect this guards: `docker-compose.yml`'s `worker` service defaulted
`WORKER` to `knowledge`, a value that crash-looped forever under
`restart: unless-stopped` (it was blocked on `DocumentContentResolver` back
then -- a debt since closed by step 16 of `docs/deferred-adapters-plan.md`,
which changes nothing about this guard: see `_EXPECTED_WORKER_DEFAULT`
below) -- while `deploy/runpod/entrypoint.sh` defaulted
the SAME variable to `memory`, the one value that actually boots. An operator
running `docker compose --profile workers up worker` with no override got a
different, broken worker than one running the RunPod image unmodified, and
the crash loop looked like a platform fault rather than the honest, 18-line
comment sitting right above it explained.

⭐ A SECOND drift, found only by running `docker compose --profile workers
config` and reading what it actually resolves to (fixing the inline
`${WORKER:-...}` fallback alone was NOT enough): Compose auto-loads a `.env`
file from the project root for variable substitution, and `.env.example`
-- the tracked template every deployment guide has an operator `cp` to
`.env` as step one (`docs/quickstart.md`, `deploy-linux-server.md`,
`deploy-runpod.md`) -- carried its own `WORKER=knowledge`. That value, not
docker-compose.yml's inline fallback, is what a real `.env` makes Compose
resolve to on every documented deployment; the fallback only ever fires for
an operator who deletes the line or never runs `cp .env.example .env` at
all. So this module checks THREE sources, not two.

Same shape as `test_ops_provision.py` (7.1) and `test_api_conventions.py`
(6.3-ج): documents drifted and nothing compared them. This compares them.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

# `WORKER: ${WORKER:-memory}` under the `worker:` service's `environment:` key.
_COMPOSE_WORKER_DEFAULT = re.compile(r"\bWORKER:\s*\$\{WORKER:-([a-z_]+)\}")
# `export WORKER="${WORKER:-memory}"`.
_ENTRYPOINT_WORKER_DEFAULT = re.compile(r'export WORKER="\$\{WORKER:-([a-z_]+)\}"')
# A plain dotenv assignment: `WORKER=memory`, start of line.
_ENV_EXAMPLE_WORKER = re.compile(r"^WORKER=([a-z_]+)\s*$", re.MULTILINE)

# The three names `app/workers/main.py::_RUNNERS` actually dispatches to.
# `media` is a real, valid name that simply crash-loops today (its own missing
# `MediaGenerator`, not a deploy defect); `knowledge` stopped crash-looping at
# step 16 of `docs/deferred-adapters-plan.md`.
_VALID_WORKER_NAMES = frozenset({"knowledge", "media", "memory"})
# The default all three deployment sources must agree on. `memory` is the one
# worker whose boot is PROVEN live inside containers (2.10 closed its
# EmbeddingProvider gap; `docs/log/3.83.md` measured the round trip).
# `knowledge` is wired but never yet booted, and `media` still has no
# `MediaGenerator` at all -- so the default stays here until a real knowledge
# boot earns the change (docker-compose.yml's comment argues it in full).
_EXPECTED_WORKER_DEFAULT = "memory"


def _compose_worker_default() -> str:
    text = _COMPOSE.read_text(encoding="utf-8")
    match = _COMPOSE_WORKER_DEFAULT.search(text)
    assert match is not None, (
        "docker-compose.yml: could not find the worker service's "
        "`WORKER: ${WORKER:-<name>}` line -- did its shape change?"
    )
    return match.group(1)


def _entrypoint_worker_default() -> str:
    text = _ENTRYPOINT.read_text(encoding="utf-8")
    match = _ENTRYPOINT_WORKER_DEFAULT.search(text)
    assert match is not None, (
        "deploy/runpod/entrypoint.sh: could not find "
        '`export WORKER="${WORKER:-<name>}"` -- did its shape change?'
    )
    return match.group(1)


def _env_example_worker() -> str:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    match = _ENV_EXAMPLE_WORKER.search(text)
    assert match is not None, (
        ".env.example: could not find a top-level `WORKER=<name>` line -- did its shape change?"
    )
    return match.group(1)


def test_the_regexes_actually_find_a_default() -> None:
    """A guard whose regex silently matches nothing would pass forever while
    guarding nothing (the 3.69 lesson, restated in test_ops_provision.py) --
    every source must resolve to one of the three real worker names."""
    assert _compose_worker_default() in _VALID_WORKER_NAMES
    assert _entrypoint_worker_default() in _VALID_WORKER_NAMES
    assert _env_example_worker() in _VALID_WORKER_NAMES


def test_compose_fallback_and_runpod_defaults_agree() -> None:
    compose_default = _compose_worker_default()
    entrypoint_default = _entrypoint_worker_default()
    assert compose_default == entrypoint_default, (
        f"docker-compose.yml's inline fallback defaults WORKER to {compose_default!r} but "
        f"deploy/runpod/entrypoint.sh defaults it to {entrypoint_default!r} -- "
        "an operator following one deployment path gets a different worker "
        "than one following the other. Keep them equal, or make one path "
        "require an explicit value with a clear failure message instead of a "
        "silent, differing default."
    )


def test_env_example_agrees_with_the_compose_fallback() -> None:
    """The one that actually matters in practice: Compose auto-loads `.env`,
    and `.env.example` is what every documented setup copies to `.env` as
    step one -- so ITS value, not docker-compose.yml's inline fallback, is
    what a real deployment resolves to. `docker compose --profile workers
    config` proved this live: fixing the inline fallback alone still
    resolved `WORKER: knowledge` until this file's `.env.example` line was
    fixed too."""
    assert _env_example_worker() == _compose_worker_default(), (
        f".env.example sets WORKER={_env_example_worker()!r}, which OVERRIDES "
        f"docker-compose.yml's inline fallback ({_compose_worker_default()!r}) "
        "for every operator who ran `cp .env.example .env` -- run `docker "
        "compose --profile workers config` and read what WORKER actually "
        "resolves to, not just the fallback text, before trusting this."
    )


def test_default_is_the_worker_whose_boot_is_actually_proven() -> None:
    """Agreeing with each other is necessary but not sufficient: all three
    sources could still agree on a NAME THAT CRASH-LOOPS. `media` is a valid
    worker name (`app/workers/main.py::_RUNNERS`) that nonetheless crash-loops
    forever under `restart: unless-stopped`, and `knowledge` -- wired since
    step 16 but never once booted -- is evidence-free either way. So the
    default must specifically be `memory`, not merely a name in the set."""
    assert _compose_worker_default() == _EXPECTED_WORKER_DEFAULT
    assert _entrypoint_worker_default() == _EXPECTED_WORKER_DEFAULT
    assert _env_example_worker() == _EXPECTED_WORKER_DEFAULT
