"""Windows low-level keyboard hook (WH_KEYBOARD_LL).

Tkinter cannot stop the OS from acting on global shortcuts (Alt+Tab, the Win
key, etc.), so pressing a combo in the controller triggers the *local*
machine. This installs a system-wide keyboard hook that, while the controller
window is in the foreground, forwards every key to the remote and swallows it
locally - the TeamViewer-style behaviour.

Only imported on Windows. ``vk_to_spec`` is a pure function and unit-tested.
"""

import ctypes
import queue
import threading
from ctypes import wintypes

# --- virtual-key -> our input-event key spec (pure, testable) --------------
MODIFIERS = {"ctrl", "alt", "shift", "cmd"}

_VK_SPECIAL = {
    0x20: "space", 0x0D: "enter", 0x09: "tab", 0x08: "backspace",
    0x1B: "escape", 0x2E: "delete", 0x2D: "insert",
    0x24: "home", 0x23: "end", 0x21: "page_up", 0x22: "page_down",
    0x25: "left", 0x26: "up", 0x27: "right", 0x28: "down",
    0x14: "caps_lock",
    0xA0: "shift", 0xA1: "shift", 0x10: "shift",
    0xA2: "ctrl", 0xA3: "ctrl", 0x11: "ctrl",
    0xA4: "alt", 0xA5: "alt", 0x12: "alt",
    0x5B: "cmd", 0x5C: "cmd",
}
for _i in range(12):
    _VK_SPECIAL[0x70 + _i] = f"f{_i + 1}"

_VK_CHAR = {}
for _vk in range(0x41, 0x5B):          # A-Z
    _VK_CHAR[_vk] = chr(_vk).lower()
for _vk in range(0x30, 0x3A):          # 0-9 (top row)
    _VK_CHAR[_vk] = chr(_vk)
for _i in range(10):                   # numpad 0-9
    _VK_CHAR[0x60 + _i] = str(_i)
_VK_CHAR.update({0x6A: "*", 0x6B: "+", 0x6D: "-", 0x6E: ".", 0x6F: "/"})
_VK_CHAR.update({                      # OEM punctuation (US layout base char)
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/",
    0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})


def vk_to_spec(vk):
    """Map a Windows virtual-key code to {"special": name} / {"key": ch}, or None."""
    if vk in _VK_SPECIAL:
        return {"special": _VK_SPECIAL[vk]}
    if vk in _VK_CHAR:
        return {"key": _VK_CHAR[vk]}
    return None


# --- Win32 plumbing --------------------------------------------------------
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT = 0x0012
GA_ROOT = 2
VK_Q = 0x51

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.wintypes.WPARAM


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.CFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = ctypes.c_void_p
_user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = LRESULT
_user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint]
_user32.GetMessageW.restype = ctypes.c_int
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.GetAncestor.restype = wintypes.HWND
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class KeyboardHook:
    """Install a global keyboard hook and forward keys while ``enabled_getter``
    is true and the target window is in the foreground.

    on_event(spec, pressed) -> forward one key to the remote
    enabled_getter()        -> bool, master on/off (a UI toggle)
    on_disconnect()         -> called on the local Ctrl+Alt+Q hotkey
    """

    def __init__(self, on_event, enabled_getter, on_disconnect):
        self._on_event = on_event
        self._enabled_getter = enabled_getter
        self._on_disconnect = on_disconnect
        self.target_hwnd = None
        self._root_hwnd = 0
        self._hook = None
        self._tid = 0
        self._stop = False
        self._held = set()
        self._was_active = False
        self._queue = queue.Queue()
        self._proc = HOOKPROC(self._low_level_proc)  # keep a strong reference

    # -- foreground / active check -----------------------------------------
    def _active(self):
        if not self._enabled_getter():
            return False
        if not self._root_hwnd:
            return True
        return _user32.GetForegroundWindow() == self._root_hwnd

    def _low_level_proc(self, nCode, wParam, lParam):
        if nCode == 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            pressed = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            if self._handle(kb.vkCode, pressed):
                return 1  # swallow locally
        return _user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _handle(self, vk, pressed):
        active = self._active()
        # if focus just left while keys were held, release them on the remote
        if self._was_active and not active and self._held:
            for name in list(self._held):
                self._queue.put(({"special": name}, False))
            self._held.clear()
        self._was_active = active

        spec = vk_to_spec(vk)
        name = spec.get("special") if spec else None
        if name in MODIFIERS:
            if pressed:
                self._held.add(name)
            else:
                self._held.discard(name)

        if not active:
            return False  # let the local machine handle it

        if pressed and vk == VK_Q and "ctrl" in self._held and "alt" in self._held:
            self._on_disconnect()
            return True
        if spec is not None:
            self._queue.put((spec, pressed))
        return True  # while active, nothing leaks to the local machine

    # -- threads -----------------------------------------------------------
    def _sender(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            spec, pressed = item
            try:
                self._on_event(spec, pressed)
            except Exception:
                pass

    def _run(self):
        self._tid = _kernel32.GetCurrentThreadId()
        self._root_hwnd = (_user32.GetAncestor(self.target_hwnd, GA_ROOT)
                           if self.target_hwnd else 0)
        self._hook = _user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            print("[winhook] SetWindowsHookEx failed; falling back to Tk keys")
            return
        msg = wintypes.MSG()
        while not self._stop:
            r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
        if self._hook:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def start(self):
        """Start the hook. Returns True if the hook installed successfully."""
        threading.Thread(target=self._sender, daemon=True).start()
        ready = threading.Event()

        def runner():
            self._run()
            ready.set()

        self._thread = threading.Thread(target=runner, daemon=True)
        self._thread.start()
        # give the hook a moment to install so we know whether it worked
        import time
        time.sleep(0.2)
        return self._hook is not None

    def stop(self):
        self._stop = True
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self._queue.put(None)
