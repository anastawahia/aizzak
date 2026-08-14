"""The interactive WebSocket endpoint — ``GET /api/v1/ws`` (03-api-spec §3.2 ·
D-10 · FR-90) — Phase 5.3-ج.

One socket, three client verbs, six server shapes, exactly as the contract
draws them:

* client → server: ``{"type":"invoke", "agent_key", "conversation_id",
  "input"}`` · ``{"type":"cancel", "conversation_id"}`` · ``{"type":"ping"}``
  (plus ``{"type":"auth", "token"}`` as the FIRST message when the token did
  not ride the handshake query string);
* server → client: ``token`` / ``tool_call`` / ``final`` (the agent event's
  ``data`` flattened beside ``type`` + ``conversation_id`` — the contract's
  own shapes), ``error`` (an RFC 9457 ``problem``, built by ``api.errors``),
  ``notification`` (pushed by the hub — the 5.3-د bridge's outlet), ``pong``.

**Auth before accept (03 §3.2: "يتحقّق قبل القبول").** A ``?token=`` present
at the handshake is verified BEFORE ``accept`` — a bad one rejects the
upgrade outright (the client sees an HTTP handshake failure, never a socket).
Without a query token the socket is accepted and the FIRST message must be
``auth`` within a short window; anything else closes 1008. The verifier sits
behind ``WsAuthenticator`` — a seam on purpose: mapping a Firebase token to a
``(workspace, user, roles)`` principal is Phase 6.4's JWT-guard work (JIT
seeding included), and this endpoint must not grow a second copy of it. 5.3
ships the protocol; 6.4 plugs in the real authenticator.

**Limits (07 §4, injected from ``Limits``):** a message over
``ws_message_bytes`` closes 1009 (the contract names the code); a user at
``ws_connections_per_user`` is refused at registration (close 1008).

**Concurrency model.** Each ``invoke`` runs as its OWN task streaming events
back over the socket, so the receive loop stays responsive — which is the
entire point of ``cancel`` (and why every server event carries its
``conversation_id``: concurrent runs interleave on one socket and the client
demultiplexes by thread). Sends are serialized by a per-socket lock so two
runs' frames never interleave mid-message. ``cancel`` targets the task by
``conversation_id`` (an invoke without one cannot be cancelled — nothing to
name it by; the contract's own cancel verb carries the id). A cancelled or
abandoned run is still billed: cancellation unwinds the orchestrator's
metered generator, whose ``finally`` captures what was consumed (5.3-أ/4.7
discipline) — nothing here needs to know that happens for it to happen.

**Pre-flight vs in-flight, over a socket.** The orchestrator's contract maps
cleanly: a pre-flight failure (unknown agent 404, quota 429, bad key 422)
raises BEFORE the first event and becomes an ``error`` message with the
problem's own code — the connection stays open, because unlike HTTP the
socket is long-lived and one bad invoke must not cost the client its other
running streams. An in-flight failure arrives as the executor's terminal B1
``error`` event and is translated the same way.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Protocol

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agents.orchestrator import AgentOrchestrator
from app.api.errors import problem, problem_from_error, problem_from_event
from app.api.v1.dependencies import Principal
from app.framework.agent_runtime.base_agent import AgentEvent, AgentRequest
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError
from app.framework.identifiers import new_uuid7
from app.framework.observability import get_logger
from app.framework.settings import Limits
from app.framework.streaming import ConnectionHub
from app.framework.types import Json
from app.modules.access.domain.value_objects import Permission
from app.modules.access.ports.inbound import AuthorizationService

_logger = get_logger(__name__)

# Close codes: 1008 = policy violation (failed auth, connection cap), 1009 =
# message too big — the contract names 1009 verbatim ("رسالة ≤ 64KB، إغلاق
# 1009"); 1008 is RFC 6455's code for a message violating the policy.
_POLICY_VIOLATION = 1008
_MESSAGE_TOO_BIG = 1009

# How long a tokenless handshake may take to send its `auth` message. Not a
# contract number (03 §3.2 fixes no window) — a hygiene bound so an
# unauthenticated socket cannot sit open indefinitely; generous next to any
# real client's immediate first send.
_AUTH_WINDOW_S = 10.0

_TERMINAL_TYPES = frozenset({"final", "error"})


# Who this socket speaks for — ONE type with the HTTP path since 6.4-أ. 5.3-ج
# declared its own three-field carrier because 6.1's `Principal` did not exist
# yet, and 6.1-a kept them apart to avoid touching shipped WS code; the moment a
# real verifier exists, two identical identities would mean two ways to be
# authenticated, and only one of them would be the one the guards read. The name
# survives as an alias because it is what this endpoint calls the thing.
WsPrincipal = Principal


class WsAuthenticator(Protocol):
    """Token → principal, or raise ``UnauthorizedError`` (any ``AppError``
    is treated as a refusal).

    Structurally identical to ``HttpAuthenticator`` (6.4-أ), and satisfied by
    the same ``ApiAuthenticator`` INSTANCE a deployment gives the HTTP path —
    a socket must not authenticate against a second JWKS cache.
    """

    async def authenticate(self, token: str) -> WsPrincipal: ...


class _SocketSession:
    """The hub-facing adapter over one accepted WebSocket.

    Owns the SEND LOCK: invoke tasks, pongs, error replies and hub
    notifications all funnel through here, so concurrent producers cannot
    interleave two messages on the wire. Satisfies ``StreamSession``
    structurally — the framework hub never sees Starlette.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send_json(self, payload: Json) -> None:
        async with self._lock:
            await self._websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def close(self, *, code: int, reason: str) -> None:
        async with self._lock:
            await self._websocket.close(code=code, reason=reason)


def create_ws_router(
    *,
    authenticator: WsAuthenticator,
    orchestrator: AgentOrchestrator,
    hub: ConnectionHub,
    limits: Limits,
    authorization: AuthorizationService,
) -> APIRouter:
    """Build the ``/ws`` router around injected collaborators.

    A factory rather than a module-level router because every collaborator
    is process-wide state the Composition Root owns (hub, orchestrator,
    authenticator) — module-level wiring would be a hidden global, and tests
    could not build two isolated apps side by side. Phase 6's ``main.py``
    mounts the result under ``settings.api_prefix``, making the wire path
    ``/api/v1/ws`` exactly as 03 §3.2 names it.
    """
    router = APIRouter()

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        connection = _Connection(
            websocket,
            authenticator=authenticator,
            orchestrator=orchestrator,
            hub=hub,
            limits=limits,
            authorization=authorization,
        )
        await connection.run()

    return router


class _Connection:
    """One accepted socket's whole lifetime: handshake → admission → the
    receive loop → teardown. Instantiated per connection (stateless module,
    per-request state on the instance — the house rule)."""

    def __init__(
        self,
        websocket: WebSocket,
        *,
        authenticator: WsAuthenticator,
        orchestrator: AgentOrchestrator,
        hub: ConnectionHub,
        limits: Limits,
        authorization: AuthorizationService,
    ) -> None:
        self._websocket = websocket
        self._authorization = authorization
        self._authenticator = authenticator
        self._orchestrator = orchestrator
        self._hub = hub
        self._limits = limits
        self._session = _SocketSession(websocket)
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._unnamed: set[asyncio.Task[None]] = set()

    async def run(self) -> None:
        principal = await self._handshake()
        if principal is None:
            return
        if not await self._hub.try_register(
            workspace_id=principal.workspace_id, user_id=principal.user_id, session=self._session
        ):
            # 07 §4: "اتصالات WS/مستخدم 5 — رفض تجاوز". The socket is already
            # accepted (auth may have needed a first message), so refusal is
            # a policy close, not a handshake rejection. Awaited since P1-8:
            # the count the hub decides on lives in Redis, so the ceiling is
            # the platform's and not each gunicorn worker's own copy of it.
            # A registry OUTAGE also lands here (the hub fails CLOSED — see
            # its module docstring), so a client meets the same 1008 it would
            # meet at a genuine cap hit.
            await self._websocket.close(code=_POLICY_VIOLATION, reason="connection limit reached")
            return
        try:
            await self._serve(principal)
        finally:
            # Teardown order matters: unregister FIRST so the notify path
            # stops targeting a dying socket, then cancel the run tasks —
            # each cancellation unwinds its metered stream, which is what
            # bills an abandoned run (send failures inside those tasks are
            # already swallowed by the pump).
            await self._hub.unregister(workspace_id=principal.workspace_id, session=self._session)
            outstanding = [*self._runs.values(), *self._unnamed]
            for task in outstanding:
                task.cancel()
            for task in outstanding:
                with contextlib.suppress(BaseException):
                    await task

    # ------------------------------------------------------------------ #
    # Admission                                                          #
    # ------------------------------------------------------------------ #
    async def _handshake(self) -> WsPrincipal | None:
        """03 §3.2's two admission paths; ``None`` = the socket is gone."""
        query_token = self._websocket.query_params.get("token")
        if query_token is not None:
            try:
                principal = await self._authenticator.authenticate(query_token)
            except AppError:
                # Verified BEFORE accept: the upgrade is refused outright and
                # the client never holds an open socket.
                await self._websocket.close(code=_POLICY_VIOLATION)
                return None
            await self._websocket.accept()
            return principal
        return await self._first_message_auth()

    async def _first_message_auth(self) -> WsPrincipal | None:
        await self._websocket.accept()
        try:
            async with asyncio.timeout(_AUTH_WINDOW_S):
                raw = await self._websocket.receive_text()
        except (TimeoutError, WebSocketDisconnect):
            with contextlib.suppress(RuntimeError):
                await self._websocket.close(code=_POLICY_VIOLATION, reason="auth required")
            return None
        message = _decode(raw)
        token = message.get("token") if message is not None else None
        if message is None or message.get("type") != "auth" or not isinstance(token, str):
            await self._websocket.close(code=_POLICY_VIOLATION, reason="auth required")
            return None
        try:
            return await self._authenticator.authenticate(token)
        except AppError:
            await self._websocket.close(code=_POLICY_VIOLATION)
            return None

    # ------------------------------------------------------------------ #
    # The receive loop                                                   #
    # ------------------------------------------------------------------ #
    async def _serve(self, principal: WsPrincipal) -> None:
        while True:
            try:
                raw = await self._receive_within_size()
            except WebSocketDisconnect:
                return
            if raw is None:
                return  # closed 1009, or the transport is gone
            message = _decode(raw)
            if message is None:
                await self._send_problem(
                    code="common.validation_error",
                    status=422,
                    detail="message is not a JSON object",
                )
                continue
            await self._dispatch(principal, message)

    async def _dispatch(self, principal: WsPrincipal, message: Json) -> None:
        kind = message.get("type")
        if kind == "ping":
            await self._session.send_json({"type": "pong"})
        elif kind == "invoke":
            await self._start_invoke(principal, message)
        elif kind == "cancel":
            conversation_id = message.get("conversation_id")
            task = self._runs.get(conversation_id) if isinstance(conversation_id, str) else None
            if task is not None:
                task.cancel()
        else:
            await self._send_problem(
                code="common.validation_error",
                status=422,
                detail=f"unknown message type {kind!r}",
            )

    async def _receive_within_size(self) -> str | None:
        """One frame, decoded to text, under the 64KB guard (close 1009).

        Byte length is measured on the WIRE form (bytes as received, text as
        UTF-8) — the limit exists to bound buffering, and that is the size
        that buffers.
        """
        message = await self._websocket.receive()
        if message.get("type") == "websocket.disconnect":
            raise WebSocketDisconnect(code=int(message.get("code") or 1000))
        text = message.get("text")
        data = text.encode() if isinstance(text, str) else message.get("bytes") or b""
        if len(data) > self._limits.ws_message_bytes:
            await self._websocket.close(code=_MESSAGE_TOO_BIG)
            return None
        if isinstance(text, str):
            return text
        try:
            return data.decode()
        except UnicodeDecodeError:
            return "\x00"  # never valid JSON ⇒ the uniform validation answer

    # ------------------------------------------------------------------ #
    # Invoke                                                             #
    # ------------------------------------------------------------------ #
    async def _start_invoke(self, principal: WsPrincipal, message: Json) -> None:
        agent_key = message.get("agent_key")
        conversation_id = message.get("conversation_id")
        payload = message.get("input")
        if (
            not isinstance(agent_key, str)
            or not agent_key
            or not isinstance(payload, dict)
            or not (conversation_id is None or isinstance(conversation_id, str))
        ):
            await self._send_problem(
                code="common.validation_error",
                status=422,
                detail="invoke requires agent_key (str), input (object), "
                "and an optional conversation_id (str)",
                conversation_id=conversation_id if isinstance(conversation_id, str) else None,
            )
            return

        # The transport-level half of RBAC (6.4-ب), and the reason it is written
        # HERE rather than left to the orchestrator: a route guard is a FastAPI
        # dependency, and a socket has no route — so without this line the one
        # permission every invoke needs (`agents:invoke`, 05 §4) would be
        # enforced on `POST /agents/{key}/invoke` and on nothing at all over
        # `/api/v1/ws`. The orchestrator adds the per-agent half from the
        # manifest; between them the two transports authorize identically,
        # which is the only arrangement in which neither is a way around the
        # other.
        if not self._authorization.is_allowed(principal.roles, Permission.AGENTS_INVOKE.value):
            await self._send_problem(
                code="authz.forbidden",
                status=403,
                detail=f"missing permission: {Permission.AGENTS_INVOKE.value}",
                conversation_id=conversation_id,
            )
            return

        ctx = ExecutionContext(
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            correlation_id=new_uuid7(),
            roles=principal.roles,
            request_id=None,
        )
        try:
            events = await self._orchestrator.invoke(
                ctx,
                agent_key,
                AgentRequest(conversation_id=conversation_id, input=payload, stream=True),
            )
        except AppError as exc:
            # Pre-flight: the socket-shaped 404/422/429 — this invoke failed,
            # the CONNECTION did not.
            await self._session.send_json(
                _error_message(
                    problem_from_error(exc, correlation_id=ctx.correlation_id), conversation_id
                )
            )
            return
        self._track(
            asyncio.create_task(self._pump(events, conversation_id, ctx.correlation_id, agent_key)),
            conversation_id,
        )

    def _track(self, task: asyncio.Task[None], conversation_id: str | None) -> None:
        if isinstance(conversation_id, str):
            # Last-in wins for a duplicate id: the id names the THREAD, and
            # cancel targets whichever run currently speaks for it.
            key = conversation_id
            self._runs[key] = task

            def _forget(done: asyncio.Task[None]) -> None:
                # Guarded so a finished PREDECESSOR never evicts the run that
                # superseded it under the same conversation id.
                if self._runs.get(key) is done:
                    self._runs.pop(key, None)

            task.add_done_callback(_forget)
        else:
            self._unnamed.add(task)
            task.add_done_callback(self._unnamed.discard)

    async def _pump(
        self,
        events: AsyncIterator[AgentEvent],
        conversation_id: str | None,
        correlation_id: str,
        agent_key: str,
    ) -> None:
        """Forward one run's events over the socket (its own task).

        The event's ``data`` is FLATTENED beside ``type``/``conversation_id``
        (03 §3.2's own message shapes: ``{"type":"token", …, "delta":"…"}``),
        with the envelope keys winning any collision. A send failure ends the
        pump quietly — the socket is dying and ``run``'s ``finally`` owns
        cleanup; ending this task closes the generator, which is what runs
        the orchestrator's billing/dispose chain.
        """
        try:
            async for event in events:
                if event.type == "error":
                    body = _error_message(
                        problem_from_event(event.data, correlation_id=correlation_id),
                        conversation_id,
                    )
                else:
                    body = {**event.data, "type": event.type, "conversation_id": conversation_id}
                await self._session.send_json(body)
                if event.type in _TERMINAL_TYPES:
                    break
        except asyncio.CancelledError:
            raise  # cancel/teardown: unwinding the generator bills the run
        except Exception:
            _logger.warning(
                "ws.pump_ended",
                extra={"agent_key": agent_key, "conversation_id": conversation_id},
                exc_info=True,
            )

    async def _send_problem(
        self,
        *,
        code: str,
        status: int,
        detail: str,
        conversation_id: str | None = None,
    ) -> None:
        # A protocol-level refusal (unparseable frame, unknown message type)
        # happens BEFORE any `ExecutionContext` exists, so there is no run
        # correlation id to borrow — but `components.schemas.Problem` lists
        # `correlation_id` as REQUIRED and 03 §4 says every error carries one.
        # 6.2-ب mints one per problem rather than emitting a malformed body:
        # the point of the field is that an operator can find THIS event in
        # the log, which a fresh id serves and an absent one does not.
        with contextlib.suppress(Exception):  # a dying socket ends the loop soon anyway
            await self._session.send_json(
                _error_message(
                    problem(code, status, detail=detail, correlation_id=new_uuid7()),
                    conversation_id,
                )
            )


def _decode(raw: str) -> Json | None:
    """A client frame as a JSON object, or ``None`` for anything else — one
    uniform malformed-input answer (the consumer engine's ``_decode``
    precedent)."""
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _error_message(problem_body: Json, conversation_id: str | None) -> Json:
    """03 §3.2's error shape; ``conversation_id`` only when there is one to
    name (the omit-absent rule, as everywhere in the API layer)."""
    body: Json = {"type": "error", "problem": problem_body}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    return body
