"""The local end of Alertmanager's one receiver -- capacity 7.3.

The owner chose local delivery for now (2026-10-04): an alert has "arrived"
when it is a line in this service's log, which Alloy ships to Loki like any
other container's, so it is readable in Grafana (`{service="alert-sink"}`)
and with `docker compose logs alert-sink`. An external channel (Telegram,
mail) is a second receiver in `alertmanager.yml` the day one is chosen; this
one stays, because it is the record that a notification left Alertmanager at
all.

ONE LINE PER ALERT, not per notification. Alertmanager batches a group into
one POST, and a line per POST would make "did AizzakX arrive" a search inside
a JSON array. Standard library only, so it runs on a stock Python image and
there is nothing to build.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 9095
# A webhook body is a handful of alerts; a megabyte is far past any real one
# and short of anything that could hurt a 64 MB container.
MAX_BODY_BYTES = 1024 * 1024


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _emit(record: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _level(status: str, severity: str) -> str:
    """What the log dashboard filters on: a firing critical reads as an error,
    a firing warning as a warning, and a resolution or the Watchdog as info."""
    if status == "resolved" or severity == "none":
        return "info"
    return "warning" if severity == "warning" else "error"


def lines_for(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The log records for one webhook body -- one per alert in it."""
    records = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        status = alert.get("status", "unknown")
        severity = labels.get("severity", "none")
        records.append(
            {
                "ts": _now(),
                "event": "alert.notification",
                "level": _level(status, severity),
                "status": status,
                "alertname": labels.get("alertname", ""),
                "severity": severity,
                "summary": annotations.get("summary", ""),
                "runbook_url": annotations.get("runbook_url", ""),
                "labels": labels,
                "starts_at": alert.get("startsAt", ""),
                "ends_at": alert.get("endsAt", ""),
                "fingerprint": alert.get("fingerprint", ""),
                "receiver": payload.get("receiver", ""),
            }
        )
    return records


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_error(413 if length > MAX_BODY_BYTES else 400)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            _emit({"ts": _now(), "event": "alert.unreadable_body", "level": "error"})
            self.send_error(400)
            return
        for record in lines_for(payload):
            _emit(record)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # The default access log is one unstructured stderr line per POST and
        # per healthcheck; the records above already say everything it would.
        return


def main() -> None:
    _emit({"ts": _now(), "event": "alert_sink.listening", "level": "info", "port": PORT})
    ThreadingHTTPServer(("0.0.0.0", PORT), _Handler).serve_forever()


if __name__ == "__main__":
    main()
