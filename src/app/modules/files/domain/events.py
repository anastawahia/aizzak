"""Files domain events (pure — 06-domain-models §6).

In-memory domain events: plain frozen records with no shared base class (the
domain imports no framework code). ``FileUploaded`` is the **global** event
consumed by ``knowledge`` (04-event-catalog); it fires on *completion*
(status -> ready), not on register, so a subscriber only ever indexes a
fully-uploaded, ready file. Its fields mirror the published schema
(``events/schemas/files.file.uploaded.v1.json``): ``file_id``, ``content_type``,
``size_bytes``, ``storage_key``, ``checksum``, and — since the spaces plan's
step 8 — ``space_id``. Dispatch to the event bus / outbox happens in the
application and infrastructure layers, not here.

``space_id`` rides on this event for the reason ``storage_key`` and
``size_bytes`` do: it is a fact about the file that its single consumer needs
and would otherwise have to read back out of the files module. The consumer
is ``knowledge``, which files every ``Document`` under the space of the file
it was built from and has no other way to learn it. An OPTIONAL added field
keeps the type at ``v1`` (04 §6), and an old envelope replayed without the
key simply registers a document with no space — the same state every document
already has today.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FileUploaded:
    """A file finished uploading and is ready for use (global -> knowledge)."""

    file_id: str
    workspace_id: str
    space_id: str | None
    content_type: str
    size_bytes: int
    storage_key: str
    checksum: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class FileDeleted:
    """A file was soft-deleted."""

    file_id: str
    workspace_id: str
    occurred_at: datetime


FileEvent = FileUploaded | FileDeleted
