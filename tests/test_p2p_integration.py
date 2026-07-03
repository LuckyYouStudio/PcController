"""End-to-end P2P: signaling server + agent (serve_via_p2p) + controller
(p2p.establish), running the real protocol over the hole-punched link.
On loopback the punch always succeeds, so this exercises the direct P2P path."""
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
from remote_control import signaling, server, p2p
from remote_control.config import ServerConfig


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class P2PIntegrationTest(unittest.TestCase):
    def test_full_session_over_p2p(self):
        port = _free_port()
        threading.Thread(target=signaling.SignalingServer().serve,
                         args=("127.0.0.1", port), daemon=True).start()
        time.sleep(0.3)

        sid = "808080808"
        cfg = ServerConfig(password="pw", input_enabled=False, fps=8,
                           scale=0.4, clipboard=False)
        stop = threading.Event()
        threading.Thread(target=server.serve_via_p2p,
                         args=(cfg, "127.0.0.1", port, sid),
                         kwargs={"stop_event": stop}, daemon=True).start()
        time.sleep(0.8)   # let the agent register + wait at signaling

        sock = None
        try:
            sock, mode = p2p.establish("127.0.0.1", port, "controller", sid, timeout=10)
            self.assertEqual(mode, "p2p")   # loopback -> direct punch succeeds

            P.send_msg(sock, P.MSG_AUTH, b"pw")
            mt, _ = P.recv_msg(sock)
            self.assertEqual(mt, P.MSG_AUTH_OK)       # auth flowed peer-to-peer
            mt, pl = P.recv_msg(sock)
            self.assertEqual(mt, P.MSG_SCREEN_INFO)
            mt, pl = P.recv_msg(sock)
            self.assertEqual(mt, P.MSG_FRAME)         # a real frame over P2P
            img = Image.open(io.BytesIO(pl)); img.load()
            self.assertEqual(img.format, "JPEG")
        finally:
            stop.set()
            if sock is not None:
                sock.close()


if __name__ == "__main__":
    unittest.main()
