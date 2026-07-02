"""Windows-side controller GUI.

Run this on the Windows PC::

    python -m remote_control.client --host 192.168.1.50 --password secret

It opens a window showing the Mac's screen and forwards your mouse and
keyboard to it. Press the window's close button (or Ctrl+Alt+Q) to quit.
"""

import argparse
import io
import json
import socket
import sys
import threading
import time
import tkinter as tk

from PIL import Image, ImageTk

from . import protocol as P
from . import clientutil as U
from .config import DEFAULT_PORT, DEFAULT_PASSWORD

MOVE_MIN_INTERVAL = 0.010  # throttle mouse-move messages to ~100/s
IS_MAC = sys.platform == "darwin"


class RemoteClient:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password

        self.sock = None
        self.remote_w = 0
        self.remote_h = 0

        self.root = None
        self.canvas = None
        self.image_id = None
        self._photo = None            # keep a reference so Tk doesn't GC it

        self._pending_jpeg = None     # latest raw frame from the reader thread
        self._pending_lock = threading.Lock()
        self._img_size = (0, 0)       # native size of the last decoded frame
        self._last_rect = (0, 0, 0, 0)

        self._running = False
        self._last_move = 0.0
        self._pressed_specials = set()   # ctrl/alt/shift/cmd currently held

    # -- connection ---------------------------------------------------------
    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)

        P.send_msg(self.sock, P.MSG_AUTH, self.password.encode("utf-8"))
        msg_type, _ = P.recv_msg(self.sock)
        if msg_type == P.MSG_AUTH_FAIL:
            raise ConnectionError("authentication failed (wrong password)")
        if msg_type != P.MSG_AUTH_OK:
            raise ConnectionError(f"unexpected auth reply: {msg_type}")

        msg_type, payload = P.recv_msg(self.sock)
        if msg_type != P.MSG_SCREEN_INFO:
            raise ConnectionError("did not receive screen info")
        self.remote_w, self.remote_h = P.SCREEN_INFO.unpack(payload)

    # -- reader thread ------------------------------------------------------
    def _reader(self):
        try:
            while self._running:
                msg_type, payload = P.recv_msg(self.sock)
                if msg_type == P.MSG_FRAME:
                    with self._pending_lock:
                        self._pending_jpeg = payload
        except (ConnectionError, OSError):
            pass
        finally:
            self._running = False

    # -- render loop (GUI thread) ------------------------------------------
    def _render(self):
        if not self._running:
            self._shutdown()
            return
        with self._pending_lock:
            jpeg = self._pending_jpeg
            self._pending_jpeg = None
        if jpeg is not None:
            try:
                img = Image.open(io.BytesIO(jpeg))
                img.load()
                self._img_size = img.size
                cw = max(1, self.canvas.winfo_width())
                ch = max(1, self.canvas.winfo_height())
                rect = U.compute_display_rect(cw, ch, img.width, img.height)
                self._last_rect = rect
                off_x, off_y, disp_w, disp_h = rect
                if (disp_w, disp_h) != img.size:
                    img = img.resize((disp_w, disp_h), Image.BILINEAR)
                self._photo = ImageTk.PhotoImage(img)
                self.canvas.coords(self.image_id, off_x, off_y)
                self.canvas.itemconfig(self.image_id, image=self._photo)
            except Exception as exc:
                print(f"[client] render error: {exc}")
        self.root.after(15, self._render)

    # -- input forwarding ---------------------------------------------------
    def _send_input(self, ev):
        try:
            P.send_msg(self.sock, P.MSG_INPUT, json.dumps(ev).encode("utf-8"))
        except (ConnectionError, OSError):
            self._running = False

    def _norm(self, event):
        return U.to_normalized(event.x, event.y, self._last_rect)

    def _on_move(self, event):
        now = time.time()
        if now - self._last_move < MOVE_MIN_INTERVAL:
            return
        self._last_move = now
        pos = self._norm(event)
        if pos:
            self._send_input({"t": "move", "x": pos[0], "y": pos[1]})

    def _on_button(self, event, button, pressed):
        pos = self._norm(event)
        if pos:
            self._send_input({
                "t": "down" if pressed else "up",
                "x": pos[0], "y": pos[1], "button": button,
            })
        return "break"

    def _on_wheel(self, event):
        steps = U.wheel_steps(getattr(event, "delta", 0),
                              getattr(event, "num", 0), IS_MAC)
        if steps:
            self._send_input({"t": "scroll", "dx": 0, "dy": steps})
        return "break"

    def _on_key(self, event, pressed):
        spec = U.translate_key(event.keysym, event.char)
        # keep our own view of held modifiers so the quit hotkey works the same
        # on every OS (Tk's event.state bits differ across platforms)
        if spec and spec.get("special") in ("ctrl", "alt", "shift", "cmd"):
            if pressed:
                self._pressed_specials.add(spec["special"])
            else:
                self._pressed_specials.discard(spec["special"])
        if pressed and event.keysym in ("q", "Q") and \
                "ctrl" in self._pressed_specials and "alt" in self._pressed_specials:
            self._running = False   # local Ctrl+Alt+Q -> disconnect
            return "break"
        if spec:
            ev = {"t": "key_down" if pressed else "key_up"}
            ev.update(spec)
            self._send_input(ev)
        return "break"

    # -- window lifecycle ---------------------------------------------------
    def _build_window(self):
        self.root = tk.Tk()
        self.root.title(f"Remote Control - {self.host} "
                        f"({self.remote_w}x{self.remote_h})  [Ctrl+Alt+Q to quit]")
        init_w = min(self.remote_w, 1280) or 1280
        init_h = min(self.remote_h, 800) or 800
        self.root.geometry(f"{init_w}x{init_h}")
        self.root.configure(bg="black")

        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0,
                                cursor="none")
        self.canvas.pack(fill="both", expand=True)
        self.image_id = self.canvas.create_image(0, 0, anchor="nw")

        c = self.canvas
        c.bind("<Motion>", self._on_move)
        for b in (1, 2, 3):
            c.bind(f"<B{b}-Motion>", self._on_move)
        c.bind("<ButtonPress-1>", lambda e: self._on_button(e, "left", True))
        c.bind("<ButtonRelease-1>", lambda e: self._on_button(e, "left", False))
        c.bind("<ButtonPress-2>", lambda e: self._on_button(e, "middle", True))
        c.bind("<ButtonRelease-2>", lambda e: self._on_button(e, "middle", False))
        c.bind("<ButtonPress-3>", lambda e: self._on_button(e, "right", True))
        c.bind("<ButtonRelease-3>", lambda e: self._on_button(e, "right", False))
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Button-4>", self._on_wheel)   # wheel up on Linux/X11
        c.bind("<Button-5>", self._on_wheel)   # wheel down on Linux/X11

        self.root.bind("<KeyPress>", lambda e: self._on_key(e, True))
        self.root.bind("<KeyRelease>", lambda e: self._on_key(e, False))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        c.focus_set()

    def _on_close(self):
        self._running = False

    def _shutdown(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        if self.root is not None:
            try:
                self.root.destroy()
            except tk.TclError:
                pass

    def run(self):
        self.connect()
        self._running = True
        threading.Thread(target=self._reader, daemon=True).start()
        self._build_window()
        self.root.after(15, self._render)
        self.root.mainloop()


def prompt_connection(default_host="", default_port=DEFAULT_PORT,
                      default_password=DEFAULT_PASSWORD):
    """Show a small dialog to collect host/port/password. Returns a dict or
    None if the user cancelled. Used when the controller is double-clicked
    (i.e. launched without a --host argument)."""
    result = {}
    win = tk.Tk()
    win.title("连接被控端 / Connect")
    win.resizable(False, False)
    frm = tk.Frame(win, padx=18, pady=14)
    frm.pack()

    tk.Label(frm, text="被控端 IP (host):").grid(row=0, column=0, sticky="e", pady=5)
    host_var = tk.StringVar(value=default_host)
    host_entry = tk.Entry(frm, textvariable=host_var, width=24)
    host_entry.grid(row=0, column=1, pady=5)

    tk.Label(frm, text="端口 (port):").grid(row=1, column=0, sticky="e", pady=5)
    port_var = tk.StringVar(value=str(default_port))
    tk.Entry(frm, textvariable=port_var, width=24).grid(row=1, column=1, pady=5)

    tk.Label(frm, text="密码 (password):").grid(row=2, column=0, sticky="e", pady=5)
    pw_var = tk.StringVar(value=default_password)
    tk.Entry(frm, textvariable=pw_var, width=24, show="*").grid(row=2, column=1, pady=5)

    def on_ok():
        try:
            port = int(port_var.get().strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT
        result.update(host=host_var.get().strip(), port=port,
                      password=pw_var.get())
        win.destroy()

    btns = tk.Frame(frm)
    btns.grid(row=3, column=0, columnspan=2, pady=(12, 0))
    tk.Button(btns, text="连接", width=9, command=on_ok).pack(side="left", padx=8)
    tk.Button(btns, text="取消", width=9, command=win.destroy).pack(side="left", padx=8)
    win.bind("<Return>", lambda e: on_ok())
    host_entry.focus_set()
    win.eval("tk::PlaceWindow . center")
    win.mainloop()

    if not result.get("host"):
        return None
    return result


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Remote-control controller (控制端).")
    p.add_argument("--host", default=None,
                   help="agent IP address on the LAN (omit to get a dialog)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--password", default=DEFAULT_PASSWORD)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    host, port, password = args.host, args.port, args.password

    if host is None:  # double-clicked / no --host: ask interactively
        chosen = prompt_connection(default_port=port)
        if chosen is None:
            return
        host, port, password = chosen["host"], chosen["port"], chosen["password"]

    client = RemoteClient(host, port, password)
    try:
        client.run()
    except ConnectionRefusedError:
        _fatal(f"连接被拒绝 - 被控端 {host}:{port} 上的 agent 启动了吗?\n"
               f"connection refused - is the agent running on {host}:{port}?")
    except Exception as exc:
        _fatal(f"错误 / error: {exc}")


def _fatal(message):
    """Print the error and, if possible, also show it in a popup so a
    double-clicked exe doesn't just flash and vanish."""
    print(f"[client] {message}")
    try:
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Remote Control", message)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    main()
