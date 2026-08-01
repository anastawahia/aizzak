"""``AgentLifecycle`` — the five-state machine's vocabulary (02 §3.2, FR-15).

Values only, on purpose: 4.1 owns the TYPE (so the registry/loader and every
agent share one vocabulary), while the transition machine itself —
``Created → Initialized → Running → Completed/Failed → Disposed``, one agent
instance per request, ``dispose`` guaranteed even on failure (FR-10/11,
AC-06) — is the 4.2 lifecycle executor's chartered work (09-testing-strategy
§2 places its tests there too). Nothing in 4.1 transitions between these.

``StrEnum``, not the contract's ``(str, Enum)`` spelling: the py312 house
form every module's value objects already use (ruff UP042; members still
compare equal to their strings).
"""

from __future__ import annotations

from enum import StrEnum


class AgentLifecycle(StrEnum):
    """The six lifecycle states of FR-15 (architecture.md Fig 5)."""

    CREATED = "created"
    INITIALIZED = "initialized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DISPOSED = "disposed"
