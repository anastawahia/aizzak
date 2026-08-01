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

**Concurrency model.** ``notify`` still fans out over a SNAPSHOT so a session
that unregisters mid-broadcast never breaks the iteration, and per-session
send failures are still swallowed and logged: a dying socket must not poison
the notification path for the workspace's other sessions — nor, in 5.3-د, the
stream consumer driving it. What changed: ``try_register``/``unregister`` are
now ``async``, because the count they mutate lives in Redis. The atomicity
the old synchronous form got for free ("no ``await`` between check and
change") is bought back inside the registry's single Lua script, NOT here —
this class never reads a count and then decides on it.

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
``WsConnectionRegistry.renew`` for every session it currently holds, every
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


class StreamSession(Protocol):
    """What the hub needs from a live session: the ability to receive one
    JSON message. Satisfied structurally by the API layer's WebSocket
    adapter (which owns locking/ordering around the real socket)."""

    async def send_json(self, payload: Json) -> None: ...


class _Slot(NamedTuple):
    """What this process must remember about one admitted session to renew
    and release its registry entry: whose slot it is, and which entry."""

    user_id: Uuid
    connection_id: Uuid


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
        self._max_per_user = max_connections_per_user
        self._registry = registry
        self._entry_ttl_s = entry_ttl_s
        self._renew_interval_s = renew_interval_s
        self._by_workspace: dict[Uuid, list[StreamSession]] = {}
        self._slots: dict[int, _Slot] = {}
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
        return True

    async def unregister(self, *, workspace_id: Uuid, session: StreamSession) -> None:
        """Idempotent removal — the endpoint calls this from ``finally``, and
        a double call (or one for a session that lost its registration race)
        must be a no-op, not a KeyError during connection teardown.

        The LOCAL registry is updated first and without awaiting, so the
        routing map is consistent before this coroutine can be suspended;
        only the shared slot release crosses the wire, and a failure there is
        swallowed (module docstring) — the entry ages out on its own.
        """
        sessions = self._by_workspace.get(workspace_id)
        if sessions is None or session not in sessions:
            return
        sessions.remove(session)
        if not sessions:
            del self._by_workspace[workspace_id]
        slot = self._slots.pop(id(session), None)
        if slot is None:
            return
        try:
            await self._registry.release(user_id=slot.user_id, connection_id=slot.connection_id)
        except AppError:
            _logger.warning(
                "hub.registry_unavailable_on_release",
                extra={"workspace_id": workspace_id},
            )

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

        Grouped per user so a user with several sockets costs ONE round trip,
        and a per-user failure is contained: an unreachable registry must not
        stop the OTHER users' entries from being kept young (the same "one
        dying party never costs the rest" rule ``notify`` already follows).
        """
        by_user: dict[Uuid, list[Uuid]] = {}
        for slot in list(self._slots.values()):
            by_user.setdefault(slot.user_id, []).append(slot.connection_id)
        for user_id, connection_ids in by_user.items():
            try:
                await self._registry.renew(
                    user_id=user_id, connection_ids=connection_ids, ttl_s=self._entry_ttl_s
                )
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
        """
        sessions = list(self._by_workspace.get(workspace_id, ()))
        if not sessions:
            return
        payload: Json = {"type": "notification", "event": event_type, "data": data}
        for session in sessions:
            try:
                await session.send_json(payload)
            except Exception:
                # A dying socket's cleanup belongs to its endpoint's finally;
                # here it must not cost the workspace's OTHER sessions their
                # notification (nor, in 5.3-د, fail the stream consumer).
                _logger.warning(
                    "hub.notify_send_failed",
                    extra={"workspace_id": workspace_id, "event_type": event_type},
                )
