"""WebSocket smoke test through the Nginx edge (7.1, brief item 6).

`/api/v1/ws` had never passed through a reverse proxy: nginx.conf was 0 bytes
until 7.1, so every WS test to date spoke to the ASGI app directly and the
Upgrade hop was pure assumption. This proves it with a real socket instead of
by inspection.

Deliberately dependency-free (raw socket + a minimal RFC 6455 codec): the
runtime image ships no WebSocket CLIENT library, and adding one to prove a
deployment property would mean the proof and the deployment no longer share
an environment.

What it asserts, in order:
  1. the handshake returns **101 Switching Protocols** -- nginx performed the
     upgrade rather than answering it as an ordinary request (the failure
     this catches is a 400/502, the classic missing-Upgrade-header symptom);
  2. `Connection: Upgrade` comes back on the response;
  3. the app then speaks the protocol: a tokenless socket that sends a
     non-`auth` first message is closed with **1008** (policy violation),
     which is 03 §3.2's own rule. A proxy that upgraded but mangled framing
     would fail here, not at step 1.

7.3 added `--tls`, which runs the SAME three assertions over `wss://` on
:443. Not a separate script and not a separate code path: a WebSocket over
TLS differs from one over TCP by exactly one wrapped socket, and duplicating
the RFC 6455 codec to prove it would mean the two transports were being
tested by two different clients -- the drift shape app-locations.conf exists
to prevent, reproduced in the very thing that verifies it.

Certificate verification is disabled under `--tls` on purpose. The local
certificate is self-signed by construction (deploy/nginx/gen-cert.sh), and
what is under test is that nginx terminates TLS and still performs the
Upgrade -- not that a development certificate chains to a public CA.

Usage: python3 ws_smoke.py [host] [port] [--tls]
"""

from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import sys

_POLICY_VIOLATION = 1008

# RFC 6455 wire constants.
_OPCODE_TEXT = 0x81  # FIN | text
_OPCODE_CLOSE = 0x8
_MASK_BIT = 0x80
_LEN_16BIT = 126  # payload length escape: the next 2 bytes hold the length
_LEN_64BIT = 127  # ... the next 8 bytes
_CLOSE_CODE_BYTES = 2
_HEADER_BYTES = 2
_DEFAULT_PORT = 80
_DEFAULT_TLS_PORT = 443
_ARGV_PORT = 2


def _handshake(sock: socket.socket, host: str, path: str) -> str:
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode())

    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit("FAIL: connection closed during handshake")
        buffer += chunk
    return buffer.split(b"\r\n\r\n", 1)[0].decode(errors="replace")


def _send_text(sock: socket.socket, payload: str) -> None:
    """A client frame MUST be masked (RFC 6455 §5.3); an unmasked one is a
    protocol error the server closes on, which would look like a proxy fault."""
    data = payload.encode()
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
    header = bytes([_OPCODE_TEXT])
    length = len(data)
    if length < _LEN_16BIT:
        header += bytes([_MASK_BIT | length])
    else:
        header += bytes([_MASK_BIT | _LEN_16BIT]) + struct.pack(">H", length)
    sock.sendall(header + mask + masked)


def _read_frame(sock: socket.socket) -> tuple[int, bytes]:
    header = sock.recv(_HEADER_BYTES)
    if len(header) < _HEADER_BYTES:
        raise SystemExit("FAIL: no frame returned")
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == _LEN_16BIT:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == _LEN_64BIT:
        length = struct.unpack(">Q", sock.recv(8))[0]
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            break
        body += chunk
    return opcode, body


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--tls"]
    tls = "--tls" in sys.argv
    host = args[0] if args else "localhost"
    default_port = _DEFAULT_TLS_PORT if tls else _DEFAULT_PORT
    port = int(args[1]) if len(args) > 1 else default_port

    sock: socket.socket = socket.create_connection((host, port), timeout=15)
    if tls:
        # See the module docstring: verification off, because a self-signed
        # development certificate is the input, not the subject.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=host)
        tls_sock: ssl.SSLSocket = sock  # type: ignore[assignment]
        print(f"tls: {tls_sock.version()} {tls_sock.cipher()[0] if tls_sock.cipher() else '?'}")

    try:
        response = _handshake(sock, host, "/api/v1/ws")
        status = response.splitlines()[0]
        print(f"handshake: {status}")
        if "101" not in status:
            print(response)
            raise SystemExit("FAIL: nginx did not upgrade the connection")
        if "upgrade" not in response.lower():
            raise SystemExit("FAIL: no Upgrade header on the response")
        print("  [1] 101 Switching Protocols through nginx  OK")
        print("  [2] Connection: Upgrade present            OK")

        # Not an `auth` message: 03 §3.2 says the socket closes 1008.
        _send_text(sock, json.dumps({"type": "ping"}))
        opcode, body = _read_frame(sock)
        if opcode == _OPCODE_CLOSE and len(body) >= _CLOSE_CODE_BYTES:
            code = struct.unpack(">H", body[:_CLOSE_CODE_BYTES])[0]
            reason = body[_CLOSE_CODE_BYTES:].decode(errors="replace")
            print(f"  [3] close frame {code} {reason!r}")
            if code != _POLICY_VIOLATION:
                raise SystemExit(f"FAIL: expected close {_POLICY_VIOLATION}, got {code}")
            print("  [3] app closed 1008 on a non-auth first message  OK")
        else:
            raise SystemExit(f"FAIL: expected a close frame, got opcode {opcode} body {body!r}")
    finally:
        sock.close()

    scheme = "wss" if tls else "ws"
    print(f"\nPASS: {scheme} /api/v1/ws upgrades and speaks its protocol through nginx")


if __name__ == "__main__":
    main()
