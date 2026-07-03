"""Public relay / rendezvous server (run this on a host with a public IP).

    python -m remote_control.relay --port 50510

It pairs an agent and a controller that present the same session id, then
pipes bytes between them. It never sees plaintext credentials it can use — it
only forwards the already-framed PcController protocol — but it can see the
byte stream, so run it on a host you trust (or add TLS later).

Model: an agent connects and waits (registered by session id). A controller
connects with the same id, gets paired, and the two sockets are bridged. If no
agent is waiting for that id, the controller is told the peer is offline.
"""

import argparse
import json
import socket
import threading

from . import protocol as P
from .config import DEFAULT_RELAY_PORT


def _pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _bridge(agent, controller):
    threading.Thread(target=_pipe, args=(agent, controller), daemon=True).start()
    threading.Thread(target=_pipe, args=(controller, agent), daemon=True).start()


class Relay:
    def __init__(self):
        self._waiting = {}          # session id -> agent socket (registered, idle)
        self._lock = threading.Lock()

    def handle(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            msg_type, payload = P.recv_msg(conn)
            if msg_type != P.MSG_RELAY_HELLO:
                conn.close()
                return
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
                old = self._waiting.get(sid)
                self._waiting[sid] = conn   # newest registration wins
            if old is not None:
                try:
                    old.close()             # drop the stale/dead registration
                except OSError:
                    pass
            print(f"[relay] agent registered: session={sid} from {addr[0]}")
            # thread ends; conn stays alive in _waiting until a controller pairs
            return

        if role == "controller":
            with self._lock:
                agent = self._waiting.pop(sid, None)
            if agent is None:
                try:
                    P.send_msg(conn, P.MSG_RELAY_ERROR,
                               "远程主机不在线 / peer offline".encode("utf-8"))
                except OSError:
                    pass
                conn.close()
                print(f"[relay] controller for session={sid}: no agent online")
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
            print(f"[relay] bridged session={sid}")
            _bridge(agent, conn)
            return

        conn.close()

    def serve(self, host, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(64)
        print(f"[relay] listening on {host}:{port}")
        try:
            while True:
                conn, addr = srv.accept()
                threading.Thread(target=self.handle, args=(conn, addr),
                                 daemon=True).start()
        except KeyboardInterrupt:
            print("\n[relay] shutting down")
        finally:
            srv.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="PcController relay / rendezvous server.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=DEFAULT_RELAY_PORT)
    args = p.parse_args(argv)
    Relay().serve(args.host, args.port)


if __name__ == "__main__":
    main()
