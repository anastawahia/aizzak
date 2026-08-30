"""The Compose worker topology, and the `WORKER` default RunPod still needs,
must not drift -- from each other, from the dispatcher that defines the valid
names, or from the evidence that justifies which workers boot by default
(release-blockers-plan.md §3, step 3 · pre-release-review.md P0-4 ·
stream-topology-plan.md §6-أ).

⭐ RESHAPED when `worker` was split into `worker-memory` / `worker-knowledge` /
`worker-media` (docs/log/3.133.md). What this file guarded before was a single
parameterised service whose `WORKER: ${WORKER:-<name>}` fallback had to agree
with RunPod's. That service is gone, and with it BOTH drift vectors it had:

  * Compose now hardcodes a literal `WORKER` per service, so the second,
    subtler defect this file was written for -- Compose auto-loads `.env`, and
    `.env.example`'s own `WORKER=knowledge` beat the inline `${WORKER:-...}`
    fallback on every documented deployment, provable only by reading
    `docker compose config` rather than the file -- cannot recur at all. It is
    designed out, not merely fixed, and `test_compose_worker_values_are_literal`
    keeps it that way.
  * The two deployment paths no longer default to "the same worker" because
    Compose no longer defaults to ANY worker; it runs all the evidenced ones.
    The surviving cross-path invariant is weaker but real, and asserted below:
    RunPod's default must be a name Compose declares AND one Compose is
    willing to boot unprofiled.

What replaces the old value check is the rule the split was granted under: a
worker rides in the default `docker compose up -d` **iff** its containerised
boot has been measured. `memory` (138 s, RestartCount 0) and `knowledge`
(≥ 5 min, RestartCount 0, its first ever container boot) were measured in
docs/log/3.105.md; `media` (45 min 46 s, RestartCount 0, one live consumer
registered on `cg.media`) in docs/log/3.134.md, which is what deleted the last
`profiles:` key in the file.

⭐ So `_UNMEASURED` is now EMPTY, and it is deliberately not modelled as an
empty constant with an `else` branch guarding it: a branch nothing can enter is
not a guard, and this repo's own rule is that every token must be killable.
What survives is stronger and fully live -- the set of workers Compose boots
WITHOUT a profile must equal `_MEASURED` exactly, which fails the moment a
`profiles:` key reappears on any of the three. A future fourth worker that has
never been run is added to `_RUNNERS` but NOT to `_MEASURED`, and
`test_the_patterns_actually_find_something` fails until someone decides which
it is -- that is the branch, and it lives in the ledger, not in an `if`.

Same shape as `test_ops_provision.py` (7.1) and `test_api_conventions.py`
(6.3-ج): documents drifted and nothing compared them. This compares them.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml

from app.framework.settings import EventSettings, Limits
from app.infrastructure.config import load_settings
from app.workers.bootstrap import knowledge_stale_idle_ms

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_ENTRYPOINT = _REPO_ROOT / "deploy" / "runpod" / "entrypoint.sh"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_DISPATCHER = _REPO_ROOT / "src" / "app" / "workers" / "main.py"

# `export WORKER="${WORKER:-memory}"`.
_ENTRYPOINT_WORKER_DEFAULT = re.compile(r'export WORKER="\$\{WORKER:-([a-z_]+)\}"')
# A plain dotenv assignment: `WORKER=memory`, start of line.
_ENV_EXAMPLE_WORKER = re.compile(r"^WORKER=([a-z_]+)\s*$", re.MULTILINE)
# A `_RUNNERS` entry in the dispatcher: `"knowledge": knowledge_worker.run,`.
# Read as TEXT on purpose -- importing app.workers.main would drag in every
# worker module (and its settings) to learn four dictionary keys.
_RUNNERS_ENTRY = re.compile(r'^\s+"([a-z_]+)":\s+\w+\.run,\s*$', re.MULTILINE)

# `_RUNNERS` also dispatches `outbox_relay`, which 08 §4 lists among "أوامر
# العمّال" but D-26 runs as its own single-instance service with its own
# command -- it is not one of the three Streams consumers this topology is
# about.
_NOT_A_STREAMS_WORKER = frozenset({"outbox_relay"})

# The ledger: workers whose containerised boot has been MEASURED, and which
# therefore ride in the default `docker compose up -d`. `memory` + `knowledge`
# in docs/log/3.105.md, `media` in docs/log/3.134.md. A name is added here by
# ONE clean measured boot -- logged, with a consumer registration to prove the
# process is consuming and not merely alive -- and by nothing else.
_MEASURED = frozenset({"memory", "knowledge", "media"})

# The SECOND ledger, and the one wave 6 of
# `summarization-scenarios-implementation-plan.md` added: how many replicas
# each worker runs, listing only those that run more than one. The owner's
# answer to that plan's §10 ق-و was (ب) -- a second hand on the knowledge
# loop rather than a fourth worker on a stream of its own.
#
# **The rule for adding a name here.** A worker may run more than one replica
# only when its longest LEGITIMATE handler cannot outlive its own death
# threshold. Rule 2 of `consumers/sweeper.py` lets any consumer reclaim the
# messages of a sibling that has been idle past that threshold, and a
# consumer's idle clock runs for the whole of a handler (the engine beats
# between messages, never during one). For the knowledge worker that reclaim
# would start a SECOND build of a summary already being built, which nothing
# downstream refuses -- `SummaryJob.start` is deliberately re-entrant.
#
# `knowledge` clears the rule because `F-4` derived its threshold from the
# build cap instead of leaving it on the shared 900 s -- asserted below, and
# by calculation over five configurations in `test_workers_bootstrap.py`.
# `media` and `memory` still read the shared number and their longest
# handlers sit well under it (`media_timeout_s` is 300 s, memory's is
# shorter), so replicating either would probably be safe too and is
# deliberately still a decision rather than a default.
_REPLICATED = {"knowledge": 2}

# `${HEARTBEAT_DIR:-/tmp/aizzak-heartbeat}` -- what Compose resolves with no
# `.env` present, which is what the shipped file promises.
_INTERPOLATED_DEFAULT = re.compile(r"^\$\{[A-Z_]+:-(.*)\}$")

# RunPod is ONE container running ONE worker chosen by `WORKER` (supervisord,
# no Compose), so it still needs a default, and it still must be the worker
# with live evidence behind it: a default is a promise to an operator who
# typed nothing.
_EXPECTED_RUNPOD_DEFAULT = "memory"


def _compose() -> dict[str, object]:
    # PyYAML resolves the `<<: [*app-env, *worker-env]` merge keys, so what
    # comes back per service is what Compose itself resolves -- verified
    # against `docker compose config` in §3.133.
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _compose_worker_services() -> dict[str, dict]:
    services = _compose()["services"]  # type: ignore[index]
    return {name: body for name, body in services.items() if name.startswith("worker-")}


def _dispatcher_streams_workers() -> frozenset[str]:
    names = frozenset(_RUNNERS_ENTRY.findall(_DISPATCHER.read_text(encoding="utf-8")))
    assert names, (
        "src/app/workers/main.py: could not find any `_RUNNERS` entry -- did its shape change?"
    )
    return names - _NOT_A_STREAMS_WORKER


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


def _replicated_services() -> dict[str, dict]:
    return {
        name: body
        for name, body in _compose_worker_services().items()
        if int(body.get("deploy", {}).get("replicas", 1)) > 1
    }


def _heartbeat_dir(body: dict) -> str:
    raw = str(body["environment"]["HEARTBEAT_DIR"])
    match = _INTERPOLATED_DEFAULT.match(raw)
    return match.group(1) if match else raw


def _mount_targets(body: dict) -> list[str]:
    """Where this service mounts something, in either Compose spelling."""
    targets: list[str] = []
    for entry in body.get("volumes", ()):
        if isinstance(entry, str):
            parts = entry.split(":")
            targets.append(parts[1] if len(parts) > 1 else parts[0])
        else:
            targets.append(str(entry.get("target", "")))
    return targets


def test_the_patterns_actually_find_something() -> None:
    """A guard whose regex silently matches nothing would pass forever while
    guarding nothing (the 3.69 lesson, restated in test_ops_provision.py)."""
    assert _dispatcher_streams_workers() == _MEASURED, (
        f"app/workers/main.py dispatches {sorted(_dispatcher_streams_workers())} "
        f"but the measured-boot ledger holds {sorted(_MEASURED)}. If a worker "
        "was just added: run it once in a container, log the measurement, and "
        "add it here -- or, if it has NOT been run, reintroduce the profiled "
        "branch this file dropped in §3.134 rather than widening the ledger."
    )
    assert _compose_worker_services()
    assert _entrypoint_worker_default() in _MEASURED
    assert _env_example_worker() in _MEASURED


def test_compose_declares_one_service_per_streams_worker() -> None:
    """08 §2 asks for three consumers, "each its own process so one module's
    backlog cannot starve another's". A single service parameterised by
    `WORKER` could not deliver that at all -- two `up`s with two values are
    the same container recreated -- so the declared topology was unreachable
    until the split (stream-topology-plan.md §6-أ). This asserts it is now
    reachable: one service per name, no name unserved, none invented."""
    services = _compose_worker_services()
    declared = {name: body["environment"]["WORKER"] for name, body in services.items()}
    assert set(declared.values()) == _dispatcher_streams_workers(), (
        f"docker-compose.yml declares workers {sorted(declared.values())} but "
        f"app/workers/main.py::_RUNNERS dispatches "
        f"{sorted(_dispatcher_streams_workers())} -- a name in the dispatcher "
        "with no service never runs on Compose; a service naming an unknown "
        "worker exits with SystemExit at boot."
    )
    assert len(declared) == len(set(declared.values())), (
        f"two Compose services select the same worker: {declared} -- two "
        "consumers in one group is a valid Streams topology but not one "
        "anything here declares, so it is far more likely a copy-paste."
    )
    for service_name, worker in declared.items():
        assert service_name == f"worker-{worker}", (
            f"service {service_name!r} selects worker {worker!r} -- the name "
            "an operator types must say which worker they get."
        )


def test_compose_worker_values_are_literal() -> None:
    """The defect that only `docker compose config` revealed, designed out.
    While the value was `${WORKER:-...}`, Compose's auto-loaded `.env` -- and
    `.env.example`, which every deployment guide copies to `.env` as step one
    -- silently won over the file, so what the YAML said and what the stack
    ran were different things. A literal cannot be overridden by `.env` at
    all, and the operator who wants another value now names another service
    instead of mutating a variable."""
    for service_name, body in _compose_worker_services().items():
        value = body["environment"]["WORKER"]
        assert "$" not in str(value), (
            f"{service_name}: WORKER={value!r} is interpolated. Compose "
            "auto-loads `.env`, so this file would stop being the truth "
            "about what runs -- read `docker compose config`, not the YAML, "
            "before trusting any interpolated value here."
        )


def test_a_worker_boots_unprofiled_iff_its_boot_was_measured() -> None:
    """The rule the split was granted under, made executable, in the one
    direction that is still reachable. Evidence, not capability, decides what
    boots by default -- all three have built a fully wired worker for a long
    time, and each still had to be run once before it was allowed into
    `docker compose up -d`. Now that all three are measured, the live content
    of the rule is that no `profiles:` key may come back: a profiled
    `knowledge` means `up -d` uploads files and never indexes them, a profiled
    `media` means image jobs sit in the stream forever, and in both cases
    nothing in any log says so -- the stack looks entirely healthy."""
    unprofiled = {
        body["environment"]["WORKER"]
        for body in _compose_worker_services().values()
        if not body.get("profiles")
    }
    assert unprofiled == _MEASURED, (
        f"`docker compose up -d` boots workers {sorted(unprofiled)} but the "
        f"measured-boot ledger says {sorted(_MEASURED)}. Fewer: a profile came "
        "back onto a worker that earned its place by measurement, and the jobs "
        "it consumes will now queue silently. More: an unmeasured worker was "
        "let into the default stack, where `restart: unless-stopped` turns its "
        "first crash into a stack that looks broken."
    )


def test_only_a_ledgered_worker_runs_more_than_one_replica() -> None:
    """A replica count is not a tuning knob here: it is a claim that this
    worker's messages may be reclaimed by a sibling without harm. The ledger
    above carries the rule; this asserts nothing has been replicated past
    it, in either direction -- a count dropped back to one would silently
    restore the head-of-line blocking wave 6 exists to lift."""
    running = {
        body["environment"]["WORKER"]: int(body["deploy"]["replicas"])
        for body in _replicated_services().values()
    }
    assert running == _REPLICATED, (
        f"docker-compose.yml runs more than one replica of {sorted(running)} "
        f"but the ledger allows {sorted(_REPLICATED)}. Adding a worker: check "
        "its longest handler against the threshold its own `stale_idle_ms` "
        "is built from, exactly as `F-4` had to for `knowledge`, and only "
        "then widen the ledger. Removing one: a worker back at a single "
        "replica has a single loop again, and one long handler blocks "
        "everything else on its stream for as long as it runs."
    )


def test_a_replicated_worker_declares_nothing_that_forbids_a_second_container() -> None:
    """Two keys make `replicas` a lie rather than an error: `container_name`
    (Compose refuses to scale a service that names its container) and a
    published `ports` mapping (the second container would collide on the
    host port). Neither is here today, and both are the kind of thing added
    for a good local reason by someone not thinking about the replica."""
    for service_name, body in _replicated_services().items():
        assert "container_name" not in body, (
            f"{service_name}: a service that names its container cannot be "
            "scaled -- Compose fails the `up` outright."
        )
        assert not body.get("ports"), (
            f"{service_name}: publishing a host port and running two "
            "replicas cannot both be true; the second container fails to "
            "bind. Workers listen on nothing, which is why this is free."
        )


def test_a_replicated_worker_keeps_its_heartbeat_off_a_shared_mount() -> None:
    """The failure this one exists for is silent, which is why it is a test
    and not a comment. Each replica's beat file must stay in its own
    writable layer (`framework/observability/heartbeat.py`: "never a
    mount"), because the healthcheck reads a PATH, not a process -- put that
    path on a shared volume and a dead replica's check passes on its live
    sibling's beat, which is ت-7's orphan exactly: a container reported
    healthy while running nothing."""
    for service_name, body in _replicated_services().items():
        beat = PurePosixPath(_heartbeat_dir(body))
        for target in _mount_targets(body):
            mount = PurePosixPath(target)
            assert mount != beat and mount not in beat.parents, (
                f"{service_name}: {target!r} mounts the heartbeat directory "
                f"({beat}) into more than one container, so a dead replica "
                "would be reported healthy on a live one's beat."
            )


def test_the_replicated_workers_build_cannot_be_stolen_by_its_sibling() -> None:
    """The property that licenses the whole key, asserted from the deploy
    side rather than only from the code side. `test_workers_bootstrap.py`
    proves the derivation holds across configurations; what is asserted HERE
    is that the shipped defaults would NOT have held without it -- 900 s
    shared against a build allowed 1,800 s. That gap is not a margin, it is
    the sweep window: for the last fifteen minutes of every long build the
    sibling would have been entitled to claim the message and build the same
    summary again.

    Imported rather than read as text, unlike the dispatcher above: what is
    under test is an arithmetic relation between two settings, and only the
    function that computes it can answer for it.
    """
    assert "knowledge" in _REPLICATED

    settings = load_settings()
    assert knowledge_stale_idle_ms(settings) > settings.limits.summarize_job_max_duration_s * 1000

    shipped_shared_ms = int(EventSettings().consumer_stale_idle_s * 1000)
    shipped_cap_ms = Limits().summarize_job_max_duration_s * 1000
    assert shipped_shared_ms < shipped_cap_ms, (
        "the shared stale threshold now clears the build cap on its own, so "
        "this test no longer proves the derivation is load-bearing -- check "
        "whether `F-4`'s derivation is still what protects the replica "
        "before trusting the pair."
    )


def test_runpod_default_agrees_with_env_example() -> None:
    """`.env.example` is what every documented setup copies to `.env` as step
    one, and RunPod's entrypoint sources it. Two files naming two different
    workers means the image runs one thing and its own template says another."""
    assert _env_example_worker() == _entrypoint_worker_default(), (
        f".env.example sets WORKER={_env_example_worker()!r} but "
        f"deploy/runpod/entrypoint.sh defaults it to "
        f"{_entrypoint_worker_default()!r} -- the single-container path runs "
        "one worker; both files must name the same one."
    )


def test_runpod_default_is_a_worker_compose_boots_unprofiled() -> None:
    """The cross-path invariant that SURVIVES the split. Compose no longer
    has a default worker to agree with -- it runs every evidenced one -- so
    what must still hold is weaker and still real: the single worker RunPod
    picks for an operator who typed nothing has to be one Compose declares,
    and one Compose is willing to boot without an opt-in profile. Anything
    else means the two deployment paths disagree about which worker is safe
    to run unattended."""
    default = _entrypoint_worker_default()
    unprofiled = {
        body["environment"]["WORKER"]
        for body in _compose_worker_services().values()
        if not body.get("profiles")
    }
    assert default in unprofiled, (
        f"deploy/runpod/entrypoint.sh defaults WORKER to {default!r}, but "
        f"Compose only boots {sorted(unprofiled)} without an explicit "
        "profile -- one path is running unattended what the other treats as "
        "needing a deliberate opt-in."
    )


def test_runpod_default_is_the_worker_whose_boot_is_best_proven() -> None:
    """Being in the unprofiled set is necessary but not sufficient: RunPod
    picks exactly ONE worker, and it should be the one with the longest live
    record, not merely a permissible one. `memory` has been booting in
    containers since docs/log/3.83.md; `knowledge`'s first container boot was
    2026-08-03. Changing this is a real decision, not a tidy-up."""
    assert _entrypoint_worker_default() == _EXPECTED_RUNPOD_DEFAULT
    assert _env_example_worker() == _EXPECTED_RUNPOD_DEFAULT
