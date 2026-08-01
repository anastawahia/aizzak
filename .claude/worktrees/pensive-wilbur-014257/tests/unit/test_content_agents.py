"""Unit tests for 4.6-b — ``read_text_file`` + the Data-Analysis / File-Editing
agents (FR-20.2/20.5, 11 §9). Purely hermetic: fake ``FilesAccess`` /
``StorageProvider`` / ``LLMProvider``."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest

from app.agents.data_analysis_agent.agent import DataAnalysisAgent
from app.agents.file_editing_agent.agent import FileEditingAgent
from app.framework.agent_runtime import (
    AgentDependencies,
    AgentRequest,
    BaseAgent,
    InMemoryAgentRegistry,
    PluginLoader,
    ResolvedLLM,
    read_text_file,
)
from app.framework.context.execution_context import ExecutionContext
from app.framework.errors import (
    AppError,
    NotFoundError,
    TooLargeError,
    UnsupportedTypeError,
    ValidationError,
)
from app.framework.identifiers import new_uuid7
from app.framework.ports.llm_provider import LlmChunk, LlmMessage, LlmParams, LlmResult

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


class FakeFileView:
    def __init__(
        self, *, content_type: str = "text/csv", size_bytes: int = 100, storage_key: str = "k1"
    ) -> None:
        self.content_type = content_type
        self.size_bytes = size_bytes
        self.storage_key = storage_key


class FakeFiles:
    def __init__(self, view: FakeFileView | None) -> None:
        self._view = view
        self.calls: list[str] = []

    async def get_readable(self, ctx: ExecutionContext, file_id: str) -> FakeFileView | None:
        self.calls.append(file_id)
        return self._view


class FakeStorage:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.gets: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        return None

    async def get(self, key: str) -> bytes:
        self.gets.append(key)
        return self._data

    async def delete(self, key: str) -> None:
        return None

    async def presign_get(self, key: str, ttl_s: int) -> str:
        return "url"

    async def presign_put(self, key: str, ttl_s: int, content_type: str) -> str:
        return "url"


class FakeLLM:
    provider = "fake"

    def __init__(self, deltas: Sequence[str]) -> None:
        self._deltas = deltas
        self.stream_calls: list[tuple[list[LlmMessage], LlmParams, str]] = []

    async def complete(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> LlmResult:  # pragma: no cover
        raise NotImplementedError

    def stream(
        self, messages: Sequence[LlmMessage], params: LlmParams, api_key: str
    ) -> AsyncIterator[LlmChunk]:
        self.stream_calls.append((list(messages), params, api_key))

        async def gen() -> AsyncIterator[LlmChunk]:
            for delta in self._deltas:
                yield LlmChunk(delta=delta)

        return gen()

    def supports(self, capability: str) -> bool:
        return capability == "streaming"


def make_deps(
    *,
    deltas: Sequence[str] = ("ok",),
    view: FakeFileView | None = None,
    data: bytes = b"a,b\n1,2\n",
) -> tuple[AgentDependencies, FakeFiles, FakeStorage, FakeLLM]:
    files = FakeFiles(view if view is not None else FakeFileView())
    storage = FakeStorage(data)
    llm = FakeLLM(deltas)
    deps = AgentDependencies(
        llm=ResolvedLLM(provider=llm, model="m", api_key="k"),
        files=files,
        storage=storage,
    )
    return deps, files, storage, llm


async def collect(agent: BaseAgent, req_input: dict[str, object]) -> list:
    return [event async for event in agent.run(AgentRequest(conversation_id=None, input=req_input))]


# --------------------------------------------------------------------------- #
# read_text_file helper                                                       #
# --------------------------------------------------------------------------- #


async def test_read_text_file_returns_decoded_bytes() -> None:
    files = FakeFiles(FakeFileView(storage_key="k9"))
    storage = FakeStorage(b"hello,world")
    text = await read_text_file(files, storage, make_ctx(), "f1", max_bytes=1000)
    assert text == "hello,world"
    assert files.calls == ["f1"]
    assert storage.gets == ["k9"]


async def test_read_text_file_is_lenient_on_bad_utf8() -> None:
    text = await read_text_file(
        FakeFiles(FakeFileView()), FakeStorage(b"a\xffb"), make_ctx(), "f", max_bytes=1000
    )
    assert text.startswith("a") and text.endswith("b")  # invalid byte replaced, no crash


@pytest.mark.parametrize("drop", ["files", "storage"])
async def test_read_text_file_unwired_is_500(drop: str) -> None:
    files = None if drop == "files" else FakeFiles(FakeFileView())
    storage = None if drop == "storage" else FakeStorage(b"x")
    with pytest.raises(AppError) as excinfo:
        await read_text_file(files, storage, make_ctx(), "f", max_bytes=1000)
    assert excinfo.value.status == 500


async def test_read_text_file_missing_file_is_404() -> None:
    with pytest.raises(NotFoundError):
        await read_text_file(FakeFiles(None), FakeStorage(b"x"), make_ctx(), "gone", max_bytes=1000)


async def test_read_text_file_non_textual_is_415() -> None:
    files = FakeFiles(FakeFileView(content_type="image/png"))
    with pytest.raises(UnsupportedTypeError):
        await read_text_file(files, FakeStorage(b"x"), make_ctx(), "f", max_bytes=1000)


async def test_read_text_file_too_large_is_413_and_never_fetches() -> None:
    files = FakeFiles(FakeFileView(size_bytes=5000))
    storage = FakeStorage(b"x")
    with pytest.raises(TooLargeError):
        await read_text_file(files, storage, make_ctx(), "f", max_bytes=1000)
    assert storage.gets == []  # bounded on metadata, before any fetch


async def test_read_text_file_accepts_application_json() -> None:
    files = FakeFiles(FakeFileView(content_type="application/json"))
    text = await read_text_file(files, FakeStorage(b'{"a":1}'), make_ctx(), "f", max_bytes=1000)
    assert text == '{"a":1}'


# --------------------------------------------------------------------------- #
# DataAnalysisAgent                                                           #
# --------------------------------------------------------------------------- #


async def test_data_analysis_streams_and_embeds_the_file_content() -> None:
    deps, files, _storage, llm = make_deps(deltas=["Two", " rows."], data=b"a,b\n1,2\n")
    events = await collect(
        DataAnalysisAgent(make_ctx(), deps), {"file_id": "f1", "text": "How many rows?"}
    )

    assert [e.data["delta"] for e in events if e.type == "token"] == ["Two", " rows."]
    assert events[-1].type == "final"
    assert events[-1].data["file_id"] == "f1"
    user_msg = llm.stream_calls[0][0][1]
    assert "How many rows?" in user_msg.content
    assert "a,b\n1,2\n" in user_msg.content
    assert files.calls == ["f1"]


async def test_data_analysis_uses_a_default_instruction() -> None:
    deps, _files, _storage, llm = make_deps()
    await collect(DataAnalysisAgent(make_ctx(), deps), {"file_id": "f1"})
    assert "Analyze this data" in llm.stream_calls[0][0][1].content


async def test_data_analysis_missing_file_id_is_422() -> None:
    deps, _files, _storage, _llm = make_deps()
    with pytest.raises(ValidationError):
        await collect(DataAnalysisAgent(make_ctx(), deps), {"text": "hi"})


async def test_data_analysis_unbound_llm_is_500() -> None:
    deps = AgentDependencies(files=FakeFiles(FakeFileView()), storage=FakeStorage(b"x"))
    with pytest.raises(AppError) as excinfo:
        await collect(DataAnalysisAgent(make_ctx(), deps), {"file_id": "f1"})
    assert excinfo.value.status == 500


async def test_data_analysis_non_textual_file_propagates_415() -> None:
    deps, _files, _storage, _llm = make_deps(view=FakeFileView(content_type="image/png"))
    with pytest.raises(UnsupportedTypeError):
        await collect(DataAnalysisAgent(make_ctx(), deps), {"file_id": "f1"})


# --------------------------------------------------------------------------- #
# FileEditingAgent                                                            #
# --------------------------------------------------------------------------- #


async def test_file_editing_streams_the_edited_content() -> None:
    deps, _files, _storage, llm = make_deps(deltas=["EDITED"], data=b"original text")
    events = await collect(
        FileEditingAgent(make_ctx(), deps),
        {"file_id": "f1", "instruction": "uppercase everything"},
    )

    assert "".join(e.data["delta"] for e in events if e.type == "token") == "EDITED"
    assert events[-1].type == "final"
    assert events[-1].data == {"text": "EDITED", "file_id": "f1"}
    user_msg = llm.stream_calls[0][0][1]
    assert "uppercase everything" in user_msg.content
    assert "original text" in user_msg.content


@pytest.mark.parametrize("missing", ["file_id", "instruction"])
async def test_file_editing_requires_file_id_and_instruction(missing: str) -> None:
    deps, _files, _storage, _llm = make_deps()
    req_input = {"file_id": "f1", "instruction": "do it"}
    del req_input[missing]
    with pytest.raises(ValidationError):
        await collect(FileEditingAgent(make_ctx(), deps), req_input)


# --------------------------------------------------------------------------- #
# Discovery                                                                    #
# --------------------------------------------------------------------------- #


async def test_loader_registers_both_content_agents() -> None:
    report = PluginLoader().load_into(InMemoryAgentRegistry())
    assert "data_analysis_agent" in report.loaded
    assert "file_editing_agent" in report.loaded
