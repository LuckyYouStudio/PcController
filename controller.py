#!/usr/bin/env python3
"""控制端 / Controller — run this ON THE MACHINE YOU CONTROL FROM.

Cross-platform: works on Windows, macOS and Linux.

    python controller.py --host 192.168.1.50 --password yoursecret
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from remote_control.client import main

if __name__ == "__main__":
    main()
