"""Unit tests for 4.6-c — the Image / Video media agents + the AC-04 add-side
proof (FR-20.3/20.4, 11 §9). Purely hermetic: a fake ``MediaRequesting``."""

from __future__ import annotations

import pytest

from app.agents.image_agent.agent import ImageAgent
from app.agents.video_agent.agent import VideoAgent
from app.framework.agent_runtime import (
    AgentDependencies,
    AgentLifecycleExecutor,
    AgentRequest,
    BaseAgent,
    InMemoryAgentRegistry,
    PluginLoader,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import AppError, ValidationError
from app.framework.identifiers import new_uuid7

# --------------------------------------------------------------------------- #
# Fakes + builders                                                            #
# --------------------------------------------------------------------------- #


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


class FakeRequestedView:
    def __init__(
        self, *, job_id: str = "job-1", status: str = "queued", kind: str = "image"
    ) -> None:
        self.job_id = job_id
        self.status = status
        self.kind = kind


class FakeMedia:
    """Structurally satisfies ``MediaRequesting``; records each request."""

    def __init__(self, view: FakeRequestedView) -> None:
        self._view = view
        self.calls: list[dict[str, object]] = []

    async def request(
        self,
        ctx: ExecutionContext,
        *,
        agent_key: str,
        kind: str,
        prompt: str,
        params: dict[str, object],
    ) -> FakeRequestedView:
        self.calls.append(
            {"agent_key": agent_key, "kind": kind, "prompt": prompt, "params": params}
        )
        return self._view


def make_deps(view: FakeRequestedView) -> tuple[AgentDependencies, FakeMedia]:
    media = FakeMedia(view)
    return AgentDependencies(media=media), media


async def collect(agent: BaseAgent, req_input: dict[str, object]) -> list:
    return [event async for event in agent.run(AgentRequest(conversation_id=None, input=req_input))]


# --------------------------------------------------------------------------- #
# Image / Video request behaviour                                             #
# --------------------------------------------------------------------------- #


async def test_image_agent_queues_a_job_and_emits_only_a_final() -> None:
    deps, media = make_deps(FakeRequestedView(job_id="j9", status="queued", kind="image"))
    events = await collect(
        ImageAgent(make_ctx(), deps),
        {"prompt": "a red bicycle", "params": {"width": 512, "height": 512, "model": "m"}},
    )

    assert [e.type for e in events] == ["final"]  # no token events — event-driven, not streamed
    assert events[0].data == {"job_id": "j9", "status": "queued", "kind": "image"}
    assert media.calls == [
        {
            "agent_key": "image_agent",
            "kind": "image",
            "prompt": "a red bicycle",
            "params": {"width": 512, "height": 512, "model": "m"},
        }
    ]


async def test_video_agent_requests_the_video_kind_with_its_key() -> None:
    deps, media = make_deps(FakeRequestedView(kind="video"))
    events = await collect(VideoAgent(make_ctx(), deps), {"prompt": "waves"})

    assert [e.type for e in events] == ["final"]
    assert media.calls[0]["agent_key"] == "video_agent"
    assert media.calls[0]["kind"] == "video"
    assert media.calls[0]["params"] == {}  # absent params default to empty


@pytest.mark.parametrize("agent_cls", [ImageAgent, VideoAgent])
@pytest.mark.parametrize("req_input", [{"params": {}}, {"prompt": "   "}, {"prompt": 123}])
async def test_missing_blank_or_non_string_prompt_is_422(
    agent_cls: type[BaseAgent], req_input: dict[str, object]
) -> None:
    deps, _media = make_deps(FakeRequestedView())
    with pytest.raises(ValidationError):
        await collect(agent_cls(make_ctx(), deps), req_input)


@pytest.mark.parametrize("agent_cls", [ImageAgent, VideoAgent])
async def test_unbound_media_seam_is_500(agent_cls: type[BaseAgent]) -> None:
    with pytest.raises(AppError) as excinfo:
        await collect(agent_cls(make_ctx(), AgentDependencies()), {"prompt": "x"})
    assert excinfo.value.status == 500


async def test_image_agent_drives_cleanly_through_the_executor() -> None:
    deps, _media = make_deps(FakeRequestedView())
    events = [
        event
        async for event in AgentLifecycleExecutor().drive(
            ImageAgent(make_ctx(), deps), AgentRequest(conversation_id=None, input={"prompt": "x"})
        )
    ]
    assert events[-1].type == "final"
    assert not any(e.type == "error" for e in events)


# --------------------------------------------------------------------------- #
# AC-04 (add side) — the real tree registers all five                         #
# --------------------------------------------------------------------------- #


async def test_ac04_real_tree_registers_and_creates_all_five_agents() -> None:
    registry = InMemoryAgentRegistry()
    report = PluginLoader().load_into(registry)
    assert set(report.loaded) == {
        "data_analysis_agent",
        "file_editing_agent",
        "image_agent",
        "rag_agent",
        "video_agent",
    }
    assert report.failures == ()
    # each is creatable per request (the shared media base still yields ONE
    # concrete class per plugin package that the loader picked up).
    for key in report.loaded:
        agent = registry.create(key, make_ctx(), AgentDependencies())
        assert isinstance(agent, BaseAgent)
        assert agent.metadata.key == key
