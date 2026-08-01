"""Shared primitive type aliases for the framework kernel (02-port-contracts §0).

These aliases keep signatures readable and provider-neutral. ``Uuid`` is a
text UUIDv7 (DD-02); ``Json`` is an already-decoded JSON object.
"""

from __future__ import annotations

from typing import Any

Uuid = str
Json = dict[str, Any]
