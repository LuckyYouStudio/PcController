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
from . import discovery
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


def serve(cfg, sock=None, on_status=None):
    """Serve one controller at a time forever.

    ``sock`` may be an already-bound/listening socket (the GUI path binds it
    up-front so port-in-use errors surface before the status window opens).
    ``on_status`` is an optional callback(str) used to mirror progress to a GUI.
    """
    def notify(msg):
        print(msg)
        if on_status is not None:
            try:
                on_status(msg)
            except Exception:
                pass

    srv = sock
    if srv is None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((cfg.host, cfg.port))
        srv.listen(1)

    # advertise this machine on the LAN so controllers can find it by name
    disc_stop = threading.Event()
    threading.Thread(
        target=discovery.run_responder, args=(cfg.port, disc_stop), daemon=True
    ).start()

    print(f"[server] discoverable as '{socket.gethostname()}' on the LAN")
    notify(f"监听中 {cfg.host}:{cfg.port} — 等待控制端连接…")
    try:
        while True:
            conn, addr = srv.accept()
            notify(f"控制端已连接：{addr[0]}:{addr[1]}")
            try:
                handle_connection(conn, cfg)
            except Exception as exc:
                print(f"[server] session error: {exc}")
            finally:
                conn.close()
                notify("控制端已断开 — 继续等待…")
    except KeyboardInterrupt:
        print("\n[server] shutting down")
    except OSError:
        pass  # listening socket closed on shutdown
    finally:
        disc_stop.set()
        srv.close()


def _local_ip():
    """Best-effort LAN IP of this machine (for display in the status window)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _gui_session(conn, cfg, inject_q):
    """Serve one controller for the GUI path (runs on a background thread).

    Screen capture and networking happen here on the background thread (safe),
    but received input events are pushed onto ``inject_q`` so pynput injection
    can run on the MAIN thread. On macOS, creating/using pynput controllers on a
    background thread while a Tk run loop is active on the main thread crashes
    the process (SIGTRAP), so all pynput work is marshalled to the main thread.
    """
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    msg_type, payload = P.recv_msg(conn)
    if msg_type != P.MSG_AUTH or payload.decode("utf-8", "ignore") != cfg.password:
        P.send_msg(conn, P.MSG_AUTH_FAIL)
        return
    P.send_msg(conn, P.MSG_AUTH_OK)

    monitor = _get_monitor(cfg.monitor)
    width, height = monitor["width"], monitor["height"]
    P.send_msg(conn, P.MSG_SCREEN_INFO, P.SCREEN_INFO.pack(width, height))

    if cfg.input_enabled and inject_q is not None:
        # tell the main thread how to map normalized coords for this monitor
        inject_q.put(("config", (width, height, monitor["left"], monitor["top"])))

    stop = threading.Event()

    def reader():
        try:
            while not stop.is_set():
                mt, pl = P.recv_msg(conn)
                if mt == P.MSG_INPUT and cfg.input_enabled and inject_q is not None:
                    try:
                        inject_q.put(("input", json.loads(pl.decode("utf-8"))))
                    except Exception as exc:
                        print(f"[server] input decode error: {exc}")
        except (ConnectionError, OSError):
            pass
        finally:
            stop.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        _capture_loop(conn, cfg, monitor, stop)  # mss on a bg thread is fine
    finally:
        stop.set()
        t.join(timeout=1.0)


def run_agent_gui(base_args):
    """Double-click entry point: a single Tk window that first collects
    port/password, then turns into a live status panel while the server runs on
    a background thread. Everything that touches pynput stays on the main thread
    so the .app neither freezes (blocked main thread) nor crashes (pynput off
    the main thread under a Tk run loop).
    """
    import queue
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("被控端 / Agent")
    root.resizable(False, False)
    body = tk.Frame(root, padx=22, pady=18)
    body.pack()

    ui_q = queue.Queue()      # status strings -> label (from bg thread)
    inject_q = queue.Queue()  # ("config"|"input"|"release", data) -> main thread
    state = {"srv": None, "handler": None}

    def show_config():
        for w in body.winfo_children():
            w.destroy()
        tk.Label(body, text="监听端口 (port):").grid(row=0, column=0, sticky="e", pady=5)
        port_var = tk.StringVar(value=str(base_args.port))
        tk.Entry(body, textvariable=port_var, width=22).grid(row=0, column=1, pady=5)
        tk.Label(body, text="设置密码 (password):").grid(row=1, column=0, sticky="e", pady=5)
        pw_var = tk.StringVar(value=base_args.password)
        tk.Entry(body, textvariable=pw_var, width=22).grid(row=1, column=1, pady=5)
        tk.Label(body, text="控制端连接时填:本机 IP + 下面的端口和密码",
                 fg="#666").grid(row=2, column=0, columnspan=2, pady=(6, 0))
        btns = tk.Frame(body)
        btns.grid(row=3, column=0, columnspan=2, pady=(12, 0))

        def on_start():
            try:
                port = int(port_var.get().strip() or DEFAULT_PORT)
            except ValueError:
                port = DEFAULT_PORT
            cfg = ServerConfig(
                host=base_args.host, port=port, password=pw_var.get(),
                fps=base_args.fps, quality=base_args.quality,
                scale=base_args.scale, monitor=base_args.monitor,
            )
            start_serving(cfg)

        tk.Button(btns, text="启动", width=9, command=on_start).pack(side="left", padx=8)
        tk.Button(btns, text="取消", width=9, command=root.destroy).pack(side="left", padx=8)
        root.bind("<Return>", lambda e: on_start())

    def start_serving(cfg):
        # Bind up-front so "port already in use" is a clear dialog, not a silent death.
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((cfg.host, cfg.port))
            srv.listen(1)
        except OSError as exc:
            srv.close()
            messagebox.showerror("启动失败",
                                 f"无法监听端口 {cfg.port}：\n{exc}\n\n"
                                 "该端口可能已被占用,请换一个端口重试。")
            return
        state["srv"] = srv
        root.unbind("<Return>")
        # pynput controllers MUST be created on the main thread (this thread)
        state["handler"] = InputHandler(1, 1) if cfg.input_enabled else None

        # advertise on the LAN so controllers can find this agent by name
        disc_stop = threading.Event()
        state["disc_stop"] = disc_stop
        threading.Thread(
            target=discovery.run_responder, args=(cfg.port, disc_stop), daemon=True
        ).start()

        def bg():
            def notify(m):
                print(m)
                ui_q.put(m)
            notify(f"监听中 {cfg.host}:{cfg.port} — 等待控制端连接…")
            try:
                while True:
                    c, addr = srv.accept()
                    notify(f"控制端已连接：{addr[0]}:{addr[1]}")
                    try:
                        _gui_session(c, cfg, inject_q)
                    except Exception as exc:
                        print(f"[server] session error: {exc}")
                    finally:
                        c.close()
                        inject_q.put(("release", None))
                        notify("控制端已断开 — 继续等待…")
            except OSError:
                pass  # listening socket closed on shutdown

        threading.Thread(target=bg, daemon=True).start()
        show_status(cfg)

    def show_status(cfg):
        for w in body.winfo_children():
            w.destroy()
        tk.Label(body, text="✅ 被控端正在运行", font=("", 15, "bold")).pack()
        tk.Label(body, text=f"本机 IP: {_local_ip()}", fg="#0a7a3a",
                 font=("", 13, "bold")).pack(pady=(10, 0))
        tk.Label(body, text=f"端口: {cfg.port}      密码: {cfg.password}").pack(pady=(2, 0))
        status_var = tk.StringVar(value="启动中…")
        tk.Label(body, textvariable=status_var, fg="#666", wraplength=300).pack(pady=(12, 0))
        tk.Label(body, text="在控制端填上面的 IP / 端口 / 密码即可连接。\n关闭此窗口即停止服务。",
                 fg="#999").pack(pady=(12, 0))

        def pump():
            try:
                while True:
                    status_var.set(ui_q.get_nowait())
            except queue.Empty:
                pass
            handler = state["handler"]
            if handler is not None:
                try:
                    while True:
                        kind, data = inject_q.get_nowait()
                        if kind == "config":
                            handler.width, handler.height, handler.left, handler.top = data
                        elif kind == "input":
                            try:
                                handler.handle(data)  # pynput on the MAIN thread
                            except Exception as exc:
                                print(f"[server] input error: {exc}")
                        elif kind == "release":
                            handler.release_all()
                except queue.Empty:
                    pass
            root.after(15, pump)

        pump()

    def on_close():
        disc_stop = state.get("disc_stop")
        if disc_stop is not None:
            disc_stop.set()  # stop advertising on the LAN
        srv = state["srv"]
        if srv is not None:
            try:
                srv.close()  # unblock accept() so the bg thread ends
            except Exception:
                pass
        handler = state["handler"]
        if handler is not None:
            try:
                handler.release_all()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    show_config()
    root.eval("tk::PlaceWindow . center")
    root.mainloop()


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


def main(argv=None):
    args = parse_args(argv)
    # Launched with no arguments (e.g. double-clicked .app): run the GUI, which
    # keeps a Tk event loop on the main thread (so the app never appears frozen)
    # and serves on a background thread.
    if argv is None and len(sys.argv) == 1:
        run_agent_gui(args)
        return
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
