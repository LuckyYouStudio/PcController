"""Peer side of the signaling handshake.

``rendezvous`` registers with the signaling server over TCP and fires UDP STUN
probes *from the P2P socket* (so the server observes the exact public endpoint
that will be hole-punched), then returns the peer's public endpoint. The caller
then hole-punches to it and builds a UDPTransport; on failure it falls back to
the relay.
"""

import json
import socket
import threading

from . import protocol as P

STUN_TAG = 0x53


def rendezvous(server_host, server_port, role, session_id, udp_sock, timeout=15):
    """Return the peer's public ``(ip, port)``, or raise ConnectionError.

    ``udp_sock`` is the bound UDP socket that will be used for hole punching.
    """
    tcp = socket.create_connection((server_host, server_port), timeout=timeout)
    tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    P.send_msg(tcp, P.MSG_SIGNAL_HELLO,
               json.dumps({"role": role, "session": str(session_id)}).encode("utf-8"))

    probe = bytes([STUN_TAG]) + json.dumps(
        {"role": role, "session": str(session_id)}).encode("utf-8")
    stop = threading.Event()

    def stun_loop():
        while not stop.is_set():
            try:
                udp_sock.sendto(probe, (server_host, server_port))
            except OSError:
                pass
            stop.wait(0.3)

    threading.Thread(target=stun_loop, daemon=True).start()
    tcp.settimeout(timeout)
    try:
        msg_type, payload = P.recv_msg(tcp)
    except (OSError, ConnectionError) as exc:
        raise ConnectionError(f"信令超时/失败: {exc}")
    finally:
        stop.set()
        try:
            tcp.close()
        except OSError:
            pass

    if msg_type != P.MSG_SIGNAL_PEER:
        raise ConnectionError("信令握手异常")
    info = json.loads(payload.decode("utf-8"))
    ep = info["endpoint"]
    return (ep[0], int(ep[1]))
