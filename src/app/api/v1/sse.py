"""SSE encoding for the single-response stream (03-api-spec §3.1 · D-10 ·
FR-91) — Phase 5.3-ب.

Turns the orchestrator's ``AgentEvent`` stream into the wire protocol the
contract fixes verbatim: ``event: <type>`` + ``data: <one-line JSON>`` frames,
UTF-8, a ``:keep-alive`` comment during idle gaps, and the stream CLOSED right
after the terminal ``final``/``error`` event. Phase 6 mounts this on the three
streaming POSTs (``/agents/{key}/invoke`` · ``/conversations/{id}/messages`` ·
``/workflows/{key}/run``); until then the encoder is proven over ASGI in
tests, which is exactly the surface those routers will hand it.

Division of labour, deliberate:

* **The orchestrator owns the TOTAL duration cap** (5.3-أ) — it sits under
  every transport. This module's only timer is the keep-alive cadence, which
  is per-IDLE-GAP and re-arms on every event: a heartbeat is not a deadline.
* **This module owns the RFC 9457 translation** of the in-band ``error``
  event (``api/errors.problem_from_event``): below the API layer failures
  travel as decision B1's ``{code, status, detail}``; 03 §3.1's example shows
  the SSE client receives a full problem object. Presentation work, API
  layer's job.
* **The encoder enforces the contract's termination clause** ("الإنهاء بحدث
  ``final`` أو ``error`` ثم إغلاق التدفّق") rather than trusting the producer
  to stop: whatever a (buggy, foreign) producer might yield after its
  terminal event, the wire never carries it.

The source generator is ALWAYS closed in ``finally`` — the 4.7-c-2 family of
lessons applied at the transport boundary: Starlette calls ``aclose()`` on a
streaming response's body iterator when the client disconnects, and that
close must cascade into the orchestrator's metered generator NOW (running its
billing/dispose ``finally`` chain) rather than whenever the GC finds it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from app.api.errors import problem_from_event
from app.framework.agent_runtime.base_agent import AgentEvent
from app.framework.types import Json

# 03 §3.1: media type + the two response headers the contract names. The
# charset is explicit because the contract says "ترميز UTF-8" and proxies
# should not have to sniff it.
SSE_MEDIA_TYPE = "text/event-stream; charset=utf-8"
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    # Operational necessity for the 08 §2 topology (Nginx in front): without
    # it, proxy buffering coalesces frames and the "stream" arrives as one
    # lump at the end. Additive to the contract, not a deviation from it.
    "X-Accel-Buffering": "no",
}

# The contract's heartbeat: a comment line every 15s of idle keeps proxies
# and clients from reaping a healthy-but-quiet connection. A comment (":")
# is invisible to EventSource clients by SSE's own grammar.
KEEPALIVE_INTERVAL_S = 15.0
_KEEPALIVE_FRAME = b":keep-alive\n\n"

# The two event types that end the stream (03 §3.1's termination clause).
_TERMINAL_TYPES = frozenset({"final", "error"})


def encode_frame(event_type: str, data: Json) -> bytes:
    """One SSE frame, bytes, exactly as 03 §3.1 draws it.

    ``json.dumps`` with ``ensure_ascii=False`` (the contract's UTF-8 — Arabic
    deltas go over the wire as themselves, not ``\\uXXXX`` escapes) and
    compact separators (the contract's own examples: ``{"delta":"مرحب"}``).
    A JSON object serialises to ONE line — no embedded newlines — so a single
    ``data:`` line is always framing-safe.
    """
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_type}\ndata: {payload}\n\n".encode()


async def sse_stream(
    events: AsyncIterator[AgentEvent],
    *,
    correlation_id: str | None = None,
    keepalive_interval_s: float = KEEPALIVE_INTERVAL_S,
) -> AsyncIterator[bytes]:
    """Encode ``events`` as SSE frames, heartbeating through idle gaps.

    ``correlation_id`` rides into the terminal ``error`` problem object (the
    03 §3.1 example carries it) — the router holds the ``ExecutionContext``
    and passes its correlation id here.

    **The heartbeat must never cancel the pull.** 5.3-أ's per-pull
    ``asyncio.timeout`` is the wrong tool HERE, and a test proved it: on
    expiry it cancels the pending ``anext``, which unwinds the producer
    mid-thought — a heartbeat that kills the very stream it exists to keep
    alive. So the pull runs as its own task and the interval is an
    ``asyncio.wait`` timeout: on expiry the task keeps right on running, the
    comment frame goes out, and the SAME pull is waited on again. (Bounding
    the stream's total life is the orchestrator's deadline, not this timer.)
    """
    pull: asyncio.Task[AgentEvent] | None = None
    try:
        while True:
            if pull is None:
                pull = asyncio.ensure_future(anext(events))
            done, _pending = await asyncio.wait({pull}, timeout=keepalive_interval_s)
            if not done:
                yield _KEEPALIVE_FRAME
                continue
            finished, pull = pull, None
            try:
                event = finished.result()
            except StopAsyncIteration:
                # A producer that ends without a terminal event (not our
                # executor — it guarantees one) just ends: inventing a
                # synthetic `final` here would fabricate an outcome the run
                # never reported.
                break
            if event.type == "error":
                yield encode_frame(
                    "error", problem_from_event(event.data, correlation_id=correlation_id)
                )
                break
            yield encode_frame(event.type, event.data)
            if event.type in _TERMINAL_TYPES:
                break
    finally:
        # Runs on terminal break AND on client disconnect (Starlette acloses
        # the body iterator): cascade the close into the producer so its
        # billing/dispose `finally` chain runs now, not at GC. An in-flight
        # pull is cancelled and reaped FIRST — `aclose()` on a generator
        # whose `__anext__` is still running is a RuntimeError, and the
        # cancellation itself already unwinds the producer's frame.
        if pull is not None:
            pull.cancel()
            with contextlib.suppress(BaseException):
                await pull
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()
