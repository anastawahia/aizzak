"""Unit tests for ``framework/workflows/`` (Phase 4.5 — 02 §3.3/§3.4, D-04/09/12).

Purely hermetic, no ``live_*`` marker (the 4.1/4.3 precedent — there is no
service here, only kernel-pure carriers and an in-memory map).

4.5-a: the §3.3 carriers (frozen), and ``InMemoryWorkflowRegistry`` semantics
— register/get round-trip, the R6 boundary guards (non-definition, blank key,
malformed steps, ``input_map`` not ``dict[str, str]``), the two NFR bounds
(1-10 steps), duplicate refusal, 404-vs-422 on ``get``, and a deterministic
``list``.

4.5-b: ``SequentialWorkflowEngine`` — sequential chaining over the REAL 4.1/4.2
collaborators (``InMemoryAgentRegistry`` + ``AgentLifecycleExecutor``) with
hand-built scripted agents: input_map projection (rename + pass-through),
blackboard accumulation across steps, halt-on-failure (a step's own error, an
unknown ``agent_key`` 404, a bad projection 422), event forwarding order, and
caller-dict immutability.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from typing import ClassVar

import pytest

from app.framework.agent_runtime import (
    AgentDependencies,
    AgentEvent,
    AgentLifecycleExecutor,
    AgentMetadata,
    AgentRequest,
    BaseAgent,
    InMemoryAgentRegistry,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.workflows import (
    InMemoryWorkflowRegistry,
    SequentialWorkflowEngine,
    StaticAgentDeps,
    WorkflowDefinition,
    WorkflowResult,
    WorkflowStep,
)

# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def make_step(agent_key: str = "rag_agent") -> WorkflowStep:
    return WorkflowStep(agent_key=agent_key, input_map={})


def make_definition(
    key: str = "content_pipeline",
    *,
    steps: tuple[WorkflowStep, ...] | None = None,
) -> WorkflowDefinition:
    return WorkflowDefinition(
        key=key,
        name=f"{key} workflow",
        steps=steps if steps is not None else (make_step(),),
    )


# --------------------------------------------------------------------------- #
# §3.3 carrier types                                                          #
# --------------------------------------------------------------------------- #


def test_carriers_are_frozen() -> None:
    step = make_step()
    definition = make_definition()
    result = WorkflowResult(workflow_key="wf", conversation_id="c1", outputs=[])
    with pytest.raises(FrozenInstanceError):
        step.agent_key = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        definition.key = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.workflow_key = "other"  # type: ignore[misc]


def test_definition_carries_ordered_steps() -> None:
    steps = (make_step("rag_agent"), make_step("image_agent"))
    definition = make_definition(steps=steps)
    assert [s.agent_key for s in definition.steps] == ["rag_agent", "image_agent"]


# --------------------------------------------------------------------------- #
# InMemoryWorkflowRegistry — happy path                                       #
# --------------------------------------------------------------------------- #


def test_register_then_get_round_trip() -> None:
    registry = InMemoryWorkflowRegistry()
    definition = make_definition("content_pipeline")
    registry.register(definition)
    assert registry.get("content_pipeline") is definition


def test_list_is_sorted_by_key_and_independent_of_insertion_order() -> None:
    registry = InMemoryWorkflowRegistry()
    registry.register(make_definition("zeta"))
    registry.register(make_definition("alpha"))
    registry.register(make_definition("mu"))
    assert [d.key for d in registry.list()] == ["alpha", "mu", "zeta"]


def test_register_ten_steps_is_the_accepted_boundary() -> None:
    registry = InMemoryWorkflowRegistry()
    steps = tuple(make_step(f"agent_{i}") for i in range(10))
    registry.register(make_definition("wide", steps=steps))
    assert len(registry.get("wide").steps) == 10


# --------------------------------------------------------------------------- #
# get — 404 vs 422                                                            #
# --------------------------------------------------------------------------- #


def test_get_unknown_key_is_not_found_404() -> None:
    registry = InMemoryWorkflowRegistry()
    with pytest.raises(NotFoundError) as excinfo:
        registry.get("nope")
    assert excinfo.value.status == 404
    # 4.7-e-1 narrowed this from the inherited `common.not_found` to the code
    # 03-api-spec's error catalog actually names, so a client can tell "no such
    # workflow" from "no such run" on the same router.
    assert excinfo.value.code == "workflow.unknown"


@pytest.mark.parametrize("bad_key", ["", "   ", 123, None, ["x"]])
def test_get_non_string_or_blank_key_is_validation_422(bad_key: object) -> None:
    registry = InMemoryWorkflowRegistry()
    with pytest.raises(ValidationError) as excinfo:
        registry.get(bad_key)  # type: ignore[arg-type]
    assert excinfo.value.status == 422


# --------------------------------------------------------------------------- #
# register — strict boundary guards (the R6 family)                          #
# --------------------------------------------------------------------------- #


def test_register_duplicate_key_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    registry.register(make_definition("dup"))
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_definition("dup"))
    assert "already registered" in str(excinfo.value)


def test_register_non_definition_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    with pytest.raises(ValidationError):
        registry.register("not-a-definition")  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_key", ["", "   ", 123, None])
def test_register_blank_or_non_string_key_is_refused(bad_key: object) -> None:
    registry = InMemoryWorkflowRegistry()
    bad = WorkflowDefinition(key=bad_key, name="x", steps=(make_step(),))  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        registry.register(bad)


def test_register_steps_not_a_tuple_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    bad = WorkflowDefinition(key="wf", name="x", steps=[make_step()])  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as excinfo:
        registry.register(bad)
    assert "must be a tuple" in str(excinfo.value)


def test_register_empty_steps_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_definition("empty", steps=()))
    assert "at least one step" in str(excinfo.value)


def test_register_more_than_ten_steps_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    steps = tuple(make_step(f"agent_{i}") for i in range(11))
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_definition("toowide", steps=steps))
    assert "exceeding the max of 10" in str(excinfo.value)


def test_register_non_workflowstep_element_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    bad = WorkflowDefinition(key="wf", name="x", steps=("nope",))  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as excinfo:
        registry.register(bad)
    assert "must be a WorkflowStep" in str(excinfo.value)


@pytest.mark.parametrize("bad_agent_key", ["", "   ", 123, None])
def test_register_step_with_blank_agent_key_is_refused(bad_agent_key: object) -> None:
    registry = InMemoryWorkflowRegistry()
    bad_step = WorkflowStep(agent_key=bad_agent_key, input_map={})  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_definition("wf", steps=(bad_step,)))
    assert "agent_key" in str(excinfo.value)


def test_register_step_with_non_mapping_input_map_is_refused() -> None:
    registry = InMemoryWorkflowRegistry()
    bad_step = WorkflowStep(agent_key="rag_agent", input_map=["x"])  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_definition("wf", steps=(bad_step,)))
    assert "input_map" in str(excinfo.value)


def test_register_step_with_empty_input_map_is_accepted() -> None:
    # Empty input_map is the valid "pass the whole context through" shape.
    registry = InMemoryWorkflowRegistry()
    registry.register(make_definition("wf", steps=(WorkflowStep("rag_agent", {}),)))
    assert registry.get("wf").steps[0].input_map == {}


@pytest.mark.parametrize(
    "bad_input_map",
    [{"draft": 123}, {5: "source"}, {"ok": "src", "bad": None}],
)
def test_register_input_map_must_be_str_to_str(bad_input_map: object) -> None:
    # v1 projection semantics: input_map is dict[str, str]; a non-string key or
    # value is an authoring error caught at register (boot), not mid-run.
    registry = InMemoryWorkflowRegistry()
    bad_step = WorkflowStep(agent_key="rag_agent", input_map=bad_input_map)  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_definition("wf", steps=(bad_step,)))
    assert "str -> str" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 4.5-b — SequentialWorkflowEngine (over the REAL 4.1/4.2 collaborators)      #
# --------------------------------------------------------------------------- #


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def make_metadata(key: str) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=f"{key} agent",
        version="1.0.0",
        description="synthetic scripted agent",
        capabilities=frozenset({"chat"}),
        required_permissions=frozenset({"agents:invoke"}),
    )


class ScriptedAgent(BaseAgent):
    """A fake agent: records each ``AgentRequest`` it is driven with, optionally
    streams token events, then either raises (scripted failure) or emits a
    ``final`` echoing its configured output. The factory ``scripted`` binds the
    per-agent config as subclass attributes."""

    _recorder: ClassVar[dict[str, list[AgentRequest]]] = {}
    _key: ClassVar[str] = ""
    _output: ClassVar[dict[str, object]] = {}
    _fail: ClassVar[bool] = False
    _tokens: ClassVar[tuple[str, ...]] = ()

    async def initialize(self) -> None:
        return None

    async def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        self._recorder.setdefault(self._key, []).append(req)
        for tok in self._tokens:
            yield AgentEvent(type="token", data={"text": tok})
        if self._fail:
            raise RuntimeError("scripted failure")
        yield AgentEvent(type="final", data=dict(self._output))


Recorder = dict[str, "list[AgentRequest]"]


def scripted(
    agent_key: str,
    recorder: Recorder,
    *,
    output: dict[str, object] | None = None,
    fail: bool = False,
    tokens: tuple[str, ...] = (),
) -> tuple[AgentMetadata, type[ScriptedAgent]]:
    metadata = make_metadata(agent_key)

    class _Agent(ScriptedAgent):
        pass

    _Agent.metadata = metadata
    _Agent._key = agent_key
    _Agent._recorder = recorder
    _Agent._output = output if output is not None else {}
    _Agent._fail = fail
    _Agent._tokens = tokens
    return metadata, _Agent


def build_engine(
    *factories: tuple[AgentMetadata, type[ScriptedAgent]],
) -> SequentialWorkflowEngine:
    registry = InMemoryAgentRegistry()
    for metadata, agent_cls in factories:
        registry.register(metadata, agent_cls)
    # 4.7-e-1 turned the engine's third argument into an `AgentDepsProvider`;
    # these 4.5 cases have nothing to resolve per step, so the static wrapper
    # preserves their exact original semantics (one bundle for every step).
    return SequentialWorkflowEngine(
        registry, AgentLifecycleExecutor(), StaticAgentDeps(AgentDependencies())
    )


async def collect(
    engine: SequentialWorkflowEngine,
    definition: WorkflowDefinition,
    initial_input: dict[str, object],
) -> list[AgentEvent]:
    return [event async for event in engine.run(make_ctx(), definition, initial_input)]


def seen(recorder: Recorder, key: str) -> list[dict[str, object]]:
    return [dict(req.input) for req in recorder.get(key, [])]


async def test_two_step_pipeline_chains_output_via_input_map() -> None:
    recorder: Recorder = {}
    engine = build_engine(
        scripted("rag_agent", recorder, output={"answer": "A"}),
        scripted("writer_agent", recorder, output={"done": True}),
    )
    definition = WorkflowDefinition(
        key="wf",
        name="wf",
        steps=(
            WorkflowStep("rag_agent", {"q": "question"}),
            WorkflowStep("writer_agent", {"draft": "answer"}),
        ),
    )
    events = await collect(engine, definition, {"question": "Q"})
    # step 1 projected {"q": "Q"} from the initial input; step 2 projected
    # {"draft": "A"} from step 1's merged output.
    assert seen(recorder, "rag_agent") == [{"q": "Q"}]
    assert seen(recorder, "writer_agent") == [{"draft": "A"}]
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 2
    assert finals[-1].data == {"done": True}


async def test_empty_input_map_passes_whole_context_through() -> None:
    recorder: Recorder = {}
    engine = build_engine(scripted("solo", recorder, output={}))
    definition = WorkflowDefinition("wf", "wf", (WorkflowStep("solo", {}),))
    await collect(engine, definition, {"a": 1, "b": 2})
    assert seen(recorder, "solo") == [{"a": 1, "b": 2}]


async def test_initial_input_is_not_mutated_by_the_blackboard() -> None:
    recorder: Recorder = {}
    engine = build_engine(scripted("s", recorder, output={"x": 9}))
    definition = WorkflowDefinition("wf", "wf", (WorkflowStep("s", {}),))
    initial = {"a": 1}
    await collect(engine, definition, initial)
    assert initial == {"a": 1}


async def test_blackboard_accumulates_across_three_steps() -> None:
    recorder: Recorder = {}
    engine = build_engine(
        scripted("a1", recorder, output={"a": 1}),
        scripted("a2", recorder, output={"b": 2}),
        scripted("a3", recorder, output={}),
    )
    definition = WorkflowDefinition(
        "wf",
        "wf",
        (
            WorkflowStep("a1", {}),
            WorkflowStep("a2", {}),
            WorkflowStep("a3", {"x": "a", "y": "b"}),
        ),
    )
    await collect(engine, definition, {"seed": 0})
    assert seen(recorder, "a3") == [{"x": 1, "y": 2}]


async def test_single_step_workflow_runs() -> None:
    recorder: Recorder = {}
    engine = build_engine(scripted("only", recorder, output={"r": 1}))
    definition = WorkflowDefinition("wf", "wf", (WorkflowStep("only", {}),))
    events = await collect(engine, definition, {"seed": True})
    assert [e.type for e in events] == ["final"]
    assert events[0].data == {"r": 1}
    assert seen(recorder, "only") == [{"seed": True}]


async def test_steps_run_with_no_conversation_id_deferred_to_orchestrator() -> None:
    # D-12: the workflow's own conversation is 4.7's job; steps get None here.
    recorder: Recorder = {}
    engine = build_engine(scripted("only", recorder, output={}))
    definition = WorkflowDefinition("wf", "wf", (WorkflowStep("only", {}),))
    await collect(engine, definition, {})
    assert recorder["only"][0].conversation_id is None


async def test_tokens_are_forwarded_and_final_is_still_captured() -> None:
    recorder: Recorder = {}
    engine = build_engine(
        scripted("t1", recorder, output={"v": 5}, tokens=("a", "b")),
        scripted("t2", recorder, output={}),
    )
    definition = WorkflowDefinition(
        "wf", "wf", (WorkflowStep("t1", {}), WorkflowStep("t2", {"got": "v"}))
    )
    events = await collect(engine, definition, {})
    assert [e.data["text"] for e in events if e.type == "token"] == ["a", "b"]
    # step 2 still received step 1's captured `final` output despite the tokens.
    assert seen(recorder, "t2") == [{"got": 5}]


async def test_step_failure_halts_workflow_and_yields_one_error_event() -> None:
    recorder: Recorder = {}
    engine = build_engine(
        scripted("boom", recorder, fail=True),
        scripted("never", recorder, output={"x": 1}),
    )
    definition = WorkflowDefinition(
        "wf", "wf", (WorkflowStep("boom", {}), WorkflowStep("never", {}))
    )
    events = await collect(engine, definition, {})
    assert seen(recorder, "boom") == [{}]  # the failing step ran
    assert "never" not in recorder  # the workflow halted before step 2
    assert events[-1].type == "error"
    assert sum(1 for e in events if e.type == "error") == 1


async def test_unknown_agent_key_yields_404_error_and_halts() -> None:
    recorder: Recorder = {}
    engine = build_engine(scripted("real", recorder, output={}))
    definition = WorkflowDefinition(
        "wf", "wf", (WorkflowStep("ghost", {}), WorkflowStep("real", {}))
    )
    events = await collect(engine, definition, {})
    assert len(events) == 1
    assert events[0].type == "error"
    # 6.2: the registry now names the miss (`agent.unknown`), so a halted
    # run says WHICH lookup failed rather than "something was not found".
    assert events[0].data["code"] == "agent.unknown"
    assert events[0].data["status"] == 404
    assert "real" not in recorder  # halted before the reachable step


async def test_bad_projection_source_yields_422_error_and_halts() -> None:
    recorder: Recorder = {}
    engine = build_engine(
        scripted("s1", recorder, output={"answer": "A"}),
        scripted("s2", recorder, output={}),
    )
    definition = WorkflowDefinition(
        "wf",
        "wf",
        (
            WorkflowStep("s1", {"q": "question"}),
            WorkflowStep("s2", {"draft": "missing"}),  # not in the context
        ),
    )
    events = await collect(engine, definition, {"question": "Q"})
    assert seen(recorder, "s1") == [{"q": "Q"}]
    assert "s2" not in recorder
    assert events[-1].type == "error"
    assert events[-1].data["status"] == 422


def test_engine_satisfies_the_workflow_engine_port() -> None:
    # Runtime companion to the TYPE_CHECKING `_conforms` proof: the concrete
    # engine exposes the port surface.
    engine = build_engine(scripted("x", {}, output={}))
    assert isinstance(engine, SequentialWorkflowEngine)
    assert hasattr(engine, "run")
