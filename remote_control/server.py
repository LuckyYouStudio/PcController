"""Mac-side agent: capture the screen and inject remote input.

Run this on the machine you want to control (the Mac)::

    python -m remote_control.server --password secret

It listens for one controller at a time, streams JPEG frames of the primary
monitor, and replays the mouse / keyboard events it receives.
"""

import argparse
import io
import json
import socket
import sys
import threading
import time
import zlib

import mss
from PIL import Image

from . import protocol as P
from .config import (
    ServerConfig,
    DEFAULT_PORT,
    DEFAULT_PASSWORD,
    DEFAULT_FPS,
    DEFAULT_QUALITY,
    DEFAULT_SCALE,
    DEFAULT_MONITOR,
)
from .input_handler import InputHandler


def _get_monitor(index):
    """Return the mss monitor dict for ``index`` (with a safe fallback)."""
    with mss.MSS() as sct:
        monitors = sct.monitors
        if index < 0 or index >= len(monitors):
            index = 1 if len(monitors) > 1 else 0
        return dict(monitors[index])


def _input_loop(conn, handler, stop):
    """Receive input messages until the connection drops."""
    try:
        while not stop.is_set():
            msg_type, payload = P.recv_msg(conn)
            if msg_type == P.MSG_INPUT and handler is not None:
                try:
                    handler.handle(json.loads(payload.decode("utf-8")))
                except Exception as exc:  # never let one bad event kill the loop
                    print(f"[server] input error: {exc}")
    except (ConnectionError, OSError):
        pass
    finally:
        stop.set()


def _capture_loop(conn, cfg, monitor, stop):
    """Grab the screen and stream JPEG frames until ``stop`` is set."""
    interval = 1.0 / max(1, cfg.fps)
    scale = cfg.scale
    last_crc = None
    with mss.MSS() as sct:
        while not stop.is_set():
            start = time.time()
            shot = sct.grab(monitor)
            crc = zlib.crc32(shot.rgb)
            if crc != last_crc:
                last_crc = crc
                img = Image.frombytes("RGB", shot.size, shot.rgb)
                if scale and scale != 1.0:
                    img = img.resize(
                        (max(1, int(img.width * scale)),
                         max(1, int(img.height * scale))),
                        Image.BILINEAR,
                    )
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=cfg.quality)
                try:
                    P.send_msg(conn, P.MSG_FRAME, buf.getvalue())
                except (ConnectionError, OSError):
                    break
            elapsed = time.time() - start
            if elapsed < interval:
                time.sleep(interval - elapsed)
    stop.set()


def handle_connection(conn, cfg):
    """Run a full session on an already-connected socket."""
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    msg_type, payload = P.recv_msg(conn)
    if msg_type != P.MSG_AUTH or payload.decode("utf-8", "ignore") != cfg.password:
        P.send_msg(conn, P.MSG_AUTH_FAIL)
        return
    P.send_msg(conn, P.MSG_AUTH_OK)

    monitor = _get_monitor(cfg.monitor)
    width, height = monitor["width"], monitor["height"]
    P.send_msg(conn, P.MSG_SCREEN_INFO, P.SCREEN_INFO.pack(width, height))

    handler = None
    if cfg.input_enabled:
        handler = InputHandler(width, height, monitor["left"], monitor["top"])

    stop = threading.Event()
    reader = threading.Thread(
        target=_input_loop, args=(conn, handler, stop), daemon=True
    )
    reader.start()
    try:
        _capture_loop(conn, cfg, monitor, stop)
    finally:
        stop.set()
        reader.join(timeout=1.0)
        if handler is not None:
            handler.release_all()


def serve(cfg):
    """Bind, then serve one controller at a time forever."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((cfg.host, cfg.port))
    srv.listen(1)
    print(f"[server] listening on {cfg.host}:{cfg.port} "
          f"(fps={cfg.fps}, quality={cfg.quality}, scale={cfg.scale})")
    print("[server] waiting for a controller to connect ...")
    try:
        while True:
            conn, addr = srv.accept()
            print(f"[server] controller connected from {addr[0]}:{addr[1]}")
            try:
                handle_connection(conn, cfg)
            except Exception as exc:
                print(f"[server] session error: {exc}")
            finally:
                conn.close()
                print("[server] controller disconnected; waiting again ...")
    except KeyboardInterrupt:
        print("\n[server] shutting down")
    finally:
        srv.close()


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Remote-control agent (被控端).")
    p.add_argument("--host", default="0.0.0.0", help="bind address")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--password", default=DEFAULT_PASSWORD)
    p.add_argument("--fps", type=int, default=DEFAULT_FPS)
    p.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                   help="JPEG quality 1-95")
    p.add_argument("--scale", type=float, default=DEFAULT_SCALE,
                   help="downscale factor, e.g. 0.75 for lower bandwidth")
    p.add_argument("--monitor", type=int, default=DEFAULT_MONITOR,
                   help="mss monitor index (1 = primary)")
    return p.parse_args(argv)


def prompt_server_config(port=DEFAULT_PORT, password=DEFAULT_PASSWORD):
    """Small dialog to set port/password when the agent is double-clicked
    (launched with no CLI arguments). Returns a dict or None if cancelled.
    Falls back to defaults if no GUI is available."""
    try:
        import tkinter as tk
    except Exception:
        return {"port": port, "password": password}
    result = {}
    win = tk.Tk()
    win.title("启动被控端 / Agent")
    win.resizable(False, False)
    frm = tk.Frame(win, padx=18, pady=14)
    frm.pack()
    tk.Label(frm, text="监听端口 (port):").grid(row=0, column=0, sticky="e", pady=5)
    port_var = tk.StringVar(value=str(port))
    tk.Entry(frm, textvariable=port_var, width=24).grid(row=0, column=1, pady=5)
    tk.Label(frm, text="设置密码 (password):").grid(row=1, column=0, sticky="e", pady=5)
    pw_var = tk.StringVar(value=password)
    tk.Entry(frm, textvariable=pw_var, width=24).grid(row=1, column=1, pady=5)
    tk.Label(frm, text="控制端连接时填:本机 IP + 上面的端口和密码",
             fg="#666").grid(row=2, column=0, columnspan=2, pady=(6, 0))

    def on_ok():
        try:
            p = int(port_var.get().strip() or DEFAULT_PORT)
        except ValueError:
            p = DEFAULT_PORT
        result.update(port=p, password=pw_var.get())
        win.destroy()

    btns = tk.Frame(frm)
    btns.grid(row=3, column=0, columnspan=2, pady=(12, 0))
    tk.Button(btns, text="启动", width=9, command=on_ok).pack(side="left", padx=8)
    tk.Button(btns, text="取消", width=9, command=win.destroy).pack(side="left", padx=8)
    win.bind("<Return>", lambda e: on_ok())
    win.eval("tk::PlaceWindow . center")
    win.mainloop()
    return result or None


def main(argv=None):
    args = parse_args(argv)
    # launched with no arguments (e.g. double-clicked exe): offer a config dialog
    if argv is None and len(sys.argv) == 1:
        chosen = prompt_server_config(port=args.port, password=args.password)
        if chosen is None:
            return
        args.port = chosen["port"]
        args.password = chosen["password"]
    cfg = ServerConfig(
        host=args.host,
        port=args.port,
        password=args.password,
        fps=args.fps,
        quality=args.quality,
        scale=args.scale,
        monitor=args.monitor,
    )
    serve(cfg)


if __name__ == "__main__":
    main()
