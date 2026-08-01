"""Unit tests for ``framework/agent_runtime/`` (Phase 4.1 — 02 §3.2/§3.4, 11 §5).

Purely hermetic, no ``live_*`` marker (the 2.7/2.9 precedent — there is no
service here at all). Discovery, isolation and the AC-04 add/delete proof all
run against SYNTHETIC agent packages built under ``tmp_path`` (4.1 decision
3): the real ``app.agents`` five are 4.6's deliverable and are enumerated in
the ``agents-independent`` import-linter contract, so these tests must not
touch them — except one test that loads the REAL tree read-only to pin
today's boot behavior (five empty stubs → five isolations, zero raise).

Focus areas, one per section below:

* the §3.2 contract types (frozen carriers, the six FR-15 states, ABC
  enforcement, dispose-default, the sealed ``AgentDependencies`` seam);
* ``InMemoryAgentRegistry`` semantics (per-request creation, 404 vs the R6
  call-time guards' 422, duplicate refusal, deterministic ``list``);
* ``PluginLoader`` discovery + the isolation matrix (a broken plugin never
  takes the scan down, and the report says why);
* AC-04 end to end: add a folder → discovered; delete it → deregistered on
  the next scan and ``create`` answers 404.
"""

from __future__ import annotations

import importlib
import logging
import shutil
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.framework.agent_runtime import (
    AgentDependencies,
    AgentEvent,
    AgentLifecycle,
    AgentMetadata,
    AgentRequest,
    BaseAgent,
    InMemoryAgentRegistry,
    PluginLoader,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7

# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def make_ctx() -> ExecutionContext:
    return ExecutionContext(
        workspace_id=new_uuid7(),
        user_id=new_uuid7(),
        correlation_id=new_uuid7(),
        roles=frozenset({"member"}),
    )


def make_metadata(key: str, *, name: str | None = None) -> AgentMetadata:
    return AgentMetadata(
        key=key,
        name=name or f"{key} agent",
        version="1.0.0",
        description="synthetic test agent",
        capabilities=frozenset({"chat"}),
        required_permissions=frozenset({"agents:invoke"}),
    )


def manifest_src(key: str) -> str:
    """A valid ``manifest.py`` — the 11 §2 shape, values matching ``make_metadata``."""
    return f'''
from app.framework.agent_runtime.metadata import AgentMetadata

METADATA = AgentMetadata(
    key="{key}",
    name="{key} agent",
    version="1.0.0",
    description="synthetic test agent",
    capabilities=frozenset({{"chat"}}),
    required_permissions=frozenset({{"agents:invoke"}}),
)
'''


def agent_src(class_name: str = "ProbeAgent") -> str:
    """A valid ``agent.py`` in the CANONICAL authoring style (11 §3):
    ``metadata = METADATA`` binding + an async-generator ``run``."""
    return f"""
from app.framework.agent_runtime.base_agent import AgentEvent, BaseAgent

from .manifest import METADATA


class {class_name}(BaseAgent):
    metadata = METADATA

    async def initialize(self) -> None:
        self.initialized = True

    async def run(self, req):
        yield AgentEvent(type="final", data={{"agent": METADATA.key}})
"""


def valid_agent_folder(key: str) -> dict[str, str]:
    return {"manifest": manifest_src(key), "agent": agent_src()}


@contextmanager
def agents_package(tmp_path: Path, agents: dict[str, dict[str, str]]) -> Iterator[str]:
    """Materialize a synthetic agents package on disk; yield its import name.

    Each entry of ``agents`` is one plugin folder; its dict maps ``manifest``/
    ``agent`` to file sources (a missing key = a missing file) and optionally
    ``__init__`` to the folder's ``__init__.py`` body. The package name is
    uuid-unique so parallel tests never collide in ``sys.modules``; teardown
    removes the ``sys.path`` entry and purges every imported fixture module.
    """
    pkg_name = f"agents_fixture_{uuid.uuid4().hex[:10]}"
    root = tmp_path / pkg_name
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for folder, files in agents.items():
        plugin = root / folder
        plugin.mkdir()
        (plugin / "__init__.py").write_text(files.get("__init__", ""), encoding="utf-8")
        for module in ("manifest", "agent"):
            if module in files:
                (plugin / f"{module}.py").write_text(files[module], encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    try:
        yield pkg_name
    finally:
        sys.path.remove(str(tmp_path))
        for mod in [m for m in sys.modules if m == pkg_name or m.startswith(f"{pkg_name}.")]:
            del sys.modules[mod]


class MinimalAgent(BaseAgent):
    """Direct-registration fixture in the SECOND legal authoring style:
    ``def run`` returning an inner async generator (the port's ``def stream``
    shape) — together with ``agent_src``'s async-generator style, both author
    forms are proven live."""

    async def initialize(self) -> None:
        return None

    def run(self, req: AgentRequest) -> AsyncIterator[AgentEvent]:
        async def _events() -> AsyncIterator[AgentEvent]:
            yield AgentEvent(type="final", data={})

        return _events()


class RecordingFactory:
    """Spy factory — proves ``create`` delegates positionally, once."""

    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionContext, AgentDependencies]] = []

    def __call__(self, ctx: ExecutionContext, deps: AgentDependencies) -> BaseAgent:
        self.calls.append((ctx, deps))
        return MinimalAgent(ctx, deps)


# --------------------------------------------------------------------------- #
# §3.2 contract types                                                         #
# --------------------------------------------------------------------------- #


def test_agent_metadata_is_frozen_and_defaults_tools_to_empty() -> None:
    metadata = make_metadata("rag_agent")
    assert metadata.default_tools == ()
    with pytest.raises(FrozenInstanceError):
        metadata.key = "other"  # type: ignore[misc]


def test_agent_request_and_event_are_frozen_with_contract_defaults() -> None:
    req = AgentRequest(conversation_id=None, input={"text": "hi"})
    assert req.stream is False
    with pytest.raises(FrozenInstanceError):
        req.stream = True  # type: ignore[misc]
    event = AgentEvent(type="token", data={"delta": "h"})
    with pytest.raises(FrozenInstanceError):
        event.type = "final"  # type: ignore[misc]


def test_agent_lifecycle_carries_exactly_the_six_fr15_states() -> None:
    assert [(state.name, state.value) for state in AgentLifecycle] == [
        ("CREATED", "created"),
        ("INITIALIZED", "initialized"),
        ("RUNNING", "running"),
        ("COMPLETED", "completed"),
        ("FAILED", "failed"),
        ("DISPOSED", "disposed"),
    ]
    assert AgentLifecycle.CREATED == "created"  # str-enum: API/DB friendly


def test_base_agent_refuses_instantiation_without_initialize_and_run() -> None:
    class Incomplete(BaseAgent):
        pass

    with pytest.raises(TypeError):
        Incomplete(make_ctx(), AgentDependencies())  # type: ignore[abstract]


async def test_base_agent_dispose_defaults_to_noop_and_init_binds() -> None:
    ctx = make_ctx()
    deps = AgentDependencies()
    agent = MinimalAgent(ctx, deps)
    assert agent.ctx is ctx
    assert agent.deps is deps
    assert await agent.dispose() is None  # type: ignore[func-returns-value]


def test_agent_dependencies_seam_is_sealed_against_ad_hoc_attributes() -> None:
    deps = AgentDependencies()
    # Since 4.4 the bundle is a frozen+slots dataclass (it grew a `web_search`
    # field): setting an UNKNOWN name raises TypeError (the CPython
    # frozen+slots `__setattr__` artifact), a KNOWN field would raise
    # FrozenInstanceError — either way the seam stays sealed (no ad-hoc attrs,
    # no mutation). Catching both keeps the test meaningful: it still fails if
    # the bundle ever becomes open/mutable.
    with pytest.raises((AttributeError, TypeError)):
        deps.llm = object()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# InMemoryAgentRegistry                                                       #
# --------------------------------------------------------------------------- #


def test_create_returns_a_new_instance_per_call_with_ctx_and_deps() -> None:
    registry = InMemoryAgentRegistry()
    factory = RecordingFactory()
    registry.register(make_metadata("rag_agent"), factory)
    ctx, deps = make_ctx(), AgentDependencies()
    first = registry.create("rag_agent", ctx, deps)
    second = registry.create("rag_agent", ctx, deps)
    assert isinstance(first, BaseAgent)
    assert first is not second  # FR-10/11: an instance per request, never cached
    assert factory.calls == [(ctx, deps), (ctx, deps)]


def test_create_unknown_key_is_not_found_404() -> None:
    registry = InMemoryAgentRegistry()
    with pytest.raises(NotFoundError) as excinfo:
        registry.create("ghost_agent", make_ctx(), AgentDependencies())
    assert excinfo.value.status == 404
    assert "ghost_agent" in str(excinfo.value)


@pytest.mark.parametrize(
    "key",
    [None, 7, "", "   ", ["rag_agent"]],
    ids=["none", "int", "empty", "blank", "unhashable_list"],
)
def test_create_non_string_key_is_a_caller_bug_422_not_a_crash(key: object) -> None:
    # The R6 family (2.9 verifier RC-C): an unhashable key must become a 422,
    # never a raw TypeError from the dict lookup.
    registry = InMemoryAgentRegistry()
    registry.register(make_metadata("rag_agent"), RecordingFactory())
    with pytest.raises(ValidationError) as excinfo:
        registry.create(key, make_ctx(), AgentDependencies())  # type: ignore[arg-type]
    assert excinfo.value.status == 422


def test_register_refuses_a_duplicate_key_and_keeps_the_first() -> None:
    registry = InMemoryAgentRegistry()
    first = RecordingFactory()
    registry.register(make_metadata("rag_agent"), first)
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_metadata("rag_agent", name="impostor"), RecordingFactory())
    assert "rag_agent" in str(excinfo.value)
    registry.create("rag_agent", make_ctx(), AgentDependencies())
    assert len(first.calls) == 1  # the original registration still serves the key


@pytest.mark.parametrize(
    "metadata",
    [None, {"key": "rag_agent"}, "rag_agent"],
    ids=["none", "dict", "str"],
)
def test_register_refuses_non_metadata_garbage_as_422(metadata: object) -> None:
    registry = InMemoryAgentRegistry()
    with pytest.raises(ValidationError):
        registry.register(metadata, RecordingFactory())  # type: ignore[arg-type]


@pytest.mark.parametrize("key", [5, "", "   "], ids=["int", "empty", "blank"])
def test_register_refuses_a_bad_key_inside_valid_metadata(key: object) -> None:
    # AgentMetadata is a dumb carrier — an int key passes the isinstance
    # guard and, unrefused, would detonate LATER as a str/int sort TypeError
    # in list() (R6 action-at-a-distance). The boundary must 422 instead.
    registry = InMemoryAgentRegistry()
    bad = AgentMetadata(
        key=key,  # type: ignore[arg-type]
        name="bad",
        version="1.0.0",
        description="bad key",
        capabilities=frozenset(),
        required_permissions=frozenset(),
    )
    with pytest.raises(ValidationError) as excinfo:
        registry.register(bad, RecordingFactory())
    assert excinfo.value.status == 422
    assert registry.list() == []  # and the map stayed sortable/clean


def test_register_refuses_a_non_callable_factory() -> None:
    registry = InMemoryAgentRegistry()
    with pytest.raises(ValidationError) as excinfo:
        registry.register(make_metadata("rag_agent"), "not-a-factory")  # type: ignore[arg-type]
    assert "rag_agent" in str(excinfo.value)


def test_list_is_key_sorted_and_returns_the_registered_objects() -> None:
    registry = InMemoryAgentRegistry()
    charlie = make_metadata("charlie_agent")
    alpha = make_metadata("alpha_agent")
    beta = make_metadata("beta_agent")
    for metadata in (charlie, alpha, beta):
        registry.register(metadata, RecordingFactory())
    listed = registry.list()
    assert [m.key for m in listed] == ["alpha_agent", "beta_agent", "charlie_agent"]
    assert listed[0] is alpha  # identity — the registry never copies metadata


# --------------------------------------------------------------------------- #
# PluginLoader — discovery                                                    #
# --------------------------------------------------------------------------- #


def test_discovers_and_registers_every_valid_agent(tmp_path: Path) -> None:
    tree = {
        "alpha_agent": valid_agent_folder("alpha_agent"),
        "beta_agent": valid_agent_folder("beta_agent"),
    }
    with agents_package(tmp_path, tree) as pkg:
        registry = InMemoryAgentRegistry()
        report = PluginLoader(package=pkg).load_into(registry)
    assert report.loaded == ("alpha_agent", "beta_agent")
    assert report.failures == ()
    assert isinstance(report.loaded, tuple)
    assert [m.key for m in registry.list()] == ["alpha_agent", "beta_agent"]
    agent = registry.create("alpha_agent", make_ctx(), AgentDependencies())
    assert isinstance(agent, BaseAgent)
    assert agent.metadata.key == "alpha_agent"


async def test_created_agent_runs_in_the_guide_authoring_style(tmp_path: Path) -> None:
    with agents_package(tmp_path, {"echo_agent": valid_agent_folder("echo_agent")}) as pkg:
        registry = InMemoryAgentRegistry()
        PluginLoader(package=pkg).load_into(registry)
        agent = registry.create("echo_agent", make_ctx(), AgentDependencies())
        await agent.initialize()
        events = [event async for event in agent.run(AgentRequest(conversation_id=None, input={}))]
    assert events == [AgentEvent(type="final", data={"agent": "echo_agent"})]


def test_registered_metadata_is_the_manifest_object_itself(tmp_path: Path) -> None:
    with agents_package(tmp_path, {"id_agent": valid_agent_folder("id_agent")}) as pkg:
        registry = InMemoryAgentRegistry()
        PluginLoader(package=pkg).load_into(registry)
        manifest = sys.modules[f"{pkg}.id_agent.manifest"]
        assert registry.list()[0] is manifest.METADATA


def test_report_order_is_sorted_not_filesystem_order(tmp_path: Path) -> None:
    tree = {
        "zulu_agent": valid_agent_folder("zulu_agent"),
        "alpha_agent": valid_agent_folder("alpha_agent"),
    }
    with agents_package(tmp_path, tree) as pkg:
        report = PluginLoader(package=pkg).load_into(InMemoryAgentRegistry())
    assert report.loaded == ("alpha_agent", "zulu_agent")


def test_loose_modules_and_underscore_packages_are_not_candidates(tmp_path: Path) -> None:
    tree = {
        "real_agent": valid_agent_folder("real_agent"),
        "_scratch": {"__init__": "raise RuntimeError('must never be imported')"},
    }
    with agents_package(tmp_path, tree) as pkg:
        stray = tmp_path / pkg / "stray.py"
        stray.write_text("raise RuntimeError('must never be imported')", encoding="utf-8")
        importlib.invalidate_caches()
        report = PluginLoader(package=pkg).load_into(InMemoryAgentRegistry())
    assert report.loaded == ("real_agent",)
    assert report.failures == ()


def test_missing_root_package_is_an_operator_error_that_passes_through() -> None:
    loader = PluginLoader(package="agents_fixture_does_not_exist")
    with pytest.raises(ModuleNotFoundError):
        loader.load_into(InMemoryAgentRegistry())


def test_non_package_root_is_a_legible_boot_error_not_an_attribute_error() -> None:
    # A plain module has no __path__; the boot error must name the mistake.
    loader = PluginLoader(package="app.framework.types")
    with pytest.raises(ValidationError) as excinfo:
        loader.load_into(InMemoryAgentRegistry())
    assert "is not a package" in str(excinfo.value)


def test_real_app_agents_tree_boots_with_all_five_agents_registered() -> None:
    # AC-04 (add side): all five FR-20 agents are valid plugins discovered by a
    # REAL scan of app.agents — zero isolation, zero raise, no kernel change.
    # `loaded` is folder-sorted (the loader's deterministic order). The delete
    # side of AC-04 is proven on synthetic trees above.
    report = PluginLoader().load_into(InMemoryAgentRegistry())
    assert report.loaded == (
        "data_analysis_agent",
        "file_editing_agent",
        "image_agent",
        "rag_agent",
        "video_agent",
    )
    assert report.failures == ()


# --------------------------------------------------------------------------- #
# PluginLoader — isolation matrix                                             #
# --------------------------------------------------------------------------- #

BROKEN_CASES = [
    pytest.param(
        "broken_agent",
        {"manifest": "raise RuntimeError('manifest boom')", "agent": agent_src()},
        "manifest boom",
        id="manifest_import_raises",
    ),
    pytest.param(
        "broken_agent",
        {"agent": agent_src()},
        "manifest",
        id="manifest_file_missing",
    ),
    pytest.param(
        "broken_agent",
        {"manifest": "X = 1", "agent": agent_src()},
        "METADATA is missing",
        id="metadata_missing",
    ),
    pytest.param(
        "broken_agent",
        {"manifest": "METADATA = {'key': 'broken_agent'}", "agent": agent_src()},
        "must be an AgentMetadata",
        id="metadata_wrong_type_r6",
    ),
    pytest.param(
        "broken_agent",
        {"manifest": manifest_src("other_key"), "agent": agent_src()},
        "does not match the agent folder name",
        id="key_folder_mismatch",
    ),
    pytest.param(
        "Broken_Agent",
        {"manifest": manifest_src("Broken_Agent"), "agent": agent_src()},
        "not snake_case",
        id="key_not_snake_case",
    ),
    pytest.param(
        "broken_agent",
        {
            "manifest": (
                "from app.framework.agent_runtime.metadata import AgentMetadata\n"
                "METADATA = AgentMetadata(\n"
                "    key=5, name='n', version='1.0.0', description='d',\n"
                "    capabilities=frozenset(), required_permissions=frozenset(),\n"
                ")\n"
            ),
            "agent": agent_src(),
        },
        "not snake_case",
        id="key_not_a_string_r6",
    ),
    pytest.param(
        "broken_agent",
        {"manifest": manifest_src("broken_agent"), "agent": "raise RuntimeError('agent boom')"},
        "agent boom",
        id="agent_import_raises",
    ),
    pytest.param(
        "broken_agent",
        {"manifest": manifest_src("broken_agent")},
        "agent",
        id="agent_file_missing",
    ),
    pytest.param(
        "broken_agent",
        {"manifest": manifest_src("broken_agent"), "agent": "X = 1"},
        "no concrete BaseAgent subclass",
        id="no_agent_class",
    ),
    pytest.param(
        "broken_agent",
        {
            "manifest": manifest_src("broken_agent"),
            "agent": (
                "from app.framework.agent_runtime.base_agent import BaseAgent\n"
                "from .manifest import METADATA\n"
                "class StillAbstract(BaseAgent):\n"
                "    metadata = METADATA\n"
                "    async def initialize(self) -> None: ...\n"
            ),
        },
        "no concrete BaseAgent subclass",
        id="only_abstract_subclass",
    ),
    pytest.param(
        "broken_agent",
        {
            "manifest": manifest_src("broken_agent"),
            "agent": agent_src("FirstAgent") + agent_src("SecondAgent"),
        },
        "ambiguous",
        id="two_concrete_subclasses",
    ),
    pytest.param(
        "broken_agent",
        {
            "manifest": manifest_src("broken_agent"),
            "agent": (
                "from app.framework.agent_runtime.base_agent import AgentEvent, BaseAgent\n"
                "from app.framework.agent_runtime.metadata import AgentMetadata\n"
                "from .manifest import METADATA\n"
                "class UnboundAgent(BaseAgent):\n"
                "    metadata = AgentMetadata(\n"
                "        key='broken_agent', name='broken_agent agent', version='1.0.0',\n"
                "        description='synthetic test agent',\n"
                "        capabilities=frozenset({'chat'}),\n"
                "        required_permissions=frozenset({'agents:invoke'}),\n"
                "    )\n"
                "    async def initialize(self) -> None: ...\n"
                "    async def run(self, req):\n"
                "        yield AgentEvent(type='final', data={})\n"
            ),
        },
        "is not the manifest's METADATA",
        id="metadata_equal_copy_fails_identity",
    ),
]


@pytest.mark.parametrize(("folder", "files", "reason_pin"), BROKEN_CASES)
def test_a_broken_plugin_is_isolated_and_the_healthy_one_survives(
    tmp_path: Path, folder: str, files: dict[str, str], reason_pin: str
) -> None:
    tree = {folder: files, "healthy_agent": valid_agent_folder("healthy_agent")}
    with agents_package(tmp_path, tree) as pkg:
        registry = InMemoryAgentRegistry()
        report = PluginLoader(package=pkg).load_into(registry)
    assert report.loaded == ("healthy_agent",)
    assert [m.key for m in registry.list()] == ["healthy_agent"]
    assert len(report.failures) == 1
    failure = report.failures[0]
    assert failure.plugin == folder
    assert reason_pin in failure.reason


def test_a_class_merely_imported_from_another_agent_does_not_count(tmp_path: Path) -> None:
    # The __module__-prefix guard in the loader's concrete-class scan: a
    # plugin whose agent.py IMPORTS a sibling's concrete class instead of
    # defining its own must be isolated as "no concrete BaseAgent subclass".
    # The REASON substring is the load-bearing assertion: without the guard
    # the scan finds the foreign class and fails on the metadata-identity
    # check instead — same isolation, different reason — which is exactly how
    # the 4.1 verifier's mutation 6 survived the suite before this test.
    tree = {
        "donor_agent": valid_agent_folder("donor_agent"),
        "importer_agent": {
            "manifest": manifest_src("importer_agent"),
            "agent": "from ..donor_agent.agent import ProbeAgent\n",
        },
    }
    with agents_package(tmp_path, tree) as pkg:
        registry = InMemoryAgentRegistry()
        report = PluginLoader(package=pkg).load_into(registry)
    assert report.loaded == ("donor_agent",)
    assert [m.key for m in registry.list()] == ["donor_agent"]
    assert len(report.failures) == 1
    assert report.failures[0].plugin == "importer_agent"
    assert "no concrete BaseAgent subclass" in report.failures[0].reason


def test_isolation_is_logged_with_the_plugin_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tree = {"broken_agent": {"manifest": "raise RuntimeError('boom')", "agent": agent_src()}}
    with (
        caplog.at_level(logging.WARNING, logger="app.framework.agent_runtime.plugin_loader"),
        agents_package(tmp_path, tree) as pkg,
    ):
        PluginLoader(package=pkg).load_into(InMemoryAgentRegistry())
    assert any(record.message == "plugin_loader.isolated" for record in caplog.records)


def test_rescanning_into_the_same_registry_isolates_every_duplicate(tmp_path: Path) -> None:
    # The registry is rebuilt per boot (11 §5); feeding one registry twice is
    # the duplicate-key path, and the strict core + forgiving edge must turn
    # the whole second scan into isolations, not silent re-registrations.
    with agents_package(tmp_path, {"dup_agent": valid_agent_folder("dup_agent")}) as pkg:
        registry = InMemoryAgentRegistry()
        loader = PluginLoader(package=pkg)
        first = loader.load_into(registry)
        second = loader.load_into(registry)
    assert first.loaded == ("dup_agent",)
    assert second.loaded == ()
    assert len(second.failures) == 1
    assert "already registered" in second.failures[0].reason
    assert [m.key for m in registry.list()] == ["dup_agent"]


# --------------------------------------------------------------------------- #
# AC-04 — add and delete without touching the core                            #
# --------------------------------------------------------------------------- #


def test_ac04_dropping_a_folder_adds_and_deleting_it_deregisters(tmp_path: Path) -> None:
    with agents_package(tmp_path, {"first_agent": valid_agent_folder("first_agent")}) as pkg:
        boot1 = InMemoryAgentRegistry()
        report1 = PluginLoader(package=pkg).load_into(boot1)
        assert report1.loaded == ("first_agent",)

        # Drop a new folder in — no core change, next boot discovers it.
        second = tmp_path / pkg / "second_agent"
        second.mkdir()
        (second / "__init__.py").write_text("", encoding="utf-8")
        (second / "manifest.py").write_text(manifest_src("second_agent"), encoding="utf-8")
        (second / "agent.py").write_text(agent_src(), encoding="utf-8")
        importlib.invalidate_caches()
        boot2 = InMemoryAgentRegistry()
        report2 = PluginLoader(package=pkg).load_into(boot2)
        assert report2.loaded == ("first_agent", "second_agent")

        # Delete the first folder — next boot deregisters it (AC-04's other
        # half) and /agents/{key}/invoke's lookup answers 404.
        shutil.rmtree(tmp_path / pkg / "first_agent")
        importlib.invalidate_caches()
        boot3 = InMemoryAgentRegistry()
        report3 = PluginLoader(package=pkg).load_into(boot3)
        assert report3.loaded == ("second_agent",)
        assert [m.key for m in boot3.list()] == ["second_agent"]
        with pytest.raises(NotFoundError) as excinfo:
            boot3.create("first_agent", make_ctx(), AgentDependencies())
        assert excinfo.value.status == 404
