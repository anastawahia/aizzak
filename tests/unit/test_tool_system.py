"""Unit tests for the tool system (Phase 4.3 — 02 §3.1/§3.4, FR-50/51/52).

Purely hermetic, no ``live_*`` marker: ``BaseTool``/``ToolRegistry`` are pure
kernel logic and the dynamic catalog is exercised through a fake satisfying the
framework-local ``DynamicToolCatalog`` twin (no ``integrations`` import — the
structural-injection boundary). Focus areas, one per section:

* the ``BaseTool``/``ToolSpec`` contract (frozen spec, ABC, the uniform
  ``__init__(deps)``);
* ``InMemoryToolRegistry`` semantics (register/get, 404 vs the R6 422 guards,
  duplicate refusal, garbage refusal, deterministic ``specs``);
* ``ToolResolver`` — the FR-51/52 merge: ``list`` unions static + dynamic,
  ``invoke`` routes by name, static-first on a (should-not-happen) collision,
  and passes ``deps``/``ctx``/``args`` through to the static tool.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import FrozenInstanceError
from typing import ClassVar

import pytest

from app.framework.agent_runtime import AgentDependencies
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.tools import (
    BaseTool,
    DiscoveredTool,
    InMemoryToolRegistry,
    ToolResolver,
    ToolSpec,
)
from app.framework.types import Json

_RESOLVER_LOGGER = "app.framework.tools.resolver"


# --------------------------------------------------------------------------- #
# Builders / fakes                                                            #
# --------------------------------------------------------------------------- #


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=f"{name} tool", parameters={"type": "object"})


class EchoTool(BaseTool):
    """A dep-free static tool — inherits the base ``__init__`` and ignores deps."""

    spec = spec("echo")

    async def run(self, ctx: ExecutionContext, args: Json) -> Json:
        return {"echoed": args, "ws": ctx.workspace_id}


class RecordingTool(BaseTool):
    """Captures the exact ``(deps, ctx, args)`` it was constructed/run with."""

    spec = spec("record")
    calls: ClassVar[list[tuple[AgentDependencies, ExecutionContext, Json]]] = []

    async def run(self, ctx: ExecutionContext, args: Json) -> Json:
        RecordingTool.calls.append((self.deps, ctx, args))
        return {"ok": True}


class FakeCatalog:
    """A fake dynamic catalog — structurally satisfies ``DynamicToolCatalog``."""

    def __init__(
        self,
        tools: Iterable[DiscoveredTool] = (),
        results: dict[str, Json] | None = None,
    ) -> None:
        self._tools = list(tools)
        self._results = results or {}
        self.invoked: list[tuple[str, Json]] = []

    async def list_tools(self, ctx: ExecutionContext) -> list[DiscoveredTool]:
        return list(self._tools)

    async def invoke_tool(self, ctx: ExecutionContext, name: str, args: Json) -> Json:
        self.invoked.append((name, args))
        if name not in self._results:
            raise NotFoundError(f"unknown tool {name!r}")
        return self._results[name]


def discovered(name: str) -> DiscoveredTool:
    return DiscoveredTool(
        name=name, description=f"{name} tool", parameters={"type": "object"}, source=f"mcp:{name}"
    )


# --------------------------------------------------------------------------- #
# BaseTool / ToolSpec contract                                                #
# --------------------------------------------------------------------------- #


def test_tool_spec_is_frozen() -> None:
    s = spec("x")
    with pytest.raises(FrozenInstanceError):
        s.name = "y"  # type: ignore[misc]


def test_base_tool_cannot_be_instantiated_without_run() -> None:
    class Incomplete(BaseTool):
        spec = spec("incomplete")

    with pytest.raises(TypeError):
        Incomplete(AgentDependencies())


def test_base_tool_init_binds_the_deps_bundle() -> None:
    deps = AgentDependencies()
    tool = EchoTool(deps)
    assert tool.deps is deps


# --------------------------------------------------------------------------- #
# InMemoryToolRegistry                                                        #
# --------------------------------------------------------------------------- #


def test_register_then_get_returns_the_class() -> None:
    registry = InMemoryToolRegistry()
    registry.register(EchoTool)
    assert registry.get("echo") is EchoTool


def test_get_unknown_name_is_not_found_404() -> None:
    registry = InMemoryToolRegistry()
    with pytest.raises(NotFoundError):
        registry.get("nope")


@pytest.mark.parametrize("name", [5, "", "   "], ids=["int", "empty", "blank"])
def test_get_non_string_name_is_a_caller_bug_422(name: object) -> None:
    registry = InMemoryToolRegistry()
    registry.register(EchoTool)
    with pytest.raises(ValidationError):
        registry.get(name)  # type: ignore[arg-type]


def test_register_refuses_a_duplicate_name() -> None:
    class OtherEcho(BaseTool):
        spec = spec("echo")  # same name as EchoTool

        async def run(self, ctx: ExecutionContext, args: Json) -> Json:
            return {}

    registry = InMemoryToolRegistry()
    registry.register(EchoTool)
    with pytest.raises(ValidationError):
        registry.register(OtherEcho)
    assert registry.get("echo") is EchoTool  # first one kept


@pytest.mark.parametrize(
    "garbage", [42, "echo", object(), lambda: None], ids=["int", "str", "instance", "callable"]
)
def test_register_refuses_non_basetool(garbage: object) -> None:
    registry = InMemoryToolRegistry()
    with pytest.raises(ValidationError):
        registry.register(garbage)  # type: ignore[arg-type]


def test_register_refuses_the_abstract_base_itself() -> None:
    registry = InMemoryToolRegistry()
    # Reason-pinned: BaseTool must be refused BY the abstract guard (it is also
    # spec-less, so an un-pinned assert would pass via the downstream no-spec
    # branch and not kill a removal of the `is BaseTool` guard).
    with pytest.raises(ValidationError, match="abstract"):
        registry.register(BaseTool)


def test_register_refuses_an_abstract_subclass_missing_run() -> None:
    class NoRun(BaseTool):
        spec = spec("norun")

    registry = InMemoryToolRegistry()
    with pytest.raises(ValidationError):
        registry.register(NoRun)


def test_register_refuses_a_concrete_tool_with_no_spec() -> None:
    class NoSpec(BaseTool):
        async def run(self, ctx: ExecutionContext, args: Json) -> Json:
            return {}

    registry = InMemoryToolRegistry()
    with pytest.raises(ValidationError):
        registry.register(NoSpec)


def test_register_refuses_a_non_toolspec_spec() -> None:
    class BadSpec(BaseTool):
        spec = "not a spec"  # type: ignore[assignment]

        async def run(self, ctx: ExecutionContext, args: Json) -> Json:
            return {}

    registry = InMemoryToolRegistry()
    with pytest.raises(ValidationError):
        registry.register(BadSpec)


def test_register_refuses_a_non_string_spec_name() -> None:
    class IntName(BaseTool):
        spec = ToolSpec(name=123, description="", parameters={})  # type: ignore[arg-type]

        async def run(self, ctx: ExecutionContext, args: Json) -> Json:
            return {}

    registry = InMemoryToolRegistry()
    with pytest.raises(ValidationError):
        registry.register(IntName)


def test_specs_is_name_sorted_regardless_of_registration_order() -> None:
    class BravoTool(BaseTool):
        spec = spec("bravo")

        async def run(self, ctx: ExecutionContext, args: Json) -> Json:
            return {}

    class AlphaTool(BaseTool):
        spec = spec("alpha")

        async def run(self, ctx: ExecutionContext, args: Json) -> Json:
            return {}

    registry = InMemoryToolRegistry()
    registry.register(BravoTool)
    registry.register(AlphaTool)
    assert [s.name for s in registry.specs()] == ["alpha", "bravo"]


# --------------------------------------------------------------------------- #
# ToolResolver — the FR-51/52 merge                                           #
# --------------------------------------------------------------------------- #


def make_resolver(
    *,
    static: Iterable[type[BaseTool]] = (),
    catalog: FakeCatalog | None = None,
    deps: AgentDependencies | None = None,
) -> ToolResolver:
    registry = InMemoryToolRegistry()
    for tool_cls in static:
        registry.register(tool_cls)
    return ToolResolver(registry, catalog or FakeCatalog(), deps or AgentDependencies())


async def test_list_unions_static_specs_and_dynamic_tools() -> None:
    catalog = FakeCatalog(tools=[discovered("gmail.send"), discovered("github.issue")])
    resolver = make_resolver(static=[EchoTool], catalog=catalog)
    names = [s.name for s in await resolver.list(make_ctx())]
    assert names == ["echo", "gmail.send", "github.issue"]


async def test_list_drops_a_dynamic_tool_that_shadows_a_static_one_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    catalog = FakeCatalog(tools=[discovered("echo"), discovered("gmail.send")])
    resolver = make_resolver(static=[EchoTool], catalog=catalog)
    with caplog.at_level(logging.WARNING):
        names = [s.name for s in await resolver.list(make_ctx())]

    assert names == ["echo", "gmail.send"]  # the shadowing dynamic "echo" dropped
    assert any(
        r.name == _RESOLVER_LOGGER and r.msg == "tool_resolver.dynamic_shadowed_by_static"
        for r in caplog.records
    )


async def test_list_drops_a_dynamic_tool_with_a_malformed_name_without_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Catalog names come from external MCP servers (untrusted). An unhashable
    # name would crash the set-membership merge, a non-string name would leak a
    # bad ToolSpec — the R6 family. Both must be dropped, not crash.
    unhashable = DiscoveredTool(
        name=["not", "a", "string"],  # type: ignore[arg-type]
        description="x",
        parameters={},
        source="mcp:evil",
    )
    int_name = DiscoveredTool(name=123, description="x", parameters={}, source="mcp:buggy")  # type: ignore[arg-type]
    catalog = FakeCatalog(tools=[unhashable, int_name, discovered("gmail.send")])
    resolver = make_resolver(static=[EchoTool], catalog=catalog)
    with caplog.at_level(logging.WARNING):
        names = [s.name for s in await resolver.list(make_ctx())]

    assert names == ["echo", "gmail.send"]  # both malformed dropped, no crash, no leak
    assert (
        sum(
            r.name == _RESOLVER_LOGGER and r.msg == "tool_resolver.dynamic_tool_malformed_name"
            for r in caplog.records
        )
        == 2
    )


async def test_invoke_routes_a_static_name_to_the_static_tool() -> None:
    resolver = make_resolver(static=[EchoTool])
    ctx = make_ctx()
    result = await resolver.invoke(ctx, "echo", {"hi": "there"})
    assert result == {"echoed": {"hi": "there"}, "ws": ctx.workspace_id}


async def test_invoke_passes_deps_ctx_and_args_to_the_static_tool() -> None:
    RecordingTool.calls.clear()
    deps = AgentDependencies()
    resolver = make_resolver(static=[RecordingTool], deps=deps)
    ctx = make_ctx()
    await resolver.invoke(ctx, "record", {"a": 1})
    assert RecordingTool.calls == [(deps, ctx, {"a": 1})]
    seen_deps, seen_ctx, _ = RecordingTool.calls[0]
    assert seen_deps is deps and seen_ctx is ctx  # exact instances, not copies


async def test_invoke_delegates_a_dynamic_name_to_the_catalog() -> None:
    catalog = FakeCatalog(tools=[discovered("gmail.send")], results={"gmail.send": {"id": "m1"}})
    resolver = make_resolver(static=[EchoTool], catalog=catalog)
    ctx = make_ctx()
    result = await resolver.invoke(ctx, "gmail.send", {"to": "x@y.z"})
    assert result == {"id": "m1"}
    assert catalog.invoked == [("gmail.send", {"to": "x@y.z"})]


async def test_invoke_resolves_static_first_on_a_collision() -> None:
    # A dynamic tool with the same name must NOT win — platform code does, and
    # the catalog is never even consulted for that name (decision Q2-A).
    catalog = FakeCatalog(results={"echo": {"from": "catalog"}})
    resolver = make_resolver(static=[EchoTool], catalog=catalog)
    ctx = make_ctx()
    result = await resolver.invoke(ctx, "echo", {"x": 1})
    assert result == {"echoed": {"x": 1}, "ws": ctx.workspace_id}
    assert catalog.invoked == []  # static short-circuited the catalog


async def test_invoke_unknown_name_surfaces_the_catalog_not_found() -> None:
    resolver = make_resolver(static=[EchoTool], catalog=FakeCatalog())
    with pytest.raises(NotFoundError):
        await resolver.invoke(make_ctx(), "does.not.exist", {})
