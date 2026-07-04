"""TeamViewer-style home window for the unified app.

One window, two tabs:

* 局域网 (LAN): a "allow this PC to be found" toggle, this machine's IP/port/
  password, and a live list of discoverable machines to control.
* 互联网 (Internet): this machine's ID + password (shareable, with regenerate /
  copy), and a "partner ID" field to control someone else through the relay.

The machine starts serving automatically on launch (TeamViewer-like), and all
pynput injection stays on the main thread (macOS-safe), reusing the session
machinery in ``server``.
"""

import queue
import random
import socket
import threading

from . import discovery
from . import identity
from . import macperms
from .config import ServerConfig, DEFAULT_RELAY_PORT, DEFAULT_SERVER
from .input_handler import InputHandler
from .server import (
    _gui_session, serve_via_p2p, _local_ip, _split_hostport, _launch_control,
)

_PW_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"   # no ambiguous 0/o/1/l

# video presets — internet links (and a small relay VPS) can't carry full-res
# 15fps, so default to something lighter and let the user pick.
QUALITY_PRESETS = {
    "流畅": {"scale": 0.5, "quality": 35, "fps": 10},
    "均衡": {"scale": 0.75, "quality": 50, "fps": 12},
    "高清": {"scale": 1.0, "quality": 70, "fps": 15},
}
_DEFAULT_QUALITY = "均衡"


def _gen_password():
    return "".join(random.choices(_PW_ALPHABET, k=8))


def _fmt_id(s):
    return " ".join(s[i:i + 3] for i in range(0, len(s), 3))


def run_home_gui(base_args):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("PcController")
    root.resizable(False, False)

    ui_q = queue.Queue()
    inject_q = queue.Queue()
    my_id = identity.get_or_create_id()

    cfg = ServerConfig(
        host=base_args.host, port=base_args.port, password=_gen_password(),
        fps=base_args.fps, quality=base_args.quality, scale=base_args.scale,
        monitor=base_args.monitor, clipboard=base_args.clipboard,
    )
    state = {"srv": None, "handler": None, "busy": threading.Lock(),
             "disc_stop": None, "relay_stop": None}

    def set_quality(name):
        p = QUALITY_PRESETS.get(name)
        if p:
            cfg.scale, cfg.quality, cfg.fps = p["scale"], p["quality"], p["fps"]

    set_quality(_DEFAULT_QUALITY)   # lighter default; full-res kills internet links

    # ---------------- serving (auto-start) ----------------
    def notify(m):
        print(m)
        ui_q.put(m)

    def run_session(sock, who):
        with state["busy"]:
            notify(f"已连接:{who}")
            try:
                _gui_session(sock, cfg, inject_q)
            except Exception as exc:
                print(f"[home] session error: {exc}")
            finally:
                try:
                    sock.close()
                except OSError:
                    pass
                inject_q.put(("release", None))
                notify("对方已断开 — 继续等待…")

    def start_serving():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((cfg.host, cfg.port))
            srv.listen(1)
        except OSError as exc:
            messagebox.showerror("启动失败", f"端口 {cfg.port} 无法监听:\n{exc}")
            return False
        state["srv"] = srv
        state["handler"] = InputHandler(1, 1) if cfg.input_enabled else None
        # keep the Mac reachable: don't let it idle-sleep while online
        state["nosleep"] = macperms.prevent_idle_sleep()

        def lan_bg():
            notify(f"监听中 :{cfg.port} — 等待连接…")
            try:
                while True:
                    c, addr = srv.accept()
                    run_session(c, f"{addr[0]} (局域网)")
            except OSError:
                pass
        threading.Thread(target=lan_bg, daemon=True).start()
        return True

    def set_discoverable(on):
        if on and state["disc_stop"] is None:
            ds = threading.Event()
            state["disc_stop"] = ds
            threading.Thread(target=discovery.run_responder,
                             args=(cfg.port, ds), daemon=True).start()
        elif not on and state["disc_stop"] is not None:
            state["disc_stop"].set()
            state["disc_stop"] = None

    def set_relay(relay_raw):
        if state["relay_stop"] is not None:
            state["relay_stop"].set()
            state["relay_stop"] = None
        relay_raw = (relay_raw or "").strip()
        if not relay_raw:
            return
        rhost, rport = _split_hostport(relay_raw, DEFAULT_RELAY_PORT)
        rs = threading.Event()
        state["relay_stop"] = rs
        threading.Thread(
            target=serve_via_p2p, args=(cfg, rhost, rport, my_id),
            kwargs={"on_status": lambda m: ui_q.put(m), "stop_event": rs,
                    "session_handler":
                        lambda s, m: run_session(s, f"远程 · 方式:{m}")},
            daemon=True).start()

    def copy_text(text):
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception:
            pass

    # ---------------- UI ----------------
    top = tk.Frame(root, padx=10)
    top.pack(fill="x", pady=(8, 0))
    tk.Label(top, text="画质:").pack(side="left")
    q_var = tk.StringVar(value=_DEFAULT_QUALITY)
    for _name in ("流畅", "均衡", "高清"):
        tk.Radiobutton(top, text=_name, value=_name, variable=q_var,
                       command=lambda: set_quality(q_var.get())).pack(side="left")
    tk.Label(top, text="(卡顿就选「流畅」;改画质后重新连接才生效)",
             fg="#888").pack(side="left", padx=(8, 0))

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=10, pady=10)

    # status bar (shows 监听中 / 已连接 · 方式:p2p 或 relay)
    status_var = tk.StringVar(value="启动中…")
    tk.Label(root, textvariable=status_var, fg="#0a55aa", anchor="w").pack(
        fill="x", padx=12, pady=(0, 6))

    # ===== 局域网 tab =====
    lan = tk.Frame(nb, padx=14, pady=12)
    nb.add(lan, text="  局域网  ")
    tk.Label(lan, text="本机(可被控制)", font=("", 12, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    info_var = tk.StringVar()
    tk.Label(lan, textvariable=info_var, fg="#0a7a3a", justify="left",
             wraplength=380).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 6))
    disc_var = tk.BooleanVar(value=True)
    tk.Checkbutton(lan, text="允许被查找(其他人可在列表中发现本机)",
                   variable=disc_var,
                   command=lambda: set_discoverable(disc_var.get())).grid(
                       row=2, column=0, columnspan=2, sticky="w")
    ttk.Separator(lan, orient="horizontal").grid(
        row=3, column=0, columnspan=2, sticky="we", pady=8)
    tk.Label(lan, text="控制局域网电脑(双击连接):").grid(
        row=4, column=0, columnspan=2, sticky="w")
    lb = tk.Listbox(lan, width=46, height=6)
    lb.grid(row=5, column=0, columnspan=2, sticky="we", pady=(4, 4))
    machines = []
    lan_status = tk.StringVar(value="")
    tk.Label(lan, textvariable=lan_status, fg="#888").grid(row=6, column=0, sticky="w")
    tk.Button(lan, text="刷新", command=lambda: scan()).grid(row=6, column=1, sticky="e")
    tk.Label(lan, text="对方密码:").grid(row=7, column=0, sticky="e", pady=(6, 0))
    lan_pw = tk.Entry(lan, width=26, show="*")
    lan_pw.grid(row=7, column=1, sticky="w", pady=(6, 0))
    tk.Button(lan, text="连接并控制", command=lambda: lan_connect()).grid(
        row=8, column=0, columnspan=2, pady=(10, 0))

    def scan():
        lan_status.set("搜索中…")
        lb.delete(0, "end")
        machines.clear()

        def worker():
            try:
                found = discovery.discover(timeout=1.5)
            except Exception:
                found = []
            root.after(0, lambda: fill(found))
        threading.Thread(target=worker, daemon=True).start()

    def fill(found):
        machines.extend(found)
        for m in found:
            lb.insert("end", f"{m['name']}   ({m['ip']}:{m['port']})")
        lan_status.set(f"找到 {len(found)} 台" if found else "未发现(可去互联网标签用ID)")

    def lan_connect(_evt=None):
        sel = lb.curselection()
        if not sel:
            lan_status.set("请先在列表里选一台电脑")
            return
        m = machines[sel[0]]
        _launch_control(["--host", m["ip"], "--port", str(m["port"]),
                         "--password", lan_pw.get()])
    lb.bind("<Double-Button-1>", lan_connect)

    # ===== 互联网 tab =====
    net = tk.Frame(nb, padx=14, pady=12)
    nb.add(net, text="  互联网  ")
    tk.Label(net, text="中转服务器:").grid(row=0, column=0, sticky="e", pady=3)
    relay_entry = tk.Entry(net, width=26)
    relay_entry.grid(row=0, column=1, sticky="w", pady=3)
    relay_entry.insert(0, getattr(base_args, "relay", "") or DEFAULT_SERVER)
    tk.Button(net, text="应用", command=lambda: set_relay(relay_entry.get())).grid(
        row=0, column=2, padx=4)

    tk.Label(net, text="使用 ID 连接", font=("", 12, "bold")).grid(
        row=1, column=0, columnspan=3, sticky="w", pady=(10, 2))
    tk.Label(net, text="以下是您的 ID 和密码,可分享给对方来连接您。",
             fg="#666").grid(row=2, column=0, columnspan=3, sticky="w")
    box = tk.Frame(net, bg="#f2f2f4", padx=14, pady=10)
    box.grid(row=3, column=0, columnspan=3, sticky="we", pady=(6, 4))
    tk.Label(box, text="您的 ID", bg="#f2f2f4", fg="#666").grid(row=0, column=0, sticky="w")
    tk.Label(box, text=_fmt_id(my_id), bg="#f2f2f4",
             font=("", 16, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 24))
    tk.Label(box, text="密码(可直接修改)", bg="#f2f2f4", fg="#666").grid(
        row=0, column=1, sticky="w")
    pw_var = tk.StringVar(value=cfg.password)
    tk.Entry(box, textvariable=pw_var, font=("", 15, "bold"), width=12,
             relief="solid", bd=1).grid(row=1, column=1, sticky="w")

    def on_pw_change(*_a):
        cfg.password = pw_var.get()   # take effect on the next connection
        refresh_info()
    pw_var.trace_add("write", on_pw_change)

    def regen_pw():
        pw_var.set(_gen_password())   # trace updates cfg.password + info
    tk.Button(box, text="↻", width=3, command=regen_pw).grid(row=1, column=2, padx=(16, 2))
    tk.Button(box, text="复制", width=5,
              command=lambda: copy_text(f"ID {my_id}  密码 {cfg.password}")).grid(
                  row=1, column=3, padx=2)

    ttk.Separator(net, orient="horizontal").grid(
        row=4, column=0, columnspan=3, sticky="we", pady=10)
    tk.Label(net, text="通过 ID 进行连接,以远程访问和控制设备。",
             fg="#666").grid(row=5, column=0, columnspan=3, sticky="w")
    tk.Label(net, text="会话对方 ID:").grid(row=6, column=0, sticky="e", pady=3)
    partner_id = tk.Entry(net, width=20)
    partner_id.grid(row=6, column=1, sticky="w", pady=3)
    tk.Label(net, text="对方密码:").grid(row=7, column=0, sticky="e", pady=3)
    partner_pw = tk.Entry(net, width=20, show="*")
    partner_pw.grid(row=7, column=1, sticky="w", pady=3)

    def net_connect():
        relay = relay_entry.get().strip()
        pid = partner_id.get().strip().replace(" ", "")
        if not relay:
            messagebox.showinfo("需要中转服务器", "远程连接需要先填写中转服务器地址。")
            return
        if not pid:
            return
        _launch_control(["--relay", relay, "--id", pid, "--password", partner_pw.get()])
    tk.Button(net, text="连接", width=8, command=net_connect).grid(
        row=8, column=0, columnspan=2, pady=(8, 0))

    # ---------------- status + inject pump ----------------
    def refresh_info():
        warn = ""
        if macperms.IS_MAC:
            st = macperms.status()
            if not (st["screen_recording"] and st["accessibility"]):
                warn = "  ⚠️ 需在系统设置授权屏幕录制+辅助功能"
        info_var.set(f"{socket.gethostname()}   IP {_local_ip()}:{cfg.port}   "
                     f"密码 {cfg.password}{warn}")

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
                            handler.handle(data)
                        except Exception as exc:
                            print(f"[home] input error: {exc}")
                    elif kind == "release":
                        handler.release_all()
            except queue.Empty:
                pass
        root.after(15, pump)

    def prompt_permissions_if_needed():
        """On launch, if macOS remote-control permissions are missing, trigger
        the native prompts AND show a guided popup. The controlled machine is
        usually unattended, so this must happen while someone is still there."""
        if not macperms.IS_MAC:
            return
        st = macperms.status()
        if st["screen_recording"] and st["accessibility"]:
            return
        # Fire the native system prompts right away (user asked for auto-popup).
        macperms.request_screen_recording()
        macperms.request_accessibility(True)

        win = tk.Toplevel(root)
        win.title("开启远程控制权限")
        win.resizable(False, False)
        win.transient(root)
        frm = tk.Frame(win, padx=22, pady=18)
        frm.pack()
        tk.Label(frm, text="⚠️ 需要开启两项权限才能被远程控制",
                 font=("", 14, "bold"), fg="#a33").pack()
        tk.Label(frm, justify="left", fg="#555",
                 text="否则对方连上后只看到黑屏、也无法控制这台电脑。\n"
                      "请现在就开启——离开后没人在场就没法补授权了。").pack(pady=(4, 10))

        rows = tk.Frame(frm)
        rows.pack()
        sr_var = tk.StringVar()
        ax_var = tk.StringVar()

        def refresh():
            s = macperms.status()
            sr_var.set("✅ 已开启" if s["screen_recording"] else "❌ 未开启")
            ax_var.set("✅ 已开启" if s["accessibility"] else "❌ 未开启")
            return s

        tk.Label(rows, text="① 屏幕录制", font=("", 12)).grid(row=0, column=0, sticky="w", pady=4)
        tk.Label(rows, textvariable=sr_var, width=8).grid(row=0, column=1, padx=8)
        tk.Button(rows, text="开启", width=6,
                  command=lambda: (macperms.request_screen_recording(),
                                   macperms.open_screen_recording_settings(),
                                   refresh())).grid(row=0, column=2)
        tk.Label(rows, text="② 辅助功能", font=("", 12)).grid(row=1, column=0, sticky="w", pady=4)
        tk.Label(rows, textvariable=ax_var, width=8).grid(row=1, column=1, padx=8)
        tk.Button(rows, text="开启", width=6,
                  command=lambda: (macperms.request_accessibility(True),
                                   macperms.open_accessibility_settings(),
                                   refresh())).grid(row=1, column=2)

        tk.Label(frm, wraplength=340, fg="#888", justify="left",
                 text="提示:点「开启」→ 在系统设置里给 PcController 打开开关。\n"
                      "⚠️ 屏幕录制开启后需退出 App 再重开才生效。").pack(pady=(12, 6))

        btns = tk.Frame(frm)
        btns.pack(pady=(4, 0))

        def recheck():
            s = refresh()
            if s["screen_recording"] and s["accessibility"]:
                win.destroy()
        tk.Button(btns, text="我已开启,重新检测", command=recheck).pack(side="left", padx=6)
        tk.Button(btns, text="稍后", command=win.destroy).pack(side="left", padx=6)

        def auto():
            if not win.winfo_exists():
                return
            s = refresh()
            if s["screen_recording"] and s["accessibility"]:
                win.destroy()
                return
            win.after(1500, auto)   # keep re-checking so ✅ shows + auto-closes

        refresh()
        win.after(1500, auto)
        win.update_idletasks()
        win.tk.call("tk::PlaceWindow", win._w, "center")
        win.lift()

    def on_close():
        for key in ("disc_stop", "relay_stop"):
            if state[key] is not None:
                state[key].set()
        if state.get("nosleep") is not None:
            try:
                state["nosleep"].terminate()   # let the Mac idle-sleep again
            except Exception:
                pass
        if state["srv"] is not None:
            try:
                state["srv"].close()
            except OSError:
                pass
        if state["handler"] is not None:
            try:
                state["handler"].release_all()
            except Exception:
                pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    if macperms.IS_MAC:
        # Minimizing to the Dock on macOS pauses the Tk timer that drives input
        # injection, which freezes the remote session. Convert a minimize into a
        # plain "hide" (withdraw): the window disappears but the event loop and
        # injection keep running. Clicking the app's Dock icon brings it back.
        def _on_unmap(e=None):
            if e is not None and getattr(e, "widget", None) is not root:
                return
            try:
                if root.state() == "iconic":
                    root.deiconify()   # cancel the Dock-minimize (resumes timer)
                    root.withdraw()    # hide without minimizing -> loop keeps running
            except Exception:
                pass
        root.bind("<Unmap>", _on_unmap)

        def _reopen(*_a):
            try:
                root.deiconify()
                root.lift()
                root.focus_force()
            except Exception:
                pass
        try:
            root.createcommand("::tk::mac::ReopenApplication", _reopen)
        except Exception:
            pass

    if start_serving():
        set_discoverable(True)
        set_relay(getattr(base_args, "relay", "") or DEFAULT_SERVER)  # auto-online
        refresh_info()
        pump()
        # auto-detect permissions on launch; pop the guided dialog if missing
        root.after(400, prompt_permissions_if_needed)
    root.eval("tk::PlaceWindow . center")
    root.mainloop()
