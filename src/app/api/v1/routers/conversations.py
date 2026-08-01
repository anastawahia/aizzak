"""The Conversations router — ``/api/v1/conversations`` (03-api-spec §1 ·
FR-100) — Phase 6.1-ج-2/ج-3.

Six routes, each a thin delegate:

* ``GET /conversations?agent_key=`` — one agent's threads, paginated;
* ``POST /conversations`` — open a thread (201);
* ``GET /conversations/{id}`` — one thread, bare;
* ``DELETE /conversations/{id}`` — soft-delete (204, idempotent);
* ``GET /conversations/{id}/messages`` — the thread's turns, paginated;
* ``POST /conversations/{id}/messages`` — post a turn and RUN the thread's
  agent on it, answering as SSE or as the persisted reply.

**Use-cases, not a new inbound port.** `10-code-standards §3` defines this
layer as "thin: DTO validation, authorization, **call a Use-Case**, map to a
response", and the layering contract allows ``app.api → app.modules``, so the
router calls ``ConversationUseCases`` directly. The module's ``ConversationThreads``
inbound port stays narrow (``start``/``append``) because its consumer is the
ORCHESTRATOR — widening it to serve the API would make the orchestrator
declare a dependency far wider than it uses.

**The agent a thread runs is the thread's own** (``conversation.agent_key``),
never a field on the request: a thread is threaded per ``(workspace, agent)``
by `06 §4`, so letting a message name a different agent would silently break
that. The router loads the conversation, hands the key to the orchestrator,
and the orchestrator writes both turns (user + assistant) itself — one
persistence path shared with ``POST /agents/{key}/invoke``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.conversations import (
    ConversationCreateIn,
    ConversationOut,
    MessageCreateIn,
    MessageOut,
)
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.api.v1.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_stream
from app.framework.agent_runtime.base_agent import AgentRequest
from app.modules.access.domain.value_objects import Permission
from app.modules.conversations.domain.entities import Conversation, Message
from app.modules.conversations.ports.inbound import AppendedMessage

# The router-level bearer gate is BELT AND BRACES here, and knowingly so: every
# route below also takes `ctx: Context`, whose dependency chain authenticates
# anyway (a mutation run proved removing this line changes no response). It
# stays because it is what protects the first route that does NOT need a context
# — the Agents router already has two such routes — so the guarantee is
# structural rather than a property each new handler must remember to preserve.
router = APIRouter(
    prefix="/conversations", tags=["conversations"], dependencies=[Depends(current_principal)]
)

# The `Accept` value that selects SSE (03 §3.1).
_EVENT_STREAM = "text/event-stream"


def _to_conversation_out(conversation: Conversation) -> ConversationOut:
    return ConversationOut(
        id=conversation.id,
        agent_key=conversation.agent_key.value,
        kind=conversation.kind.value,
        title=conversation.title,
        created_at=conversation.created_at,
    )


def _to_message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role.value,
        content={"text": message.content.text, "attachments": list(message.content.attachments)},
        token_count=message.token_count,
        seq=message.seq,
        created_at=message.created_at,
    )


def appended_to_message_out(message: AppendedMessage) -> MessageOut:
    """The inbound port's handle → the same wire shape as a stored ``Message``.

    Two mappers rather than one because the two sources are genuinely
    different objects: reads hand back the module's entity, while the
    orchestrator hands back the port's flat handle (it may not hold the
    aggregate — see ``ports/inbound.py``). Shared with the Agents router,
    which renders the very same handle.
    """
    return MessageOut(
        id=message.id,
        role=message.role,
        content={"text": message.text, "attachments": list(message.attachments)},
        token_count=message.token_count,
        seq=message.seq,
        created_at=message.created_at,
    )


def wants_sse(request: Request, stream: bool) -> bool:
    """Whether this request should be answered as an event stream.

    Either signal is enough: `03 §2` puts a ``stream`` flag in the body and
    `03 §3.1` keys SSE off ``Accept: text/event-stream``. Honouring only one
    would make a correct client of the other contract wrong.

    Matched against the BARE media type, not ``SSE_MEDIA_TYPE`` — that constant
    carries the response's ``; charset=utf-8``, which an ``Accept`` header has
    no reason to repeat.
    """
    return stream or _EVENT_STREAM in request.headers.get("accept", "")


@router.get("", dependencies=[Depends(require(Permission.CONVERSATIONS_READ))])
async def list_conversations(
    services: Services,
    ctx: Context,
    agent_key: str,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> Page[ConversationOut]:
    """One agent's threads in this workspace (`03 §1`), newest-cursor paginated.

    ``agent_key`` is a REQUIRED query parameter, per the contract: threads are
    threaded per agent, so an unqualified "all conversations" listing is not a
    view the data model offers.
    """
    page = await services.conversations.list_by_agent.execute(
        ctx, agent_key, limit=limit, cursor=cursor
    )
    return Page(
        data=[_to_conversation_out(conversation) for conversation in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.post("", status_code=201, dependencies=[Depends(require(Permission.CONVERSATIONS_WRITE))])
async def create_conversation(
    body: ConversationCreateIn, services: Services, ctx: Context
) -> ConversationOut:
    """Open a thread under one agent (201 + the bare resource)."""
    conversation, _events = await services.conversations.start.execute(
        ctx, agent_key=body.agent_key, title=body.title
    )
    return _to_conversation_out(conversation)


@router.get("/{conversation_id}", dependencies=[Depends(require(Permission.CONVERSATIONS_READ))])
async def get_conversation(
    conversation_id: str, services: Services, ctx: Context
) -> ConversationOut:
    """One thread, bare. Unknown, another tenant's, or soft-deleted ⇒ 404."""
    conversation = await services.conversations.get.execute(ctx, conversation_id)
    return _to_conversation_out(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=204,
    dependencies=[Depends(require(Permission.CONVERSATIONS_DELETE))],
)
async def delete_conversation(conversation_id: str, services: Services, ctx: Context) -> None:
    """Soft-delete a thread (204, no body).

    Idempotent by the use-case's own contract — deleting twice emits no second
    event — so a client retrying a lost 204 gets a 204, not a 409.

    Returns ``None`` and lets the decorator's ``status_code`` produce the empty
    204, rather than constructing a ``Response`` here: two sources for one
    status is one too many, and the declared one is what the OpenAPI schema
    shows a client.
    """
    await services.conversations.soft_delete.execute(ctx, conversation_id)


@router.get(
    "/{conversation_id}/messages", dependencies=[Depends(require(Permission.CONVERSATIONS_READ))]
)
async def list_messages(
    services: Services,
    ctx: Context,
    conversation_id: str,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> Page[MessageOut]:
    """The thread's turns in ``seq`` order, soft-deleted ones excluded."""
    page = await services.conversations.list_messages.execute(
        ctx, conversation_id, limit=limit, cursor=cursor
    )
    return Page(
        data=[_to_message_out(message) for message in page.data],
        meta=PageMeta(next_cursor=page.next_cursor, limit=page.limit),
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=None,
    dependencies=[Depends(require(Permission.CONVERSATIONS_WRITE))],
)
async def post_message(
    conversation_id: str,
    body: MessageCreateIn,
    request: Request,
    services: Services,
    ctx: Context,
) -> MessageOut | StreamingResponse:
    """Post a turn and run the thread's agent on it (`03 §1`, ``postMessage``).

    The conversation is loaded first — it is what names the agent, and it is
    what makes an unknown/foreign/deleted thread a 404 before anything runs.
    Everything after that is the same single-agent turn ``POST
    /agents/{key}/invoke`` performs, so both routes persist identically:
    the user's ``content`` becomes the user message, the agent's terminal
    ``final`` becomes the assistant message.

    ``response_model=None`` because the two answers are genuinely different
    types (a JSON body vs. an open stream); FastAPI cannot infer one model for
    both, and pretending otherwise would document a lie.
    """
    conversation = await services.conversations.get.execute(ctx, conversation_id)
    req = AgentRequest(conversation_id=conversation_id, input=body.content, stream=body.stream)
    agent_key = conversation.agent_key.value
    if wants_sse(request, body.stream):
        events = await services.orchestrator.invoke(ctx, agent_key, req)
        return StreamingResponse(
            sse_stream(events, correlation_id=ctx.correlation_id),
            media_type=SSE_MEDIA_TYPE,
            headers=SSE_HEADERS,
        )
    turn = await services.orchestrator.invoke_once(ctx, agent_key, req)
    return appended_to_message_out(turn.message)
