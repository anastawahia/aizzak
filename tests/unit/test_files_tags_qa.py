"""QA coverage for file tags (PLT-26, INC-1 PR-A) -- the gaps the developers'
own suites leave, found by walking INC1-AC-1..3 clause by clause.

Four families, each a claim the AC makes that no other test pins:

* **Nothing is spent by a refused tag, whichever refusal answers first.**
  ``SpaceQuotaService`` runs BEFORE ``RegisterUpload`` parses the tags, so a
  tag the DOMAIN refuses (empty, over 128, a forbidden character) loses the
  precedence to an unknown space (404) or a full one (409). That is the same
  order a bad ``name`` has always had and it is not what AC-1 forbids: AC-1
  says "before any limit is spent and before a multipart upload is opened",
  and what these tests pin is exactly that -- no row, no presigned URL, no
  storage upload, whatever the status. A tag pydantic refuses (an unknown key,
  a non-string) never reaches the quota at all: its lock is never taken.
* **The ledger and the retag over the wire** -- a 422 does not burn an
  ``Idempotency-Key``, a replay with the same tags is the same file, and a
  re-tag whose value only differs in NFC form / padding is still a no-op.
* **The contracts agree with the code in both directions** -- the OpenAPI
  tag schemas, the event schema and the migration's CHECK all carry the one
  ``MAX_TAG_CHARS`` and the one set of ``TagKind`` keys.
* **Domain edges** -- code points not bytes, NFC before the length check, a
  non-breaking space is whitespace, keys are case-sensitive.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.framework.clock import utc_now
from app.framework.context.execution_context import ExecutionContext
from app.framework.settings.settings import Limits
from app.modules.files.domain.entities import File
from app.modules.files.domain.errors import InvalidFileInput
from app.modules.files.domain.value_objects import MAX_TAG_CHARS, FileStatus, FileTags, TagKind
from tests.unit.support_files_media import InMemoryFileRepository
from tests.unit.test_api_files_media_router import (
    _BIG,
    _SPACE,
    _auth,
    _make_app,
    _register,
)
from tests.unit.test_file_replacement import _PUMP
from tests.unit.test_file_replacement import _build as _repl_build
from tests.unit.test_file_replacement import _ctx as _repl_ctx
from tests.unit.test_file_replacement import _seed as _repl_seed

_ROOT = Path(__file__).resolve().parents[2]
_NO_SPACE = "018f0000-0000-7000-8000-0000000000zz"

# What the DOMAIN refuses (FileTags), so it reaches the use-case after the quota.
_DOMAIN_BAD: list[dict[str, str]] = [
    {"system": ""},
    {"system": "   "},
    {"system": "\u00a0"},
    {"system": "x" * (MAX_TAG_CHARS + 1)},
    {"equipment": "a\x00b"},
    {"system": "a\u200bb"},  # zero-width space (Cf)
]
# What pydantic refuses (FileTagsIn), before the handler runs at all.
_WIRE_BAD: list[dict[str, Any]] = [{"section": "elec"}, {"System": "x"}, {"system": 7}]

_PATHS = ("/api/v1/files", "/api/v1/files/multipart")


def _body(*, size: int, name: str, tags: object, space: str = _SPACE) -> dict[str, object]:
    return {
        "space_id": space,
        "name": name,
        "content_type": "application/pdf",
        "size_bytes": size,
        "tags": tags,
    }


# --------------------------------------------------------------------------- #
# AC-1 -- a refused tag spends nothing, whichever refusal answers first        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("tags", _DOMAIN_BAD)
def test_a_domain_bad_tag_into_a_full_space_spends_nothing(path: str, tags: dict[str, str]) -> None:
    app, stack = _make_app(limits=Limits(max_space_bytes=100))
    client = TestClient(app)
    _register(client, name="fill.pdf", size_bytes=90)
    rows = dict(stack.file_repository.rows)
    presigned = list(stack.storage.presigned_puts)

    response = client.post(path, json=_body(size=50, name="over.pdf", tags=tags), headers=_auth())

    # Rejected -- by the quota (409) or by the tag (422); never created.
    assert response.status_code in (409, 422), response.text
    assert response.json()["code"] in ("spaces.quota_exceeded", "common.validation_error")
    assert stack.file_repository.rows == rows
    assert stack.storage.presigned_puts == presigned
    assert stack.multipart_storage.uploads == {}


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("tags", _DOMAIN_BAD)
def test_a_domain_bad_tag_into_an_unknown_space_spends_nothing(
    path: str, tags: dict[str, str]
) -> None:
    app, stack = _make_app()
    client = TestClient(app)

    response = client.post(
        path, json=_body(size=_BIG, name="ghost.pdf", tags=tags, space=_NO_SPACE), headers=_auth()
    )

    assert response.status_code in (404, 422), response.text
    assert response.json()["code"] in ("common.not_found", "common.validation_error")
    assert stack.file_repository.rows == {}
    assert stack.storage.presigned_puts == []
    assert stack.multipart_storage.uploads == {}


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("tags", _WIRE_BAD)
def test_a_wire_bad_tag_is_a_422_before_the_quota_is_even_entered(
    path: str, tags: dict[str, Any]
) -> None:
    """pydantic answers (``extra='forbid'``, ``str | None``) ahead of the quota
    wrapper, so a full or unknown space cannot mask it."""
    app, stack = _make_app(limits=Limits(max_space_bytes=100))
    client = TestClient(app)
    _register(client, name="fill.pdf", size_bytes=90)
    locks_before = list(stack.space_quota._lock.held)  # type: ignore[attr-defined]
    multipart_locks_before = list(stack.multipart_quota._lock.held)  # type: ignore[attr-defined]

    full = client.post(path, json=_body(size=50, name="over.pdf", tags=tags), headers=_auth())
    ghost = client.post(
        path, json=_body(size=10, name="g.pdf", tags=tags, space=_NO_SPACE), headers=_auth()
    )

    for response in (full, ghost):
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "common.validation_error"
    assert stack.space_quota._lock.held == locks_before  # type: ignore[attr-defined]
    assert stack.multipart_quota._lock.held == multipart_locks_before  # type: ignore[attr-defined]
    assert stack.multipart_storage.uploads == {}


# --------------------------------------------------------------------------- #
# AC-1 -- the ledger and the retag over the wire                               #
# --------------------------------------------------------------------------- #
def test_a_422_for_a_bad_tag_does_not_burn_the_idempotency_key() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    headers = {**_auth(), "Idempotency-Key": "fix-the-tag"}
    base = {"space_id": _SPACE, "name": "p.pdf", "content_type": "application/pdf"}

    bad = client.post(
        "/api/v1/files", json={**base, "size_bytes": 10, "tags": {"system": ""}}, headers=headers
    )
    good = client.post(
        "/api/v1/files",
        json={**base, "size_bytes": 10, "tags": {"system": "ok"}},
        headers=headers,
    )

    assert bad.status_code == 422
    assert good.status_code == 201, good.text
    assert len(stack.file_repository.rows) == 1


def test_replaying_a_key_with_the_same_tags_is_the_same_file() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    headers = {**_auth(), "Idempotency-Key": "same-tags"}
    body = {
        "space_id": _SPACE,
        "name": "p.pdf",
        "content_type": "application/pdf",
        "size_bytes": 10,
        "tags": {"system": "s", "department": "d"},
    }

    first = client.post("/api/v1/files", json=body, headers=headers)
    second = client.post("/api/v1/files", json=body, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["file_id"] == second.json()["file_id"]
    assert len(stack.file_repository.rows) == 1


def test_a_retag_that_only_differs_by_nfc_form_or_padding_does_not_write() -> None:
    """The comparison is on the NORMALISED value: the decomposed spelling and a
    padded one of the stored tag are the same tag, so no `save`, no
    `updated_at` bump (INC1-AC-1: the no-op is judged after sanitation)."""
    app, stack = _make_app()
    client = TestClient(app)
    stored = "أجهزة"  # alef-with-hamza (composed) + "devices"
    decomposed = unicodedata.normalize("NFD", stored)
    assert decomposed != stored
    file_id = str(_register(client, tags={"system": stored})["file_id"])
    stamp = stack.file_repository.rows[file_id].updated_at
    saved = list(stack.file_repository.saved)

    for value in (decomposed, f"  {stored}  ", f"\u00a0{decomposed}\t"):
        response = client.put(
            f"/api/v1/files/{file_id}/tags", json={"system": value}, headers=_auth()
        )
        assert response.status_code == 200, response.text
        assert response.json()["tags"]["system"] == stored

    assert stack.file_repository.saved == saved
    assert stack.file_repository.rows[file_id].updated_at == stamp


def test_the_event_carries_the_present_tags_and_an_untagged_file_has_no_key() -> None:
    """End to end through the wired stack (register -> complete -> outbox): the
    published `data.tags` holds exactly the present tags, and an untagged
    file's `data` has no `tags` key -- never `null`, never `{}`."""
    app, stack = _make_app()
    client = TestClient(app)
    tagged = _register(client, name="a.pdf", tags={"system": "s", "department": None})["file_id"]
    plain = _register(client, name="b.pdf")["file_id"]
    for file_id in (tagged, plain):
        done = client.post(f"/api/v1/files/{file_id}/complete", json={}, headers=_auth())
        assert done.status_code == 200, done.text

    published = {
        record.payload["data"]["file_id"]: record.payload["data"]
        for _, records in stack.outbox.calls
        for record in records
        if record.event_type == "files.file.uploaded.v1"
    }
    assert published[tagged]["tags"] == {"system": "s"}
    assert "tags" not in published[plain]


# --------------------------------------------------------------------------- #
# AC-3 -- the contracts agree with the code, in both directions                #
# --------------------------------------------------------------------------- #
def _live_and_spec() -> tuple[dict[str, Any], dict[str, Any]]:
    app, _ = _make_app()
    spec = yaml.safe_load((_ROOT / "docs/design/openapi.yaml").read_text(encoding="utf-8"))
    return app.openapi(), spec


def _props(schema: dict[str, Any]) -> set[str]:
    return set(schema["properties"])


def test_openapi_and_the_application_agree_on_the_tag_schemas() -> None:
    live, spec = _live_and_spec()
    for name in ("FileTagsIn", "FileTagsOut", "FileRegisterIn", "FileOut"):
        assert _props(live["components"]["schemas"][name]) == _props(
            spec["components"]["schemas"][name]
        ), name
    assert _props(spec["components"]["schemas"]["FileTagsIn"]) == {k.value for k in TagKind}
    # Unknown keys are refused on BOTH sides of the contract.
    assert live["components"]["schemas"]["FileTagsIn"]["additionalProperties"] is False
    assert spec["components"]["schemas"]["FileTagsIn"]["additionalProperties"] is False
    # `tags` is optional on the way in, always present on the way out.
    assert "tags" not in live["components"]["schemas"]["FileRegisterIn"]["required"]
    assert "tags" in live["components"]["schemas"]["FileOut"]["required"]
    assert set(live["components"]["schemas"]["FileTagsOut"]["required"]) == {
        k.value for k in TagKind
    }


def test_retag_file_is_declared_by_both_documents_with_the_same_shape() -> None:
    live, spec = _live_and_spec()
    live_op = live["paths"]["/api/v1/files/{file_id}/tags"]["put"]
    spec_op = spec["paths"]["/files/{id}/tags"]["put"]

    assert spec_op["operationId"] == "retagFile"
    assert set(spec_op["responses"]) == {"200", "default"}
    assert set(live_op["responses"]) >= {"200"}
    for op in (live_op, spec_op):
        body = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        assert body.endswith("/FileTagsIn")
        ok = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ok.endswith("/FileOut")
    # No other PUT on /files/{id}: the new route is the only addition.
    assert [p for p in spec["paths"] if p.startswith("/files/{id}/tags")] == ["/files/{id}/tags"]


def test_one_tag_limit_and_one_key_set_across_domain_openapi_event_and_migration() -> None:
    _, spec = _live_and_spec()
    event = json.loads(
        (_ROOT / "docs/design/events/schemas/files.file.uploaded.v1.json").read_text("utf-8")
    )
    migration = (_ROOT / "migrations/versions/files/0006_file_tags.py").read_text("utf-8")
    kinds = {k.value for k in TagKind}

    event_tags = event["properties"]["tags"]
    assert set(event_tags["properties"]) == kinds
    assert event_tags["additionalProperties"] is False
    assert event_tags["minProperties"] == 1
    assert {p["maxLength"] for p in event_tags["properties"].values()} == {MAX_TAG_CHARS}
    assert {p["minLength"] for p in event_tags["properties"].values()} == {1}

    # The 128 limit counts AFTER NFC and trimming, which a JSON-schema
    # `maxLength` (raw length) cannot express: stating it would reject a padded
    # or decomposed value the server accepts. So the input schema carries no
    # `maxLength` and its description states the limit and the order.
    spec_in_schema = spec["components"]["schemas"]["FileTagsIn"]
    assert all("maxLength" not in p for p in spec_in_schema["properties"].values())
    assert str(MAX_TAG_CHARS) in spec_in_schema["description"]
    assert "AFTER normalisation and trimming" in spec_in_schema["description"]
    spec_out = spec["components"]["schemas"]["FileTagsOut"]
    assert set(spec_out["required"]) == kinds

    bounds = re.findall(r"char_length\(tag_\w+\)\s+BETWEEN 1 AND (\d+)", migration)
    assert len(bounds) == 3
    assert {int(b) for b in bounds} == {MAX_TAG_CHARS}
    assert {f"tag_{k}" for k in kinds} == set(re.findall(r"ADD COLUMN (tag_\w+)", migration))


# --------------------------------------------------------------------------- #
# AC-1 -- domain edges                                                         #
# --------------------------------------------------------------------------- #
def test_the_length_limit_counts_code_points_after_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", "أ") * MAX_TAG_CHARS  # 2 code points each
    assert len(decomposed) == 2 * MAX_TAG_CHARS

    tags = FileTags(system=decomposed)

    assert tags.system == "أ" * MAX_TAG_CHARS  # composed first, THEN measured
    with pytest.raises(InvalidFileInput):
        FileTags(system=decomposed + "أ")


def test_an_astral_character_counts_as_one() -> None:
    assert FileTags(system="\U0001f600" * MAX_TAG_CHARS).system is not None
    with pytest.raises(InvalidFileInput):
        FileTags(system="\U0001f600" * (MAX_TAG_CHARS + 1))


def test_a_non_breaking_space_is_whitespace_not_a_value() -> None:
    with pytest.raises(InvalidFileInput):
        FileTags(system="\u00a0\u3000 ")
    assert FileTags(system="\u00a0x\u3000").system == "x"


def test_the_keys_of_a_mapping_are_case_sensitive() -> None:
    with pytest.raises(InvalidFileInput):
        FileTags.from_mapping({"System": "x"})


# --------------------------------------------------------------------------- #
# Rework round 1 (S-3) -- the forbidden-character rule, walked exhaustively    #
# --------------------------------------------------------------------------- #
_FORBIDDEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def test_every_code_point_is_judged_by_its_unicode_category() -> None:
    """All 1,114,112 code points, one by one, in the middle of a value: refused
    exactly when the category is one of the seven forbidden ones. Pins the rule
    as a whole -- a category dropped from (or added to) the set, or a stray
    allow-list, changes this -- instead of the dozen samples the unit tests use.
    Nothing but ordinary spaces, letters, marks, numbers, punctuation and
    symbols may survive."""
    wrong: list[str] = []
    accepted: set[str] = set()
    refused: set[str] = set()
    for code_point in range(0x110000):
        char = chr(code_point)
        category = unicodedata.category(char)
        try:
            FileTags(system=f"a{char}b")
        except InvalidFileInput:
            refused.add(category)
            if category not in _FORBIDDEN:
                wrong.append(f"U+{code_point:04X} ({category}) refused")
        else:
            accepted.add(category)
            if category in _FORBIDDEN:
                wrong.append(f"U+{code_point:04X} ({category}) accepted")

    assert wrong == []
    assert refused == _FORBIDDEN  # each forbidden category really has members
    assert not accepted & _FORBIDDEN
    assert {"Zs", "Lo", "Ll", "Lu", "Mn", "Mc", "Nd", "So", "Po"} <= accepted


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("\t\u0085x\u2028 ", "x"),  # control and separator whitespace at both ends
        ("\x1f x \x1c", "x"),  # C0 information separators are str.strip whitespace
        ("\u2029x\u2029", "x"),
        ("  \u00a0مضخة-7\u3000", "مضخة-7"),
    ],
)
def test_edge_whitespace_is_stripped_before_the_category_check(raw: str, kept: str) -> None:
    assert FileTags(system=raw).system == kept


@pytest.mark.parametrize("raw", ["\x00x", "x\x00", "\u200bx", "x\u200b", "\ufeffx", "\u202ex"])
def test_edge_characters_that_are_not_whitespace_are_refused_not_stripped(raw: str) -> None:
    """`strip()` removes only whitespace. A NUL, a zero-width space, a BOM or a
    bidi override at an edge is a forbidden character, never silently trimmed
    away -- that would store a value the caller did not send."""
    with pytest.raises(InvalidFileInput):
        FileTags(system=raw)


def test_a_value_that_is_only_invisible_is_refused() -> None:
    for raw in ("\u200b", "\u200b\u200d", "\ufeff", "\u00ad", " \u200b "):
        with pytest.raises(InvalidFileInput):
            FileTags(equipment=raw)


@pytest.mark.parametrize(
    "value",
    [
        "ẹ́",  # combining marks stack, NFC reorders and composes
        "أ",  # alef + hamza above (Mn) -> NFC alef-hamza
        "مَضخة",  # Arabic with a fatha (Mn)
        "กิ",  # Thai base + vowel mark
        "\U0001f468\u200d\U0001f469",  # a ZWJ emoji sequence is refused (Cf inside)
    ],
)
def test_combining_marks_pass_and_a_zwj_sequence_does_not(value: str) -> None:
    if "\u200d" in value:
        with pytest.raises(InvalidFileInput):
            FileTags(system=value)
    else:
        assert FileTags(system=value).system == unicodedata.normalize("NFC", value)


# --------------------------------------------------------------------------- #
# Rework round 1 (S-3 / B-1) -- over the wire, on every route that takes tags  #
# --------------------------------------------------------------------------- #
# JSON escapes, because a lone surrogate cannot be encoded by httpx itself.
_HOSTILE_ESCAPES = {
    "surrogate": "a\\ud800b",
    "nul": "a\\u0000b",
    "zwsp": "a\\u200bb",
    "bom": "\\ufeffa",
    "bidi-override": "a\\u202eb",
    "bidi-isolate": "a\\u2066b",
    "private-use": "a\\ue000b",
    "unassigned": "a\\u0378b",
    "line-separator": "a\\u2028b",
    "c1-control": "a\\u0085b",
}


def _raw(path_body: str) -> dict[str, Any]:
    return {"content": path_body, "headers": {**_auth(), "Content-Type": "application/json"}}


@pytest.mark.parametrize("label", sorted(_HOSTILE_ESCAPES))
@pytest.mark.parametrize("path", _PATHS)
def test_a_forbidden_character_at_registration_is_a_422_that_never_echoes_it(
    path: str, label: str
) -> None:
    escaped = _HOSTILE_ESCAPES[label]
    app, stack = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = (
        f'{{"space_id":"{_SPACE}","name":"plan.pdf","content_type":"application/pdf",'
        f'"size_bytes":{_BIG},"tags":{{"department":"{escaped}"}}}}'
    )

    response = client.post(path, **_raw(body))

    assert response.status_code == 422, (label, response.text)
    assert response.json()["code"] == "common.validation_error"
    for char in json.loads(f'"{escaped}"'):
        if unicodedata.category(char) in _FORBIDDEN:
            assert char not in response.text
            assert f"\\u{ord(char):04x}" not in response.text.lower()
    assert stack.file_repository.rows == {}
    assert stack.storage.presigned_puts == []
    assert stack.multipart_storage.uploads == {}


@pytest.mark.parametrize("label", sorted(_HOSTILE_ESCAPES))
def test_a_forbidden_character_in_a_retag_is_a_422_and_changes_nothing(label: str) -> None:
    app, stack = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    file_id = str(_register(client, tags={"system": "keep-me"})["file_id"])
    saved = list(stack.file_repository.saved)

    response = client.put(
        f"/api/v1/files/{file_id}/tags",
        **_raw(f'{{"system":"keep-me","equipment":"{_HOSTILE_ESCAPES[label]}"}}'),
    )

    assert response.status_code == 422, (label, response.text)
    assert stack.file_repository.saved == saved
    assert stack.file_repository.rows[file_id].tags.as_mapping() == {"system": "keep-me"}
    assert stack.knowledge_purge.calls == []


def test_allowed_text_of_other_scripts_registers_and_reads_back_unchanged() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    tags = {"system": "مضخة الماء \u0667", "equipment": "泵房-3号", "department": "😀 é"}
    file_id = str(_register(client, tags=tags)["file_id"])

    got = client.get(f"/api/v1/files/{file_id}", headers=_auth()).json()

    assert got["tags"] == {
        "system": "مضخة الماء \u0667",
        "equipment": "泵房-3号",
        "department": "😀 é",  # NFC-composed, the emoji and the space kept
    }


# --------------------------------------------------------------------------- #
# Rework round 1 (S-1 / S-4) -- who is swept, and what the audit line says     #
# --------------------------------------------------------------------------- #
_NOT_READY = [status for status in FileStatus if status is not FileStatus.READY]


def test_the_not_ready_statuses_are_exactly_the_three_the_contract_names() -> None:
    """The openapi text says `uploaded`, `scanning`, `quarantined`. If a status
    is ever added (a `failed`, say) this fails, so the sweep rule is re-read."""
    assert {s.value for s in _NOT_READY} == {"uploaded", "scanning", "quarantined"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _NOT_READY)
async def test_a_no_op_rename_or_retag_of_a_file_that_is_not_ready_deletes_nothing(
    status: FileStatus,
) -> None:
    """Even when the file already collides with an older namesake (the state a
    failed sweep leaves), a write that changes nothing on a not-ready file
    still destroys nothing -- the status, not the write, decides."""
    ctx = _repl_ctx()
    files = InMemoryFileRepository()
    older = _repl_seed(
        files, ctx, name="plan.pdf", created_at=utc_now() - timedelta(days=1), tags=_PUMP
    )
    held = _repl_seed(files, ctx, name="plan.pdf", tags=_PUMP, status=status)
    service, erase = _repl_build(files)
    saves = len(files.saved)

    await service.rename(ctx, held.id, name="plan.pdf")
    await service.retag(ctx, held.id, tags=_PUMP.as_mapping())

    assert len(files.saved) == saves  # both really were no-ops
    assert erase.deleted == []
    assert files.rows[older.id].deleted_at is None


@pytest.mark.asyncio
async def test_a_no_op_rename_of_a_ready_file_still_sweeps() -> None:
    ctx = _repl_ctx()
    files = InMemoryFileRepository()
    older = _repl_seed(files, ctx, name="plan.pdf", created_at=utc_now() - timedelta(days=1))
    ready = _repl_seed(files, ctx, name="plan.pdf")
    service, erase = _repl_build(files)

    await service.rename(ctx, ready.id, name="plan.pdf")

    assert erase.deleted == [older.id]


@pytest.mark.asyncio
async def test_a_retag_that_makes_a_file_not_ready_never_happens_and_status_is_untouched() -> None:
    """A re-tag/rename must not change the lifecycle status (a quarantined file
    stays quarantined), otherwise the sweep gate could be walked around."""
    ctx = _repl_ctx()
    files = InMemoryFileRepository()
    _repl_seed(files, ctx, name="plan.pdf", created_at=utc_now() - timedelta(days=1), tags=_PUMP)
    held = _repl_seed(files, ctx, name="draft.pdf", status=FileStatus.QUARANTINED)
    service, _ = _repl_build(files)

    renamed = await service.rename(ctx, held.id, name="plan.pdf")
    retagged = await service.retag(ctx, held.id, tags=_PUMP.as_mapping())

    assert renamed.status is FileStatus.QUARANTINED
    assert retagged.status is FileStatus.QUARANTINED
    assert files.rows[held.id].status is FileStatus.QUARANTINED


class _LookupFails(InMemoryFileRepository):
    async def live_namesakes(self, ctx: ExecutionContext, file: File) -> list[str]:
        raise RuntimeError("postgres is down: secret-plan.pdf pump-7")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["complete", "rename", "retag"])
async def test_a_failed_lookup_is_logged_with_the_audit_fields_and_does_not_fail_the_call(
    caplog: pytest.LogCaptureFixture, source: str
) -> None:
    ctx = _repl_ctx()
    files = _LookupFails()
    row = _repl_seed(
        files,
        ctx,
        name="draft.pdf",
        tags=_PUMP,
        status=FileStatus.UPLOADED if source == "complete" else FileStatus.READY,
    )
    service, erase = _repl_build(files)

    with caplog.at_level(logging.INFO):
        if source == "complete":
            out = await service.complete(ctx, file_id=row.id, checksum=None)
        elif source == "rename":
            out = await service.rename(ctx, row.id, name="secret-plan.pdf")
        else:
            out = await service.retag(ctx, row.id, tags={"system": "pump-7"})

    assert out.status is FileStatus.READY  # the call succeeded
    assert erase.deleted == []
    (record,) = [r for r in caplog.records if r.getMessage() == "file.replacement.lookup_failed"]
    assert record.__dict__["user_id"] == "u1"
    assert record.__dict__["source"] == source
    assert record.__dict__["workspace_id"] == "w1"
    assert record.__dict__["file_id"] == row.id
    # The exception text is in the traceback (exc_info), the extra fields are not.
    extra = {k: v for k, v in record.__dict__.items() if k in _AUDIT_KEYS}
    assert "secret-plan" not in str(extra) and "pump-7" not in str(extra)


_AUDIT_KEYS = {"file_id", "workspace_id", "user_id", "source", "replaced_id"}


@pytest.mark.asyncio
async def test_no_sweep_log_field_ever_carries_a_name_or_a_tag_value() -> None:
    """Whatever the source, every record the service emits holds ids and the
    source word only: names and tags are customer data (and a tag may be a
    UUID of another system)."""
    ctx = _repl_ctx()
    files = InMemoryFileRepository()
    older = _repl_seed(
        files, ctx, name="secret-plan.pdf", created_at=utc_now() - timedelta(days=1), tags=_PUMP
    )
    newer = _repl_seed(files, ctx, name="draft.pdf", tags=_PUMP)
    service, _ = _repl_build(files, fails={older.id})
    handler = _Capture()
    logger = logging.getLogger("app.framework.di.file_replacement")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        await service.rename(ctx, newer.id, name="secret-plan.pdf")
        await service.retag(ctx, newer.id, tags=_PUMP.as_mapping())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert handler.records, "the failed purge should have been logged"
    for record in handler.records:
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STD_LOG_ATTRS and k not in {"exc_info", "exc_text", "stack_info"}
        }
        assert set(extras) <= _AUDIT_KEYS, extras
        assert "secret-plan" not in str(extras) and "pump-7" not in str(extras)
        assert "secret-plan" not in record.getMessage() and "pump-7" not in record.getMessage()
        assert extras["source"] in {"complete", "rename", "retag"}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


_STD_LOG_ATTRS = set(logging.LogRecord("x", 0, "x", 0, "x", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


# At the route: a quarantined file is renamed and re-tagged (200), destroys
# nothing, stays quarantined -- and a ready file re-tagged onto an older one
# still does replace it.
def test_the_routes_sweep_for_a_ready_file_and_not_for_a_quarantined_one() -> None:
    app, stack = _make_app()
    client = TestClient(app)
    older = str(_register(client, tags=_TAGS_ROUTE)["file_id"])
    client.post(f"/api/v1/files/{older}/complete", json={}, headers=_auth())
    held = str(_register(client, name="draft.pdf")["file_id"])
    stack.file_repository.rows[held].status = FileStatus.QUARANTINED

    tagged = client.put(f"/api/v1/files/{held}/tags", json=_TAGS_ROUTE, headers=_auth())
    renamed = client.patch(f"/api/v1/files/{held}", json={"name": "report.pdf"}, headers=_auth())

    assert tagged.status_code == 200 and renamed.status_code == 200, (tagged.text, renamed.text)
    assert stack.knowledge_purge.calls == []
    assert stack.file_repository.rows[older].deleted_at is None
    assert stack.file_repository.rows[held].status is FileStatus.QUARANTINED
    assert tagged.json()["status"] == "quarantined"

    ready = str(_register(client, name="other.pdf")["file_id"])
    client.post(f"/api/v1/files/{ready}/complete", json={}, headers=_auth())
    client.put(f"/api/v1/files/{ready}/tags", json=_TAGS_ROUTE, headers=_auth())
    assert stack.knowledge_purge.calls == []  # still a different name
    swept = client.patch(f"/api/v1/files/{ready}", json={"name": "report.pdf"}, headers=_auth())

    assert swept.status_code == 200
    # `older` and the quarantined `held` (now its namesake too) are both OLDER
    # than the ready file that arrived, so both go.
    assert sorted(stack.knowledge_purge.calls) == sorted([older, held])


_TAGS_ROUTE = {"system": "sys-1", "equipment": "pump-7"}
