"""Unit tests for the Phase 1 foundation (identifiers, pagination, errors,
redaction, structured logging, settings). Pure — no infrastructure."""

from __future__ import annotations

import json
import logging
from uuid import UUID

import pytest

from app.framework.errors import AppError, NotFoundError, ValidationError
from app.framework.identifiers import new_uuid7
from app.framework.observability.logging import JsonFormatter, configure_logging
from app.framework.observability.redaction import REDACTED, redact
from app.framework.pagination import (
    decode_id_cursor,
    decode_seq_cursor,
    encode_id_cursor,
    encode_seq_cursor,
)
from app.framework.settings import Settings


# --------------------------------------------------------------------------- #
# identifiers                                                                  #
# --------------------------------------------------------------------------- #
def test_new_uuid7_is_valid_version_7() -> None:
    value = new_uuid7()
    parsed = UUID(value)
    assert parsed.version == 7


def test_new_uuid7_is_unique_and_time_sortable() -> None:
    ids = [new_uuid7() for _ in range(200)]
    assert len(set(ids)) == len(ids)
    # UUIDv7 is time-ordered → lexicographic sort matches generation order.
    assert ids == sorted(ids)


# --------------------------------------------------------------------------- #
# pagination                                                                   #
# --------------------------------------------------------------------------- #
def test_id_cursor_round_trips() -> None:
    last_id = new_uuid7()
    assert decode_id_cursor(encode_id_cursor(last_id)) == last_id


def test_seq_cursor_round_trips() -> None:
    assert decode_seq_cursor(encode_seq_cursor(0)) == 0
    assert decode_seq_cursor(encode_seq_cursor(4_211)) == 4_211


def test_a_cursor_is_opaque_and_url_safe() -> None:
    """The payload never appears in the cursor a client is handed, and no
    padding rides along into a query string.

    Pinned on a ``seq`` cursor because that is where padding actually occurs:
    a 36-character UUID encodes to a whole number of base64 groups, so an
    ``id`` cursor never carries a ``=`` to strip in the first place.
    """
    last_id = new_uuid7()
    assert last_id not in encode_id_cursor(last_id)
    assert "=" not in encode_seq_cursor(42)


# Every class of malformed cursor 6.3-أ made total. The first two are the ones
# that used to get THROUGH: the lenient decoder silently dropped non-alphabet
# characters, so `"!!!!"` became the empty string and `"aGVsbG8"` became
# `"hello"`, and both then reached Postgres as a comparison against a `uuid`
# column — a 500 for a plain client mistake.
@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("!!!!", id="alphabet-violation-decoding-to-empty"),
        pytest.param("aGVsbG8", id="well-formed-base64-carrying-non-uuid"),
        pytest.param("!!!not-base64!!!", id="not-base64-at-all"),
        pytest.param("", id="empty"),
        pytest.param("=", id="padding-only"),
        pytest.param("abcde", id="truncated-group"),
        pytest.param("_w", id="non-utf8-bytes"),
        pytest.param("NDI", id="a-seq-cursor-replayed-against-an-id-collection"),
    ],
)
def test_decode_id_cursor_rejects(cursor: str) -> None:
    with pytest.raises(ValidationError) as exc:
        decode_id_cursor(cursor)
    assert exc.value.code == "common.invalid_cursor"
    # The opaque value is never echoed back into the message (or the log).
    assert cursor not in str(exc.value) or not cursor


def test_decode_seq_cursor_rejects_an_id_cursor() -> None:
    """The type IS the tag: no discriminator in the envelope, yet a cursor
    minted for an ``id`` collection cannot be spent on a ``seq`` one."""
    with pytest.raises(ValidationError) as exc:
        decode_seq_cursor(encode_id_cursor(new_uuid7()))
    assert exc.value.code == "common.invalid_cursor"


def test_decode_seq_cursor_rejects_garbage() -> None:
    with pytest.raises(ValidationError) as exc:
        decode_seq_cursor("!!!!")
    assert exc.value.code == "common.invalid_cursor"


def test_a_mangled_cursor_is_refused_rather_than_silently_re_read() -> None:
    """The strict-alphabet case, and the reason it is not decoration.

    ``"!!!!NDI"`` is ``encode_seq_cursor(42)`` with junk injected. A LENIENT
    base64 decoder discards the junk and hands back 42 — the client silently
    pages from a position it never asked for, and no layer reports a fault.
    Rejecting is the only answer that tells the truth.
    """
    assert encode_seq_cursor(42) == "NDI"
    with pytest.raises(ValidationError) as exc:
        decode_seq_cursor("!!!!NDI")
    assert exc.value.code == "common.invalid_cursor"


# --------------------------------------------------------------------------- #
# errors                                                                       #
# --------------------------------------------------------------------------- #
def test_error_defaults_and_override() -> None:
    assert NotFoundError().status == 404
    assert NotFoundError().code == "common.not_found"

    err = AppError("boom", code="files.too_large", status=413)
    assert (err.code, err.status, err.detail) == ("files.too_large", 413, "boom")


# --------------------------------------------------------------------------- #
# redaction                                                                    #
# --------------------------------------------------------------------------- #
def test_redact_masks_secret_keys_and_keeps_others() -> None:
    payload = {
        "password": "hunter2",
        "access_token": "abc",
        "user": {"api_key": "k", "name": "zaid"},
        "items": [{"client_secret": "s"}, {"id": "1"}],
        "count": 3,
    }
    out = redact(payload)
    assert out["password"] == REDACTED
    assert out["access_token"] == REDACTED
    assert out["user"]["api_key"] == REDACTED
    assert out["user"]["name"] == "zaid"
    assert out["items"][0]["client_secret"] == REDACTED
    assert out["items"][1]["id"] == "1"
    assert out["count"] == 3


# --------------------------------------------------------------------------- #
# structured logging                                                           #
# --------------------------------------------------------------------------- #
def test_json_formatter_emits_object_with_redacted_extra() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.api_key = "secret-value"  # type: ignore[attr-defined]
    record.workspace = "ws-1"  # type: ignore[attr-defined]

    parsed = json.loads(formatter.format(record))
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "app.test"
    assert parsed["message"] == "hello world"
    assert parsed["api_key"] == REDACTED
    assert parsed["workspace"] == "ws-1"


def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("DEBUG")
    assert len(logging.getLogger().handlers) == 1


# --------------------------------------------------------------------------- #
# settings contract                                                            #
# --------------------------------------------------------------------------- #
def test_settings_defaults_match_design() -> None:
    settings = Settings()
    assert settings.api_prefix == "/api/v1"
    # OPS-02: statement cache disabled behind PgBouncer transaction pooling.
    assert settings.database.statement_cache_size == 0
    # 07-nfr-slo §4 approved limits.
    assert settings.limits.max_upload_bytes == 52_428_800
    assert settings.integrations.mcp_allowed_transports == ("http", "sse")


def test_settings_is_immutable() -> None:
    settings = Settings()
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError on frozen set
        settings.app_env = "prod"  # type: ignore[misc]
