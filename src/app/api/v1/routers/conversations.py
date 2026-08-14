"""The Conversations router — ``/api/v1/conversations`` (03-api-spec §1 ·
FR-100) — Phase 6.1-ج-2/ج-3.

Twelve routes, each a thin delegate:

* ``GET /conversations?agent_key=`` — one agent's threads, paginated;
* ``POST /conversations`` — open a thread (201);
* ``GET /conversations/{id}`` — one thread, bare;
* ``PATCH /conversations/{id}`` — rename a thread, returning it renamed;
* ``PUT /conversations/{id}/model`` — pin (or unpin) the configured model
  route that answers this thread;
* ``DELETE /conversations/{id}`` — soft-delete (204, idempotent);
* ``GET /conversations/{id}/messages`` — the thread's turns, paginated;
* ``POST /conversations/{id}/messages`` — post a turn and RUN the thread's
  agent on it, answering as SSE or as the persisted reply;
* ``DELETE /conversations/{id}/messages/{message_id}`` — soft-delete one turn
  (204, idempotent);
* ``GET /conversations/{id}/files`` — the files this thread's retrieval is
  pinned to;
* ``POST /conversations/{id}/files`` — pin one (201, idempotent);
* ``DELETE /conversations/{id}/files/{file_id}`` — unpin one (204,
  idempotent).

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
    ConversationFileIn,
    ConversationFileOut,
    ConversationModelIn,
    ConversationOut,
    ConversationPatchIn,
    MessageCreateIn,
    MessageOut,
)
from app.api.v1.dto.pagination import DEFAULT_LIMIT, Cursor, Limit, Page, PageMeta
from app.api.v1.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_stream
from app.framework.agent_runtime.base_agent import AgentRequest
from app.modules.access.domain.value_objects import Permission
from app.modules.conversations.domain.entities import Conversation, Message, PinnedFile
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
        model_route=conversation.model_route,
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


def _to_conversation_file_out(pin: PinnedFile) -> ConversationFileOut:
    # `created_at` on the row, `pinned_at` on the wire: the column names when
    # the ROW was made, and the wire name says what that means to a reader who
    # is looking at a list of files, not a list of rows.
    return ConversationFileOut(file_id=pin.file_id, pinned_at=pin.created_at)


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


@router.patch("/{conversation_id}", dependencies=[Depends(require(Permission.CONVERSATIONS_WRITE))])
async def rename_conversation(
    conversation_id: str, body: ConversationPatchIn, services: Services, ctx: Context
) -> ConversationOut:
    """Rename a thread (`03 §1`, ``renameConversation``) and return it renamed.

    ``conversations:write`` — the permission that authored the thread's title
    on ``POST``, not ``conversations:delete``: editing a title is the same
    class of act as choosing one, and a member who may open threads and post
    turns has no reason to be locked out of naming them.

    A soft-deleted thread answers 409 here, not 404, because the use-case
    surfaces the domain's deleted-guard for WRITES while hiding the thread
    from reads — the asymmetry ``ListMessages`` documents. Telling a client
    "gone" when the thread is visibly still in its list would be the less
    truthful of the two.
    """
    conversation, _events = await services.conversations.rename.execute(
        ctx, conversation_id, body.title
    )
    return _to_conversation_out(conversation)


@router.put(
    "/{conversation_id}/model", dependencies=[Depends(require(Permission.CONVERSATIONS_WRITE))]
)
async def pin_conversation_model(
    conversation_id: str, body: ConversationModelIn, services: Services, ctx: Context
) -> ConversationOut:
    """Pin which configured model route answers this thread (`03 §1`).

    ``PUT``, not ``PATCH``: the route is a single-valued sub-resource and the
    body REPLACES it whole — there is no partial state to merge, and ``null``
    is a value here rather than an omission.

    ``conversations:write``, the same permission as the rename: choosing which
    configured model answers a thread you may already post turns into is not a
    privileged act, and the set of choices is the operator's regardless.

    An unknown route is 422 with the configured keys named, a soft-deleted
    thread is 409 and an unknown one 404 — the same read/write asymmetry the
    rename documents.
    """
    conversation, _events = await services.conversations.pin_model.execute(
        ctx, conversation_id, body.route
    )
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


@router.delete(
    "/{conversation_id}/messages/{message_id}",
    status_code=204,
    dependencies=[Depends(require(Permission.CONVERSATIONS_DELETE))],
)
async def delete_message(
    conversation_id: str, message_id: str, services: Services, ctx: Context
) -> None:
    """Soft-delete one turn (`03 §1`, ``deleteMessage``) — 204, no body.

    **Nested under its thread, not ``DELETE /messages/{id}``.** A message is a
    child entity of the conversation aggregate (`06 §4`), never a root, so the
    path names the parent the same way every other route in this module does —
    and that is what makes the ownership check structural: the repository
    matches on BOTH ids, so a message quoted against the wrong thread is a 404
    rather than a delete that crosses threads.

    ``conversations:delete``, not the ``conversations:write`` a rename takes.
    The permission that removes a thread must also be the one that removes its
    turns: with ``write`` here, a member could erase an entire transcript
    message by message while still being refused the empty thread — a boundary
    that permits the destructive act and forbids only the tidy-up.

    Idempotent (a second call is another 204) and, like every other WRITE in
    this module, 409 rather than 404 on a soft-deleted thread.
    """
    await services.conversations.soft_delete_message.execute(ctx, conversation_id, message_id)


@router.get(
    "/{conversation_id}/files", dependencies=[Depends(require(Permission.CONVERSATIONS_READ))]
)
async def list_conversation_files(
    conversation_id: str, services: Services, ctx: Context
) -> Page[ConversationFileOut]:
    """The files this thread's retrieval is pinned to (`03 §1`,
    ``listConversationFiles``).

    **A ``Page`` with no cursor parameter, deliberately.** The pinned set is
    bounded (``MAX_PINNED_FILES``), so every read carries all of it and
    ``next_cursor`` is always ``null``; the envelope is kept because the client
    reads one shape for every collection this API returns, and a bare array
    here would be the only exception. Accepting a ``cursor`` it can never
    honour would be the worse lie.
    """
    pinned = await services.conversations.list_files.execute(ctx, conversation_id)
    return Page(
        data=[_to_conversation_file_out(pin) for pin in pinned],
        meta=PageMeta(next_cursor=None, limit=len(pinned)),
    )


@router.post(
    "/{conversation_id}/files",
    status_code=201,
    dependencies=[Depends(require(Permission.CONVERSATIONS_WRITE))],
)
async def pin_conversation_file(
    conversation_id: str, body: ConversationFileIn, services: Services, ctx: Context
) -> ConversationFileOut:
    """Pin one file into the thread's retrieval scope (`03 §1`,
    ``pinConversationFile``) — 201 with the stored pin.

    ``conversations:write``, the permission the model pin takes, because that
    is what this is: both configure how the thread answers, neither destroys
    anything, and a member who may steer a thread's model may steer what it
    reads from.

    Idempotent, and it returns 201 on the repeat rather than 200: the resource
    named by ``(thread, file)`` exists afterwards either way, and switching
    status codes would make a client's retry look like a different outcome
    than its first attempt. The ``pinned_at`` it carries back is the
    ORIGINAL one.
    """
    pin = await services.conversations.pin_file.execute(ctx, conversation_id, body.file_id)
    return _to_conversation_file_out(pin)


@router.delete(
    "/{conversation_id}/files/{file_id}",
    status_code=204,
    dependencies=[Depends(require(Permission.CONVERSATIONS_WRITE))],
)
async def unpin_conversation_file(
    conversation_id: str, file_id: str, services: Services, ctx: Context
) -> None:
    """Drop one pin (`03 §1`, ``unpinConversationFile``) — 204, no body.

    ``conversations:write`` and NOT ``conversations:delete``, which is the one
    place this route parts company with ``delete_message`` above. Nothing is
    destroyed: the file survives, its index survives, and every other thread
    that pinned it is untouched — all that changes is how wide this thread
    searches. Requiring the delete permission would mean a member who may pin
    a file cannot undo their own pin.

    Idempotent all the way down, and un-pinning the last file returns the
    thread to the whole workspace corpus rather than to an empty one.
    """
    await services.conversations.unpin_file.execute(ctx, conversation_id, file_id)
