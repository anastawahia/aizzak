"""Application-side UUIDv7 generation (DD-02, RFC 9562).

Identifiers are minted in the application layer *before* persistence so the
domain owns its identity ahead of any write, and so the API never leaks
sequential counters. UUIDv7 is time-sortable, which keeps B-Tree inserts and
Redis-stream ``event_id`` ordering cheap under write load.
"""

from __future__ import annotations

from uuid6 import uuid7

from app.framework.types import Uuid


def new_uuid7() -> Uuid:
    """Return a fresh, time-ordered UUIDv7 as its canonical text form."""
    return str(uuid7())
