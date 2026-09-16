"""What the notification fan-out costs Redis, measured rather than modelled
(capacity 5.4, ``docs/capacity-plan.md`` §5).

**The claim this tool exists to test.** Every API process runs its own
notification bridge under its own consumer group (``composition_root``'s
comment above ``_NOTIFY_STREAMS``, and the defect it fixes), so every entry on
``stream.knowledge`` / ``stream.media`` is delivered once PER PROCESS. At the
shipped topology -- three ``app`` replicas x ``WEB_CONCURRENCY=4`` (3.4) --
that is twelve deliveries of every event. The design is correct and 5.4 does
not propose changing it; what 5.4 says is that its cost "is proportional to
(events x processes) and has never been measured". This measures it.

**Two numbers, and the second is the one the plan's sentence misses.**

* **Fan-out cost** -- what N processes cost while events are actually
  flowing. Proportional to (events x processes), as the plan says.
* **Idle cost** -- what N processes cost while NOTHING is flowing. A bridge
  blocked in ``XREADGROUP ... BLOCK 5000`` is not free: it re-issues the read
  every ``consumer_block_ms``, plus a recovery read each time round
  (``RedisStreamsConsumer.read`` issues TWO ``XREADGROUP``s per pass, its own
  "Deviation" paragraph), so the floor is proportional to processes ALONE and
  a platform at zero events still pays it. ``--rate 0`` measures exactly that,
  and it is the number that decides whether twelve bridges are affordable on
  an idle Sunday, not just at peak.

**Why the measurement is a delta of ``INFO``, not a wrapper around a clock.**
``INFO commandstats`` gives per-command ``calls`` and total ``usec`` inside
Redis itself, ``INFO cpu`` gives the server process's own CPU, and ``INFO
stats`` gives bytes on the wire. Timing from the client would measure this
tool's event loop and the container network; these counters measure the thing
under test. Everything reported is ``after - before`` around one window.

**What it publishes, and why nothing durable happens.** Well-formed
``knowledge.document.indexed.v1`` / ``media.job.generated.v1`` envelopes --
the notify catalog's own types (``streaming/notifications.py``'s
``NOTIFY_EVENT_TYPES``). Each is delivered to every ``cg.notify.*`` group,
whose handler pushes to a hub with no sessions for the synthetic workspace and
returns; and to the static ``cg.knowledge`` / ``cg.media`` group, which does
not handle result types and so takes the engine's policy-3 path -- a plain
``XACK``, no DLQ entry and no ERROR log (``engine.py``'s ``handler is None``
branch). The notification bridge is deliberately NOT idempotent
(``notifications.py``), so not one Postgres round trip is involved either.
That is what makes this safe to run against a live stack: it measures the
fan-out and leaves nothing behind but stream entries the ``MAXLEN`` trim
reclaims.

**Two verbs.**

* ``census``  -- how many notify groups exist, on which streams, from how
  many hostnames, and how many of those hostnames are still alive. Reads
  only. This is the shape ``5.4``'s second half is graded on.
* ``measure`` -- run one window at ``--rate`` events/s for ``--seconds``, and
  print the deltas. ``--json`` writes the whole record for archiving beside
  the run's commit SHA, the ``0.5`` convention.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.framework.events.envelope import build_envelope
from app.infrastructure.cache.redis_cache import create_redis_client
from app.infrastructure.config import load_settings
from app.infrastructure.messaging.consumers.sweeper import (
    DEFAULT_STALE_IDLE_SECONDS,
    is_orphan,
    read_notify_groups,
)
from app.infrastructure.messaging.redis_streams import RedisStreamsConsumer, RedisStreamsPublisher

# The two streams the bridge subscribes to -- `composition_root._NOTIFY_
# STREAMS`' keys, with one of that stream's own notify types beside each.
# Written out for the same reason `sweeper.py` writes the group prefix out:
# `app.ops` may import `app.framework`, but importing the composition root
# here would drag the whole DI graph into a measurement tool. Guarded by a
# test that imports both instead.
NOTIFY_STREAM_TYPES: tuple[tuple[str, str], ...] = (
    ("stream.knowledge", "knowledge.document.indexed.v1"),
    ("stream.media", "media.job.generated.v1"),
)

# Reported individually; everything else lands in `other`. These are the
# commands the fan-out is actually made of -- the reads the bridges issue,
# the writes this tool issues, and the acks the deliveries produce.
_TRACKED = ("xreadgroup", "xadd", "xack", "xpending", "xinfo|groups", "xinfo|consumers", "xlen")


@dataclass(frozen=True, slots=True)
class RedisSample:
    """One ``INFO`` snapshot, thinned to what a delta of it can answer."""

    at: float
    cpu_sys_s: float
    cpu_user_s: float
    ops_per_sec: int
    net_in_bytes: int
    net_out_bytes: int
    used_memory: int
    commands: dict[str, tuple[int, float]] = field(default_factory=dict)

    @property
    def cpu_s(self) -> float:
        return self.cpu_sys_s + self.cpu_user_s


async def sample_redis(client: Redis) -> RedisSample:
    """``INFO cpu`` + ``INFO stats`` + ``INFO memory`` + ``INFO commandstats``
    as one record.

    Four calls rather than one ``INFO all`` deliberately: ``INFO all`` on this
    server costs ~270 us per call (measured in its own ``commandstats``),
    which is a real perturbation of the thing being measured when the window
    is short. Four narrow sections cost a fraction of that, and the ``INFO``
    calls this tool makes are themselves visible in the delta -- so they can
    be subtracted rather than guessed at.
    """
    cpu, stats, memory, commandstats = await asyncio.gather(
        client.info("cpu"),
        client.info("stats"),
        client.info("memory"),
        client.info("commandstats"),
    )
    commands = {
        name.removeprefix("cmdstat_"): (int(row["calls"]), float(row["usec"]))
        for name, row in commandstats.items()
    }
    return RedisSample(
        at=time.monotonic(),
        cpu_sys_s=float(cpu["used_cpu_sys"]),
        cpu_user_s=float(cpu["used_cpu_user"]),
        ops_per_sec=int(stats["instantaneous_ops_per_sec"]),
        net_in_bytes=int(stats["total_net_input_bytes"]),
        net_out_bytes=int(stats["total_net_output_bytes"]),
        used_memory=int(memory["used_memory"]),
        commands=commands,
    )


@dataclass(frozen=True, slots=True)
class Census:
    """Who is reading the notify streams right now, by group and by host."""

    groups: dict[str, int]
    hosts: dict[str, int]
    live_hosts: tuple[str, ...]
    dead_hosts: tuple[str, ...]
    orphans: tuple[str, ...]
    live_processes: int = 0

    @property
    def total_groups(self) -> int:
        return sum(self.groups.values())

    @property
    def processes(self) -> int:
        """Every distinct ``<host>.<pid>`` tag with a group on these streams,
        live or tombstoned. Counted per HOST-AND-PID, not per group: one
        process holds one group on each of the two notify streams, so
        counting groups would report twice the fleet.
        """
        return sum(self.hosts.values())


async def take_census(consumer: RedisStreamsConsumer, *, min_idle_ms: int) -> Census:
    """Group and host counts for every notify stream, with each host's
    verdict taken from the sweeper's OWN rule rather than a second copy of
    it -- a census that disagreed with ``app.ops.notify_groups list`` would be
    worse than no census."""
    streams = [stream for stream, _ in NOTIFY_STREAM_TYPES]
    groups = await read_notify_groups(consumer, streams)
    per_stream: Counter[str] = Counter()
    tags: dict[str, bool] = {}
    orphans: list[str] = []
    for group in groups:
        per_stream[group.stream] += 1
        orphan, _ = is_orphan(group, min_idle_ms=min_idle_ms)
        tag = group.process_tag or group.name
        # A process is dead only if EVERY group it holds says so: one live
        # reading anywhere outranks any number of quiet ones.
        tags[tag] = tags.get(tag, True) and orphan
        if orphan:
            orphans.append(f"{group.stream}/{group.name}")
    hosts: Counter[str] = Counter(tag.rsplit(".", 1)[0] for tag in tags)
    dead = sorted({tag.rsplit(".", 1)[0] for tag, orphan in tags.items() if orphan})
    return Census(
        groups=dict(per_stream),
        hosts=dict(hosts),
        live_hosts=tuple(sorted(set(hosts) - set(dead))),
        dead_hosts=tuple(dead),
        orphans=tuple(sorted(orphans)),
        # ⚠️ The denominator for every per-process ratio, and it is NOT
        # `processes`. A tombstoned group issues no `XREADGROUP` and costs
        # Redis nothing per second, so dividing by the total would credit the
        # fleet with readers that are not reading -- and on the stack this
        # tool was first run against, 13 of the 25 tags were tombstones. That
        # is a 2x error in the direction of "cheaper than it is".
        live_processes=sum(1 for tag, orphan in tags.items() if not orphan),
    )


def _envelope(stream: str, event_type: str, workspace_id: str) -> dict[str, Any]:
    """One synthetic notify event, built through the REAL ``build_envelope``
    so a malformed one fails here rather than as a DLQ entry on a live
    stack -- the same reason ``load_seed`` writes through the real
    repositories."""
    return build_envelope(
        event_id=str(uuid.uuid4()),
        source=f"app.ops.notify_cost/{socket.gethostname()}",
        event_type=event_type,
        subject=str(uuid.uuid4()),
        occurred_at=datetime.now(UTC),
        workspace_id=workspace_id,
        data={"measurement": "capacity-5.4", "stream": stream},
    )


async def publish_at_rate(
    publisher: RedisStreamsPublisher, *, rate: float, seconds: float, workspace_id: str
) -> tuple[int, float]:
    """Publish ``rate`` events/s for ``seconds``, alternating between the two
    notify streams. Returns ``(published, achieved_rate)``.

    **Paced against a deadline, never by sleeping a fixed interval.** A loop
    that sleeps ``1/rate`` after each ``XADD`` drifts by the publish latency
    on every iteration and lands well under the requested rate over a minute
    -- which would silently make every derived per-event number wrong in the
    flattering direction. Each entry has an absolute due time; the loop
    sleeps until it, and a publish that overruns simply does not sleep. The
    achieved rate is returned so the report can state it rather than restate
    the request.

    ``rate <= 0`` publishes nothing and returns immediately -- the idle-cost
    baseline, where the window is pure blocking-read churn.
    """
    if rate <= 0:
        await asyncio.sleep(seconds)
        return 0, 0.0

    interval = 1.0 / rate
    started = time.monotonic()
    published = 0
    while True:
        due = started + published * interval
        if due - started >= seconds:
            break
        now = time.monotonic()
        if due > now:
            await asyncio.sleep(due - now)
        stream, event_type = NOTIFY_STREAM_TYPES[published % len(NOTIFY_STREAM_TYPES)]
        await publisher.publish(stream, _envelope(stream, event_type, workspace_id))
        published += 1
    elapsed = time.monotonic() - started
    return published, published / elapsed if elapsed else 0.0


async def await_drain(
    consumer: RedisStreamsConsumer, *, skip: frozenset[str], timeout_s: float
) -> tuple[bool, float]:
    """Block until every LIVE group on the notify streams has caught up, or
    ``timeout_s``. Returns ``(drained, seconds_waited)``.

    The window has to close on DELIVERY, not on the last ``XADD``: the
    fan-out is the thing being measured, and it trails publication by up to
    one ``consumer_block_ms``. A run that timed out reports itself as such
    rather than quietly producing a number that undercounts the reads.

    **⚠️ ``lag``, not ``pending`` -- and getting this wrong makes the tool
    lie in the flattering direction.** ``pending`` is delivered-and-unacked,
    so a group that has read NOTHING reports ``pending == 0``: a drain check
    keyed on it returns "caught up" immediately, closes the window before a
    single bridge has read, and reports a fan-out cost near zero. ``lag`` is
    entries not yet delivered, which is the question actually being asked.

    **``skip`` carries the census's orphans, and it cannot be replaced by a
    consumer-count test.** A tombstoned group's lag grows forever and never
    drains -- nothing is reading it -- so waiting for one would time out every
    run on any stack carrying one. And ``consumers > 0`` does NOT identify a
    live group: a dead bridge's consumer entry survives it (the whole subject
    of ``sweeper.is_orphan``), which is exactly why the caller passes the
    verdicts the sweeper's own rule reached rather than re-deriving a weaker
    one here.

    ``lag is None`` (Redis cannot determine it) is skipped rather than treated
    as zero: unknown is not caught-up, but it is not a reason to hang either,
    and the report says how many groups were in that state.
    """
    started = time.monotonic()
    while True:
        behind = 0
        for stream, _ in NOTIFY_STREAM_TYPES:
            for info in await consumer.group_infos(stream):
                if f"{stream}/{info.name}" in skip or info.lag is None:
                    continue
                behind += info.lag
        waited = time.monotonic() - started
        if behind == 0:
            return True, waited
        if waited >= timeout_s:
            return False, waited
        await asyncio.sleep(0.25)


def _delta(before: RedisSample, after: RedisSample, *, census: Census) -> dict[str, Any]:
    """Everything the report says, computed in one place so the printed table
    and the archived JSON can never disagree."""
    window = after.at - before.at
    commands = {}
    for name in sorted(set(before.commands) | set(after.commands)):
        calls_before, usec_before = before.commands.get(name, (0, 0.0))
        calls_after, usec_after = after.commands.get(name, (0, 0.0))
        calls = calls_after - calls_before
        if calls > 0:
            commands[name] = {"calls": calls, "usec": round(usec_after - usec_before, 1)}
    tracked = {name: commands[name] for name in _TRACKED if name in commands}
    other_calls = sum(row["calls"] for name, row in commands.items() if name not in _TRACKED)
    other_usec = sum(row["usec"] for name, row in commands.items() if name not in _TRACKED)
    return {
        "window_s": round(window, 3),
        "redis_cpu_s": round(after.cpu_s - before.cpu_s, 3),
        "redis_cpu_pct": round(100.0 * (after.cpu_s - before.cpu_s) / window, 2) if window else 0.0,
        "net_in_bytes": after.net_in_bytes - before.net_in_bytes,
        "net_out_bytes": after.net_out_bytes - before.net_out_bytes,
        "used_memory_delta_bytes": after.used_memory - before.used_memory,
        "commands": tracked,
        "other_commands": {"calls": other_calls, "usec": round(other_usec, 1)},
        "processes": census.processes,
        "live_processes": census.live_processes,
        "notify_groups": census.total_groups,
    }


def _print_report(record: dict[str, Any]) -> None:
    census = record["census"]
    delta = record["delta"]
    rate = record["achieved_rate_per_s"]
    published = record["published"]
    processes = census["live_processes"] or 1

    print(
        f"topology     {census['live_processes']} live process(es) of {census['processes']} tagged,"
        f" {census['total_groups']} notify groups"
    )
    print(f"             live hosts {list(census['live_hosts'])}")
    if census["dead_hosts"]:
        dead = list(census["dead_hosts"])
        print(f"             ⚠️  dead hosts {dead} -- {len(census['orphans'])} orphan group(s)")
    print(f"window       {delta['window_s']} s   published {published} @ {rate:.1f}/s")
    if not record["drained"]:
        print("             ⚠️  NOT DRAINED before the deadline -- reads below are undercounted")
    print()
    print(f"redis cpu    {delta['redis_cpu_s']} s  ({delta['redis_cpu_pct']}% of one core)")
    print(f"net          in {delta['net_in_bytes']:,} B   out {delta['net_out_bytes']:,} B")
    print(f"memory       {delta['used_memory_delta_bytes']:+,} B")
    print()
    print(f"{'command':<18}{'calls':>10}{'usec':>14}{'usec/call':>12}")
    for name, row in delta["commands"].items():
        per = row["usec"] / row["calls"] if row["calls"] else 0.0
        print(f"{name:<18}{row['calls']:>10,}{row['usec']:>14,.0f}{per:>12.2f}")
    other = delta["other_commands"]
    print(f"{'(everything else)':<18}{other['calls']:>10,}{other['usec']:>14,.0f}")
    print()

    derived = record["derived"]
    if published:
        print(
            f"per event    {derived['redis_us_per_event']:.1f} us redis cpu"
            f"   {derived['reads_per_event']:.2f} xreadgroup"
            f"   {derived['out_bytes_per_event']:,.0f} B out"
        )
        print(
            f"per event x process ({processes})"
            f"   {derived['redis_us_per_event_process']:.2f} us"
            f"   {derived['out_bytes_per_event_process']:,.0f} B out"
        )
    print(
        f"idle floor   {derived['redis_us_per_process_s']:.1f} us/s of redis cpu per process"
        f"   ({derived['reads_per_process_s']:.2f} xreadgroup/s each)"
    )


def _derive(record: dict[str, Any]) -> dict[str, Any]:
    """The per-event and per-process ratios, kept out of the printer so the
    JSON carries them too -- an archived run has to be comparable to the next
    one without re-deriving anything by hand."""
    delta = record["delta"]
    published = record["published"]
    processes = record["census"]["live_processes"] or 1
    window = delta["window_s"] or 1.0
    reads = delta["commands"].get("xreadgroup", {"calls": 0})["calls"]
    cpu_us = delta["redis_cpu_s"] * 1_000_000
    per_event = cpu_us / published if published else 0.0
    return {
        "redis_us_per_event": per_event,
        "redis_us_per_event_process": per_event / processes,
        "reads_per_event": reads / published if published else 0.0,
        "out_bytes_per_event": delta["net_out_bytes"] / published if published else 0.0,
        "out_bytes_per_event_process": delta["net_out_bytes"] / published / processes
        if published
        else 0.0,
        # The floor: CPU and reads attributable to nothing but processes
        # existing. At `--rate 0` this is the whole measurement.
        "redis_us_per_process_s": cpu_us / processes / window,
        "reads_per_process_s": reads / processes / window,
    }


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = create_redis_client(settings.redis)
    consumer = RedisStreamsConsumer(client)
    min_idle_ms = int(args.min_idle_seconds * 1000)
    try:
        census = await take_census(consumer, min_idle_ms=min_idle_ms)
        if args.action == "census":
            print(f"notify groups   {census.total_groups}  {census.groups}")
            print(f"processes       {census.processes} tagged, {census.live_processes} live")
            print(f"live hosts      {len(census.live_hosts)}  {list(census.live_hosts)}")
            print(f"dead hosts      {len(census.dead_hosts)}  {list(census.dead_hosts)}")
            print(f"orphan groups   {len(census.orphans)}")
            for name in census.orphans:
                print(f"                {name}")
            return 0

        publisher = RedisStreamsPublisher(client, maxlen=settings.events.stream_maxlen)
        workspace_id = str(uuid.uuid4())
        before = await sample_redis(client)
        published, achieved = await publish_at_rate(
            publisher, rate=args.rate, seconds=args.seconds, workspace_id=workspace_id
        )
        drained, drain_s = await await_drain(
            consumer, skip=frozenset(census.orphans), timeout_s=args.drain_timeout_s
        )
        after = await sample_redis(client)

        record: dict[str, Any] = {
            "measured_at": datetime.now(UTC).isoformat(),
            "requested_rate_per_s": args.rate,
            "requested_seconds": args.seconds,
            "achieved_rate_per_s": achieved,
            "published": published,
            "workspace_id": workspace_id,
            "drained": drained,
            "drain_wait_s": round(drain_s, 3),
            "census": {
                "groups": census.groups,
                "total_groups": census.total_groups,
                "processes": census.processes,
                "live_processes": census.live_processes,
                "live_hosts": list(census.live_hosts),
                "dead_hosts": list(census.dead_hosts),
                "orphans": list(census.orphans),
            },
            "delta": _delta(before, after, census=census),
        }
        record["derived"] = _derive(record)
        _print_report(record)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump(record, handle, indent=2)
            print(f"\nwrote {args.json}")
        return 0
    finally:
        await client.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ops.notify_cost",
        description="Measure what the cg.notify fan-out costs Redis (capacity 5.4).",
    )
    parser.add_argument(
        "--min-idle-seconds",
        type=float,
        default=DEFAULT_STALE_IDLE_SECONDS,
        help=f"liveness threshold for the census (default {DEFAULT_STALE_IDLE_SECONDS})",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("census", help="how many notify groups and processes exist right now")

    measure = sub.add_parser("measure", help="run one window and print the Redis deltas")
    measure.add_argument(
        "--rate",
        type=float,
        default=100.0,
        help="events per second to publish (default 100; 0 measures the idle floor)",
    )
    measure.add_argument("--seconds", type=float, default=60.0, help="window length (default 60)")
    measure.add_argument(
        "--drain-timeout-s",
        type=float,
        default=30.0,
        help="how long to wait for every group's pending list to empty (default 30)",
    )
    measure.add_argument("--json", help="also write the whole record here, for archiving")
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    raise SystemExit(asyncio.run(_run(_build_parser().parse_args())))


if __name__ == "__main__":
    main()
