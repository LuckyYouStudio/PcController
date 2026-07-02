"""End-to-end test: a real server session over loopback.

This captures the actual desktop (read-only) and verifies the auth handshake,
screen-info message, and at least one decodable JPEG frame. Input injection is
DISABLED so the test never moves the real mouse or types anything.
"""
import io
import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from remote_control import protocol as P
from remote_control import server
from remote_control.config import ServerConfig


class IntegrationTest(unittest.TestCase):
    def _start_server(self, cfg):
        """Listen on an ephemeral port, run one session in a thread."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def accept_and_run():
            conn, _ = listener.accept()
            try:
                server.handle_connection(conn, cfg)
            finally:
                conn.close()
                listener.close()

        t = threading.Thread(target=accept_and_run, daemon=True)
        t.start()
        return port, t

    def test_auth_screeninfo_and_frame(self):
        cfg = ServerConfig(password="pw", fps=10, quality=40, scale=0.5,
                           input_enabled=False)
        port, t = self._start_server(cfg)

        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            # correct password -> AUTH_OK
            P.send_msg(sock, P.MSG_AUTH, b"pw")
            mtype, _ = P.recv_msg(sock)
            self.assertEqual(mtype, P.MSG_AUTH_OK)

            # screen info
            mtype, payload = P.recv_msg(sock)
            self.assertEqual(mtype, P.MSG_SCREEN_INFO)
            w, h = P.SCREEN_INFO.unpack(payload)
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)

            # at least one frame that decodes as a JPEG image
            mtype, payload = P.recv_msg(sock)
            self.assertEqual(mtype, P.MSG_FRAME)
            img = Image.open(io.BytesIO(payload))
            img.load()
            self.assertEqual(img.format, "JPEG")
            self.assertGreater(img.width, 0)
            self.assertGreater(img.height, 0)

            # sending an input event must not crash the (input-disabled) server
            P.send_msg(sock, P.MSG_INPUT,
                       b'{"t":"move","x":0.5,"y":0.5}')
            mtype, payload = P.recv_msg(sock)  # should still get frames
            self.assertEqual(mtype, P.MSG_FRAME)
        finally:
            sock.close()
        t.join(timeout=5)

    def test_wrong_password_is_rejected(self):
        cfg = ServerConfig(password="right", input_enabled=False)
        port, t = self._start_server(cfg)

        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        try:
            P.send_msg(sock, P.MSG_AUTH, b"wrong")
            mtype, _ = P.recv_msg(sock)
            self.assertEqual(mtype, P.MSG_AUTH_FAIL)
        finally:
            sock.close()
        t.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
