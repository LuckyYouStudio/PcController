import io
import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from remote_control import protocol as P
from remote_control import relay, relayclient, server
from remote_control.config import ServerConfig


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class RelayTest(unittest.TestCase):
    def _start_relay(self):
        port = _free_port()
        r = relay.Relay()
        threading.Thread(target=r.serve, args=("127.0.0.1", port), daemon=True).start()
        time.sleep(0.3)
        return port

    def test_full_session_through_relay(self):
        rport = self._start_relay()
        sid = "123456789"
        cfg = ServerConfig(password="pw", input_enabled=False, fps=8,
                           scale=0.4, clipboard=False)

        def agent():
            try:
                sock = relayclient.connect_agent("127.0.0.1", rport, sid)
                server.handle_connection(sock, cfg)
            except Exception:
                pass

        threading.Thread(target=agent, daemon=True).start()
        time.sleep(0.5)   # let the agent register with the relay

        sock = relayclient.connect_controller("127.0.0.1", rport, sid, timeout=10)
        try:
            P.send_msg(sock, P.MSG_AUTH, b"pw")
            mt, _ = P.recv_msg(sock)
            self.assertEqual(mt, P.MSG_AUTH_OK)         # auth flowed through relay
            mt, pl = P.recv_msg(sock)
            self.assertEqual(mt, P.MSG_SCREEN_INFO)
            w, h = P.SCREEN_INFO.unpack(pl)
            self.assertGreater(w, 0)
            mt, pl = P.recv_msg(sock)
            self.assertEqual(mt, P.MSG_FRAME)           # a real frame relayed
            img = Image.open(io.BytesIO(pl)); img.load()
            self.assertEqual(img.format, "JPEG")
        finally:
            sock.close()

    def test_controller_gets_error_when_peer_offline(self):
        rport = self._start_relay()
        with self.assertRaises(ConnectionError):
            relayclient.connect_controller("127.0.0.1", rport, "000000000", timeout=5)


if __name__ == "__main__":
    unittest.main()
