"""The Workflows router — ``/api/v1/workflows`` (`03-api-spec` §1 · FR-100) —
Phase 6.1-د-2.

Three routes, each a thin delegate over machinery that was already built:

* ``GET /workflows`` — the static D-09 catalog, straight from
  ``WorkflowRegistry.list``, in the ``API-04`` envelope;
* ``POST /workflows/{key}/run`` — hand the request to
  ``AgentOrchestrator.invoke_workflow`` and answer in the shape the caller
  asked for: an SSE stream, or the collected ``WorkflowRunOut``;
* ``GET /workflows/runs/{run_id}`` — read a run back.

**⚠️ The catalog is empty by a product decision, not an oversight** (§3.42):
`11 §8`'s example workflow does not wire up, so nothing is registered at boot.
``GET /workflows`` therefore returns an honest empty page and every ``run`` is
a truthful ``workflow.unknown``/404 today. The routes are real, tested against
real definitions, and the first definition that registers finds them working —
that is exactly why they are worth building now rather than stubbing.

**Reading a run means reading its conversation.** `01-data-model` has no runs
table; a run's only persistent trace is the D-12 thread 6.1-د-1 fills with the
initial input and one turn per completed step. So ``GET /workflows/runs/{id}``
loads that conversation, refuses anything that is not a workflow thread, and
DERIVES the status from the transcript. That derivation is deliberately
conservative — see ``_stored_status``.

**Pre-flight failures answer with a real status**, the same contract the
Agents router relies on: ``invoke_workflow`` is awaited before any response is
constructed, so an unknown workflow (404), an exhausted quota (429), or an
unwired deployment (500) becomes a problem response while the HTTP status is
still open. Once the stream starts, decision B1's terminal ``error`` event is
the only honest failure channel.

**Auth on every route** via the router-level ``current_principal`` dependency
(`03 §0`); RBAC guards are 6.4's.

**``Idempotency-Key`` on ``run`` (3.79), and a stated limit.**
``openapi.yaml`` declares the header on ``runWorkflow`` — this router never
even mentioned it before — and a workflow run is billed per step, so a
duplicate submission is the most expensive duplicate in the API. It is now
backed by the Postgres ledger for the COLLECTED answer: same key + same body
replays the first ``WorkflowRunOut`` (the same ``run_id``) instead of starting
a second run; same key + a different body is a 409.

The STREAMED answer is deliberately outside the ledger, and this is a limit,
not an omission: an SSE stream is consumed as it is produced, so there is no
response body to store and no honest way to replay one — a client that
reconnects would have to be handed a body it never asked for, in a media type
it did not request. A client that wants an idempotent workflow run therefore
asks for the collected answer; a client that wants live events already has the
run's ``conversation_id`` and ``GET /workflows/runs/{id}`` to reconcile with.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.api.middleware.rbac import require
from app.api.v1.dependencies import Context, Services, current_principal
from app.api.v1.dto.pagination import Page, PageMeta
from app.api.v1.dto.workflows import WorkflowOut, WorkflowRunIn, WorkflowRunOut
from app.api.v1.idempotency import IdempotencyKey, idempotent
from app.api.v1.routers.conversations import wants_sse
from app.api.v1.sse import SSE_HEADERS, SSE_MEDIA_TYPE, sse_stream
from app.framework.errors import NotFoundError
from app.framework.workflows.engine import WorkflowDefinition
from app.modules.access.domain.value_objects import Permission

router = APIRouter(
    prefix="/workflows", tags=["workflows"], dependencies=[Depends(current_principal)]
)

# `ConversationKind.WORKFLOW` as a plain string — the same boundary reasoning
# as the orchestrator's `_WORKFLOW_KIND`: the enum belongs to the conversations
# module, and this layer only ever compares the rendered value.
_WORKFLOW_KIND = "workflow"

# The two values a run READ BACK FROM STORAGE can honestly carry. A live handle
# is richer (running/completed/failed, 6.1-د-1); storage is not, because no run
# state is stored anywhere — see `_stored_status`.
_COMPLETED = "completed"
_UNKNOWN = "unknown"


def _to_workflow_out(definition: WorkflowDefinition) -> WorkflowOut:
    return WorkflowOut(
        key=definition.key,
        name=definition.name,
        steps=[step.agent_key for step in definition.steps],
    )


@router.get("", dependencies=[Depends(require(Permission.WORKFLOWS_READ))])
async def list_workflows(services: Services) -> Page[WorkflowOut]:
    """The workflow catalog (`03 §1`). Unpaginated — a small static map, like
    ``GET /agents`` — so ``API-04``'s envelope carries ``next_cursor: null``.

    Empty today by the product decision recorded in §3.42, and empty is what it
    reports: no placeholder entry is invented to make the list look alive.
    """
    data = [_to_workflow_out(definition) for definition in services.workflows.list()]
    return Page(data=data, meta=PageMeta(next_cursor=None, limit=len(data)))


@router.post(
    "/{key}/run", response_model=None, dependencies=[Depends(require(Permission.WORKFLOWS_RUN))]
)
async def run_workflow(
    key: str,
    body: WorkflowRunIn,
    request: Request,
    services: Services,
    ctx: Context,
    idempotency_key: IdempotencyKey = None,
) -> WorkflowRunOut | StreamingResponse:
    """Start a run (`03 §2`), streamed or collected.

    ``response_model=None`` for the same reason the invoke routes carry it: the
    two answers are genuinely different types, and declaring one model for both
    would document a lie.

    The idempotency guard wraps only the COLLECTED branch (module docstring):
    the streamed branch has no storable response, and the decision of which
    branch to take is the client's ``Accept``/``stream``, read BEFORE any run
    starts so the guard's claim and the answer's shape can never disagree.

    **The collected path does NOT raise on an in-flight failure**, unlike
    ``invoke_once``. The difference is the DTO: an ``AgentInvokeOut`` must name
    an assistant message, so a failed turn cannot be rendered at all and the
    honest answer is the problem itself — whereas ``WorkflowRunOut`` has a
    ``status`` field precisely to carry the outcome, and a failed run has real
    things the caller needs: the thread that holds the steps that DID complete,
    and the fact that they were billed. Answering 502 and dropping the run id
    would hide work the workspace already paid for.
    """
    if wants_sse(request, body.stream):
        run = await services.orchestrator.invoke_workflow(ctx, key, body.input)
        return StreamingResponse(
            sse_stream(run.events(), correlation_id=ctx.correlation_id),
            media_type=SSE_MEDIA_TYPE,
            headers=SSE_HEADERS,
        )

    async def _collect() -> WorkflowRunOut:
        run = await services.orchestrator.invoke_workflow(ctx, key, body.input)
        await run.collect()
        return WorkflowRunOut(
            run_id=run.run_id, conversation_id=run.conversation_id, status=run.status
        )

    return await idempotent(
        services.idempotency,
        ctx,
        endpoint=f"POST /workflows/{key}/run",
        key=idempotency_key,
        body=body,
        model=WorkflowRunOut,
        run=_collect,
    )


@router.get("/runs/{run_id}", dependencies=[Depends(require(Permission.WORKFLOWS_READ))])
async def get_workflow_run(run_id: str, services: Services, ctx: Context) -> WorkflowRunOut:
    """Read a run back (`03 §1`).

    The run IS its conversation (see ``WorkflowRun.run_id``), so this loads the
    thread — which also supplies tenant isolation and the 404 for an unknown,
    foreign, or deleted id for free — and refuses a thread that is not a
    workflow's: an agent conversation is a perfectly valid resource, just not a
    run, and answering with a run-shaped body for one would be a lie about what
    the id names.
    """
    conversation = await services.conversations.get.execute(ctx, run_id)
    if conversation.kind.value != _WORKFLOW_KIND:
        raise NotFoundError(f"no workflow run {run_id!r}")
    return WorkflowRunOut(
        run_id=conversation.id,
        conversation_id=conversation.id,
        status=_stored_status(services, conversation.agent_key.value, conversation.message_count),
    )


def _stored_status(services: Services, workflow_key: str, message_count: int) -> str:
    """A stored run's status, DERIVED from its transcript — the only source
    there is.

    A run's thread holds the opening turn plus one turn per completed step
    (6.1-د-1), so a transcript at least that long is a run that reached its
    last step: ``completed``. Anything shorter is ``unknown`` — and that word
    is chosen carefully, because a shorter transcript is a run that is still
    going, one that failed, one whose consumer walked away, or one whose step
    write degraded, and NOTHING stored can tell those apart. Reporting
    ``running`` or ``failed`` here would be a guess dressed as a fact; a client
    that needs the detail can read the thread itself.

    A workflow key that is no longer registered is ``unknown`` too: without the
    definition there is no step count to compare against. That is also why the
    lookup is a scan of ``list()`` rather than ``registry.get`` — a definition
    retired since the run happened must not turn reading an old run into a 404
    about the definition.

    **The real fix is a runs table with a stored state** (`01 §2.4` has none —
    the same gap ``run_id`` documents). This derivation is what an honest read
    can do until then, not a substitute for it.
    """
    definition = next((d for d in services.workflows.list() if d.key == workflow_key), None)
    if definition is None:
        return _UNKNOWN
    return _COMPLETED if message_count >= 1 + len(definition.steps) else _UNKNOWN
