import os
import socket
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_control.udptransport as U


class DeadPeerTest(unittest.TestCase):
    def test_dead_peer_unblocks_recv(self):
        """A silent peer must be detected so recv_stream raises (session ends),
        which is what lets the agent re-register after a controller disappears."""
        old = U.DEAD_TIMEOUT
        U.DEAD_TIMEOUT = 1.0
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        # a peer address with nothing listening -> never responds
        dead = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dead.bind(("127.0.0.1", 0))
        dead_addr = dead.getsockname()
        dead.close()

        transport = U.UDPTransport(sock, dead_addr)
        cs = transport.control_socket()
        result = {}

        def reader():
            try:
                cs.recv(10)
                result["v"] = "returned"
            except ConnectionError:
                result["v"] = "raised"
            except Exception as exc:
                result["v"] = f"other:{exc!r}"

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(5)   # dead detection (~1s) must fire well within this
        try:
            self.assertEqual(result.get("v"), "raised")
        finally:
            U.DEAD_TIMEOUT = old
            transport.close()


if __name__ == "__main__":
    unittest.main()
