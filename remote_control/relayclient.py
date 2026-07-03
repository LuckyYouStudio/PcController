"""Endpoint side of the relay handshake.

Both the agent and the controller connect OUT to a public relay and announce a
role + shared session id. The relay pairs an agent with a controller that use
the same session id, then transparently pipes bytes between them so the normal
PcController protocol (auth / frames / input / clipboard) flows unchanged.
"""

import json
import socket

from . import protocol as P


def _open(relay_host, relay_port, role, session_id, timeout):
    sock = socket.create_connection((relay_host, relay_port), timeout=timeout)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    hello = json.dumps({"role": role, "session": str(session_id)}).encode("utf-8")
    P.send_msg(sock, P.MSG_RELAY_HELLO, hello)
    return sock


def connect_agent(relay_host, relay_port, session_id, timeout=15):
    """Register this machine with the relay and wait to be controlled.

    Returns a socket that behaves like a freshly ``accept()``ed connection:
    the caller should hand it to ``server.handle_connection``.
    """
    sock = _open(relay_host, relay_port, "agent", session_id, timeout)
    sock.settimeout(None)   # block indefinitely until a controller pairs
    return sock


def connect_controller(relay_host, relay_port, session_id, timeout=15):
    """Reach a remote agent through the relay. Returns a socket ready for the
    controller protocol, or raises ConnectionError if the peer is offline."""
    sock = _open(relay_host, relay_port, "controller", session_id, timeout)
    sock.settimeout(timeout)
    try:
        msg_type, payload = P.recv_msg(sock)
    except (OSError, ConnectionError) as exc:
        sock.close()
        raise ConnectionError(f"中转连接失败: {exc}")
    if msg_type == P.MSG_RELAY_ERROR:
        sock.close()
        raise ConnectionError(payload.decode("utf-8", "ignore") or "远程主机不在线")
    if msg_type != P.MSG_RELAY_OK:
        sock.close()
        raise ConnectionError("中转握手异常")
    sock.settimeout(None)
    return sock
