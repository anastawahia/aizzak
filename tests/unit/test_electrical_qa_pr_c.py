"""QA of PR-C (electrical-drawings INC-1): the checks the developers' suites did not write.

Four groups, each independent of how the developers built their own fixtures:

1. the job state machine as a full MATRIX (every status against every transition, against the
   table of LLD 3.3.6), so a transition nobody thought of cannot appear unseen;
2. the SEQUENCES around it (cancel while a proposal waits, takeover after an expired lease, a
   redelivered message for a job that is no longer runnable);
3. ``ele-parse`` against hostile HTTP bodies and a real PDF whose content stream is damaged;
4. the tenant wall of the in-memory fakes, because the other suites trust them as a stand-in
   for RLS.

A test that exposes a defect is ``xfail(strict=True)`` and names the bug id of
``inc-1/pr-c/test-plan.md``; it turns red the day the defect is fixed, which is the signal to
delete the marker.
"""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import threading
import zlib
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pymupdf
import pytest
from asyncpg.pgproto import pgproto
from services.ele_parse import app, child, contract, sandbox

from app.framework.errors import AppError, ConflictError, NotFoundError
from app.framework.observability.metrics import ele_documents_excluded_total
from app.framework.pagination import decode_id_cursor, encode_id_cursor
from app.modules.electrical.drawings.adapters import sql_repository
from app.modules.electrical.drawings.domain.document import ExclusionReason, ProfileState
from app.modules.electrical.drawings.domain.errors import JobStateError
from app.modules.electrical.drawings.domain.job import IngestionJob, JobStatus
from tests.unit.support_electrical import (
    IEC_TOKENS,
    NOW,
    FakePdfInspection,
    Stack,
    build_stack,
    make_ctx,
    make_job,
    make_summary,
    seed_file,
    seed_project,
    tokens_page,
)
from tests.unit.test_api_electrical_router import SPACE, WS, Env, _make_env

LEASE_S = 600
LIVE = NOW + timedelta(seconds=LEASE_S)  # a lease that has not expired at NOW
GONE = NOW - timedelta(seconds=1)  # a lease that expired before NOW


# --------------------------------------------------------------------------------- 1. the matrix
def _job(status: JobStatus, *, stale: bool = False) -> IngestionJob:
    """A job in ``status``; a RUNNING one holds a live lease unless ``stale``."""
    lease = (GONE if stale else LIVE) if status is JobStatus.RUNNING else None
    return make_job(status=status, lease_until=lease)


def _apply(job: IngestionJob, action: str) -> object:
    do: dict[str, Callable[[], object]] = {
        "take_lease": lambda: job.take_lease(NOW, LEASE_S),
        "extend_lease": lambda: job.extend_lease(NOW, LEASE_S),
        "await_confirmation": lambda: job.await_confirmation(NOW),
        "requeue": lambda: job.requeue(NOW),
        "succeed": lambda: job.succeed(NOW),
        "fail": lambda: job.fail("electrical.pdf_rejected", "x", NOW),
        "request_cancel": lambda: job.request_cancel(NOW),
        "cancel": lambda: job.cancel(NOW),
    }
    return do[action]()


# (status, stale) -> the actions LLD 3.3.6 allows, and the status each leads to
_ALLOWED: dict[tuple[JobStatus, bool], dict[str, JobStatus]] = {
    (JobStatus.QUEUED, False): {
        "take_lease": JobStatus.RUNNING,
        "cancel": JobStatus.CANCELLED,
    },
    (JobStatus.RUNNING, False): {
        "extend_lease": JobStatus.RUNNING,
        "await_confirmation": JobStatus.AWAITING_CONFIRMATION,
        "succeed": JobStatus.SUCCEEDED,
        "fail": JobStatus.FAILED,
        "request_cancel": JobStatus.RUNNING,
        "cancel": JobStatus.CANCELLED,
    },
    (JobStatus.RUNNING, True): {
        "take_lease": JobStatus.RUNNING,  # the takeover
        "requeue": JobStatus.QUEUED,
        "extend_lease": JobStatus.RUNNING,
        "await_confirmation": JobStatus.AWAITING_CONFIRMATION,
        "succeed": JobStatus.SUCCEEDED,
        "fail": JobStatus.FAILED,
        "request_cancel": JobStatus.RUNNING,
        "cancel": JobStatus.CANCELLED,
    },
    (JobStatus.AWAITING_CONFIRMATION, False): {
        "requeue": JobStatus.QUEUED,
        "cancel": JobStatus.CANCELLED,
    },
    (JobStatus.SUCCEEDED, False): {},
    (JobStatus.FAILED, False): {"requeue": JobStatus.QUEUED},
    (JobStatus.CANCELLED, False): {"requeue": JobStatus.QUEUED},
}
_ACTIONS = (
    "take_lease",
    "extend_lease",
    "await_confirmation",
    "requeue",
    "succeed",
    "fail",
    "request_cancel",
    "cancel",
)
_CASES = [(status, stale, action) for (status, stale) in _ALLOWED for action in _ACTIONS]


@pytest.mark.parametrize(("status", "stale", "action"), _CASES)
def test_every_transition_is_allowed_or_refused_exactly_as_the_table_says(
    status: JobStatus, stale: bool, action: str
) -> None:
    job = _job(status, stale=stale)
    before = (job.status, job.attempt, job.lease_until, job.cancel_requested_at, job.finished_at)
    allowed = _ALLOWED[(status, stale)]

    if action in allowed:
        result = _apply(job, action)
        if action == "take_lease":
            assert result is True
        assert job.status is allowed[action], (status, stale, action)
        return

    if action == "take_lease":
        # the one transition that answers instead of raising: "not yours" is False, no change
        assert _apply(job, action) is False
    else:
        with pytest.raises(JobStateError):
            _apply(job, action)
    after = (job.status, job.attempt, job.lease_until, job.cancel_requested_at, job.finished_at)
    assert after == before, "a refused transition must not change the job"


def test_a_job_that_finished_is_never_open_and_the_three_open_states_are_exactly_three() -> None:
    open_states = {s for s in JobStatus if _job(s).is_open}
    assert open_states == {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.AWAITING_CONFIRMATION,
    }


def test_the_attempt_counts_every_takeover_and_every_requeue_and_nothing_else() -> None:
    job = _job(JobStatus.QUEUED)
    job.take_lease(NOW, LEASE_S)  # first claim: attempt stays 1
    assert job.attempt == 1
    job.extend_lease(NOW, LEASE_S)
    job.request_cancel(NOW)
    assert job.attempt == 1
    later = NOW + timedelta(seconds=LEASE_S + 1)
    assert job.take_lease(later, LEASE_S) is True  # takeover of an expired lease
    assert job.attempt == 2
    job.fail("electrical.pdf_rejected", "x", later)
    job.requeue(later)
    assert job.attempt == 3 and job.cancel_requested_at is None
    assert job.failure_code is None and job.finished_at is None


# ------------------------------------------------------------------------------- 2. the sequences
LOW = {"schema": 1, "auto_accept_confidence": 0.5}
HIGH = {"schema": 1, "auto_accept_confidence": 1.0}


def _pdf(pages: int = 3) -> FakePdfInspection:
    return FakePdfInspection(
        make_summary(page_count=pages), [tokens_page(n, IEC_TOKENS) for n in range(1, pages + 1)]
    )


async def _registered(stack: Stack, profile: Any = LOW) -> tuple[str, str]:
    project = await seed_project(stack, **profile)
    await seed_file(stack, "f1", body=b"%PDF f1")
    registered = await stack.document_service.register(
        stack.ctx, project.id, file_id="f1", checksum=None
    )
    return registered.document.id, registered.job.id


async def test_a_redelivered_message_for_a_cancelled_job_does_nothing_and_is_claimed() -> None:
    stack = build_stack()
    _, job_id = await _registered(stack)
    await stack.job_service.cancel(stack.ctx, job_id)
    stack.outbox.records.clear()
    pdf = _pdf()

    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="redelivery")

    assert stack.jobs.rows[job_id].status is JobStatus.CANCELLED
    assert pdf.calls == [], "a cancelled job must not reach ele-parse"
    assert stack.outbox.records == [], "and must publish nothing"


async def test_a_live_lease_blocks_a_second_run_and_an_expired_one_is_taken_over() -> None:
    stack = build_stack()
    doc_id, job_id = await _registered(stack)
    stack.jobs.rows[job_id].take_lease(stack.clock(), LEASE_S)  # worker A is mid-run
    pdf = _pdf()

    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="b1")
    assert pdf.calls == [], "worker B must not touch a job whose lease is live"
    assert stack.jobs.rows[job_id].attempt == 1

    stack.clock.advance(LEASE_S + 1)  # A died; the redelivered message reaches B
    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="b2")
    job = stack.jobs.rows[job_id]
    assert job.status is JobStatus.SUCCEEDED and job.attempt == 2
    assert stack.documents.rows[doc_id].page_count == 3


async def test_cancelling_a_proposal_leaves_two_ways_out_and_resume_is_one_of_them() -> None:
    """The proposal waits for a person; the person cancels the JOB. The document is still
    ``proposed``: ``confirm`` has no job to confirm and ``reprocess`` says the profile awaits
    confirmation. The two ways out are ``resume`` (detects again) and a profile override."""
    stack = build_stack()
    doc_id, job_id = await _registered(stack, profile=HIGH)
    await stack.inspector(_pdf()).run(stack.ctx, job_id=job_id, event_id="e1")
    assert stack.documents.rows[doc_id].profile_state is ProfileState.PROPOSED
    await stack.job_service.cancel(stack.ctx, job_id)

    with pytest.raises(ConflictError) as confirm:
        await stack.document_service.confirm_profile(stack.ctx, doc_id, body=None)
    assert confirm.value.code == "electrical.job_conflict"
    with pytest.raises(ConflictError) as reprocess:
        await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    assert reprocess.value.code == "electrical.profile_unconfirmed"
    # QA-4: both answers name the exits that work, in the same words
    for answer in (str(confirm.value), str(reprocess.value)):
        assert "ingestion-jobs/{id}/resume" in answer, answer
        assert "documents/{id}/profile" in answer, answer

    view = await stack.job_service.resume(stack.ctx, job_id)  # the way out
    assert view.job.status is JobStatus.QUEUED


async def test_a_profile_override_gets_a_document_out_of_proposed_after_the_job_was_cancelled() -> (
    None
):
    stack = build_stack()
    doc_id, job_id = await _registered(stack, profile=HIGH)
    await stack.inspector(_pdf()).run(stack.ctx, job_id=job_id, event_id="e1")
    await stack.job_service.cancel(stack.ctx, job_id)

    await stack.document_service.replace_profile(stack.ctx, doc_id, LOW)

    assert stack.documents.rows[doc_id].profile_state is ProfileState.CONFIRMED
    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=None)
    assert job.status is JobStatus.QUEUED


async def test_cancelling_a_running_job_twice_keeps_the_first_request_and_writes_once() -> None:
    stack = build_stack()
    _, job_id = await _registered(stack)
    stack.jobs.rows[job_id].take_lease(stack.clock(), LEASE_S)
    first = await stack.job_service.cancel(stack.ctx, job_id)
    stamp = first.job.cancel_requested_at
    stack.clock.advance(5)
    version = stack.jobs.rows[job_id].version

    second = await stack.job_service.cancel(stack.ctx, job_id)

    assert second.job.cancel_requested_at == stamp
    assert stack.jobs.rows[job_id].version == version, "the repeat wrote nothing"


async def test_a_cancel_request_on_a_job_whose_worker_is_gone_is_honoured_on_takeover() -> None:
    """The user cancels a running job whose lease has already expired: nothing can honour the
    request until some worker takes the job over. It must then be honoured, not run to the end."""
    stack = build_stack()
    doc_id, job_id = await _registered(stack)
    stack.jobs.rows[job_id].take_lease(stack.clock(), LEASE_S)
    stack.clock.advance(LEASE_S + 1)
    await stack.job_service.cancel(stack.ctx, job_id)
    assert stack.jobs.rows[job_id].status is JobStatus.RUNNING  # only a request so far
    pdf = _pdf()

    await stack.inspector(pdf).run(stack.ctx, job_id=job_id, event_id="redelivery")

    assert stack.jobs.rows[job_id].status is JobStatus.CANCELLED
    assert stack.documents.rows[doc_id].profile_state is ProfileState.NONE


async def test_reprocess_and_resume_cannot_both_win_for_one_document() -> None:
    stack = build_stack()
    doc_id, job_id = await _registered(stack)
    await stack.inspector(_pdf()).run(stack.ctx, job_id=job_id, event_id="e1")
    job = await stack.document_service.reprocess(stack.ctx, doc_id, pages=[1])
    with pytest.raises(ConflictError) as second:
        await stack.document_service.reprocess(stack.ctx, doc_id, pages=[2])
    assert second.value.code == "electrical.job_conflict"
    # the first job, now succeeded, cannot be resumed while the new one is the open one
    with pytest.raises(ConflictError):
        await stack.job_service.resume(stack.ctx, job_id)
    assert job.status is JobStatus.QUEUED


# --------------------------------------------------------------------------- 4. the tenant wall
async def test_another_workspaces_ids_are_invisible_through_every_service_read() -> None:
    stack = build_stack()
    doc_id, job_id = await _registered(stack)
    project = await stack.projects.get_by_space(stack.ctx, "22222222-2222-7222-8222-222222222222")
    assert project is not None
    stranger = make_ctx("bbbbbbbb-2222-7222-8222-222222222222")

    for call in (
        stack.document_service.get(stranger, doc_id),
        stack.document_service.list_pages(stranger, doc_id, limit=10, cursor=None),
        stack.document_service.get_profile(stranger, doc_id),
        stack.job_service.get(stranger, job_id),
        stack.job_service.cancel(stranger, job_id),
        stack.job_service.resume(stranger, job_id),
        stack.document_service.reprocess(stranger, doc_id, pages=None),
        stack.document_service.confirm_profile(stranger, doc_id, body=None),
        stack.document_service.register(stranger, project.id, file_id="f1", checksum=None),
    ):
        with pytest.raises((NotFoundError, ConflictError)) as raised:
            await call
        assert isinstance(raised.value, NotFoundError), type(raised.value)
    # nothing of the stranger's attempts reached the owner's rows
    assert stack.jobs.rows[job_id].status is JobStatus.QUEUED


# ------------------------------------------------------------------------ 3. ele-parse, hostile
@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[app.EleParseServer, int]]:
    config = app.Config(stage_root=tmp_path, port=0, max_children=1, memory_mb=512)
    srv = app.make_server(config, host="127.0.0.1")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv, srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _post(
    port: int, path: str, body: bytes, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request("POST", path, body, headers or {})
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b"null")
    finally:
        conn.close()


_UUID_A = "aaaaaaaa-1111-7111-8111-111111111111"
_UUID_B = "22222222-2222-7222-8222-222222222222"
_HOSTILE_BODIES = {
    "deep array": b"[" * 3000,
    "deep object": b'{"file":' * 400 + b"1" + b"}" * 400,
    "not utf-8": b"\xff\xfe\x00{",
    "nan": b'{"file": NaN}',
    "array": b"[]",
    "null": b"null",
    "empty": b"",
    "file is a number": b'{"file": 7}',
    "trailing newline in the name": json.dumps({"file": f"{_UUID_A}/{_UUID_B}.pdf\n"}).encode(),
    "upper-case uuid": json.dumps({"file": f"{_UUID_A.upper()}/{_UUID_B}.pdf"}).encode(),
    "arabic-indic digits": json.dumps({"file": "\u0660" + f"{_UUID_A[1:]}/{_UUID_B}.pdf"}).encode(),
    "dot dot": json.dumps({"file": f"../{_UUID_B}.pdf"}).encode(),
    "absolute path": json.dumps({"file": "/etc/passwd"}).encode(),
    "nul byte": json.dumps({"file": f"{_UUID_A}/{_UUID_B}.pdf\x00.txt"}).encode(),
    "url scheme": json.dumps({"file": f"file:///{_UUID_A}/{_UUID_B}.pdf"}).encode(),
}


@pytest.mark.parametrize("name", sorted(_HOSTILE_BODIES))
def test_a_hostile_body_is_a_400_with_a_code_never_a_dropped_connection(
    server: Any, name: str
) -> None:
    _, port = server
    status, answer = _post(port, "/v1/documents/summary", _HOSTILE_BODIES[name])
    assert status == 400 and answer["code"] in {"bad_request", "bad_path"}, (name, status, answer)


def test_a_page_range_with_huge_or_odd_numbers_is_a_422_bad_range(server: Any) -> None:
    _, port = server
    for first, last in ((0, 1), (-1, 5), (1, 10**9), (True, 2), (1.5, 2), ("1", "2")):
        body = json.dumps(
            {"file": f"{_UUID_A}/{_UUID_B}.pdf", "first_page": first, "last_page": last}
        )
        status, answer = _post(port, "/v1/documents/pages", body.encode())
        assert (status, answer["code"]) == (422, "bad_range"), (first, last, status, answer)


def test_a_symlink_to_a_file_outside_the_root_and_a_directory_symlink_are_both_refused(
    server: Any, tmp_path: Path
) -> None:
    _, port = server
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / f"{_UUID_B}.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / _UUID_A).symlink_to(outside, target_is_directory=True)
    body = json.dumps({"file": f"{_UUID_A}/{_UUID_B}.pdf"}).encode()
    status, answer = _post(port, "/v1/documents/summary", body)
    assert (status, answer["code"]) == (400, "bad_path")

    (tmp_path / "w2").mkdir()
    other = "bbbbbbbb-1111-7111-8111-111111111111"
    (tmp_path / other).mkdir()
    (tmp_path / other / f"{_UUID_B}.pdf").symlink_to(outside / f"{_UUID_B}.pdf")
    body = json.dumps({"file": f"{other}/{_UUID_B}.pdf"}).encode()
    status, answer = _post(port, "/v1/documents/summary", body)
    assert (status, answer["code"]) == (400, "bad_path")


def _damaged_content_pdf(path: Path) -> Path:
    """A valid one-page PDF whose content stream holds some good operators, then garbage. Real
    CAD exports have this often enough; MuPDF reads the page and warns."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "-K1 KM1 X1:14")
    xref = page.get_contents()[0]
    doc.update_stream(
        xref,
        b"BT /helv 12 Tf 40 700 Td (hi) Tj ET\n)))) <<<< garbage ] ] 1 2 3 foo bar",
        compress=False,
    )
    doc.save(str(path))
    doc.close()
    return path


def test_a_page_with_a_damaged_content_stream_is_still_inspected(tmp_path: Path) -> None:
    path = _damaged_content_pdf(tmp_path / "damaged.pdf")
    result = sandbox.run_child(
        {"op": "pages", "path": str(path), "first": 1, "last": 1, "token_sample": 50},
        timeout_s=30,
        limits=sandbox.Limits(512, 30),
    )
    assert [page["page_no"] for page in result["pages"]] == [1]


def _bomb_pdf(path: Path) -> Path:
    """A one-page PDF whose content stream is one 300 MiB string literal, Flate-compressed to
    ~0.3 MiB: MuPDF has to hold the whole literal, which a 256 MiB limit refuses."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((40, 60), "x")  # gives the page a content stream to replace
    xref = page.get_contents()[0]
    doc.update_stream(
        xref,
        zlib.compress(b"BT /helv 12 Tf (" + b"a" * (300 * 1024 * 1024) + b") Tj ET", 9),
        compress=False,
    )
    doc.xref_set_key(xref, "Filter", "/FlateDecode")
    doc.save(str(path))
    doc.close()
    return path


def test_a_decompression_bomb_ends_in_memory_limit_not_parse_error(tmp_path: Path) -> None:
    path = _bomb_pdf(tmp_path / "bomb.pdf")
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child(
            {"op": "pages", "path": str(path), "first": 1, "last": 1, "token_sample": 50},
            timeout_s=60,
            limits=sandbox.Limits(256, 60),
        )
    assert (raised.value.status, raised.value.code) == (507, "memory_limit")


def test_allocation_failures_are_told_apart_from_other_errors() -> None:
    assert child.is_allocation_failure(RuntimeError("code=2: realloc (268435456 bytes) failed"))
    assert child.is_allocation_failure(RuntimeError("code=2: calloc (4104 x 1 bytes) failed"))
    assert not child.is_allocation_failure(RuntimeError("code=7: syntax error"))
    assert not child.is_allocation_failure(ValueError("realloc failed"))


def test_native_noise_on_fd_1_never_reaches_the_answer_channel(tmp_path: Path) -> None:
    program = (
        "import os, sys, json;"
        "from services.ele_parse import child;"
        "fd = child._claim_answer_channel();"
        "os.write(1, b'MuPDF error: noise');"
        "child._write_answer(fd, {'ok': True, 'result': {}})"
    )
    done = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
        check=True,
    )
    assert json.loads(done.stdout) == {"ok": True, "result": {}}


def test_a_client_that_connects_and_goes_quiet_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert app._Handler.timeout == app.SOCKET_TIMEOUT_S == 30.0
    monkeypatch.setattr(app._Handler, "timeout", 0.3)
    srv = app.make_server(app.Config(stage_root=tmp_path, port=0), host="127.0.0.1")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=5) as sock:
            sock.sendall(b"POST /v1/documents/summary HTTP/1.1\r\nContent-Length: 50\r\n\r\n")
            assert sock.recv(1024) == b""  # the server closed the half-open request
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_a_summary_of_the_same_damaged_file_is_fine_which_is_why_the_defect_hides(
    tmp_path: Path,
) -> None:
    path = _damaged_content_pdf(tmp_path / "damaged.pdf")
    result = sandbox.run_child(
        {"op": "summary", "path": str(path), "token_sample": 50},
        timeout_s=30,
        limits=sandbox.Limits(512, 30),
    )
    assert result["page_count"] == 1


def test_the_child_prints_nothing_but_its_answer_on_stdout_for_a_clean_pdf(
    tmp_path: Path,
) -> None:
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 60), "-K1")
    path = tmp_path / "clean.pdf"
    doc.save(str(path))
    doc.close()
    done = subprocess.run(
        [*sandbox.child_argv(sandbox.Limits(512, 30))],
        input=json.dumps(
            {"op": "pages", "path": str(path), "first": 1, "last": 1, "token_sample": 5}
        ).encode(),
        capture_output=True,
        env={"PYTHONPATH": str(sandbox.ROOT), "PATH": "/usr/bin:/bin"},
        cwd=sandbox.ROOT,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0 and json.loads(done.stdout)["ok"] is True


# ------------------------------------------------------------- 5. the HTTP surface, exploratory
@pytest.fixture
def env() -> Env:
    return _make_env()


def _problem(response: Any) -> dict[str, Any]:
    assert response.headers["content-type"].startswith("application/problem+json"), response.text
    body: dict[str, Any] = response.json()
    assert {"type", "title", "status", "detail", "code"} <= set(body), body
    assert body["status"] == response.status_code
    return body


def test_a_replayed_registration_that_excluded_an_orphan_counts_the_exclusion_once(
    env: Env,
) -> None:
    project = env.project()
    for name in ("gone", "new"):
        env.file(name)
    env.register(project["id"], "gone")
    del env.stack.files._files[(WS, "gone")]
    series = ele_documents_excluded_total.labels(reason="file_unreadable")
    before = series._value.get()
    path = f"/projects/{project['id']}/documents"

    first = env.post(path, {"file_id": "new"}, **{"Idempotency-Key": "k-orphan"})
    again = env.post(path, {"file_id": "new"}, **{"Idempotency-Key": "k-orphan"})

    assert first.status_code == again.status_code == 202 and again.json() == first.json()
    assert series._value.get() == before + 1, "a replay must not count the exclusion twice"


def test_every_error_of_the_surface_is_a_rfc_9457_problem_with_its_own_status(env: Env) -> None:
    project = env.project()
    env.file("f1")
    env.register(project["id"], "f1")
    cases = [
        (env.get("/projects/missing"), 404),
        (env.get("/documents/missing"), 404),
        (env.get("/ingestion-jobs/missing"), 404),
        (env.post("/projects", {"space_id": SPACE, "name": "again"}), 409),
        (env.register(project["id"], "f1"), 409),
        (env.register(project["id"], "no-such-file"), 404),
        (env.register(project["id"], "f1", checksum="zz"), 422),
        (_make_env().post("/projects", {"space_id": SPACE, "name": ""}), 422),
        (env.post("/projects", {"space_id": SPACE}), 422),
        (env.post("/projects", None), 422),
        (env.get(f"/projects/{project['id']}/documents?limit=0"), 422),
        (env.get(f"/projects/{project['id']}/documents?limit=101"), 422),
        (env.get(f"/projects/{project['id']}/documents?status=nonsense"), 422),
        (env.get("/projects"), 422),
    ]
    for response, status in cases:
        assert response.status_code == status, (response.request.url, response.text[:200])
        _problem(response)


def test_arabic_names_round_trip_and_hidden_or_huge_ones_are_a_clean_422(env: Env) -> None:
    name = "مشروع المصنع \u0667 — لوحة التحكم"  # Arabic, with an Eastern Arabic digit seven
    created = env.project(name=name, description="وصف طويل لمشروع المصنع")
    assert created["name"] == name
    assert env.get(f"/projects/{created['id']}").json()["name"] == name

    other = _make_env()
    for bad in ("x" * 10_000, "\x00", "a\x00b", "\u202e" + name, "a\u200bb"):
        response = other.post("/projects", {"space_id": SPACE, "name": bad})
        assert response.status_code == 422, (bad[:10], response.status_code)
        _problem(response)
        assert "x" * 200 not in response.text


def test_a_profile_of_a_megabyte_is_refused_without_being_echoed(env: Env) -> None:
    project = env.project()
    body = {"schema": 1, "wire_number_patterns": ["w" * 1_000_000]}
    response = env.put(f"/projects/{project['id']}/profile", {"body": body})
    assert response.status_code in {413, 422}
    assert "w" * 100 not in response.text


def test_an_excluded_document_cannot_be_reprocessed_confirmed_or_resumed(env: Env) -> None:
    project = env.project()
    env.file("f1")
    registered = env.register(project["id"], "f1").json()
    doc_id, job_id = registered["document"]["id"], registered["job"]["id"]
    document = env.stack.documents.rows[doc_id]
    document.exclude(ExclusionReason.FILE_UNREADABLE, document.updated_at)
    env.stack.jobs.rows[job_id].cancel(document.updated_at)

    for response in (
        env.post(f"/documents/{doc_id}/reprocessing", {}),
        env.post(f"/documents/{doc_id}/profile-confirmation", None),
        env.post(f"/ingestion-jobs/{job_id}/resume"),
    ):
        assert response.status_code == 409 and _problem(response)["code"].startswith(
            "electrical."
        ), response.text


# ------------------------------------------------------------------- QA-3: ids that are no UUID
@pytest.mark.parametrize(
    "value",
    ["nope", "", "not-a-uuid", "1" * 36, "0190f1d2-0000-7000-8000-00000000000g", "{x}" * 12],
)
def test_the_sql_adapter_recognises_a_value_that_is_not_a_uuid(value: str) -> None:
    assert sql_repository._is_uuid(value) is False


def test_the_sql_adapter_accepts_a_canonical_uuid() -> None:
    assert sql_repository._is_uuid("0190f1d2-0000-7000-8000-000000000000") is True


@pytest.mark.parametrize("repo_name", ["documents", "jobs", "projects", "characterizations"])
async def test_a_lookup_by_an_id_that_is_no_uuid_is_none_without_touching_the_database(
    repo_name: str,
) -> None:
    def boom(_ctx: Any) -> Any:
        raise AssertionError("the database must not be asked")

    repos: dict[str, Any] = {
        "documents": sql_repository.SqlDocumentRepository(boom),
        "jobs": sql_repository.SqlJobRepository(boom),
        "projects": sql_repository.SqlProjectRepository(boom),
        "characterizations": sql_repository.SqlCharacterizationRepository(boom),
    }
    repo = repos[repo_name]
    ctx = make_ctx()
    assert await repo.get(ctx, "nope") is None
    if repo_name == "documents":
        assert await repo.lock_live(ctx, "nope") is None
    if repo_name == "projects":
        assert await repo.get_by_space(ctx, "nope") is None
        page = await repo.list_by_space(ctx, "nope", limit=5, cursor=None)
        assert list(page.data) == [] and page.next_cursor is None


# --------------------------------------- QA-5: forms Python's UUID() takes and the driver refuses
_LENIENT_NOT_UUIDS = [
    "0190f1d2-0000-7000-8000-0000000_0000",  # an underscore: int(hex, 16) allows it
    "+190f1d2-0000-7000-8000-000000000000",  # a sign
    " 0190f1d2-0000-7000-8000-00000000000",  # a leading space
    "0190f1d2-0000-7000-8000-00000000000\n",  # a trailing newline
    "\u0660" * 32 + "----",  # Arabic-Indic digits
]


@pytest.mark.parametrize("value", _LENIENT_NOT_UUIDS)
def test_the_sql_adapter_refuses_what_the_driver_would_refuse(value: str) -> None:
    with pytest.raises(ValueError):
        pgproto.UUID(value)  # the driver's own parser: these are errors there
    assert sql_repository._is_uuid(value) is False


@pytest.mark.parametrize("value", _LENIENT_NOT_UUIDS)
def test_an_id_cursor_the_driver_would_refuse_is_rejected_as_a_cursor(value: str) -> None:
    with pytest.raises(AppError):
        decode_id_cursor(encode_id_cursor(value))


def test_a_non_utf8_or_oversized_stdout_consumer_cannot_break_the_answer_channel(
    tmp_path: Path,
) -> None:
    """The child that dies before writing leaves the parent an empty stdout and exit 4: a
    500 parse_error, never a half answer."""
    done = subprocess.run(
        sandbox.child_argv(sandbox.Limits(512, 30)),
        input=b"not json",
        capture_output=True,
        env={"PYTHONPATH": str(sandbox.ROOT), "PATH": "/usr/bin:/bin"},
        cwd=sandbox.ROOT,
        timeout=60,
        check=False,
    )
    assert done.returncode == child.EXIT_FAILED and done.stdout == b""
    with pytest.raises(contract.ContractError) as raised:
        sandbox._answer(done.returncode, done.stdout)
    assert (raised.value.status, raised.value.code) == (500, "parse_error")


@pytest.mark.parametrize("op", ["pages", "summary"])
@pytest.mark.parametrize("damage", ["flate", "missing_font", "bad_resources", "bad_annots"])
def test_other_native_warnings_never_cost_a_page(tmp_path: Path, op: str, damage: str) -> None:
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((40, 60), f"-K{i}")
    if damage == "flate":
        x = doc[0].get_contents()[0]
        doc.update_stream(x, b"\x78\x9c garbage", compress=False)
        doc.xref_set_key(x, "Filter", "/FlateDecode")
    elif damage == "missing_font":
        x = doc[2].get_contents()[0]
        doc.update_stream(x, b"BT /NOPE 12 Tf (hi) Tj ET /Im9 Do /Gs9 gs Q Q", compress=False)
    elif damage == "bad_resources":
        doc.xref_set_key(doc[1].xref, "Resources", "99999 0 R")
    else:
        doc.xref_set_key(doc[1].xref, "Annots", "[99999 0 R 3 0 R]")
    path = tmp_path / "d.pdf"
    doc.save(str(path))
    doc.close()
    result = sandbox.run_child(
        {"op": op, "path": str(path), "first": 1, "last": 3, "token_sample": 10},
        timeout_s=30,
        limits=sandbox.Limits(512, 30),
    )
    assert len(result["pages"]) == 3 if op == "pages" else result["page_count"] == 3


def test_a_bomb_on_page_two_does_not_hurt_a_range_that_is_only_page_one(tmp_path: Path) -> None:
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page().insert_text((40, 60), "x")
    x = doc[1].get_contents()[0]
    doc.update_stream(
        x,
        zlib.compress(b"BT /helv 12 Tf (" + b"a" * (300 * 1024 * 1024) + b") Tj ET", 9),
        compress=False,
    )
    doc.xref_set_key(x, "Filter", "/FlateDecode")
    path = tmp_path / "b.pdf"
    doc.save(str(path))
    doc.close()
    ok = sandbox.run_child(
        {"op": "pages", "path": str(path), "first": 1, "last": 1, "token_sample": 5},
        timeout_s=60,
        limits=sandbox.Limits(256, 60),
    )
    assert [p["page_no"] for p in ok["pages"]] == [1]
    with pytest.raises(contract.ContractError) as raised:
        sandbox.run_child(
            {"op": "pages", "path": str(path), "first": 2, "last": 2, "token_sample": 5},
            timeout_s=60,
            limits=sandbox.Limits(256, 60),
        )
    assert (raised.value.status, raised.value.code) == (507, "memory_limit")
