#!/usr/bin/env python3
"""PcController rendezvous server — run on a host with a public IP (a VPS).

It does P2P signaling (STUN + endpoint exchange) so agents and controllers can
hole-punch a direct connection, and also byte-relays as a fallback for strict
NATs. One server, one port (TCP + UDP).

    python relay_server.py --port 50510      # open this port (TCP + UDP)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from remote_control.signaling import main

if __name__ == "__main__":
    main()
