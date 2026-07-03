import os
import random
import socket
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import protocol as P
from remote_control.udptransport import UDPTransport


class LossySocket:
    """Wrap a UDP socket and drop a fraction of outgoing packets."""
    def __init__(self, sock, loss):
        self._s = sock
        self._loss = loss

    def sendto(self, data, addr):
        if random.random() < self._loss:
            return len(data)     # pretend it went out, but drop it
        return self._s.sendto(data, addr)

    def recvfrom(self, n):
        return self._s.recvfrom(n)

    def settimeout(self, t):
        self._s.settimeout(t)

    def close(self):
        self._s.close()


class UDPTransportTest(unittest.TestCase):
    def _pair(self, loss=0.0):
        sa = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sa.bind(("127.0.0.1", 0))
        sb = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sb.bind(("127.0.0.1", 0))
        ta = UDPTransport(LossySocket(sa, loss), sb.getsockname())
        tb = UDPTransport(LossySocket(sb, loss), sa.getsockname())
        self.addCleanup(ta.close)
        self.addCleanup(tb.close)
        return ta, tb

    def test_reliable_control_in_order(self):
        ta, tb = self._pair(loss=0.0)
        sa, sb = ta.control_socket(), tb.control_socket()
        msgs = [(P.MSG_AUTH, b"pw"), (P.MSG_SCREEN_INFO, b"\x00\x01\x02\x03"),
                (P.MSG_INPUT, b'{"t":"move"}')]
        for mt, pl in msgs:
            P.send_msg(sa, mt, pl)
        got = [P.recv_msg(sb) for _ in msgs]
        self.assertEqual(got, msgs)

    def test_reliable_control_survives_20pct_loss(self):
        ta, tb = self._pair(loss=0.2)
        sa, sb = ta.control_socket(), tb.control_socket()
        msgs = [(P.MSG_AUTH, b"password123"),
                (P.MSG_INPUT, b'{"t":"down","button":"left"}' * 20),
                (P.MSG_CLIPBOARD, b"clip" * 1500)]   # ~6 KB, multi-packet
        for mt, pl in msgs:
            P.send_msg(sa, mt, pl)
        got = [P.recv_msg(sb) for _ in msgs]
        self.assertEqual(got, msgs)   # retransmit must recover every byte, in order

    def test_frame_reassembly(self):
        ta, tb = self._pair(loss=0.0)
        blob = bytes(range(256)) * 40   # ~10 KB -> multiple fragments
        ta.send_frame(blob)
        got = tb.recv_frame(timeout=2)
        self.assertEqual(got, blob)

    def test_frames_survive_loss_dropping_incomplete(self):
        ta, tb = self._pair(loss=0.1)
        blob = b"payload" * 300         # ~2 KB -> ~2 fragments
        for i in range(20):
            ta.send_frame(i.to_bytes(4, "big") + blob)
            time.sleep(0.01)
        received = []
        deadline = time.time() + 3
        while time.time() < deadline and len(received) < 20:
            f = tb.recv_frame(timeout=0.3)
            if f is not None:
                received.append(f)
        self.assertGreaterEqual(len(received), 5)   # plenty get through
        for f in received:                          # and every delivered frame is intact
            self.assertEqual(f[4:], blob)


if __name__ == "__main__":
    unittest.main()
