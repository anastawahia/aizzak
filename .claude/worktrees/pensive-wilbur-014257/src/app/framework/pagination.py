"""Cursor pagination primitives (DD-06, API-03, 02-port-contracts §2).

Collections are paginated by an *opaque* cursor. A caller treats it as
meaningless text and simply echoes ``meta.next_cursor`` back on the next
request; internally it is a base64url envelope around one keyset position.

**Two typed codecs, because there are two keysets** (6.3-أ). 02 §2 defines the
general case — a keyset on the UUIDv7 ``id`` — and every repository but one
uses it. ``list_messages`` keysets on ``seq`` instead, deliberately (§3.55:
``seq`` IS a conversation's canonical order and ``uq_msg_seq`` makes it
index-backed). Until 6.3 both rode through a single ``encode_cursor(last_id:
Uuid)`` with the seq stringified at the call site, and mypy could not object
because ``Uuid`` is an alias for ``str`` — the alias made the type lie
invisible. Naming the keyset in the function makes it visible instead.

**The type is the tag.** A cursor minted for one collection and replayed
against another is rejected without any kind marker inside the payload: a
``seq`` cursor carries ``"42"``, which ``decode_id_cursor`` refuses because it
is not a UUID, and an ``id`` cursor carries a UUID, which ``decode_seq_cursor``
refuses because it is not an integer. A discriminator in the envelope would
buy a better error message for a case the types already close.

**One direction, and it is NEWEST FIRST** (6.3-ب). A keyset cannot exist
without a direction, and an API whose collections disagree about theirs makes
every client guess: before 6.3 the paginated collections read oldest-first
(``ORDER BY id``, ``id > cursor``) while every unpaginated one documented and
returned newest-first, so ``GET /files`` opened on a workspace's oldest upload
while ``GET /credentials`` opened on its newest key. Resource listings are
inboxes — the rows that matter are the recent ones — so a paginated repository
orders ``id DESC`` and filters ``id < cursor``. The predicate and the ORDER BY
must point the same way; pointing them apart yields a page that is silently
empty or endless. The ONE exception is ``list_messages``: a transcript is a
narrative, not an inbox, so it reads forward by ``seq``.

**Decoding is TOTAL.** Every malformed input — non-base64 text, an alphabet
violation, a truncated group, non-UTF-8 bytes, an empty payload, well-formed
base64 carrying the wrong keyset — leaves through ``common.invalid_cursor``
(422). Nothing reaches a WHERE clause unvalidated. That matters because the
decoded value is compared against a ``uuid`` column: before 6.3 the lenient
decoder silently DROPPED non-alphabet characters, so ``"!!!!"`` decoded to the
empty string and ``"aGVsbG8"`` to ``"hello"``, and both then arrived at
Postgres as a UUID comparison — a 500 for what is plainly a client mistake,
with the catalog's ``common.invalid_cursor`` sitting unreachable beside it.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import NoReturn
from uuid import UUID

from app.framework.errors import ValidationError
from app.framework.types import Uuid

# base64url's alphabet, spelled for `b64decode(validate=True)` — the strict
# decoder. `urlsafe_b64decode` has no `validate` parameter, which is exactly
# how the lenient behaviour got in.
_URLSAFE = b"-_"


@dataclass(frozen=True, slots=True)
class Page[T]:
    """A single page of results plus the cursor to fetch the next one."""

    data: list[T]
    next_cursor: str | None
    limit: int


def encode_id_cursor(last_id: Uuid) -> str:
    """Encode an ``id`` keyset position (the last row's UUIDv7) as a cursor."""
    return _encode(last_id)


def decode_id_cursor(cursor: str) -> Uuid:
    """Decode an ``id`` cursor back to its UUIDv7, or reject it.

    The UUID check is the contract's, not a nicety: 02 §2 says the cursor
    encodes "a keyset on ``id`` (UUIDv7) as text", and the column it is
    compared against is a ``uuid``. Text that survives base64 but is not a
    UUID has no meaning here, and the only alternative to rejecting it is
    letting the database reject it — as a 500.
    """
    raw = _decode(cursor)
    try:
        UUID(raw)
    except ValueError:
        _reject()
    return raw


def encode_seq_cursor(last_seq: int) -> str:
    """Encode a ``seq`` keyset position (a message's ordinal) as a cursor."""
    return _encode(str(last_seq))


def decode_seq_cursor(cursor: str) -> int:
    """Decode a ``seq`` cursor back to its ordinal, or reject it."""
    raw = _decode(cursor)
    try:
        return int(raw)
    except ValueError:
        _reject()


def _encode(payload: str) -> str:
    """Wrap a keyset payload in the opaque envelope (padding stripped)."""
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def _decode(cursor: str) -> str:
    """Unwrap the envelope strictly, or reject the cursor.

    ``validate=True`` is the load-bearing word. The lenient decoder DISCARDS
    characters outside the alphabet, so a cursor a proxy or a copy-paste
    mangled does not fail — it quietly decodes to a DIFFERENT, valid position
    (``"!!!!NDI"`` and ``"NDI"`` both yield ``42``). A cursor that no longer
    says what it said is worse than one that is rejected: the client pages
    from somewhere it never asked for, and nothing anywhere reports a fault.

    No guard against an empty payload: both typed callers reject one on their
    own terms (``""`` is neither a UUID nor an int), so a check here would be
    a layer that reads as load-bearing while being unreachable — precisely
    what a mutation survives (the §3.69-و lesson, and this one WAS a survivor
    before it was deleted).
    """
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.b64decode(cursor + padding, altchars=_URLSAFE, validate=True)
    except (binascii.Error, ValueError):
        _reject()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        _reject()


def _reject() -> NoReturn:
    """The single exit for every malformed cursor.

    The message never echoes the cursor back: it is opaque by contract, so
    quoting it tells the client nothing it can act on, and a cursor is one of
    the few request values that can carry a keyset id into a log line.
    """
    raise ValidationError("malformed pagination cursor", code="common.invalid_cursor")
