"""QA coverage for file tags (PLT-26, INC-1 PR-A) -- the gaps the developers'
own suites leave, found by walking INC1-AC-1..3 clause by clause.

Four families, each a claim the AC makes that no other test pins:

* **Nothing is spent by a refused tag, whichever refusal answers first.**
  ``SpaceQuotaService`` runs BEFORE ``RegisterUpload`` parses the tags, so a
  tag the DOMAIN refuses (empty, over 128, a control character) loses the
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
import re
import unicodedata
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.framework.settings.settings import Limits
from app.modules.files.domain.errors import InvalidFileInput
from app.modules.files.domain.value_objects import MAX_TAG_CHARS, FileTags, TagKind
from tests.unit.test_api_files_media_router import (
    _BIG,
    _SPACE,
    _auth,
    _make_app,
    _register,
)

_ROOT = Path(__file__).resolve().parents[2]
_NO_SPACE = "018f0000-0000-7000-8000-0000000000zz"

# What the DOMAIN refuses (FileTags), so it reaches the use-case after the quota.
_DOMAIN_BAD: list[dict[str, str]] = [
    {"system": ""},
    {"system": "   "},
    {"system": "\u00a0"},
    {"system": "x" * (MAX_TAG_CHARS + 1)},
    {"equipment": "a\x00b"},
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

    spec_in = spec["components"]["schemas"]["FileTagsIn"]["properties"]
    assert {p["maxLength"] for p in spec_in.values()} == {MAX_TAG_CHARS}

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
