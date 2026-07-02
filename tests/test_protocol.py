import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import protocol as P


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        # macOS AF_UNIX socketpair defaults to ~8 KB buffers; the ~10 KB frame
        # case would deadlock a single-threaded send-then-recv without this
        for s in (self.a, self.b):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 262144)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def test_round_trip_various_payloads(self):
        cases = [
            (P.MSG_AUTH, b"password123"),
            (P.MSG_AUTH_OK, b""),
            (P.MSG_FRAME, bytes(range(256)) * 40),  # ~10 KB binary
            (P.MSG_INPUT, b'{"t":"move","x":0.5,"y":0.25}'),
        ]
        for msg_type, payload in cases:
            P.send_msg(self.a, msg_type, payload)
            rt, rp = P.recv_msg(self.b)
            self.assertEqual(rt, msg_type)
            self.assertEqual(rp, payload)

    def test_screen_info_struct(self):
        packed = P.SCREEN_INFO.pack(1920, 1080)
        P.send_msg(self.a, P.MSG_SCREEN_INFO, packed)
        rt, rp = P.recv_msg(self.b)
        self.assertEqual(rt, P.MSG_SCREEN_INFO)
        self.assertEqual(P.SCREEN_INFO.unpack(rp), (1920, 1080))

    def test_recv_exact_raises_on_close(self):
        self.a.sendall(b"\x01\x00\x00")  # a partial header
        self.a.close()
        with self.assertRaises(ConnectionError):
            P.recv_msg(self.b)


if __name__ == "__main__":
    unittest.main()
