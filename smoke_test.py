#!/usr/bin/env python3
"""Loopback self-test: run the server and a real client on this one machine.

It proves the whole pipeline works without a second computer:
  * the client's real connect()/handshake code path,
  * live JPEG frame streaming (prints measured FPS), and
  * real pynput mouse injection (saves and restores your cursor position).

    python smoke_test.py

The mouse cursor will jump to the screen centre for a split second and then
return to where it was. Nothing is typed.
"""
import io
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from pynput.mouse import Controller as MouseController

from remote_control import protocol as P
from remote_control import server
from remote_control.client import RemoteClient
from remote_control.config import ServerConfig

PASSWORD = "smoketest"


def start_server():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def run():
        conn, _ = listener.accept()
        try:
            server.handle_connection(conn, ServerConfig(
                password=PASSWORD, fps=20, quality=50, scale=0.6,
                input_enabled=True))
        finally:
            conn.close()
            listener.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def main():
    print("== LAN Remote Control :: loopback smoke test ==")
    port = start_server()
    time.sleep(0.3)

    client = RemoteClient("127.0.0.1", port, PASSWORD)
    client.connect()
    print(f"[1/3] connected + authenticated OK; "
          f"remote screen = {client.remote_w}x{client.remote_h}")

    # --- measure streaming for ~1.5s using the real protocol reader ---------
    frames = 0
    total_bytes = 0
    first_size = None
    deadline = time.time() + 1.5
    while time.time() < deadline:
        mtype, payload = P.recv_msg(client.sock)
        if mtype == P.MSG_FRAME:
            frames += 1
            total_bytes += len(payload)
            if first_size is None:
                img = Image.open(io.BytesIO(payload))
                img.load()
                first_size = img.size
    fps = frames / 1.5
    print(f"[2/3] streamed {frames} frames in 1.5s (~{fps:.1f} fps), "
          f"first frame {first_size}, avg {total_bytes // max(1, frames) // 1024} KB/frame")

    # --- real input injection round-trip (save + restore cursor) ------------
    mouse = MouseController()
    saved = mouse.position
    try:
        P.send_msg(client.sock, P.MSG_INPUT,
                   b'{"t":"move","x":0.5,"y":0.5}')
        time.sleep(0.4)
        now = mouse.position
        # expected centre of the primary monitor
        exp_x = client.remote_w / 2
        exp_y = client.remote_h / 2
        dx = abs(now[0] - exp_x)
        dy = abs(now[1] - exp_y)
        ok = dx < 8 and dy < 8
        print(f"[3/3] injected move to centre -> cursor at {now}, "
              f"expected ~({exp_x:.0f},{exp_y:.0f})  "
              f"[{'PASS' if ok else 'FAIL'}]")
    finally:
        mouse.position = saved  # restore where the user's cursor was
        client.sock.close()

    print("== smoke test done ==")
    return 0 if (frames > 0 and ok) else 1


if __name__ == "__main__":
    sys.exit(main())
