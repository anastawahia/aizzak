"""Connection hub for live streaming sessions (5.3-ج · 03-api-spec §3.2 ·
07-nfr-slo §4).

The workspace-keyed registry behind two consumers that must never import each
other:

* the **WebSocket endpoint** (``app/api/v1/websocket/``) registers each
  authenticated session and unregisters it on disconnect;
* the **notification bridge** (5.3-د — the ``cg.notify`` consumer built by
  the Composition Root) pushes worker results to every live session of the
  event's ``workspaceid`` — 03 §3.2's "الجسر": the worker publishes a global
  event, the WS subscriber routes it to the workspace's sessions.

**Why it lives in the framework** and not next to the endpoint: the bridge's
assembly needs ``app.infrastructure`` (the Streams consumer engine), which
``.importlinter`` forbids ``app.api`` from importing — while the Composition
Root may import BOTH. A hub in the kernel is importable from each side of
that boundary, and carries no transport knowledge beyond the ``send_json``
shape a session exposes (``StreamSession`` is a structural Protocol; the
API layer's adapter over a real WebSocket satisfies it without the kernel
ever seeing Starlette).

**Two halves with deliberately different homes (P1-8, step 11).** *Routing*
is per process and stays here: ``_by_workspace`` holds the sockets THIS
process owns, and a session can only ever be written to by the process that
accepted it, so a per-process map is the correct and complete answer (P0-2
already fixed the other half of that — every sibling reads the notify stream
under its OWN group, ``docs/log/3.81.md``). *Admission control* is *not* per
process: ``_user_counts`` used to be a plain ``dict`` on this heap, which
made the announced ceiling of ``Limits.ws_connections_per_user`` really
"5 x gunicorn workers x replicas" — a user could open five sockets against
every worker without one refusal. The count therefore moved behind
``WsConnectionRegistry`` (``framework/ports/ws_connection_registry.py``), a
driven port the Composition Root binds to Redis; see that port's docstring
for the two properties its implementation owes.

**Concurrency model — one bounded outbox and one sender task per session
(3.6).** ``notify`` does not touch a socket. It builds the payload once and
``put_nowait``\\ s it into each live session's own queue; a per-session task
drains that queue and does the awaiting. Per-session send failures are still
swallowed and logged: a dying socket must not poison the notification path
for the workspace's other sessions — nor, in 5.3-د, the stream consumer
driving it.

*Why that indirection exists, measured rather than assumed.* ``notify`` used
to ``await session.send_json(...)`` in a ``for`` loop over a snapshot, which
made the fan-out only as fast as its SLOWEST member: an awaited send on a
socket whose peer has stopped reading does not fail, it parks — TCP applies
back-pressure, the buffers fill, and the coroutine waits for a drain that
needs the client to read. Measured on real sockets through the API's own
``_SocketSession`` adapter, three sessions in one workspace, one of them a
client that completes the handshake and then never reads: 300 notifications
that take **53 ms** to reach all three when nobody is slow delivered **21**
of 300 to each HEALTHY session, and nothing at all for the next 15 seconds,
while the fan-out sat inside one ``send_json``.

*And the blast radius was never one workspace.* The 5.3-د bridge dispatches
one event at a time off ``stream.knowledge``/``stream.media``, so a parked
``notify`` parks the CONSUMER: in the same measurement a reader in a
DIFFERENT workspace — a different tenant, whose events were interleaved with
the blocked one's — also received 21 of 300 and then silence. Tenant
isolation is the routing (below) and it held; availability is not routing,
and one browser tab that stopped reading took the whole process's
notification path down with it. A queue per session is what converts that
into "one session falls behind".

**The bound is the whole idea.** An unbounded queue does not fix a slow
consumer, it renames it: the memory the socket refuses to drain accumulates
on this heap instead, and the process dies of a client's bad wifi. So each
outbox holds ``session_queue_size`` payloads and no more; what happens at the
ceiling is argued under "Overflow policy" below.

``try_register``/``unregister`` are ``async`` because the count they mutate
lives in Redis. The atomicity the old synchronous form got for free ("no
``await`` between check and change") is bought back inside the registry's
single Lua script, NOT here — this class never reads a count and then decides
on it.

**Overflow policy — close 1013, not silent dropping.** The step's plan offers
either ("تُسقَط الإشعاراتُ غير الحرجة، أو يُغلَق الاتصال بـ1013"), and only
one of the two is implementable honestly here: dropping the *non-critical*
notifications presumes a criticality axis, and this hub carries none — every
payload it fans out is one of ``notifications.NOTIFY_EVENT_TYPES``, the four
worker results a live client is waiting on ("your document is indexed", "your
job failed"). Dropping one silently leaves a client showing a wrong world
with no signal that it is wrong, forever. Closing says so: 1013 (RFC 6455
"Try Again Later") is the code whose meaning is "come back", the client
reconnects and refetches, and the wrong world lasts one reconnect.

The close is best-effort and never on ``notify``'s path: the session is
dropped from routing SYNCHRONOUSLY (so the next event does not queue behind
it either), its sender task is cancelled — which is also what releases the
send lock the API adapter holds while parked inside a blocked write, so the
close frame has a chance to go out at all — and the close itself runs in its
own task under ``_CLOSE_TIMEOUT_S``, because a close on a socket that is not
draining can park exactly like the send did. The registry slot is NOT
released here: the endpoint's own ``finally`` still owns that, and it runs
when the socket dies.

**Redis failure policy — fail CLOSED on admission, swallow on release.** The
choice is explicit because both directions are wrong in some way and the
lesser wrong has to be argued, not assumed.

*Admission refuses.* Failing open would mean a Redis outage silently deletes
the cap — precisely the P1-8 defect this step exists to remove, reachable by
anyone who can degrade Redis. And a socket admitted while Redis is down is a
socket that cannot do its job anyway: the 5.3-د bridge that feeds it worker
results rides Redis Streams, so it would receive nothing (Redis is already a
hard boot dependency of this API — release-blockers-plan §5-ب ن-1). Refusing
costs a client a retry against a platform that is already degraded; admitting
costs the platform its only ceiling on concurrent sockets. The endpoint turns
the refusal into its ordinary close 1008, so a client sees the same outcome
as a genuine cap hit.

*Release swallows.* ``unregister`` is called from the endpoint's ``finally``
during teardown, where there is nothing left to retry with and where raising
would abort the rest of the teardown (cancelling the run tasks — which is
what BILLS an abandoned run, 5.3-أ/4.7). The leak this leaves is already
bounded by the same entry-ageing that covers a SIGKILLed process, so the
failure mode is "one slot returns late", not "one slot is lost".

**Who renews, and when (the leak the shared counter would otherwise have).**
A process that dies never releases its entries, so without ageing a crash
would spend a real user's slots forever. Entries therefore expire
individually after ``entry_ttl_s``, and this hub keeps its OWN live entries
young: ``start_renewal`` runs one background task per process that calls
``WsConnectionRegistry.renew`` ONCE for every session it currently holds
(3.6 — the port takes the whole tick's entries; it used to take one user,
and the loop that called it once per user cost a measured 489 ms of
sequential round trips at 1,500 sockets, against 16.5 ms batched), every
``renew_interval_s`` seconds, and ``stop_renewal`` cancels it. The API's
lifespan owns both ends (``api/main.py``'s ``startup=``/``shutdown=``
tuples), the same place the notify bridge's own lifecycle already lives. The
invariant that makes a long-lived socket safe is checked in ``__init__``:
``renew_interval_s < entry_ttl_s``, with the defaults leaving room for
several consecutive missed renewals (a Redis blip, a stalled loop) before a
genuinely live connection could ever be aged out of the count.

**Why ageing rather than the ``sweep_stale_notify_groups`` pattern** (P0-2's
orphan cleanup, ``framework/di/composition_root.py``): that sweep is
host-scoped by construction — it only inspects names carrying its OWN
hostname, because a pid means nothing across hosts. Under Compose/RunPod a
container's hostname is its container id, which CHANGES on every recreate, so
entries left by a container that is never recreated under the same name would
never be swept by anyone — a permanently spent slot. Ageing is bounded
regardless of whether the dead process's host ever comes back, so it is
strictly the stronger guarantee here; the sweep pattern remains the right one
for the notify groups it was built for, where an orphan costs only
bookkeeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Callable
from typing import NamedTuple, Protocol

from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.ports.ws_connection_registry import WsConnectionRegistry
from app.framework.types import Json, Uuid

_logger = get_logger(__name__)

# How long ONE registry entry survives without renewal, and how often a live
# entry is renewed. Neither is a contract number (07 §4 fixes the CEILING,
# not the bookkeeping) — they are hygiene bounds, the ``_AUTH_WINDOW_S``
# precedent in the WS endpoint. 90/20 leaves room for three consecutive
# missed renewals before a live socket could be aged out, and bounds a
# crashed process's leaked slot at 90 seconds.
DEFAULT_ENTRY_TTL_S = 90
DEFAULT_RENEW_INTERVAL_S = 20

# How many undelivered notifications ONE session may be behind by before the
# hub stops waiting for it (3.6). Not a contract number either, and it is a
# MEMORY bound first: a stuck session costs at most this many payloads on this
# heap, so the worst case is `sessions x this x payload`, not "whatever the
# client's wifi decides".
#
# The floor is what a healthy session must never lose: the notify bridge reads
# up to `EventSettings.consumer_batch_count` (16) events per stream read and
# dispatches them one after another, so a queue at or below 16 could drop a
# normal burst from a client that is merely scheduled late. 64 is four such
# batches -- room for a real burst, while a genuinely dead consumer is
# recognised within one.
DEFAULT_SESSION_QUEUE_SIZE = 64

# RFC 6455 1013 "Try Again Later": the close code whose meaning is "reconnect",
# which is exactly what a client that fell too far behind must do to get a
# correct view again (module docstring, "Overflow policy").
_OVERFLOW_CLOSE_CODE = 1013

# A close on a socket that is not draining can park exactly like the send that
# overflowed it, so every close this class initiates is bounded. Generous next
# to a healthy close (sub-millisecond on loopback), short next to `entry_ttl_s`.
_CLOSE_TIMEOUT_S = 5.0

# RFC 6455 1012 "Service Restart" — capacity step 7.2. A DIFFERENT statement
# from 1013 above, and the difference is what the client does with it: 1013
# ("Try Again Later") says *this* connection fell behind, 1012 says the SERVER
# is going away and every socket on it is going with it. Both mean reconnect;
# only one of them means "and so will everyone else on this replica", which is
# the fact `drain` exists to spread out. It is also the code uvicorn already
# sends on shutdown (measured in 3.3: 1012 at 0.10s), so a client that handles
# a rolling deploy at all already handles this one.
_RESTART_CLOSE_CODE = 1012

# How long a draining session is given to flush what it has already been
# handed, before the close frame goes out regardless. This is the "drain
# timeout" of step 7.2's own sequence, per session rather than global: a peer
# that stopped reading must not hold the deploy open, and a peer that is
# reading normally empties a 64-slot queue in microseconds.
_DRAIN_FLUSH_TIMEOUT_S = 2.0


class StreamSession(Protocol):
    """What the hub needs from a live session: the ability to receive one
    JSON message. Satisfied structurally by the API layer's WebSocket
    adapter (which owns locking/ordering around the real socket)."""

    async def send_json(self, payload: Json) -> None: ...

    async def close(self, *, code: int, reason: str) -> None: ...


class _Slot(NamedTuple):
    """What this process must remember about one admitted session to renew
    and release its registry entry: whose slot it is, and which entry."""

    user_id: Uuid
    connection_id: Uuid


class _Outbox(NamedTuple):
    """One session's bounded queue of undelivered payloads, and the task that
    drains it. Both die together: cancelling the sender without dropping the
    queue would leave a queue nothing empties, and dropping the queue without
    cancelling the sender would leave a task waiting on an object no producer
    can reach."""

    queue: asyncio.Queue[Json]
    sender: asyncio.Task[None]


class ConnectionHub:
    """Registry of live sessions per workspace, with the 07 §4 per-user cap
    enforced ACROSS processes.

    ``max_connections_per_user`` is injected (``Limits.ws_connections_per_user``
    — the number's one home is Settings, the 5.2-ب precedent), and the cap is
    counted per USER across workspaces, exactly as the limits table words it
    ("اتصالات WS/مستخدم"). ``registry`` is the cross-process counter (P1-8);
    see the module docstring for the failure policy and the renewal schedule.
    """

    def __init__(
        self,
        *,
        max_connections_per_user: int,
        registry: WsConnectionRegistry,
        entry_ttl_s: int = DEFAULT_ENTRY_TTL_S,
        renew_interval_s: int = DEFAULT_RENEW_INTERVAL_S,
        session_queue_size: int = DEFAULT_SESSION_QUEUE_SIZE,
    ) -> None:
        if renew_interval_s >= entry_ttl_s:
            # A wiring bug, caught where it is written rather than as a
            # mysterious disconnect-free eviction hours later: renewing no
            # more often than entries expire means a genuinely live socket
            # eventually stops being counted, and the cap silently loosens.
            raise ValueError(
                f"renew_interval_s ({renew_interval_s}) must be strictly less than "
                f"entry_ttl_s ({entry_ttl_s}) or live connections would age out of the cap"
            )
        if session_queue_size < 1:
            raise ValueError("session_queue_size must be at least 1")
        self._max_per_user = max_connections_per_user
        self._registry = registry
        self._entry_ttl_s = entry_ttl_s
        self._renew_interval_s = renew_interval_s
        self._session_queue_size = session_queue_size
        self._by_workspace: dict[Uuid, list[StreamSession]] = {}
        self._slots: dict[int, _Slot] = {}
        self._outboxes: dict[int, _Outbox] = {}
        # Fire-and-forget close tasks, held only so the loop cannot garbage
        # collect a task nobody awaits mid-flight (the documented asyncio
        # hazard); each removes itself when it finishes.
        self._closing: set[asyncio.Task[None]] = set()
        self._renewal: asyncio.Task[None] | None = None

    async def try_register(
        self, *, workspace_id: Uuid, user_id: Uuid, session: StreamSession
    ) -> bool:
        """Admit the session, or refuse it (``False``) when the user is at
        the cap — the endpoint turns a refusal into its close code. Never
        raises: admission control is an expected outcome, not an error, and a
        registry OUTAGE is refused for the reasons the module docstring
        argues (fail closed), not propagated as a 500.

        The claim itself is one atomic registry call; this method never reads
        a count and then decides on it, so two sockets arriving together
        cannot both be admitted into the last free slot.
        """
        connection_id = new_uuid7()
        try:
            admitted = await self._registry.try_acquire(
                user_id=user_id,
                connection_id=connection_id,
                max_connections=self._max_per_user,
                ttl_s=self._entry_ttl_s,
            )
        except AppError:
            _logger.warning(
                "hub.registry_unavailable_refusing_connection",
                extra={"workspace_id": workspace_id},
            )
            return False
        if not admitted:
            return False
        self._by_workspace.setdefault(workspace_id, []).append(session)
        self._slots[id(session)] = _Slot(user_id=user_id, connection_id=connection_id)
        queue: asyncio.Queue[Json] = asyncio.Queue(maxsize=self._session_queue_size)
        self._outboxes[id(session)] = _Outbox(
            queue=queue,
            sender=asyncio.create_task(self._drain(workspace_id, session, queue)),
        )
        return True

    async def unregister(self, *, workspace_id: Uuid, session: StreamSession) -> None:
        """Idempotent removal — the endpoint calls this from ``finally``, and
        a double call (or one for a session that lost its registration race)
        must be a no-op, not a KeyError during connection teardown.

        The LOCAL registry is updated first and without awaiting, so the
        routing map is consistent before this coroutine can be suspended;
        only the shared slot release crosses the wire, and a failure there is
        swallowed (module docstring) — the entry ages out on its own.

        **``_slots`` is what "registered" means, not ``_by_workspace`` (3.6).**
        A session that overflowed its outbox is dropped from ROUTING while its
        endpoint is still unwinding, so keying idempotence off the routing map
        would make that session's ``finally`` a no-op and leak its registry
        slot until the entry aged out — a slot the cap would spend on nobody.

        The outbox is torn down LAST, after the release has already happened,
        because cancelling the sender can cancel THIS coroutine when the caller
        IS the sender (a session whose ``send_json`` unregisters itself — the
        snapshot-mutation case the suite pins).
        """
        self._drop_from_routing(workspace_id, session)
        slot = self._slots.pop(id(session), None)
        if slot is None:
            self._close_outbox(session)
            return
        try:
            await self._registry.release(user_id=slot.user_id, connection_id=slot.connection_id)
        except AppError:
            _logger.warning(
                "hub.registry_unavailable_on_release",
                extra={"workspace_id": workspace_id},
            )
        self._close_outbox(session)

    async def disconnect_user(self, user_id: Uuid) -> None:
        """Close every live socket for one user owned by this process.

        The durable account state and shared denylist protect every replica;
        this local eviction makes an already-open socket stop immediately on
        the process that handles the admin request. Its normal endpoint
        teardown unregisters and releases each shared connection slot.
        """
        sessions = tuple(
            session
            for workspace_sessions in self._by_workspace.values()
            for session in workspace_sessions
            if (slot := self._slots.get(id(session))) is not None and slot.user_id == user_id
        )
        for session in sessions:
            # Bounded, and the outbox is torn down FIRST: an admin eviction
            # must not inherit the very stall this step exists to remove — a
            # session parked mid-write holds the API adapter's send lock, and
            # a `close` behind that lock would park the admin request too
            # (3.6; `_CLOSE_TIMEOUT_S` is the ceiling on how long one dead
            # socket may cost the caller).
            self._close_outbox(session)
            try:
                await asyncio.wait_for(
                    session.close(code=1008, reason="account disabled"),
                    timeout=_CLOSE_TIMEOUT_S,
                )
            except (Exception, TimeoutError):
                _logger.warning("hub.user_disconnect_failed", extra={"user_id": user_id})

    async def drain(
        self,
        *,
        window_s: float,
        jitter: Callable[[], float] | None = None,
    ) -> int:
        """Ask every socket this process holds to come back later, SPREAD OVER
        ``window_s`` — capacity step 7.2, and the half its own text says is the
        one that gets forgotten.

        Returns immediately with the number of sessions scheduled; the closes
        themselves run as background tasks. That is deliberate: the caller is a
        deploy script holding an HTTP request open, and a drain that only
        answers once the last slow peer has gone would make the script's own
        timeout the real drain window.

        ⭐ WHY SPREADING IS THE WHOLE POINT, AND WHY IT IS NOT COSMETIC. A
        replica carrying five hundred sockets that closes them in one tick
        produces five hundred simultaneous reconnects, and they do not arrive
        at the replica that just left — they arrive at the ones still serving.
        The next replica in the rollout then meets that herd on top of its own
        share of traffic, fails or slows, sheds ITS sockets, and the herd grows.
        Step 7.2's acceptance criterion names exactly this ("no reconnect peak
        exceeding accept capacity"), and `3.5` measured what the accept queue
        does when it is exceeded: it does not refuse, it silently drops the
        final ACK, so the herd shows up as latency nobody can attribute.

        ⚠️ UNIFORM RANDOM DELAYS, NOT AN EVENLY SPACED COMB. A comb is easy to
        write and is the wrong shape: every client here reconnects on the same
        exponential backoff, so a deterministic spacing can line up with it and
        rebuild the very peak it was meant to break. Drawing each session's
        delay independently from ``[0, window_s)`` gives an arrival process
        with no period for a backoff to resonate with. ``jitter`` is injectable
        for exactly one reason — a test that must assert the ORDER of the three
        steps below cannot also be asking a random number generator what it
        will do.

        The per-session order is `_evict`'s, for `_evict`'s reasons, plus one
        step that is only correct here:

        1. **Flush first, bounded.** The session keeps receiving notifications
           for its whole delay — a socket that is going to be asked to
           reconnect in eight seconds should still get the eight seconds of
           events it would otherwise have got. Only then is what it has already
           been handed given ``_DRAIN_FLUSH_TIMEOUT_S`` to reach the wire.
           `_evict` cannot do this: there the queue is full BECAUSE the peer
           stopped reading, so waiting on it is waiting forever.
        2. **Stop routing, then cancel the sender**, so nothing new is queued
           and the API adapter's single send/close lock is free.
        3. **Close 1012, bounded**, in the caller's own task rather than a
           detached one — this coroutine IS the detached task.
        """
        sessions = tuple(
            (workspace_id, session)
            for workspace_id, workspace_sessions in self._by_workspace.items()
            for session in workspace_sessions
        )
        if not sessions:
            return 0
        draw = random.random if jitter is None else jitter
        for workspace_id, session in sessions:
            delay = max(0.0, window_s) * draw()
            task = asyncio.create_task(self._drain_one(workspace_id, session, delay))
            self._closing.add(task)
            task.add_done_callback(self._closing.discard)
        _logger.info(
            "hub.drain_started",
            extra={"sessions": len(sessions), "window_s": window_s},
        )
        return len(sessions)

    async def _drain_one(self, workspace_id: Uuid, session: StreamSession, delay: float) -> None:
        """One session's share of the drain. Every await here is bounded, and
        every failure is swallowed: a deploy must not be held up, nor aborted,
        by one peer that has stopped reading its socket."""
        await asyncio.sleep(delay)
        outbox = self._outboxes.get(id(session))
        if outbox is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(outbox.queue.join(), timeout=_DRAIN_FLUSH_TIMEOUT_S)
        self._drop_from_routing(workspace_id, session)
        self._close_outbox(session)
        try:
            await asyncio.wait_for(
                session.close(code=_RESTART_CLOSE_CODE, reason="server restarting"),
                timeout=_CLOSE_TIMEOUT_S,
            )
        except (Exception, TimeoutError):
            # The endpoint's own `finally` owns the registry slot either way,
            # and the process is about to exit regardless.
            _logger.warning("hub.drain_close_failed", extra={"workspace_id": workspace_id})

    def user_connection_count(self, user_id: Uuid) -> int:
        """Connections THIS process holds for one user — introspection for
        tests and the operator's future health surface (07 §7). Deliberately
        NOT the cap's number since P1-8: the cap is counted across every
        sibling process, and ``global_user_connection_count`` is the answer to
        that question."""
        return sum(1 for slot in self._slots.values() if slot.user_id == user_id)

    async def global_user_connection_count(self, user_id: Uuid) -> int:
        """Connections held by one user across EVERY process sharing this
        registry — the number the cap is actually enforced against."""
        return await self._registry.count(user_id=user_id, ttl_s=self._entry_ttl_s)

    def workspace_session_count(self, workspace_id: Uuid) -> int:
        return len(self._by_workspace.get(workspace_id, ()))

    # ------------------------------------------------------------------ #
    # Renewal — the anti-leak half (module docstring: "Who renews, when")  #
    # ------------------------------------------------------------------ #
    async def start_renewal(self) -> None:
        """Start this process's renewal loop (a lifespan ``startup=`` hook).
        Idempotent: a lifespan re-entered in tests must not leave two loops
        racing on the same sessions."""
        if self._renewal is not None and not self._renewal.done():
            return
        self._renewal = asyncio.create_task(self._renew_loop())

    async def stop_renewal(self) -> None:
        """Cancel and REAP the renewal loop (a lifespan ``shutdown=`` thunk).
        Reaping matters: the loop's last act may be an in-flight Redis call,
        and the shutdown sequence closes that client right after this."""
        task = self._renewal
        self._renewal = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task

    async def renew_once(self) -> None:
        """One renewal pass over every session this process holds — public so
        a test can drive the anti-leak mechanism deterministically instead of
        sleeping on the loop's period.

        **One call for the whole tick (3.6), not one per user.** Grouping by
        user was already here — a user with several sockets has always cost one
        entry list, not one per socket — but the tick then AWAITED that list
        once per user, so its cost grew with the number of connected users and
        did so invisibly, inside a ``for`` loop in this method. Measured at the
        plan's 1,500 live sockets held by 1,500 distinct users: **489 ms** of
        sequential round trips every 20 seconds, for work the registry
        completes in **16.5 ms** when it is handed the whole tick (that is 1
        pipeline; the adapter ships 15, chunked, for the reason its
        ``_RENEW_CHUNK`` comment measures). At the cap's other shape — 1,500
        sockets held by 300 users at five each — the same tick was 102 ms.

        What that costs in exchange, stated rather than hidden: a registry
        outage now fails the tick as a whole instead of user by user. The
        containment it replaces was never real — a driver failure here is a
        connection-level failure, so the ``for`` loop's next user was going to
        raise too — and the consequence of a failed tick is unchanged either
        way: entries keep their previous age, and ``entry_ttl_s`` is several
        ticks wide precisely so a lost one is survivable.
        """
        by_user: dict[Uuid, list[Uuid]] = {}
        for slot in list(self._slots.values()):
            by_user.setdefault(slot.user_id, []).append(slot.connection_id)
        if not by_user:
            return
        try:
            await self._registry.renew(entries=by_user, ttl_s=self._entry_ttl_s)
        except AppError:
            _logger.warning("hub.registry_unavailable_on_renew")

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self._renew_interval_s)
            await self.renew_once()

    async def notify(self, workspace_id: Uuid, event_type: str, data: Json) -> None:
        """Push one ``notification`` message (03 §3.2's exact shape) to every
        live session of ``workspace_id``.

        Tenant isolation is the ROUTING here: only the named workspace's
        sessions are even considered. Unknown workspace ⇒ silent no-op — the
        normal case for an event about a tenant with nobody connected.

        **This method never touches a socket (3.6).** It enqueues. There is no
        ``await`` between the snapshot and the last ``put_nowait``, so the
        fan-out cannot be suspended part-way and no session can be delayed by
        another session's peer.

        It then yields ONCE, after the last enqueue — and that single
        ``sleep(0)`` is load-bearing, not politeness. The bridge dispatches a
        whole stream read one event after another, and a ``notify`` that never
        suspends never lets the sender tasks it just fed run: the outboxes
        would then fill at the PRODUCER's pace, and a perfectly healthy
        session could hit the ceiling simply because 65 events were dispatched
        without the consumer awaiting anything in between. With the yield, the
        depth measures what it is meant to measure — how far behind the SOCKET
        is — so eviction means a peer that stopped reading, never a peer that
        was merely not scheduled yet.

        *What the caller loses, deliberately:* ``notify`` returning no longer
        means "delivered", so the bridge ``XACK``\\ s an event whose payload is
        still in a queue. That was already the standing policy for this path
        and is argued where it was decided — ``streaming/notifications.py``: a
        notification has no durable effect, a push failure never fails the
        handler, and the durable record of what happened lives in the
        producing module's tables, never in whether a UI toast landed. What
        changes is only WHERE the undeliverable payload is dropped.
        """
        sessions = self._by_workspace.get(workspace_id)
        if not sessions:
            return
        payload: Json = {"type": "notification", "event": event_type, "data": data}
        for session in list(sessions):
            outbox = self._outboxes.get(id(session))
            if outbox is None:  # unregistered between the snapshot and here
                continue
            try:
                outbox.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # The session is `session_queue_size` notifications behind:
                # not slow, stopped. It stops being routed to RIGHT HERE, so
                # the next event does not queue behind it either, and it is
                # told to come back (module docstring, "Overflow policy").
                _logger.warning(
                    "hub.session_outbox_full_closing",
                    extra={
                        "workspace_id": workspace_id,
                        "event_type": event_type,
                        "queued": outbox.queue.qsize(),
                    },
                )
                self._evict(workspace_id, session)
        # After the fan-out, never inside it (docstring above).
        await asyncio.sleep(0)

    # ------------------------------------------------------------------ #
    # Outboxes — the per-session half of the 3.6 slow-consumer policy     #
    # ------------------------------------------------------------------ #
    async def _drain(
        self, workspace_id: Uuid, session: StreamSession, queue: asyncio.Queue[Json]
    ) -> None:
        """One session's sender: the only place in this class that awaits a
        socket, and it awaits exactly one session's.

        A send failure is swallowed and logged, unchanged from before the
        queues existed: a dying socket's cleanup belongs to its endpoint's
        ``finally``, and here it must not cost the workspace's other sessions
        their notification (nor, in 5.3-د, fail the stream consumer). The task
        keeps draining afterwards rather than exiting, so a socket that fails
        every write still empties its queue instead of filling it — the
        overflow path is for a peer that never reads, not for one that errors.
        """
        while True:
            payload = await queue.get()
            try:
                await session.send_json(payload)
            except Exception:
                _logger.warning("hub.notify_send_failed", extra={"workspace_id": workspace_id})
            finally:
                queue.task_done()

    def _drop_from_routing(self, workspace_id: Uuid, session: StreamSession) -> None:
        """Stop notifying this session, keeping ``_by_workspace`` free of
        empty lists. Synchronous and idempotent — every caller needs the map
        to be correct before it can be suspended."""
        sessions = self._by_workspace.get(workspace_id)
        if sessions is None or session not in sessions:
            return
        sessions.remove(session)
        if not sessions:
            del self._by_workspace[workspace_id]

    def _close_outbox(self, session: StreamSession) -> None:
        """Cancel a session's sender and drop its queue, freeing whatever it
        still held. Self-aware: a sender that reaches this through its own
        ``send_json`` (the self-unregistering session the suite pins) requests
        its own cancellation and returns, rather than awaiting itself."""
        outbox = self._outboxes.pop(id(session), None)
        if outbox is not None:
            outbox.sender.cancel()

    def _evict(self, workspace_id: Uuid, session: StreamSession) -> None:
        """The overflow path: stop routing, cancel the sender, then ask the
        socket to come back later — in that order, and none of it awaited by
        the caller.

        Cancelling before closing is load-bearing: the API's adapter holds ONE
        lock across ``send_json`` and ``close``, so while the sender is parked
        inside a blocked write the close frame cannot even be attempted. The
        close then runs in its own bounded task because a socket that will not
        drain a notification will not necessarily drain a close either — and
        the slot it holds is NOT released here: that stays the endpoint's
        ``finally``, which runs when the socket actually dies.
        """
        self._drop_from_routing(workspace_id, session)
        self._close_outbox(session)
        task = asyncio.create_task(self._close_slow_session(session))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _close_slow_session(self, session: StreamSession) -> None:
        try:
            await asyncio.wait_for(
                session.close(code=_OVERFLOW_CLOSE_CODE, reason="notification backlog"),
                timeout=_CLOSE_TIMEOUT_S,
            )
        except (Exception, TimeoutError):
            # Nothing left to try: the peer is not reading its socket, and the
            # endpoint's teardown owns the registry slot either way.
            _logger.warning("hub.slow_session_close_failed")

    async def deliver_pending(self) -> None:
        """Wait until every queued payload has been handed to its session —
        public for the same reason ``renew_once`` is: a test must be able to
        drive this mechanism deterministically instead of sleeping.

        Not a production seam, and deliberately not a "flush before shutdown":
        a session whose peer has stopped reading never drains, so this waits
        for exactly as long as the slowest peer takes. Callers that cannot
        wait forever are the ones with the timeout.
        """
        outboxes = list(self._outboxes.values())
        if outboxes:
            await asyncio.gather(*(outbox.queue.join() for outbox in outboxes))

    def session_backlog(self, workspace_id: Uuid, session: StreamSession) -> int:
        """How many notifications this session has not been handed yet —
        introspection for the operator surface (07 §7) and for tests that
        assert the bound HOLDS rather than that a queue exists."""
        outbox = self._outboxes.get(id(session))
        return 0 if outbox is None else outbox.queue.qsize()
