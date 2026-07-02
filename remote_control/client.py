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
from . import discovery
from .config import DEFAULT_PORT, DEFAULT_PASSWORD

MOVE_MIN_INTERVAL = 0.010  # throttle mouse-move messages to ~100/s
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")


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
        self._send_lock = threading.Lock()   # GUI + hook threads both send
        self._hook = None                # Windows keyboard hook (if available)
        self._kbd_capture = True         # master toggle for keyboard forwarding
        self._status_var = None

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
        data = json.dumps(ev).encode("utf-8")
        try:
            with self._send_lock:
                P.send_msg(self.sock, P.MSG_INPUT, data)
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
        self.root.title(f"Remote Control - {self.host}:{self.port} "
                        f"({self.remote_w}x{self.remote_h})")
        init_w = min(self.remote_w, 1280) or 1280
        init_h = min(self.remote_h, 800) or 800
        self.root.geometry(f"{init_w}x{init_h + 30}")
        self.root.configure(bg="black")

        # --- top toolbar ---
        bar = tk.Frame(self.root, bg="#222")
        bar.pack(side="top", fill="x")
        self._status_var = tk.StringVar()
        tk.Label(bar, textvariable=self._status_var, bg="#222", fg="#ddd",
                 anchor="w", padx=8).pack(side="left")
        tk.Button(bar, text="发送 Ctrl+Alt+Del",
                  command=self._send_ctrl_alt_del).pack(side="right", padx=4, pady=3)
        self._cap_btn = tk.Button(bar, text="键盘捕获", command=self._toggle_capture)
        self._cap_btn.pack(side="right", padx=4, pady=3)

        # --- remote screen canvas ---
        # mss screen capture does NOT include the remote cursor, so we show the
        # local system cursor - its position maps 1:1 to where clicks land.
        self.canvas = tk.Canvas(self.root, bg="black", highlightthickness=0,
                                cursor="arrow")
        self.canvas.pack(side="top", fill="both", expand=True)
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

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        c.focus_set()

        self._start_keyboard()
        self._update_status()

    # -- keyboard capture ---------------------------------------------------
    def _start_keyboard(self):
        """Use the Windows global hook (full combo capture) if possible,
        otherwise fall back to Tk key bindings."""
        if IS_WIN:
            try:
                from . import winhook
                self._hook = winhook.KeyboardHook(
                    on_event=self._forward_key,
                    enabled_getter=lambda: self._kbd_capture and self._running,
                    on_disconnect=self._request_stop)
                self._hook.target_hwnd = self.root.winfo_id()
                if self._hook.start():
                    return   # hook is live; Tk key bindings not needed
                self._hook = None
            except Exception as exc:
                print(f"[client] keyboard hook unavailable: {exc}")
                self._hook = None
        # fallback: Tk bindings (cannot suppress OS-global hotkeys)
        self._cap_btn.pack_forget()
        self.root.bind("<KeyPress>", lambda e: self._on_key(e, True))
        self.root.bind("<KeyRelease>", lambda e: self._on_key(e, False))

    def _forward_key(self, spec, pressed):
        ev = {"t": "key_down" if pressed else "key_up"}
        ev.update(spec)
        self._send_input(ev)

    def _toggle_capture(self):
        self._kbd_capture = not self._kbd_capture
        self._update_status()

    def _send_ctrl_alt_del(self):
        for spec in ({"special": "ctrl"}, {"special": "alt"}, {"special": "delete"}):
            self._forward_key(spec, True)
        for spec in ({"special": "delete"}, {"special": "alt"}, {"special": "ctrl"}):
            self._forward_key(spec, False)

    def _update_status(self):
        if self._status_var is None:
            return
        if self._hook is not None:
            state = "开 (组合键→远程)" if self._kbd_capture else "关 (本机)"
            self._cap_btn.configure(text=f"键盘捕获: {state}")
            tip = "Ctrl+Alt+Q 断开"
        else:
            tip = "组合键有限 (未启用全局钩子)  |  Ctrl+Alt+Q 断开"
        self._status_var.set(
            f"{self.host}:{self.port}  {self.remote_w}x{self.remote_h}  |  {tip}")

    def _request_stop(self):
        self._running = False   # render loop will pick this up and shut down

    def _on_close(self):
        self._running = False

    def _shutdown(self):
        if self._hook is not None:
            try:
                self._hook.stop()
            except Exception:
                pass
            self._hook = None
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


def connection_manager(default_port=DEFAULT_PORT, default_password=DEFAULT_PASSWORD):
    """Show a TeamViewer-style picker: auto-discovered LAN machines (name + IP)
    plus manual entry. Returns {host, port, password} or None if cancelled."""
    result = {}
    machines = []

    win = tk.Tk()
    win.title("PcController - 选择要控制的电脑")
    win.resizable(False, False)
    outer = tk.Frame(win, padx=16, pady=12)
    outer.pack()

    tk.Label(outer, text="局域网中发现的电脑(双击直接连接):", anchor="w").grid(
        row=0, column=0, columnspan=2, sticky="w")

    listframe = tk.Frame(outer)
    listframe.grid(row=1, column=0, columnspan=2, pady=(4, 6), sticky="we")
    scroll = tk.Scrollbar(listframe, orient="vertical")
    listbox = tk.Listbox(listframe, width=48, height=7, yscrollcommand=scroll.set)
    scroll.config(command=listbox.yview)
    scroll.pack(side="right", fill="y")
    listbox.pack(side="left", fill="both", expand=True)

    status = tk.StringVar(value="")
    tk.Label(outer, textvariable=status, fg="#666", anchor="w").grid(
        row=2, column=0, sticky="w")
    refresh_btn = tk.Button(outer, text="刷新")
    refresh_btn.grid(row=2, column=1, sticky="e")

    form = tk.Frame(outer)
    form.grid(row=3, column=0, columnspan=2, pady=(8, 0), sticky="we")
    tk.Label(form, text="IP:").grid(row=0, column=0, sticky="e", pady=3)
    host_var = tk.StringVar()
    tk.Entry(form, textvariable=host_var, width=26).grid(row=0, column=1, pady=3)
    tk.Label(form, text="端口:").grid(row=1, column=0, sticky="e", pady=3)
    port_var = tk.StringVar(value=str(default_port))
    tk.Entry(form, textvariable=port_var, width=26).grid(row=1, column=1, pady=3)
    tk.Label(form, text="密码:").grid(row=2, column=0, sticky="e", pady=3)
    pw_var = tk.StringVar(value=default_password)
    pw_entry = tk.Entry(form, textvariable=pw_var, width=26, show="*")
    pw_entry.grid(row=2, column=1, pady=3)

    def populate(found):
        machines.extend(found)
        for m in found:
            listbox.insert("end", f"{m['name']}   ({m['ip']}:{m['port']})")
        status.set(f"找到 {len(found)} 台" if found else "未发现,请手动输入 IP")
        refresh_btn.configure(state="normal")

    def do_scan():
        status.set("正在搜索…")
        refresh_btn.configure(state="disabled")
        listbox.delete(0, "end")
        machines.clear()

        def worker():
            try:
                found = discovery.discover(timeout=1.5)
            except Exception:
                found = []
            win.after(0, lambda: populate(found))

        threading.Thread(target=worker, daemon=True).start()

    def on_select(_evt=None):
        sel = listbox.curselection()
        if sel:
            m = machines[sel[0]]
            host_var.set(m["ip"])
            port_var.set(str(m["port"]))
            pw_entry.focus_set()

    def on_connect(_evt=None):
        on_select()
        host = host_var.get().strip()
        if not host:
            status.set("请先选择或输入 IP")
            return
        try:
            port = int(port_var.get().strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT
        result.update(host=host, port=port, password=pw_var.get())
        win.destroy()

    listbox.bind("<<ListboxSelect>>", on_select)
    listbox.bind("<Double-Button-1>", on_connect)
    refresh_btn.configure(command=do_scan)

    btns = tk.Frame(outer)
    btns.grid(row=4, column=0, columnspan=2, pady=(12, 0))
    tk.Button(btns, text="连接", width=10, command=on_connect).pack(side="left", padx=8)
    tk.Button(btns, text="取消", width=10, command=win.destroy).pack(side="left", padx=8)
    win.bind("<Return>", on_connect)

    win.eval("tk::PlaceWindow . center")
    win.after(200, do_scan)   # auto-scan when the window opens
    win.mainloop()

    return result or None


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

    if host is None:  # double-clicked / no --host: show the LAN picker
        chosen = connection_manager(default_port=port, default_password=password)
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
