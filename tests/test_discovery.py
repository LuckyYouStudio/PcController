import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import discovery


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DiscoveryTest(unittest.TestCase):
    def test_responder_answers_discover(self):
        disc_port = _free_udp_port()
        tcp_port = 55123
        stop = threading.Event()
        t = threading.Thread(
            target=discovery.run_responder,
            args=(tcp_port, stop),
            kwargs={"name": "TESTHOST", "discovery_port": disc_port},
            daemon=True,
        )
        t.start()
        time.sleep(0.2)
        try:
            found = discovery.discover(timeout=1.0, discovery_port=disc_port,
                                       broadcast_addr="127.0.0.1")
        finally:
            stop.set()

        by_name = {m["name"]: m for m in found}
        self.assertIn("TESTHOST", by_name)
        self.assertEqual(by_name["TESTHOST"]["port"], tcp_port)
        self.assertTrue(by_name["TESTHOST"]["ip"])

    def test_discover_no_agents_returns_empty(self):
        disc_port = _free_udp_port()  # nobody listening here
        found = discovery.discover(timeout=0.4, discovery_port=disc_port,
                                   broadcast_addr="127.0.0.1")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
