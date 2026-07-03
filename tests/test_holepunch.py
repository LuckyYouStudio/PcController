import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import holepunch


class HolePunchTest(unittest.TestCase):
    def test_punch_succeeds_on_loopback(self):
        sa = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sa.bind(("127.0.0.1", 0))
        sb = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sb.bind(("127.0.0.1", 0))
        self.addCleanup(sa.close)
        self.addCleanup(sb.close)

        results = {}
        ta = threading.Thread(
            target=lambda: results.__setitem__(
                "a", holepunch.punch(sa, sb.getsockname(), timeout=3)))
        tb = threading.Thread(
            target=lambda: results.__setitem__(
                "b", holepunch.punch(sb, sa.getsockname(), timeout=3)))
        ta.start(); tb.start()
        ta.join(5); tb.join(5)

        self.assertTrue(results.get("a"))
        self.assertTrue(results.get("b"))

    def test_punch_fails_to_dead_peer(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        self.addCleanup(s.close)
        # nobody is listening on this port -> no punch packet ever comes back
        dead = ("127.0.0.1", 9)
        self.assertFalse(holepunch.punch(s, dead, timeout=1))


if __name__ == "__main__":
    unittest.main()
