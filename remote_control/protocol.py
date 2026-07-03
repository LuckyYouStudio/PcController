"""Length-prefixed binary message framing over a TCP socket.

Wire format for every message:

    +--------+------------------+------------------+
    | type   | length (uint32)  | payload (length) |
    | 1 byte | 4 bytes, big-end | length bytes     |
    +--------+------------------+------------------+

The helpers here are deliberately socket-library only so they can be unit
tested with ``socket.socketpair()`` without any of the heavier dependencies.
"""

import struct

# --- message types ---------------------------------------------------------
MSG_AUTH = 0x01         # client -> server: payload = utf-8 password
MSG_AUTH_OK = 0x02      # server -> client: no payload
MSG_AUTH_FAIL = 0x03    # server -> client: no payload
MSG_SCREEN_INFO = 0x04  # server -> client: payload = SCREEN_INFO struct (w, h)
MSG_FRAME = 0x05        # server -> client: payload = JPEG bytes
MSG_INPUT = 0x06        # client -> server: payload = utf-8 JSON input event
MSG_CLIPBOARD = 0x07    # both directions: payload = utf-8 clipboard text

# relay handshake (endpoint <-> relay, before the session is bridged)
MSG_RELAY_HELLO = 0x10  # endpoint -> relay: payload = JSON {role, session}
MSG_RELAY_OK = 0x11     # relay -> controller: paired, session bridged
MSG_RELAY_ERROR = 0x12  # relay -> controller: payload = utf-8 reason

_HEADER = struct.Struct(">BI")
SCREEN_INFO = struct.Struct(">II")  # width, height


def send_msg(sock, msg_type, payload=b""):
    """Send one framed message. ``payload`` must be ``bytes``."""
    sock.sendall(_HEADER.pack(msg_type, len(payload)) + payload)


def recv_exact(sock, n):
    """Receive exactly ``n`` bytes or raise ConnectionError on early close."""
    chunks = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed while reading")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_msg(sock):
    """Receive one framed message. Returns ``(msg_type, payload_bytes)``."""
    header = recv_exact(sock, _HEADER.size)
    msg_type, length = _HEADER.unpack(header)
    payload = recv_exact(sock, length) if length else b""
    return msg_type, payload
