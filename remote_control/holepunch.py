"""UDP hole punching.

Given our own bound UDP socket and the peer's public ``(ip, port)`` (learned
from the signaling server), both sides fire small packets at each other. The
first packet each NAT sees going *out* opens a mapping, so the peer's packets
start getting *in*. When we receive the peer's punch packet the path is open in
both directions and the same socket can be handed to ``UDPTransport``.

Success isn't guaranteed — symmetric NATs may map a different external port per
destination, defeating this — so the caller falls back to the TCP relay when
``punch`` returns False.
"""

import socket
import time

from .udptransport import TAG_PUNCH

_PUNCH = bytes([TAG_PUNCH])


def punch(sock, peer_addr, timeout=5.0, interval=0.1):
    """Open a bidirectional UDP path to ``peer_addr``. Returns True on success.

    On success the socket has (probably) buffered punch packets from the peer;
    ``UDPTransport`` ignores TAG_PUNCH so that's harmless.
    """
    deadline = time.time() + timeout
    sock.settimeout(interval)
    while time.time() < deadline:
        try:
            sock.sendto(_PUNCH, peer_addr)
        except OSError:
            pass
        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break
        if data and data[0] == TAG_PUNCH:
            # confirmed inbound; spray a few more so the peer also confirms
            for _ in range(4):
                try:
                    sock.sendto(_PUNCH, peer_addr)
                except OSError:
                    pass
            return True
    return False
