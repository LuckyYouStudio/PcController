"""Translate JSON input events from the controller into pynput actions.

An input event is a small dict, e.g.::

    {"t": "move",  "x": 0.5, "y": 0.3}
    {"t": "down",  "x": 0.5, "y": 0.3, "button": "left"}
    {"t": "up",    "x": 0.5, "y": 0.3, "button": "left"}
    {"t": "scroll", "dx": 0, "dy": -2}
    {"t": "key_down", "key": "a"}
    {"t": "key_up",   "special": "enter"}

Mouse coordinates are *normalised* to ``[0, 1]`` relative to the remote
screen, so the two machines never need to agree on a resolution. The handler
multiplies them by the captured monitor geometry to get absolute pixels.
"""

from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, KeyCode, Controller as KeyboardController

BUTTON_MAP = {
    "left": Button.left,
    "right": Button.right,
    "middle": Button.middle,
}

SPECIAL_KEY_MAP = {
    "enter": Key.enter,
    "return": Key.enter,
    "escape": Key.esc,
    "esc": Key.esc,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "tab": Key.tab,
    "space": Key.space,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "home": Key.home,
    "end": Key.end,
    "page_up": Key.page_up,
    "page_down": Key.page_down,
    "insert": Key.insert,
    "shift": Key.shift,
    "ctrl": Key.ctrl,
    "alt": Key.alt,
    "cmd": Key.cmd,      # maps to the Command key on macOS
    "caps_lock": Key.caps_lock,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
}


class InputHandler:
    """Replay input events on the local machine via pynput.

    ``mouse`` and ``keyboard`` may be injected for testing; by default the
    real pynput controllers are created.
    """

    def __init__(self, width, height, left=0, top=0, mouse=None, keyboard=None):
        self.width = width
        self.height = height
        self.left = left
        self.top = top
        self.mouse = mouse if mouse is not None else MouseController()
        self.keyboard = keyboard if keyboard is not None else KeyboardController()
        # track what is currently held so we can release it if the controller
        # disconnects mid-gesture (avoids "stuck" modifier keys / buttons)
        self._pressed_buttons = set()
        self._pressed_keys = set()

    def _abs(self, nx, ny):
        x = int(round(self.left + nx * self.width))
        y = int(round(self.top + ny * self.height))
        return x, y

    def _resolve_key(self, ev):
        special = ev.get("special")
        if special:
            key = SPECIAL_KEY_MAP.get(special)
            if key is not None:
                return key
        ch = ev.get("key")
        if ch:
            return KeyCode.from_char(ch)
        raise ValueError(f"unresolvable key event: {ev!r}")

    def handle(self, ev):
        """Apply a single input event dict."""
        t = ev.get("t")
        if t == "move":
            self.mouse.position = self._abs(ev["x"], ev["y"])
        elif t == "down":
            self.mouse.position = self._abs(ev["x"], ev["y"])
            button = BUTTON_MAP.get(ev.get("button", "left"), Button.left)
            self.mouse.press(button)
            self._pressed_buttons.add(button)
        elif t == "up":
            self.mouse.position = self._abs(ev["x"], ev["y"])
            button = BUTTON_MAP.get(ev.get("button", "left"), Button.left)
            self.mouse.release(button)
            self._pressed_buttons.discard(button)
        elif t == "scroll":
            self.mouse.scroll(ev.get("dx", 0), ev.get("dy", 0))
        elif t == "key_down":
            key = self._resolve_key(ev)
            self.keyboard.press(key)
            self._pressed_keys.add(key)
        elif t == "key_up":
            key = self._resolve_key(ev)
            self.keyboard.release(key)
            self._pressed_keys.discard(key)
        else:
            raise ValueError(f"unknown input event type: {t!r}")

    def release_all(self):
        """Release every button / key still held. Call on disconnect so the
        controlled machine is never left with a stuck modifier."""
        for button in list(self._pressed_buttons):
            try:
                self.mouse.release(button)
            except Exception:
                pass
        self._pressed_buttons.clear()
        for key in list(self._pressed_keys):
            try:
                self.keyboard.release(key)
            except Exception:
                pass
        self._pressed_keys.clear()
