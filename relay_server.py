#!/usr/bin/env python3
"""PcController relay / rendezvous server.

Run this on a host with a public IP (a cheap VPS). Agents and controllers both
connect OUT to it and are paired by session id, so remote control works even
when both machines are behind NAT.

    python relay_server.py --port 50510
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from remote_control.relay import main

if __name__ == "__main__":
    main()
