#!/usr/bin/env python3
"""被控端 / Agent — run this ON THE MACHINE YOU WANT TO CONTROL.

Cross-platform: works on Windows, macOS and Linux.

    python agent.py --password yoursecret
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from remote_control.server import main

if __name__ == "__main__":
    main()
