"""Media value objects (pure — 06-domain-models §8).

Frozen, self-validating primitives. ``AgentKey`` is a module-local copy of the
same-shaped value object in ``conversations``/``memory`` (module independence,
12-module-authoring-guide §3) — media does not import another module's domain,
even one that happens to look identical. ``GenParams`` validates only the
*shape* of a generation job's parameters (known keys per ``MediaKind``, types,
positive values) — the *configurable* numeric ceilings (07-nfr-slo §4:
``max_image_dim``, ``max_video_seconds``) are deliberately left to the
application layer (``RequestMedia``), which enforces them against an injected
``Limits`` instead of hard-coding them here (INV-MJ3; the ``files``
``RegisterUpload``/``Limits`` precedent). Identifiers stay plain UUIDv7 text;
the domain imports no framework.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.modules.media.domain.errors import InvalidMediaInput

_MAX_AGENT_KEY_LEN = 64
# Slug: starts with a lowercase letter/digit, then any run of lowercase
# letters/digits/underscore/hyphen (06-domain-models §4/§8, copied verbatim
# from conversations/memory's AgentKey).
_AGENT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class MediaKind(StrEnum):
    """What a ``MediaJob`` generates (06 §8)."""

    IMAGE = "image"
    VIDEO = "video"


class JobStatus(StrEnum):
    """A ``MediaJob``'s lifecycle (06 §8, INV-MJ2): one-way ``queued ->
    running -> (succeeded | failed)``."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentKey:
    """The requesting agent's slug (from its manifest).

    Deliberately self-contained to this module (12-module-authoring-guide §3):
    each module that needs an agent slug validates its own copy rather than
    depending on a shared kernel type — the ``conversations``/``memory``
    precedent.
    """

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not normalized:
            raise InvalidMediaInput("agent key must not be empty")
        if len(normalized) > _MAX_AGENT_KEY_LEN:
            raise InvalidMediaInput(f"agent key must be at most {_MAX_AGENT_KEY_LEN} characters")
        if not _AGENT_KEY_RE.match(normalized):
            raise InvalidMediaInput(f"invalid agent key: {self.value!r}")
        object.__setattr__(self, "value", normalized)


# Known params keys per kind (06 §8: "أبعاد/مدّة/نموذج" — dimensions for
# image, duration for video, an optional model for either). Any other key is
# rejected by ``GenParams.from_dict`` — the VO validates *shape*, not the
# provider-specific meaning of a field.
_IMAGE_KEYS = frozenset({"width", "height", "model"})
_VIDEO_KEYS = frozenset({"duration_seconds", "model"})


@dataclass(frozen=True, slots=True)
class GenParams:
    """Generation parameters, validated by *shape* against ``kind`` (06 §8).

    Structural-only invariants enforced HERE (INV-MJ3, first half): known keys
    per ``kind``, correct types, and strictly positive values for
    ``width``/``height``/``duration_seconds``. The *configurable* numeric
    ceilings from 07-nfr-slo §4 (``max_image_dim``, ``max_video_seconds``) are
    NOT checked here — ``RequestMedia`` enforces those against an injected
    ``Limits`` (INV-MJ3, second half), exactly like ``files.RegisterUpload``
    enforces its whitelist/size caps outside the ``ContentType``/``FileName``
    value objects.
    """

    kind: MediaKind
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.kind is MediaKind.IMAGE:
            if self.duration_seconds is not None:
                raise InvalidMediaInput("duration_seconds is only valid for video params")
            _require_positive_int(self.width, "width")
            _require_positive_int(self.height, "height")
        else:
            if self.width is not None or self.height is not None:
                raise InvalidMediaInput("width/height are only valid for image params")
            _require_positive_int(self.duration_seconds, "duration_seconds")

        if self.model is not None:
            trimmed = self.model.strip()
            if not trimmed:
                raise InvalidMediaInput("model must not be blank")
            object.__setattr__(self, "model", trimmed)

    @classmethod
    def from_dict(cls, kind: MediaKind, raw: Mapping[str, Any]) -> GenParams:
        """Build a validated ``GenParams`` from a raw, wire-shaped mapping
        (e.g. an API request body or a hydrated ``params jsonb`` column).

        Rejects any key not in the known set for ``kind``; all other
        structural checks happen in ``__post_init__`` so there is exactly one
        place that decides whether a ``GenParams`` is well-formed, regardless
        of whether it was built via this factory or the constructor directly.
        """
        known = _IMAGE_KEYS if kind is MediaKind.IMAGE else _VIDEO_KEYS
        unknown = sorted(set(raw) - known)
        if unknown:
            raise InvalidMediaInput(f"unknown params key(s) for {kind.value}: {unknown}")
        return cls(
            kind=kind,
            width=raw.get("width"),
            height=raw.get("height"),
            duration_seconds=raw.get("duration_seconds"),
            model=raw.get("model"),
        )

    def as_dict(self) -> dict[str, Any]:
        """The wire/storage representation: only the keys actually set (never
        ``None``) — used both for the ``media.media_jobs.params jsonb`` column
        and the ``MediaRequested`` event's ``params`` field (matches the
        published wire schema's ``"params": {"type": "object"}``)."""
        data: dict[str, Any] = {}
        if self.width is not None:
            data["width"] = self.width
        if self.height is not None:
            data["height"] = self.height
        if self.duration_seconds is not None:
            data["duration_seconds"] = self.duration_seconds
        if self.model is not None:
            data["model"] = self.model
        return data


def _require_positive_int(value: int | None, field: str) -> None:
    """INV-MJ3 (shape half): ``field`` must be present and a strictly
    positive ``int`` — never ``None``, never a ``bool`` (a ``bool`` is a
    stdlib ``int`` subclass in Python, so it is rejected explicitly rather
    than silently accepted as ``0``/``1``), never a float/str smuggled in
    through an untyped raw dict."""
    if value is None:
        raise InvalidMediaInput(f"{field} is required")
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidMediaInput(f"{field} must be an integer")
    if value <= 0:
        raise InvalidMediaInput(f"{field} must be a positive integer")
