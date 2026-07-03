#!/usr/bin/env python3
"""Unified PcController — control AND be controlled from one program.

Double-click / no args:  opens the home window. This machine becomes
controllable (LAN + optional remote via relay) and you can also connect out to
control others.

With --host / --relay / --control:  opens the controller directly.

    python pccontroller.py                       # home (agent + launcher)
    python pccontroller.py --control             # just the connect picker
    python pccontroller.py --host 192.168.1.50 --password secret
    python pccontroller.py --relay relay.example.com --id 123456789 --password secret
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _is_controller(argv):
    return any(flag in argv for flag in ("--control", "--host", "--relay", "--id"))


def main():
    argv = sys.argv[1:]
    if _is_controller(argv):
        from remote_control.client import main as client_main
        client_main([a for a in argv if a != "--control"])
    else:
        from remote_control.server import run_agent_gui, parse_args
        run_agent_gui(parse_args([]))


if __name__ == "__main__":
    main()
