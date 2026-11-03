"""QA of PR-C, re-check after rework round 2: variants the developers' suites did not write.

1. QA-5 closure: every non-canonical UUID text is refused by both guards (and by the driver's own
   parser), every canonical one still passes; the int4 bound of the pages listing at its edge,
   and the bigint range of the conversation cursor untouched.
2. A transient fault releases the lease and the redelivery of the same message completes.
3. The hostile-input surface of the rework: profile PUT (no echo of client text anywhere in the
   problem), and the Markdown report.
"""

from __future__ import annotations

import base64
import inspect
from contextlib import asynccontextmanager
from typing import Any

import pytest
from asyncpg.pgproto import pgproto

from app.framework.errors import AppError, ValidationError
from app.framework.pagination import (
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
    is_uuid_text,
)
from app.modules.conversations.adapters import sql_repository as conv
from app.modules.electrical.drawings.adapters import sql_repository
from app.modules.electrical.drawings.domain.errors import InspectionUnavailable
from app.modules.electrical.drawings.domain.job import JobStatus
from tests.unit.support_electrical import (
    IEC_TOKENS,
    FakePdfInspection,
    build_stack,
    make_ctx,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)
from tests.unit.test_api_electrical_router import _make_env

CANON = "0190f1d2-0000-7000-8000-00000000abcd"

NOT_CANONICAL = [
    CANON.upper().replace("-", "_", 1),  # underscore for a hyphen
    "{" + CANON + "}",  # braces
    "urn:uuid:" + CANON,  # URN form
    CANON.replace("-", ""),  # 32 hex digits, no hyphens
    CANON[:-1],  # 35 chars
    CANON + "0",  # 37 chars
    CANON + "\x00",  # NUL byte (37 chars)
    CANON[:-1] + "\x00",  # NUL in place of the last digit (36 chars)
    "\x00" + CANON[1:],  # NUL first
    " " + CANON[1:],  # leading blank
    CANON[:-1] + "\n",  # trailing newline in place of the last digit
    CANON + "\n",  # a trailing newline after a full UUID ($ would accept it)
    "\uff10" * 8 + CANON[8:],  # full-width digits
    "\u0660" * 8 + CANON[8:],  # Arabic-Indic digits
    "g" + CANON[1:],  # non-hex letter
    "",
]


@pytest.mark.parametrize("value", NOT_CANONICAL)
def test_noncanonical_uuid_text_is_refused_by_every_guard(value: str) -> None:
    assert is_uuid_text(value) is False
    assert sql_repository._is_uuid(value) is False
    with pytest.raises(AppError):
        decode_id_cursor(encode_id_cursor(value))


@pytest.mark.parametrize(
    "value",
    [
        CANON,
        CANON.upper(),
        "ABCDEF01-2345-6789-abcd-ef0123456789",
        "0" * 8 + "-0000" * 3 + "-" + "0" * 12,
    ],
)
def test_canonical_uuid_text_passes_and_the_driver_agrees(value: str) -> None:
    assert is_uuid_text(value) is True
    pgproto.UUID(value)  # the driver takes it too
    assert decode_id_cursor(encode_id_cursor(value)) == value


@pytest.mark.parametrize("value", [None, 5, b"0" * 36, ["x"]])
def test_non_string_is_not_a_uuid_text(value: object) -> None:
    assert is_uuid_text(value) is False


def test_garbage_cursors_are_rejected_not_crashed() -> None:
    for raw in ("", "!!!", "%%%", "a" * 5000, base64.urlsafe_b64encode(b"\xff\xfe").decode()):
        with pytest.raises(AppError):
            decode_id_cursor(raw)
        with pytest.raises(AppError):
            decode_seq_cursor(raw)


class _Result:
    def mappings(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return []


class _Session:
    def __init__(self, sink: list[Any]) -> None:
        self.sink = sink

    async def execute(self, statement: Any) -> _Result:
        self.sink.append(statement)
        return _Result()


def _pages_repo(sink: list[Any]) -> sql_repository.SqlPageRepository:
    @asynccontextmanager
    async def tenant_session(_ctx: Any) -> Any:
        yield _Session(sink)

    return sql_repository.SqlPageRepository(tenant_session)


@pytest.mark.parametrize("n", [2**31 - 1, 0, -(2**31 - 1)])
async def test_pages_listing_accepts_a_seq_cursor_up_to_int32(n: int) -> None:
    sink: list[Any] = []
    page = await _pages_repo(sink).list(make_ctx(), CANON, limit=5, cursor=encode_seq_cursor(n))
    assert list(page.data) == [] and sink  # reached the database


@pytest.mark.parametrize("n", [2**31, 2**31 + 1, 99_999_999_999, -(2**31), 2**63])
async def test_pages_listing_refuses_a_seq_cursor_past_int32_before_the_database(n: int) -> None:
    sink: list[Any] = []
    with pytest.raises(ValidationError):
        await _pages_repo(sink).list(make_ctx(), CANON, limit=5, cursor=encode_seq_cursor(n))
    assert sink == []


def test_conversation_style_seq_cursors_keep_the_bigint_range() -> None:
    for n in (2**31, 2**40, 2**63 - 1, -(2**40)):
        assert decode_seq_cursor(encode_seq_cursor(n)) == n


def test_the_conversations_and_files_adapters_still_call_the_unbounded_forms() -> None:
    text = inspect.getsource(conv)
    assert "decode_seq_cursor(cursor)" in text and "maximum" not in text


# ------------------------------------------------ a transient fault, then the redelivery
async def test_a_transient_fault_releases_the_lease_and_the_redelivery_completes() -> None:
    stack = build_stack()
    project = await seed_project(stack, schema=1, auto_accept_confidence=0.5)
    await seed_file(stack, "f1", body=b"%PDF-1.7 a drawing")
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    job_id = registered.job.id

    class _Flaky(FakePdfInspection):
        broken = True

        async def pages(self, source: Any, first: int, last: int) -> Any:
            if self.broken:
                raise InspectionUnavailable("down")
            return await super().pages(source, first, last)

    pdf = _Flaky(
        make_summary(page_count=3, creator="EPLAN Electric P8", producer="EPLAN"),
        [tokens_page(n, IEC_TOKENS) for n in range(1, 4)],
    )
    with pytest.raises(InspectionUnavailable):
        await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="e1")
    held = stack.jobs.rows[job_id]
    assert held.status is JobStatus.RUNNING and held.is_stale(stack.clock())
    assert stack.ledger.claimed == []  # nothing was claimed: the message is still owed

    pdf.broken = False
    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="e1")  # same message
    assert stack.jobs.rows[job_id].status in (JobStatus.SUCCEEDED, JobStatus.AWAITING_CONFIRMATION)
    assert stack.ledger.claimed == ["e1"]
    # and a third delivery of the same message is a no-op
    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="e1")
    assert stack.ledger.claimed == ["e1"]


# ------------------------------------------------ the profile PUT, no echo anywhere
HOSTILE = "ZQXHOSTILE"


@pytest.mark.parametrize(
    "body",
    [
        {"voltage_labels": [f"-{HOSTILE}"]},
        {"voltage_labels": [f"+{HOSTILE}V"]},
        {"voltage_labels": [f"24\u202e{HOSTILE}V"]},
        {"extra_aspects": [f"={HOSTILE}"]},
        {"extra_aspects": [f"=+{HOSTILE}"]},
        {"symbol_standard": "vendor", "vendor_library": f"{HOSTILE}\u200b"},
        {"symbol_standard": "vendor", "vendor_library": f"{HOSTILE}\x07"},
        {"languages": [[HOSTILE]]},
        {"plc_vendor": [HOSTILE]},
        {"plc_vendor": {HOSTILE: 1}},
    ],
)
def test_profile_put_refusals_never_echo_the_client_text(body: dict[str, Any]) -> None:
    env = _make_env()
    project = env.project()
    response = env.put(f"/projects/{project['id']}/profile", {"body": {"schema": 1, **body}})
    assert response.status_code == 422, response.text
    assert HOSTILE not in response.text
