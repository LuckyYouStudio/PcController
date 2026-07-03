import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import signaling, signalingclient


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SignalingTest(unittest.TestCase):
    def _start_server(self):
        port = _free_port()
        threading.Thread(target=signaling.SignalingServer().serve,
                         args=("127.0.0.1", port), daemon=True).start()
        time.sleep(0.3)
        return port

    def test_endpoint_exchange(self):
        port = self._start_server()
        sid = "700700700"

        ua = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ua.bind(("127.0.0.1", 0))
        uc = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        uc.bind(("127.0.0.1", 0))
        self.addCleanup(ua.close)
        self.addCleanup(uc.close)

        results = {}

        def peer(role, usock, key):
            try:
                results[key] = signalingclient.rendezvous(
                    "127.0.0.1", port, role, sid, usock, timeout=8)
            except Exception as exc:
                results[key] = exc

        ta = threading.Thread(target=peer, args=("agent", ua, "a"))
        tc = threading.Thread(target=peer, args=("controller", uc, "c"))
        ta.start(); tc.start()
        ta.join(10); tc.join(10)

        # each side must learn the OTHER's public UDP endpoint (its loopback port)
        self.assertEqual(results.get("a"), ("127.0.0.1", uc.getsockname()[1]))
        self.assertEqual(results.get("c"), ("127.0.0.1", ua.getsockname()[1]))

    def test_controller_alone_times_out(self):
        port = self._start_server()
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.bind(("127.0.0.1", 0))
        self.addCleanup(u.close)
        with self.assertRaises(ConnectionError):
            signalingclient.rendezvous("127.0.0.1", port, "controller",
                                       "no-agent", u, timeout=1)


if __name__ == "__main__":
    unittest.main()
