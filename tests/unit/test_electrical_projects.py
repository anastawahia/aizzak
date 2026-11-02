"""``ProjectService`` and the document-level profile flow (INC1-AC-5).

One project per space, created with its first profile version in one unit; the profile series
are append-only; a bad body is ``422 electrical.invalid_profile`` naming the field; the
document's effective profile follows the state machine of ``DocumentService``.
"""

from __future__ import annotations

import pytest

from app.framework.errors import ConflictError, NotFoundError, ValidationError
from app.modules.electrical.drawings.domain.document import ProfileState
from app.modules.electrical.drawings.domain.job import JobStatus
from app.modules.electrical.drawings.domain.profile import DrawingProfile, ProfileSource
from tests.unit.support_electrical import (
    IEC_TOKENS,
    SPACE,
    FakePdfInspection,
    Stack,
    build_stack,
    make_summary,
    seed_file,
    tokens_page,
)

HIGH = {"schema": 1, "auto_accept_confidence": 1.0}


@pytest.fixture
def stack() -> Stack:
    return build_stack()


async def _create(stack: Stack, **kwargs: object) -> object:
    values: dict[str, object] = {
        "space_id": SPACE,
        "name": "Plant 7",
        "description": None,
        "profile": None,
    }
    return await stack.project_service.create(stack.ctx, **{**values, **kwargs})


async def test_a_project_is_created_with_its_default_profile_in_one_unit(stack: Stack) -> None:
    detail = await _create(stack, name="  Plant 7  ", description="Line 3")

    assert detail.project.name == "Plant 7" and detail.project.description == "Line 3"
    assert detail.project.space_id == SPACE and detail.project.created_by == "u1"
    assert detail.profile.version == 1 and detail.profile.source is ProfileSource.DEFAULT
    assert detail.profile.profile == DrawingProfile.default()
    assert detail.profile.fingerprint == DrawingProfile.default().fingerprint()
    assert stack.uow.begun == 1  # the project and its first version together


async def test_a_profile_sent_with_the_project_is_a_user_version(stack: Stack) -> None:
    detail = await _create(stack, profile={"schema": 1, "languages": ["zh"]})
    assert detail.profile.source is ProfileSource.USER
    assert detail.profile.profile.languages == frozenset({"zh"})


async def test_the_second_project_of_a_space_is_a_conflict_and_leaves_nothing(stack: Stack) -> None:
    await _create(stack)
    with pytest.raises(ConflictError) as raised:
        await _create(stack, name="Another")
    assert raised.value.code == "electrical.project_exists"
    assert len(stack.projects.rows) == 1 and len(stack.profiles.rows) == 1  # rolled back


async def test_an_unknown_or_deleted_space_is_a_404(stack: Stack) -> None:
    with pytest.raises(NotFoundError):
        await _create(stack, space_id="33333333-3333-7333-8333-333333333333")
    assert stack.projects.rows == {}


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({"schema": 2}, "schema"),
        ({"schema": 1, "nonsense": 1}, "nonsense"),
        ({"schema": 1, "identifier_convention": "nope"}, "identifier_convention"),
        ({"schema": 1, "auto_accept_confidence": 2}, "auto_accept_confidence"),
        ({"schema": 1, "wire_number_patterns": ["no-such-pattern"]}, "wire_number_patterns"),
        ({"schema": 1, "classification": {"garbled_ratio": 9}}, "garbled_ratio"),
    ],
)
async def test_an_invalid_profile_is_a_422_naming_the_field_and_writes_nothing(
    stack: Stack, body: dict[str, object], path: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        await _create(stack, profile=body)
    assert raised.value.code == "electrical.invalid_profile" and path in raised.value.detail
    assert stack.projects.rows == {} and stack.uow.begun == 0


async def test_a_bad_name_is_a_422(stack: Stack) -> None:
    for name in ("", "   ", "x" * 201, "a\x00b"):
        with pytest.raises(ValidationError):
            await _create(stack, name=name)


async def test_get_list_and_update(stack: Stack) -> None:
    detail = await _create(stack)
    got = await stack.project_service.get(stack.ctx, detail.project.id)
    assert got.project.id == detail.project.id and got.profile.version == 1
    page = await stack.project_service.list(stack.ctx, space_id=SPACE, limit=10, cursor=None)
    assert [p.id for p in page.data] == [detail.project.id]
    elsewhere = await stack.project_service.list(stack.ctx, space_id="other", limit=10, cursor=None)
    assert elsewhere.data == []

    updated = await stack.project_service.update(
        stack.ctx, detail.project.id, name="Plant 8", description="x"
    )
    assert updated.project.name == "Plant 8" and updated.project.description == "x"
    assert updated.project.version == 2
    same = await stack.project_service.update(
        stack.ctx, detail.project.id, name=None, description=None
    )
    assert same.project.version == 2  # nothing changed, nothing written
    cleared = await stack.project_service.update(
        stack.ctx, detail.project.id, name=None, description=" "
    )
    assert cleared.project.description is None
    with pytest.raises(NotFoundError):
        await stack.project_service.get(stack.ctx, "missing")
    with pytest.raises(ValidationError):
        await stack.project_service.update(stack.ctx, detail.project.id, name=" ", description=None)


async def test_the_project_profile_is_append_only_with_consecutive_versions(stack: Stack) -> None:
    detail = await _create(stack)
    first = detail.profile
    second = await stack.project_service.replace_profile(
        stack.ctx, detail.project.id, {"schema": 1, "languages": ["en"]}
    )
    third = await stack.project_service.replace_profile(
        stack.ctx, detail.project.id, {"schema": 1, "languages": ["en"]}
    )
    assert (first.version, second.version, third.version) == (1, 2, 3)
    assert second.source is ProfileSource.USER and second.fingerprint == third.fingerprint
    assert second.fingerprint != first.fingerprint
    assert (await stack.project_service.get_profile(stack.ctx, detail.project.id)).version == 3
    assert [v.version for v in stack.profiles.rows] == [1, 2, 3]  # nothing was edited in place
    with pytest.raises(ValidationError):
        await stack.project_service.replace_profile(stack.ctx, detail.project.id, {"schema": 9})
    assert len(stack.profiles.rows) == 3


async def test_the_key_order_of_a_body_does_not_change_its_fingerprint(stack: Stack) -> None:
    detail = await _create(stack)
    a = await stack.project_service.replace_profile(
        stack.ctx, detail.project.id, {"schema": 1, "languages": ["en"], "plc_vendor": "siemens"}
    )
    b = await stack.project_service.replace_profile(
        stack.ctx, detail.project.id, {"plc_vendor": "siemens", "languages": ["en"], "schema": 1}
    )
    assert a.fingerprint == b.fingerprint


# --------------------------------------------------------------------- the document's profile
async def _proposed(stack: Stack) -> tuple[str, str]:
    detail = await _create(stack, profile=HIGH)
    await seed_file(stack, "f1")
    registered = await stack.document_service.register(
        stack.ctx, detail.project.id, file_id="f1", checksum=None
    )
    pdf = FakePdfInspection(
        make_summary(page_count=4), [tokens_page(n, IEC_TOKENS) for n in range(1, 5)]
    )
    await stack.inspector(pdf).run(stack.ctx, job_id=registered.job.id, event_id="e1")
    return registered.document.id, registered.job.id


async def test_the_effective_profile_follows_the_documents_state(stack: Stack) -> None:
    doc_id, _ = await _proposed(stack)

    pending = await stack.document_service.get_profile(stack.ctx, doc_id)
    assert pending.state is ProfileState.PROPOSED
    assert pending.effective.document_id is None  # the PROJECT's profile is still in force
    assert pending.proposal is not None and pending.proposal.source is ProfileSource.DETECTED
    assert pending.project_version == 1

    await stack.document_service.confirm_profile(stack.ctx, doc_id, body=None)
    confirmed = await stack.document_service.get_profile(stack.ctx, doc_id)
    assert confirmed.state is ProfileState.CONFIRMED and confirmed.proposal is None
    assert confirmed.effective.source is ProfileSource.CONFIRMED
    assert confirmed.effective.document_id == doc_id


async def test_confirming_an_edited_proposal_stores_the_edit(stack: Stack) -> None:
    doc_id, job_id = await _proposed(stack)
    job = await stack.document_service.confirm_profile(
        stack.ctx, doc_id, body={"schema": 1, "identifier_convention": "gbt_5094"}
    )
    assert job.id == job_id and job.status is JobStatus.QUEUED
    effective = (await stack.document_service.get_profile(stack.ctx, doc_id)).effective
    assert effective.profile.identifier_convention.value == "gbt_5094"
    assert stack.outbox.data("electrical.job.requested.v1")[-1]["reason"] == "confirmation"


async def test_a_document_override_is_refused_while_a_job_awaits_confirmation(
    stack: Stack,
) -> None:
    doc_id, _ = await _proposed(stack)
    with pytest.raises(ConflictError) as raised:
        await stack.document_service.replace_profile(stack.ctx, doc_id, {"schema": 1})
    assert raised.value.code == "electrical.job_conflict"


async def test_a_document_override_confirms_the_document_under_a_user_version(stack: Stack) -> None:
    doc_id, job_id = await _proposed(stack)
    await stack.job_service.cancel(stack.ctx, job_id)  # nothing awaits confirmation any more
    version = await stack.document_service.replace_profile(
        stack.ctx, doc_id, {"schema": 1, "languages": ["zh"]}
    )
    assert version.source is ProfileSource.USER and version.document_id == doc_id
    state = await stack.document_service.get_profile(stack.ctx, doc_id)
    assert state.state is ProfileState.CONFIRMED and state.effective.version == version.version
    # changing a profile does not reprocess anything
    assert [r.event_type for r in stack.outbox.records].count("electrical.job.requested.v1") == 0


async def test_confirming_without_an_awaiting_job_is_a_conflict(stack: Stack) -> None:
    detail = await _create(stack)
    await seed_file(stack, "f1")
    registered = await stack.document_service.register(
        stack.ctx, detail.project.id, file_id="f1", checksum=None
    )
    with pytest.raises(ConflictError) as raised:
        await stack.document_service.confirm_profile(stack.ctx, registered.document.id, body=None)
    assert raised.value.code == "electrical.job_conflict"
    with pytest.raises(ValidationError):
        await stack.document_service.confirm_profile(
            stack.ctx, registered.document.id, body={"x": 1}
        )
