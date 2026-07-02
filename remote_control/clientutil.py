"""Pure helpers for the controller GUI (no tkinter import, so unit-testable).

Two concerns live here:

* mapping a mouse position inside the display widget to a normalised
  ``[0, 1]`` coordinate on the remote screen (with letterbox handling), and
* translating a Tk key event (keysym + char) into an input-event key spec.
"""

# Tk keysym -> our SPECIAL_KEY_MAP name
TK_SPECIAL = {
    "Return": "enter",
    "KP_Enter": "enter",
    "Escape": "escape",
    "BackSpace": "backspace",
    "Delete": "delete",
    "Tab": "tab",
    "space": "space",
    "Up": "up",
    "Down": "down",
    "Left": "left",
    "Right": "right",
    "Home": "home",
    "End": "end",
    "Prior": "page_up",
    "Next": "page_down",
    "Insert": "insert",
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Control_L": "ctrl",
    "Control_R": "ctrl",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "Super_L": "cmd",
    "Super_R": "cmd",
    "Win_L": "cmd",
    "Win_R": "cmd",
    # macOS Tk reports the Command key as Meta_*/Command
    "Meta_L": "cmd",
    "Meta_R": "cmd",
    "Command": "cmd",
    "Caps_Lock": "caps_lock",
    "F1": "f1", "F2": "f2", "F3": "f3", "F4": "f4",
    "F5": "f5", "F6": "f6", "F7": "f7", "F8": "f8",
    "F9": "f9", "F10": "f10", "F11": "f11", "F12": "f12",
}


def compute_display_rect(canvas_w, canvas_h, img_w, img_h):
    """Return ``(off_x, off_y, disp_w, disp_h)`` for the image letterboxed
    to fit inside the canvas while preserving aspect ratio."""
    if img_w <= 0 or img_h <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return (0, 0, max(0, canvas_w), max(0, canvas_h))
    scale = min(canvas_w / img_w, canvas_h / img_h)
    disp_w = max(1, int(img_w * scale))
    disp_h = max(1, int(img_h * scale))
    off_x = (canvas_w - disp_w) // 2
    off_y = (canvas_h - disp_h) // 2
    return (off_x, off_y, disp_w, disp_h)


def to_normalized(ex, ey, rect):
    """Map a widget-relative point to normalised remote coords, or ``None``
    if the point falls outside the displayed image."""
    off_x, off_y, disp_w, disp_h = rect
    if disp_w <= 0 or disp_h <= 0:
        return None
    nx = (ex - off_x) / disp_w
    ny = (ey - off_y) / disp_h
    if nx < 0.0 or nx > 1.0 or ny < 0.0 or ny > 1.0:
        return None
    return (nx, ny)


def wheel_steps(delta, num=0, is_mac=False):
    """Normalise a Tk wheel event into an integer number of scroll steps.

    Platforms disagree on how the wheel is reported:
      * Windows  -> <MouseWheel>, ``delta`` in multiples of 120
      * macOS    -> <MouseWheel>, ``delta`` is already a small integer
      * Linux/X11-> <Button-4> (up) / <Button-5> (down), ``num`` set
    """
    if num == 4:
        return 1
    if num == 5:
        return -1
    if not delta:
        return 0
    if is_mac:
        return int(delta)
    step = int(delta / 120)
    if step == 0:  # tiny/high-res wheels: keep at least one step of motion
        step = 1 if delta > 0 else -1
    return step


def translate_key(keysym, char):
    """Translate a Tk key event into a key spec dict, or ``None`` to ignore.

    Returns either ``{"special": name}`` or ``{"key": char}``.
    """
    if keysym in TK_SPECIAL:
        return {"special": TK_SPECIAL[keysym]}
    if char and len(char) == 1 and char.isprintable():
        return {"key": char}
    if len(keysym) == 1 and keysym.isprintable():
        return {"key": keysym}
    return None
