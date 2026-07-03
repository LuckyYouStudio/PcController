import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control.server import _enable_keepalive


class KeepaliveTest(unittest.TestCase):
    """A dead / half-open controller must not wedge the agent forever; the
    accepted socket gets TCP keepalive so the OS surfaces the dead peer and the
    stuck session ends, freeing the next connection."""

    def test_enables_keepalive(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        _enable_keepalive(s)
        self.assertTrue(s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE))

    def test_applies_tuning_when_supported(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(s.close)
        _enable_keepalive(s, idle=7, intvl=3, cnt=4)
        intvl = getattr(socket, "TCP_KEEPINTVL", None)
        if intvl is not None:
            self.assertEqual(s.getsockopt(socket.IPPROTO_TCP, intvl), 3)
        cnt = getattr(socket, "TCP_KEEPCNT", None)
        if cnt is not None:
            self.assertEqual(s.getsockopt(socket.IPPROTO_TCP, cnt), 4)

    def test_no_raise_on_unsupported_socket(self):
        # UDP: keepalive options don't apply — must be a no-op, never raise.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(s.close)
        _enable_keepalive(s)


if __name__ == "__main__":
    unittest.main()
