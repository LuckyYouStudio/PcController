"""Mac-side agent: capture the screen and inject remote input.

Run this on the machine you want to control (the Mac)::

    python -m remote_control.server --password secret

It listens for one controller at a time, streams JPEG frames of the primary
monitor, and replays the mouse / keyboard events it receives.
"""

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zlib

import mss
from PIL import Image

from . import protocol as P
from . import discovery
from . import identity
from . import macperms
from . import p2p
from . import relayclient
from .clipboard import ClipboardSync
from .config import (
    ServerConfig,
    DEFAULT_PORT,
    DEFAULT_RELAY_PORT,
    DEFAULT_PASSWORD,
    DEFAULT_FPS,
    DEFAULT_QUALITY,
    DEFAULT_SCALE,
    DEFAULT_MONITOR,
)
from .input_handler import InputHandler


def _send(conn, lock, msg_type, payload):
    """Send a framed message, serialized behind ``lock`` when given (frames and
    clipboard messages are sent from different threads)."""
    if lock is None:
        P.send_msg(conn, msg_type, payload)
    else:
        with lock:
            P.send_msg(conn, msg_type, payload)


def _make_clipboard(conn, cfg, send_lock):
    """Create + start a ClipboardSync that mirrors this machine's clipboard to
    the controller, or return None if disabled."""
    if not cfg.clipboard:
        return None
    clip = ClipboardSync(
        send_text=lambda t: _send(conn, send_lock, P.MSG_CLIPBOARD,
                                  t.encode("utf-8")))
    clip.start()
    return clip


def _get_monitor(index):
    """Return the mss monitor dict for ``index`` (with a safe fallback)."""
    with mss.MSS() as sct:
        monitors = sct.monitors
        if index < 0 or index >= len(monitors):
            index = 1 if len(monitors) > 1 else 0
        return dict(monitors[index])


def _input_loop(conn, handler, stop, clip=None):
    """Receive input / clipboard messages until the connection drops."""
    try:
        while not stop.is_set():
            msg_type, payload = P.recv_msg(conn)
            if msg_type == P.MSG_INPUT and handler is not None:
                try:
                    handler.handle(json.loads(payload.decode("utf-8")))
                except Exception as exc:  # never let one bad event kill the loop
                    print(f"[server] input error: {exc}")
            elif msg_type == P.MSG_CLIPBOARD and clip is not None:
                clip.apply_remote(payload.decode("utf-8", "ignore"))
    except (ConnectionError, OSError):
        pass
    finally:
        stop.set()


def _capture_loop(conn, cfg, monitor, stop, send_lock=None):
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
                    _send(conn, send_lock, P.MSG_FRAME, buf.getvalue())
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

    send_lock = threading.Lock()
    clip = _make_clipboard(conn, cfg, send_lock)

    stop = threading.Event()
    reader = threading.Thread(
        target=_input_loop, args=(conn, handler, stop, clip), daemon=True
    )
    reader.start()
    try:
        _capture_loop(conn, cfg, monitor, stop, send_lock)
    finally:
        stop.set()
        reader.join(timeout=1.0)
        if clip is not None:
            clip.stop()
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
    if macperms.IS_MAC:
        st = macperms.status()
        if not st["screen_recording"]:
            print("[server] ⚠️ 屏幕录制未授权：控制端只能看到本程序的窗口。"
                  "请在 系统设置→隐私与安全性→屏幕录制 中授权后重启。")
        if not st["accessibility"]:
            print("[server] ⚠️ 辅助功能未授权：无法注入鼠标/键盘。"
                  "请在 系统设置→隐私与安全性→辅助功能 中授权。")
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


def serve_via_relay(cfg, relay_host, relay_port, session_id,
                    on_status=None, stop_event=None, session_handler=None):
    """Register with a public relay and serve remote controllers, reconnecting
    after each session. Runs until ``stop_event`` is set. Meant to run on a
    background thread alongside the LAN listener.

    ``session_handler(sock)`` runs one paired session; it defaults to
    ``handle_connection`` but the GUI passes a main-thread-safe handler.
    """
    if session_handler is None:
        session_handler = lambda s: handle_connection(s, cfg)

    def notify(msg):
        print(f"[relay-agent] {msg}")
        if on_status is not None:
            try:
                on_status(msg)
            except Exception:
                pass

    while stop_event is None or not stop_event.is_set():
        try:
            sock = relayclient.connect_agent(relay_host, relay_port, session_id)
        except Exception as exc:
            notify(f"连接中转失败({exc});5 秒后重试")
            if stop_event is not None and stop_event.wait(5):
                break
            elif stop_event is None:
                time.sleep(5)
            continue
        notify(f"已在中转注册(远程ID {session_id}),等待远程控制端…")
        try:
            session_handler(sock)   # blocks until a controller pairs + ends
        except Exception as exc:
            print(f"[relay-agent] session error: {exc}")
        finally:
            try:
                sock.close()
            except OSError:
                pass


def _split_hostport(text, default_port):
    text = text.strip()
    if ":" in text:
        host, _, port = text.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            return host, default_port
    return text, default_port


def serve_via_p2p(cfg, server_host, server_port, session_id,
                  on_status=None, stop_event=None, session_handler=None):
    """Make this machine reachable over the internet via signaling+P2P (with
    relay fallback), serving one controller at a time and reconnecting after
    each session. Runs on a background thread alongside the LAN listener."""
    if session_handler is None:
        session_handler = lambda s, m: handle_connection(s, cfg)

    def notify(msg):
        print(f"[p2p-agent] {msg}")
        if on_status is not None:
            try:
                on_status(msg)
            except Exception:
                pass

    notify(f"已上线(远程ID {session_id}),等待远程控制端…")
    while stop_event is None or not stop_event.is_set():
        try:
            sock, mode = p2p.establish(server_host, server_port, "agent",
                                       session_id, timeout=30)
        except ConnectionError:
            continue   # nobody connected within the window; wait again
        except Exception as exc:
            notify(f"上线失败({exc});5 秒后重试")
            if stop_event is not None and stop_event.wait(5):
                break
            continue
        notify(f"远程控制端已接入(方式:{mode})")
        try:
            session_handler(sock, mode)
        except Exception as exc:
            print(f"[p2p-agent] session error: {exc}")
        finally:
            try:
                sock.close()
            except OSError:
                pass


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
    send_lock = threading.Lock()
    clip = _make_clipboard(conn, cfg, send_lock)

    def reader():
        try:
            while not stop.is_set():
                mt, pl = P.recv_msg(conn)
                if mt == P.MSG_INPUT and cfg.input_enabled and inject_q is not None:
                    try:
                        inject_q.put(("input", json.loads(pl.decode("utf-8"))))
                    except Exception as exc:
                        print(f"[server] input decode error: {exc}")
                elif mt == P.MSG_CLIPBOARD and clip is not None:
                    clip.apply_remote(pl.decode("utf-8", "ignore"))
        except (ConnectionError, OSError):
            pass
        finally:
            stop.set()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        _capture_loop(conn, cfg, monitor, stop, send_lock)  # mss on a bg thread is fine
    finally:
        stop.set()
        t.join(timeout=1.0)
        if clip is not None:
            clip.stop()


def _launch_control(extra_args=None):
    """Spawn a controller instance (its own window) to control another machine.
    Re-invokes this same program (default: --control to show the picker; or an
    explicit arg list like ['--host', ip, ...]) so a frozen exe can relaunch
    itself; falls back to python + this script when running from source."""
    args = extra_args if extra_args is not None else ["--control"]
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.argv[0]] + args
        else:
            cmd = [sys.executable, sys.argv[0]] + args
        subprocess.Popen(cmd)
    except Exception as exc:
        print(f"[server] launch control failed: {exc}")


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
    root.title("PcController")
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
        tk.Label(body, text="中转服务器 (远程用,可留空):").grid(row=2, column=0, sticky="e", pady=5)
        relay_var = tk.StringVar(value=getattr(base_args, "relay", "") or "")
        tk.Entry(body, textvariable=relay_var, width=22).grid(row=2, column=1, pady=5)
        tk.Label(body, text="局域网:对方填你的 IP;远程:填中转地址,对方用你的远程ID",
                 fg="#666", wraplength=320, justify="left").grid(
                     row=3, column=0, columnspan=2, pady=(6, 0))
        btns = tk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, pady=(12, 0))

        def on_start():
            try:
                port = int(port_var.get().strip() or DEFAULT_PORT)
            except ValueError:
                port = DEFAULT_PORT
            state["relay_host_raw"] = relay_var.get().strip()
            cfg = ServerConfig(
                host=base_args.host, port=port, password=pw_var.get(),
                fps=base_args.fps, quality=base_args.quality,
                scale=base_args.scale, monitor=base_args.monitor,
                clipboard=base_args.clipboard,
            )
            # On macOS, capturing other windows needs Screen Recording and
            # controlling the mouse/keyboard needs Accessibility. Gate here so
            # the user isn't left with a black screen / dead input after connect.
            st = macperms.status()
            if not (st["screen_recording"] and st["accessibility"]):
                show_permissions(cfg)
            else:
                start_serving(cfg)

        tk.Button(btns, text="启动", width=9, command=on_start).pack(side="left", padx=8)
        tk.Button(btns, text="取消", width=9, command=root.destroy).pack(side="left", padx=8)
        root.bind("<Return>", lambda e: on_start())

    def show_permissions(cfg):
        for w in body.winfo_children():
            w.destroy()
        root.unbind("<Return>")
        tk.Label(body, text="需要开启两项 macOS 权限", font=("", 15, "bold")).pack()
        tk.Label(body, text="否则控制端只能看到本窗口、且无法控制这台 Mac。",
                 fg="#a33").pack(pady=(4, 10))

        rows = tk.Frame(body)
        rows.pack()
        sr_var = tk.StringVar()
        ax_var = tk.StringVar()

        def refresh_labels():
            st = macperms.status()
            sr_var.set("✅ 已开启" if st["screen_recording"] else "❌ 未开启")
            ax_var.set("✅ 已开启" if st["accessibility"] else "❌ 未开启")
            return st

        tk.Label(rows, text="① 屏幕录制", font=("", 13)).grid(row=0, column=0, sticky="w", pady=4)
        tk.Label(rows, textvariable=sr_var, width=8).grid(row=0, column=1, padx=8)
        tk.Button(rows, text="开启并打开设置",
                  command=lambda: (macperms.request_screen_recording(),
                                   macperms.open_screen_recording_settings(),
                                   refresh_labels())
                  ).grid(row=0, column=2)
        tk.Label(rows, text="② 辅助功能", font=("", 13)).grid(row=1, column=0, sticky="w", pady=4)
        tk.Label(rows, textvariable=ax_var, width=8).grid(row=1, column=1, padx=8)
        tk.Button(rows, text="开启并打开设置",
                  command=lambda: (macperms.request_accessibility(True),
                                   macperms.open_accessibility_settings(),
                                   refresh_labels())
                  ).grid(row=1, column=2)

        tk.Label(body, wraplength=340, fg="#666", justify="left",
                 text="步骤:点每行的按钮 → 在系统设置里给 PcControllerAgent 打开开关。\n"
                      "⚠️ 开启「屏幕录制」后,必须完全退出本 App 再重新打开才生效。"
                 ).pack(pady=(12, 6))

        btns2 = tk.Frame(body)
        btns2.pack(pady=(4, 0))

        def recheck():
            st = refresh_labels()
            if st["screen_recording"] and st["accessibility"]:
                start_serving(cfg)

        tk.Button(btns2, text="重新检测", width=10, command=recheck).pack(side="left", padx=6)
        tk.Button(btns2, text="仍然启动", width=10,
                  command=lambda: start_serving(cfg)).pack(side="left", padx=6)
        tk.Button(btns2, text="返回", width=6, command=show_config).pack(side="left", padx=6)
        refresh_labels()

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
        busy = threading.Lock()   # serve one controller at a time (LAN or relay)

        # advertise on the LAN so controllers can find this agent by name
        disc_stop = threading.Event()
        state["disc_stop"] = disc_stop
        threading.Thread(
            target=discovery.run_responder, args=(cfg.port, disc_stop), daemon=True
        ).start()

        def notify(m):
            print(m)
            ui_q.put(m)

        def run_session(sock, who):
            with busy:
                notify(f"控制端已连接:{who}")
                try:
                    _gui_session(sock, cfg, inject_q)
                except Exception as exc:
                    print(f"[server] session error: {exc}")
                finally:
                    try:
                        sock.close()
                    except OSError:
                        pass
                    inject_q.put(("release", None))
                    notify("控制端已断开 — 继续等待…")

        def lan_bg():
            notify(f"监听中 {cfg.host}:{cfg.port} — 等待控制端连接…")
            try:
                while True:
                    c, addr = srv.accept()
                    run_session(c, f"{addr[0]}:{addr[1]} (局域网)")
            except OSError:
                pass  # listening socket closed on shutdown

        threading.Thread(target=lan_bg, daemon=True).start()

        # remote control via relay (optional)
        relay_raw = state.get("relay_host_raw") or ""
        if relay_raw:
            rhost, rport = _split_hostport(relay_raw, DEFAULT_RELAY_PORT)
            sid = identity.get_or_create_id()
            state["session_id"] = sid
            relay_stop = threading.Event()
            state["relay_stop"] = relay_stop
            threading.Thread(
                target=serve_via_relay,
                args=(cfg, rhost, rport, sid),
                kwargs={"on_status": lambda m: ui_q.put(m),
                        "stop_event": relay_stop,
                        "session_handler":
                            lambda s: run_session(s, f"远程 (中转 {rhost})")},
                daemon=True).start()

        show_status(cfg)

    def show_status(cfg):
        for w in body.winfo_children():
            w.destroy()
        tk.Label(body, text="PcController 正在运行", font=("", 15, "bold")).pack()
        tk.Label(body, text="别人可以控制这台电脑:", fg="#666").pack(pady=(8, 0))
        tk.Label(body, text=f"局域网 IP: {_local_ip()}    端口: {cfg.port}",
                 fg="#0a7a3a", font=("", 13, "bold")).pack(pady=(2, 0))
        tk.Label(body, text=f"密码: {cfg.password}").pack(pady=(2, 0))
        if state.get("session_id"):
            tk.Label(body, text=f"远程 ID: {state['session_id']}",
                     fg="#0a55aa", font=("", 13, "bold")).pack(pady=(4, 0))
            tk.Label(body, text="(远程:对方填中转地址 + 此远程ID + 密码)",
                     fg="#999").pack()

        tk.Frame(body, height=1, bg="#ccc").pack(fill="x", pady=(12, 8))
        tk.Label(body, text="控制别的电脑:", fg="#666").pack()
        tk.Button(body, text="连接并控制对方…",
                  command=lambda: _launch_control()).pack(pady=(4, 2))

        status_var = tk.StringVar(value="启动中…")
        tk.Label(body, textvariable=status_var, fg="#666", wraplength=300).pack(pady=(10, 0))

        # live permission warning (helps if the user chose "仍然启动")
        warn_var = tk.StringVar(value="")
        warn_lbl = tk.Label(body, textvariable=warn_var, fg="#a33", wraplength=320,
                            justify="left")
        warn_lbl.pack(pady=(6, 0))
        perm_btn = tk.Button(body, text="打开权限设置",
                             command=lambda: show_permissions(cfg))
        perm_btn.pack(pady=(4, 0))

        tk.Label(body, text="在控制端填上面的 IP / 端口 / 密码即可连接。\n关闭此窗口即停止服务。",
                 fg="#999").pack(pady=(12, 0))

        perm_state = {"tick": 0}

        def refresh_warn():
            miss = []
            st = macperms.status()
            if not st["screen_recording"]:
                miss.append("屏幕录制未开启 → 控制端只能看到本窗口(需授权后重启本 App)")
            if not st["accessibility"]:
                miss.append("辅助功能未开启 → 无法控制这台 Mac")
            warn_var.set(("⚠️ " + "\n⚠️ ".join(miss)) if miss else "")

        refresh_warn()  # show any warning immediately

        def pump():
            try:
                while True:
                    status_var.set(ui_q.get_nowait())
            except queue.Empty:
                pass
            perm_state["tick"] += 1
            if perm_state["tick"] % 60 == 0:   # ~ once per second
                try:
                    refresh_warn()
                except Exception:
                    pass  # never let the perm check stall the injection pump
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
        relay_stop = state.get("relay_stop")
        if relay_stop is not None:
            relay_stop.set()  # stop registering with the relay
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
    p.add_argument("--no-clipboard", action="store_false", dest="clipboard",
                   help="disable clipboard sync")
    p.add_argument("--relay", default="",
                   help="relay server host[:port] to register with for remote control")
    p.add_argument("--check-perms", action="store_true",
                   help="print macOS permission diagnostics and exit")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.check_perms:
        import json as _json
        print(_json.dumps(macperms.diagnose(), ensure_ascii=False, indent=2))
        return
    # Launched with no arguments (e.g. double-clicked .app): run the GUI, which
    # keeps a Tk event loop on the main thread (so the app never appears frozen)
    # and serves on a background thread.
    if argv is None and len(sys.argv) == 1:
        from .homeapp import run_home_gui
        run_home_gui(args)
        return
    cfg = ServerConfig(
        host=args.host,
        port=args.port,
        password=args.password,
        fps=args.fps,
        quality=args.quality,
        scale=args.scale,
        monitor=args.monitor,
        clipboard=args.clipboard,
    )
    serve(cfg)


if __name__ == "__main__":
    main()
