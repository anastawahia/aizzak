"""The Agents router — ``/api/v1/agents`` (03-api-spec §1 · FR-100) — Phase
6.1-b, completed by 6.1-ج-3.

Three routes, each a THIN delegate (FR-100: "no business logic in the API
layer"):

* ``GET /agents`` — the workspace's agent catalog, from ``AgentRegistry.list``,
  wrapped in the ``API-04`` ``Page`` envelope (unpaginated ⇒ ``next_cursor:
  null``);
* ``GET /agents/{key}`` — one manifest, bare, or a 404;
* ``POST /agents/{key}/invoke`` — hand the request to
  ``AgentOrchestrator.invoke`` and stream its events as SSE.

**Both invoke shapes are live as of 6.1-ج-3.** SSE (``stream: true`` or
``Accept: text/event-stream``) hands the orchestrator's event iterator to the
03 §3.1 encoder; otherwise ``invoke_once`` drains the same turn and the reply
is ``AgentInvokeOut`` — the thread the turn landed in, the assistant message as
PERSISTED, and the meter's real ``prompt``/``completion`` split. Nothing on
that reply is synthesised: the withheld DTO of 6.1-b was withheld precisely
because those three facts did not exist yet.

**Pre-flight failures answer with a real status.** ``invoke`` is awaited
BEFORE the ``StreamingResponse`` is constructed, so an unknown agent (404), a
bad request shape (422), or an exhausted quota (429) is RAISED while the HTTP
status is still open and the app's RFC 9457 handler (6.1-a) turns it into a
problem response — never a 200 body that then contradicts itself. Once the
first event is yielded the status is committed and failures travel in-band as
the SSE terminal ``error`` frame, which the encoder owns.

**Auth on every route** via the router-level ``current_principal`` dependency
(03 §0: bearer on all paths but ``/health``). ``list``/``get`` need only that
gate; ``invoke`` additionally builds the ``ExecutionContext`` its delegate call
carries. RBAC (``required_permissions``) is Phase 6.4's guard — the manifests
already publish the permissions each agent will demand.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.agents import AgentInvokeIn, AgentInvokeOut, AgentOut, Usage
from app.api.v1.dto.pagination import Page, PageMeta
from app.api.v1.routers.conversations import appended_to_message_out, wants_sse
from app.api.v1.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_stream
from app.framework.agent_runtime.base_agent import AgentRequest
from app.framework.agent_runtime.metadata import AgentMetadata
from app.framework.errors import NotFoundError
from app.modules.access.domain.value_objects import Permission

router = APIRouter(prefix="/agents", tags=["agents"], dependencies=[Depends(current_principal)])


def _to_agent_out(metadata: AgentMetadata) -> AgentOut:
    """A registry manifest → its wire DTO, frozensets rendered as sorted lists
    so the exposed order is deterministic (independent of set iteration)."""
    return AgentOut(
        key=metadata.key,
        name=metadata.name,
        version=metadata.version,
        description=metadata.description,
        capabilities=sorted(metadata.capabilities),
        required_permissions=sorted(metadata.required_permissions),
    )


@router.get("", dependencies=[Depends(require(Permission.AGENTS_READ))])
async def list_agents(services: Services) -> Page[AgentOut]:
    """The agent catalog (03 §1). Unpaginated — the registry is a small static
    map — so the ``API-04`` envelope carries ``next_cursor: null`` and a
    ``limit`` equal to the returned count."""
    data = [_to_agent_out(metadata) for metadata in services.agents.list()]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.get("/{key}", dependencies=[Depends(require(Permission.AGENTS_READ))])
async def get_agent(key: str, services: Services) -> AgentOut:
    """One agent's manifest, returned bare (03 §0: a single resource is not
    wrapped). An unregistered key is a 404.

    The 404 carries ``agent.unknown`` (03 §4), the same code
    ``AgentRegistry.create`` now raises for an unregistered key — so the
    catalog entry means one thing whether the client reached it by reading the
    manifest or by invoking (6.2 resolved the reconciliation 5.3-ج deferred).
    """
    for metadata in services.agents.list():
        if metadata.key == key:
            return _to_agent_out(metadata)
    raise NotFoundError(f"unknown agent {key!r}", code="agent.unknown")


@router.post(
    "/{key}/invoke", response_model=None, dependencies=[Depends(require(Permission.AGENTS_INVOKE))]
)
async def invoke_agent(
    key: str, body: AgentInvokeIn, request: Request, services: Services, ctx: Context
) -> AgentInvokeOut | StreamingResponse:
    """Run one agent for one request (03 §2/§3.1).

    Both paths await the orchestrator first, so a pre-flight failure (unknown
    agent 404, bad shape 422, exhausted quota 429, unknown thread 404) becomes a
    proper problem response while the status is still open. The streaming path
    then hands the event iterator to ``sse_stream``, which owns the wire
    framing, the keep-alive, the terminal-event close, and cascading the
    client-disconnect close back into the orchestrator's billing/dispose chain;
    the collected path lets ``invoke_once`` drain the turn and RAISE an in-flight
    failure as the problem it is, since nothing has been written yet.
    """
    req = AgentRequest(conversation_id=body.conversation_id, input=body.input, stream=body.stream)
    if wants_sse(request, body.stream):
        events = await services.orchestrator.invoke(ctx, key, req)
        return StreamingResponse(
            sse_stream(events, correlation_id=ctx.correlation_id),
            media_type=SSE_MEDIA_TYPE,
            headers=SSE_HEADERS,
        )
    turn = await services.orchestrator.invoke_once(ctx, key, req)
    return AgentInvokeOut(
        conversation_id=turn.conversation_id,
        message=appended_to_message_out(turn.message),
        usage=Usage(prompt_tokens=turn.prompt_tokens, completion_tokens=turn.completion_tokens),
    )
