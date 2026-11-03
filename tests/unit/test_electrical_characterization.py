"""The characterization records and the project report (BT22; INC1-AC-13, FR-EVAL-14).

STG-01 writes one record per document; the project's report is DERIVED from its ACTIVE
documents' records, so it always equals their sum and an excluded or purging document is out of
it.
"""

from __future__ import annotations

import pytest

from app.framework.errors import NotFoundError
from app.modules.electrical.drawings.domain.characterization import aggregate
from app.modules.electrical.drawings.domain.document import ExclusionReason
from app.modules.electrical.drawings.domain.pages import PageKind, PageLanguage
from app.modules.electrical.drawings.domain.pipeline import PIPELINE_VERSION
from app.modules.electrical.drawings.domain.profile import SymbolStandard
from app.modules.electrical.shared.kinds import Convention, PlcFamily
from app.modules.electrical.shared.version import SHARED_VERSION
from tests.unit.support_electrical import (
    CN_TOKENS,
    IEC_TOKENS,
    FakePdfInspection,
    Stack,
    build_stack,
    make_font,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)

LOW = {"schema": 1, "auto_accept_confidence": 0.5}


@pytest.fixture
def stack() -> Stack:
    return build_stack()


async def _ingest(stack: Stack, file_id: str, pdf: FakePdfInspection) -> str:
    project = await stack.projects.get_by_space(stack.ctx, "22222222-2222-7222-8222-222222222222")
    if project is None:
        project = await seed_project(stack, **LOW)
    await seed_file(stack, file_id, body=f"%PDF {file_id}".encode())
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id=file_id, checksum=None
    )
    await stack.inspector(pdf).run(stack.ctx, job_id=registered.job.id, event_id=f"e-{file_id}")
    return registered.document.id


def _eplan(pages: int = 3) -> FakePdfInspection:
    plc = ("I0.0", "Q0.1", "MB10")
    return FakePdfInspection(
        make_summary(page_count=pages, creator="EPLAN Electric P8", producer="EPLAN"),
        [
            tokens_page(
                n,
                (*IEC_TOKENS, *plc, "/12.3"),
                fonts=(
                    make_font("Arial", "TrueType", False, True, True),
                    make_font("CIDFont+F1", "Type0", True, True, False),
                ),
            )
            for n in range(1, pages + 1)
        ],
    )


async def test_a_document_record_carries_every_field_fr_eval_14_lists(stack: Stack) -> None:
    doc_id = await _ingest(stack, "f1", _eplan())

    record = await stack.services["characterization"].for_document(stack.ctx, doc_id)

    assert record.document_id == doc_id and record.page_count == 3
    assert record.producer_family == "eplan"  # the program
    assert (record.creator, record.producer) == ("EPLAN Electric P8", "EPLAN")
    assert record.text_kinds == {PageKind.VECTOR_TEXT: 3}  # the text kinds
    assert record.page_languages == {PageLanguage.EN: 3} and record.document_language == "en"
    assert record.conventions == {Convention.IEC_81346: 90}  # the conventions
    assert record.symbol_standard_guess is SymbolStandard.IEC_60617  # the inferred standard...
    assert record.symbol_standard_basis  # ...and what it rests on
    assert record.plc_families == {PlcFamily.S7_EN: 9}  # the PLC families
    assert record.cross_reference_formats == {"slash_page_dot_column": 3}
    assert record.ocr_needed_pages == 0  # the OCR share, as a count
    assert record.cid_fonts_without_tounicode == 1
    assert (record.pipeline_version, record.shared_version) == (PIPELINE_VERSION, SHARED_VERSION)
    assert record.rules_version == stack.rules.identifier_rules().version  # the three versions
    assert record.content_sha256 and len(record.content_sha256) == 64


async def test_the_project_report_is_the_aggregate_of_its_active_documents(stack: Stack) -> None:
    first = await _ingest(stack, "f1", _eplan(3))
    second = await _ingest(
        stack,
        "f2",
        FakePdfInspection(
            make_summary(page_count=2, creator="ZWCAD", producer="ZWCAD"),
            [tokens_page(n, CN_TOKENS) for n in (1, 2)],
        ),
    )
    project_id = stack.documents.rows[first].project_id
    records = [
        await stack.services["characterization"].for_document(stack.ctx, d)
        for d in sorted((first, second))
    ]

    report = await stack.services["characterization"].for_project(stack.ctx, project_id)

    assert report == aggregate(records)  # the report IS the aggregate, nothing else
    assert report.documents == 2 and report.pages == 5
    assert report.producers == {"eplan": 1, "zwcad": 1}
    assert report.conventions == {Convention.IEC_81346: 1, Convention.CN_LETTER_CODES: 1}
    assert report.versions["pipeline"] == (PIPELINE_VERSION,)
    assert report.versions["shared"] == (SHARED_VERSION,)
    assert report.versions["rules"] == (stack.rules.identifier_rules().version,)


async def test_an_excluded_or_purging_document_is_out_of_the_report(stack: Stack) -> None:
    first = await _ingest(stack, "f1", _eplan(3))
    second = await _ingest(stack, "f2", _eplan(2))
    project_id = stack.documents.rows[first].project_id
    assert (await stack.services["characterization"].for_project(stack.ctx, project_id)).pages == 5

    stack.documents.rows[second].exclude(ExclusionReason.FILE_UNREADABLE, stack.clock())
    excluded = await stack.services["characterization"].for_project(stack.ctx, project_id)
    assert excluded.documents == 1 and excluded.pages == 3
    with pytest.raises(NotFoundError):  # the document's own record is hidden too
        await stack.services["characterization"].for_document(stack.ctx, second)

    await stack.services["purge_file"].execute(stack.ctx, "f1")
    empty = await stack.services["characterization"].for_project(stack.ctx, project_id)
    assert empty.documents == 0 and empty.pages == 0 and empty.ocr_needed_ratio == 0.0


async def test_a_document_not_yet_inspected_has_no_record_and_unknown_ids_are_404s(
    stack: Stack,
) -> None:
    project = await seed_project(stack, **LOW)
    await seed_file(stack, "f1")
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    with pytest.raises(NotFoundError):
        await stack.services["characterization"].for_document(stack.ctx, registered.document.id)
    with pytest.raises(NotFoundError):
        await stack.services["characterization"].for_document(stack.ctx, "missing")
    with pytest.raises(NotFoundError):
        await stack.services["characterization"].for_project(stack.ctx, "missing")


async def test_a_reprocessing_replaces_the_record_not_adds_one(stack: Stack) -> None:
    doc_id = await _ingest(stack, "f1", _eplan(3))
    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    await stack.inspector(_eplan(3)).run(stack.ctx, job_id=job.id, event_id="e-again")
    assert len([k for k in stack.characterizations.rows if k[1] == doc_id]) == 1
