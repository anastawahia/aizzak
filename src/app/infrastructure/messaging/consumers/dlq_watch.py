"""Periodic reporting of what is sitting in the dead-letter queues -- the
code half of ت-6 (``docs/operational-findings.md`` §6).

**The gap this closes, exactly as it was measured.** On 2026-08-15 the live
stack held one entry on ``stream.memory.dlq``, dead-lettered on 2026-08-03
(``handler_failed: NotFoundError``, five deliveries). Nothing had reported it
in the twelve days between: there is no consumer group on any ``*.dlq``
stream -- deliberately, see the note below -- so no ``lag``/``pending``
counter covers them, and the one signal that WOULD have (``aizzak_dlq_depth``,
``api/metrics.py``) is scraped by nothing today, since no Prometheus service
exists in ``docker-compose.yml`` (``deploy/prometheus/alerts.yml``'s own
"NOT wired into any running Prometheus" note, still true). The DLQ was
therefore observable only by an operator who thought to run ``python -m
app.ops.dlq peek`` -- i.e. by remembering to ask a question nothing prompts.

**Why a log line rather than a new service.** The infrastructure decision
this platform already made ("لا بنية تحتيّة جديدة الآن، كاشط لاحقاً" -- no new
infrastructure now, a scraper later) is not reopened here. What changes is
that the platform now says the thing out loud, on its own, forever, instead of
waiting to be asked: every ``dlq_watch_interval_s`` each worker reads the DLQs
of the streams IT consumes and emits one ``dlq.backlog`` WARNING per non-empty
one, carrying the depth, the oldest entry's id, its AGE, and its ``reason``.
When a scraper does arrive it reads the same fact off ``/metrics`` and fires
``AizzakDlqNotEmpty``; neither replaces the other, and this one needs nothing
deployed to work.

**Silence is the healthy state.** An empty DLQ logs nothing at all -- the
liveness of the loop that does the reading is already covered by the ت-3
heartbeat (``ops/healthcheck.py``), so an "all clear" line here would be pure
volume, and volume is exactly what makes a real warning unreadable.

**Why the age, not just the depth (the actual defect in §6).** The finding's
sharpest sentence is not "there is an entry"; it is that a permanently
non-empty DLQ makes «DLQ is not empty» read as NORMAL, at which point the
signal is worth nothing. A watcher that only ever said "depth=1" would
reproduce that failure faithfully. Reporting how long the oldest entry has
been sitting there makes an ageing backlog visibly different from a fresh
one -- and it is the number an operator triages by.

**Why no consumer group on the DLQ streams, ever.** §6 records the absence as
part of the finding, so the decision not to add one is explicit rather than
inherited: a consumer group implies a CONSUMER, and an automatic DLQ consumer
has only two possible behaviours, both wrong. Re-injecting entries onto the
source stream automatically re-runs the exact handler that already failed
``MAX_RETRIES_BEFORE_DLQ`` times -- a poison loop, and one that would have
re-run the measured entry above roughly 3,400 times on a five-minute cadence
for a memory item that no longer exists. Draining them anywhere else is
deletion with extra steps. The DLQ is a queue whose only correct consumer is a
human with ``python -m app.ops.dlq`` (P1-4), so what was missing was never a
consumer: it was somebody telling the human.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.framework.observability import get_logger
from app.infrastructure.messaging.redis_streams import DlqBacklog, RedisStreamsConsumer

_logger = get_logger(__name__)


async def report_dlq_backlog(
    consumer: RedisStreamsConsumer, *, streams: Iterable[str]
) -> list[DlqBacklog]:
    """Read every ``<stream>.dlq`` once and log one ``dlq.backlog`` WARNING
    per NON-EMPTY queue; return those same non-empty backlogs so a caller (and
    a test) can assert on what was found rather than on a call count.

    ``streams`` are SOURCE stream names (``dlq_backlog`` derives the ``.dlq``
    suffix itself, the same way ``dead_letter`` does when writing). Duplicates
    are collapsed -- a worker whose subscriptions name the same stream under
    two groups must not double-report it.

    Reads only (``XLEN`` + a one-entry ``XRANGE``): this function can never
    move, ack, requeue or delete anything, which is what makes it safe to run
    unattended on a timer inside a live worker.
    """
    found: list[DlqBacklog] = []
    for stream in dict.fromkeys(streams):
        backlog = await consumer.dlq_backlog(stream)
        if backlog.is_empty:
            continue
        found.append(backlog)
        _logger.warning(
            "dlq.backlog",
            extra={
                "stream": backlog.stream,
                "dlq": f"{backlog.stream}.dlq",
                "depth": backlog.depth,
                "oldest_entry_id": backlog.oldest_entry_id,
                "oldest_age_s": round(backlog.oldest_age_s or 0.0, 1),
                "oldest_reason": backlog.oldest_reason,
                # The one thing an operator reading this line needs next, in
                # the line itself -- P1-4's tool, already installed in every
                # image, with the SOURCE stream name it takes.
                "triage": f"python -m app.ops.dlq peek {backlog.stream}",
            },
        )
    return found
