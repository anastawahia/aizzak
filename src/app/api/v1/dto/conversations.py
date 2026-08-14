"""Conversations DTOs — the wire shapes of `03-api-spec §2` — Phase 6.1-ج-2.

Field-for-field the spec's models. Two notes on what is deliberately NOT here:

* **No ``version``, no ``deleted_at``, no ``message_count``.** The aggregate
  carries them; the wire does not. ``version`` is the optimistic lock, which
  is the repository's business and would invite a client to reason about it;
  a soft-deleted conversation is simply absent from every read route.
* **No ``agent_key`` on the patch DTO.** A thread is threaded per
  ``(workspace, agent)`` by `06 §4`, so re-pointing an existing thread at a
  different agent is not a rename — it is a different thread.

``MessageOut.content`` is ``dict[str, Any]`` (the spec's own type) and is
rendered from the domain's ``MessageContent`` as ``{"text", "attachments"}``
— today the only shape that value object can hold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConversationOut(BaseModel):
    """One conversation thread, as returned by every conversations route."""

    id: str
    agent_key: str
    kind: str
    title: str | None
    # BE-RAG-003 — the pinned D-16 routing key (a value from
    # `ModelOut.capability`), or null when the thread routes by agent key.
    # On the response and not only on the pin route, because a client that can
    # set it must be able to render it without a second request.
    model_route: str | None
    created_at: datetime


class ConversationCreateIn(BaseModel):
    """``POST /conversations`` — open a thread under one agent.

    ``kind`` is absent on purpose: `06 §4` reserves ``workflow`` threads for
    runs the orchestrator opens itself (D-12), so a client-created thread is
    always an ``agent`` one and there is nothing to choose.
    """

    agent_key: str = Field(min_length=1)
    title: str | None = None


class ConversationPatchIn(BaseModel):
    """``PATCH /conversations/{id}`` — the title, and only the title.

    ``title`` is REQUIRED to be present even though its value may be ``null``:
    with an optional field, "clear the title" and "do not touch the title"
    would be the same request body, and a client that meant one would silently
    get the other. Present-and-``null`` clears; present-and-string renames; an
    empty body is a 422.

    No ``max_length`` although `01 §2.4` types the column ``text``: the create
    route accepts any title, and a rename stricter than the creation it edits
    would let a client author a title it could then never modify.
    """

    title: str | None


class ConversationModelIn(BaseModel):
    """``PUT /conversations/{id}/model`` — which configured route answers here.

    A SEPARATE route rather than a field on ``ConversationPatchIn``, because
    that DTO's ``title`` is required-to-be-present. A second required-present
    field would force a client renaming a thread to restate its route (and
    vice versa), and making this one optional instead would reintroduce, for
    the route, exactly the "clear vs. do not touch" ambiguity ``title`` is
    shaped to avoid. Two single-valued sub-resources, two writes.

    ``route`` is likewise required-to-be-present: a string pins,
    present-and-``null`` unpins, an empty body is a 422.

    The value is a ROUTING KEY — one of ``ModelOut.capability`` from
    ``GET /models`` — never a model name. `D-16`/`FR-73` put the
    provider/model pair in configuration, so a free-text model name would name
    something the platform may not be able to route; the key moves provider and
    model together and is validated against the live table on the way in.
    """

    route: str | None


class MessageOut(BaseModel):
    """One message within a thread."""

    id: str
    role: str
    content: dict[str, Any]
    token_count: int | None
    seq: int
    created_at: datetime


class MessageCreateIn(BaseModel):
    """``POST /conversations/{id}/messages`` — post a turn and run the thread's
    agent on it (`03 §2`: "يشغّل الوكيل ويعيد ردّه").

    ``content`` is passed to the agent verbatim as its request ``input`` AND
    persisted as the user's message, which is why there is no separate
    ``input`` field: one payload, one turn, no way for the two to disagree.
    """

    content: dict[str, Any]
    stream: bool = False


class ConversationFileIn(BaseModel):
    """``POST /conversations/{id}/files`` — pin one workspace file into this
    thread's retrieval scope (`03 §2`, BE-RAG-005).

    A body rather than a path segment on the POST, so the pin reads as
    "create this member of the collection" instead of "PUT this id" — and so
    the 422 for an unreadable file lands on a field the client can point at.
    """

    file_id: str


class ConversationFileOut(BaseModel):
    """One pin: a REFERENCE, not the file.

    No name, size or status. Those belong to ``FileOut`` (``GET /files``) and
    the client joins them by id — exactly as it already joins a file to its
    knowledge-document status. Projecting them here would make the
    conversations module answer for another module's columns, and would put
    two sources of truth for a file's name in one screen.
    """

    file_id: str
    pinned_at: datetime
