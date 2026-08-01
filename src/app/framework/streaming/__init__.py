"""Streaming kernel components (5.3): the in-process connection hub shared by
the WebSocket endpoint (API layer) and the notification bridge (Composition
Root), plus the bridge's handler body — see ``hub.py`` / ``notifications.py``
for why these seams live in the framework."""

from app.framework.streaming.hub import ConnectionHub, StreamSession
from app.framework.streaming.notifications import (
    NOTIFY_EVENT_TYPES,
    NotificationHandler,
    make_notification_handler,
)

__all__ = [
    "NOTIFY_EVENT_TYPES",
    "ConnectionHub",
    "NotificationHandler",
    "StreamSession",
    "make_notification_handler",
]
