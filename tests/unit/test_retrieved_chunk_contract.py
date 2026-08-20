"""Contract-match test — retrieval plan §3.1/§3.9 step 1 (س-19, ``P-18``).

Three layers were meant to expand IN LOCKSTEP: the knowledge module's
``RetrievedChunk`` port DTO, the framework kernel's ``RetrievedChunkView``
Protocol (the agent-facing read shape), and the API's ``RetrievedChunkOut``
wire DTO. A step that widened only one or two of them would leave a citation
field an agent or a client could never actually reach — silent drift of
EXACTLY the kind ``test_api_conventions.py`` exists to catch for the wire
shape against ``openapi.yaml``; this is the analogous guard for the three
Python-side shapes that feed it.

Two things are checked, not one: the field NAMES match across all three
(structural), AND real, non-``None`` values placed on a ``RetrievedChunk``
survive being read back through each shape unchanged (§6 risk 1's test
rule — a contract-match test that only asserted field presence would not
have caught three always-``None`` fields; this one would).
"""

from __future__ import annotations

import dataclasses

from app.api.v1.dto.knowledge import RetrievedChunkOut
from app.framework.agent_runtime.deps_ports import RetrievedChunkView
from app.modules.knowledge.ports.retrieval import RetrievedChunk


def _view_property_names() -> set[str]:
    return {
        name for name, member in vars(RetrievedChunkView).items() if isinstance(member, property)
    }


def test_retrieved_chunk_field_names_match_across_the_three_layers() -> None:
    port_fields = {f.name for f in dataclasses.fields(RetrievedChunk)}
    view_properties = _view_property_names()
    api_fields = set(RetrievedChunkOut.model_fields)

    assert port_fields == view_properties == api_fields
    # Pinned explicitly (not just mutual equality) so a step that widens all
    # three in the SAME wrong way still fails loudly.
    assert port_fields == {
        "document_id",
        "chunk_id",
        "text",
        "score",
        "file_name",
        "page_number",
        "section",
    }


def test_retrieved_chunk_view_is_satisfied_structurally_by_the_port_dataclass() -> None:
    """``RetrievedChunkView``'s own docstring claims ``RetrievedChunk``
    "satisfies this structurally" — checked here by actually reading every
    declared property name off a real instance, the same access an agent
    (``self.deps.knowledge.retrieve(...)`` consumers) performs."""
    chunk = RetrievedChunk(
        document_id="doc-1",
        chunk_id="chunk-1",
        text="quarterly revenue figures",
        score=0.83,
        file_name="quarterly-report.pdf",
        page_number=4,
        section="Regional Breakdown",
    )
    view: RetrievedChunkView = chunk  # structural — no cast, no adapter
    for name in _view_property_names():
        assert getattr(view, name) == getattr(chunk, name)


def test_real_citation_values_survive_dataclass_to_api_dto_unchanged() -> None:
    """The values themselves round-trip, not merely the field names — a
    dataclass->DTO mapping that silently dropped a field (or forgot to wire
    it, leaving it at its ``None`` default) would still pass the field-name
    check above but fail HERE."""
    chunk = RetrievedChunk(
        document_id="doc-1",
        chunk_id="chunk-1",
        text="quarterly revenue figures",
        score=0.83,
        file_name="quarterly-report.pdf",
        page_number=4,
        section="Regional Breakdown",
    )

    out = RetrievedChunkOut(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        score=chunk.score,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section=chunk.section,
    )

    assert out.file_name == "quarterly-report.pdf"
    assert out.page_number == 4
    assert out.section == "Regional Breakdown"
    assert out.model_dump() == {
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "text": "quarterly revenue figures",
        "score": 0.83,
        "file_name": "quarterly-report.pdf",
        "page_number": 4,
        "section": "Regional Breakdown",
    }


def test_absent_citation_fields_degrade_to_none_not_a_crash() -> None:
    """A chunk from a point that predates ``P-18`` (or whose parser never
    emitted one of the three keys) must construct cleanly across every
    layer — ``| None`` is the contract, not an afterthought."""
    chunk = RetrievedChunk(document_id="doc-1", chunk_id="chunk-1", text="plain text", score=0.5)

    assert chunk.file_name is None
    assert chunk.page_number is None
    assert chunk.section is None

    out = RetrievedChunkOut(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        score=chunk.score,
        file_name=chunk.file_name,
        page_number=chunk.page_number,
        section=chunk.section,
    )
    assert (out.file_name, out.page_number, out.section) == (None, None, None)
