"""Cross-platform clipboard text sync.

Both the agent and the controller run a ``ClipboardSync``: a background poller
watches the local clipboard and, when the text changes, calls ``send_text`` to
mirror it to the peer. Incoming peer text is applied with ``apply_remote``.

A single ``_last`` value (the text we most recently sent OR applied) breaks the
feedback loop: applying the peer's text updates ``_last`` so the poller doesn't
bounce it straight back.

Uses ``pyperclip`` (Windows via ctypes, macOS via pbcopy/pbpaste, Linux via
xclip/xsel). If it isn't importable the sync silently no-ops.
"""

import threading

try:
    import pyperclip
    _AVAILABLE = True
except Exception:  # pragma: no cover - only when pyperclip missing
    pyperclip = None
    _AVAILABLE = False

MAX_LEN = 1_000_000  # skip clipboards larger than ~1 MB of text


def available():
    return _AVAILABLE


class ClipboardSync:
    def __init__(self, send_text, poll_interval=0.5):
        self._send_text = send_text
        self._interval = poll_interval
        self._last = None            # last text we sent or applied
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        """Begin watching the local clipboard. Returns False if unavailable."""
        if not _AVAILABLE:
            return False
        try:
            self._last = pyperclip.paste()   # seed so we don't blast existing text
        except Exception:
            self._last = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self._interval):
            self._poll_once()

    def _poll_once(self):
        """One poll: if the local clipboard changed, mirror it to the peer."""
        try:
            cur = pyperclip.paste()
        except Exception:
            return
        if not cur or len(cur) > MAX_LEN:
            return
        with self._lock:
            if cur == self._last:
                return
            self._last = cur
        try:
            self._send_text(cur)
        except Exception:
            pass

    def apply_remote(self, text):
        """Set the local clipboard to text received from the peer (no echo)."""
        if not _AVAILABLE or not text or len(text) > MAX_LEN:
            return
        with self._lock:
            if text == self._last:
                return
            self._last = text
        try:
            pyperclip.copy(text)
        except Exception:
            pass
