"""P2P signaling / rendezvous server (run on a host with a public IP).

    python -m remote_control.signaling --port 50510

It does three jobs on one TCP+UDP port:

1. **STUN** — a peer sends a UDP packet from the very socket it will use for
   P2P; the server observes the public ``(ip, port)`` that packet arrived from.
2. **Endpoint exchange** — an agent and a controller that present the same
   session id are paired; once both public UDP endpoints are known, each peer
   is told the *other's* endpoint (over TCP) so they can hole punch directly.
3. **Relay fallback** — a peer that fails to punch (strict NAT) reconnects with
   ``MSG_RELAY_HELLO`` and the server byte-pipes the two like the plain relay,
   so a connection is always possible.

Only signaling is done here; the actual hole punch + transport live on the
peers (``holepunch`` + ``udptransport``).
"""

import argparse
import json
import socket
import threading

from . import protocol as P
from .config import DEFAULT_RELAY_PORT
from .relay import _bridge

STUN_TAG = 0x53   # 'S' — UDP STUN probe: STUN_TAG + JSON {role, session}


class _Peer:
    def __init__(self, conn, role, sid):
        self.conn = conn
        self.role = role
        self.sid = sid
        self.udp = None          # observed public (ip, port)
        self.sent_peer = False


class SignalingServer:
    def __init__(self):
        self._sessions = {}      # sid -> {role: _Peer}
        self._relay_wait = {}    # sid -> agent conn (relay fallback)
        self._lock = threading.Lock()

    # ---- UDP STUN ---------------------------------------------------------
    def _udp_loop(self, udp):
        while True:
            try:
                data, addr = udp.recvfrom(2048)
            except OSError:
                break
            if not data or data[0] != STUN_TAG:
                continue
            try:
                info = json.loads(data[1:].decode("utf-8"))
                sid = str(info["session"])
                role = info["role"]
            except Exception:
                continue
            with self._lock:
                sess = self._sessions.get(sid)
                peer = sess.get(role) if sess else None
                if peer is not None and peer.udp is None:
                    peer.udp = addr
                    print(f"[signaling] STUN {role} session={sid} -> {addr[0]}:{addr[1]}")
            self._maybe_exchange(sid)

    def _maybe_exchange(self, sid):
        to_send = []
        with self._lock:
            sess = self._sessions.get(sid)
            if not sess:
                return
            a = sess.get("agent")
            c = sess.get("controller")
            if not (a and c and a.udp and c.udp):
                return
            for me, other in ((a, c), (c, a)):
                if not me.sent_peer:
                    me.sent_peer = True
                    to_send.append((me.conn, other.udp))
        for conn, ep in to_send:
            try:
                P.send_msg(conn, P.MSG_SIGNAL_PEER,
                           json.dumps({"endpoint": [ep[0], ep[1]]}).encode("utf-8"))
            except OSError:
                pass
        if to_send:
            print(f"[signaling] exchanged endpoints for session={sid}")

    # ---- TCP --------------------------------------------------------------
    def handle_tcp(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            msg_type, payload = P.recv_msg(conn)
        except Exception:
            conn.close()
            return
        if msg_type == P.MSG_RELAY_HELLO:
            self._handle_relay(conn, payload)
            return
        if msg_type != P.MSG_SIGNAL_HELLO:
            conn.close()
            return
        try:
            info = json.loads(payload.decode("utf-8"))
            role = info["role"]
            sid = str(info["session"])
        except Exception:
            conn.close()
            return
        if role not in ("agent", "controller"):
            conn.close()
            return

        peer = _Peer(conn, role, sid)
        with self._lock:
            self._sessions.setdefault(sid, {})[role] = peer
        print(f"[signaling] {role} registered session={sid}")
        self._maybe_exchange(sid)
        # hold the connection open (the peer waits for MSG_SIGNAL_PEER); drain
        # anything it sends and clean up on disconnect
        try:
            while True:
                P.recv_msg(conn)
        except (ConnectionError, OSError):
            pass
        finally:
            with self._lock:
                sess = self._sessions.get(sid)
                if sess and sess.get(role) is peer:
                    del sess[role]
                    if not sess:
                        self._sessions.pop(sid, None)
            conn.close()

    def _handle_relay(self, conn, payload):
        try:
            info = json.loads(payload.decode("utf-8"))
            role = info.get("role")
            sid = str(info.get("session", ""))
        except Exception:
            conn.close()
            return
        if not sid:
            conn.close()
            return
        if role == "agent":
            with self._lock:
                old = self._relay_wait.get(sid)
                self._relay_wait[sid] = conn
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            print(f"[signaling] relay-fallback agent waiting session={sid}")
            return
        with self._lock:
            agent = self._relay_wait.pop(sid, None)
        if agent is None:
            try:
                P.send_msg(conn, P.MSG_RELAY_ERROR,
                           "远程主机不在线 / peer offline".encode("utf-8"))
            except OSError:
                pass
            conn.close()
            return
        try:
            P.send_msg(conn, P.MSG_RELAY_OK)
        except OSError:
            try:
                agent.close()
            except OSError:
                pass
            conn.close()
            return
        print(f"[signaling] relay-fallback bridged session={sid}")
        _bridge(agent, conn)

    def serve(self, host, port):
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp.bind((host, port))
        tcp.listen(64)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((host, port))
        threading.Thread(target=self._udp_loop, args=(udp,), daemon=True).start()
        print(f"[signaling] listening on {host}:{port} (TCP signaling + UDP STUN)")
        try:
            while True:
                conn, addr = tcp.accept()
                threading.Thread(target=self.handle_tcp, args=(conn, addr),
                                 daemon=True).start()
        except KeyboardInterrupt:
            print("\n[signaling] shutting down")
        finally:
            tcp.close()
            udp.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="PcController P2P signaling server.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT)
    args = p.parse_args(argv)
    SignalingServer().serve(args.host, args.port)


if __name__ == "__main__":
    main()
