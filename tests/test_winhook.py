import ctypes
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(sys.platform.startswith("win"), "winhook is Windows-only")
class WinhookVkTest(unittest.TestCase):
    def setUp(self):
        from remote_control import winhook
        self.W = winhook

    def test_letters_and_digits(self):
        self.assertEqual(self.W.vk_to_spec(0x41), {"key": "a"})   # 'A'
        self.assertEqual(self.W.vk_to_spec(0x5A), {"key": "z"})   # 'Z'
        self.assertEqual(self.W.vk_to_spec(0x30), {"key": "0"})
        self.assertEqual(self.W.vk_to_spec(0x39), {"key": "9"})

    def test_specials(self):
        self.assertEqual(self.W.vk_to_spec(0x0D), {"special": "enter"})
        self.assertEqual(self.W.vk_to_spec(0x1B), {"special": "escape"})
        self.assertEqual(self.W.vk_to_spec(0x11), {"special": "ctrl"})
        self.assertEqual(self.W.vk_to_spec(0x12), {"special": "alt"})
        self.assertEqual(self.W.vk_to_spec(0x10), {"special": "shift"})
        self.assertEqual(self.W.vk_to_spec(0x5B), {"special": "cmd"})
        self.assertEqual(self.W.vk_to_spec(0x70), {"special": "f1"})
        self.assertEqual(self.W.vk_to_spec(0x7B), {"special": "f12"})

    def test_punctuation_and_unknown(self):
        self.assertEqual(self.W.vk_to_spec(0xBF), {"key": "/"})
        self.assertEqual(self.W.vk_to_spec(0xC0), {"key": "`"})
        self.assertEqual(self.W.vk_to_spec(0x6B), {"key": "+"})   # numpad add
        self.assertIsNone(self.W.vk_to_spec(0xFF))


class WinhookActiveTest(unittest.TestCase):
    """Foreground detection runs on ANY OS by mocking the Win32 layer.

    Regression guard for the bug where combos leaked to the local machine
    because _active() required an exact top-level HWND match (which fails under
    Tk's wrapper window). The fix: active whenever the foreground window belongs
    to our process (PID match).
    """

    def setUp(self):
        # Load winhook with a mocked WinDLL so it imports off-Windows too.
        self._saved_windll = getattr(ctypes, "WinDLL", None)
        ctypes.WinDLL = lambda *a, **k: mock.MagicMock()
        self._saved_mod = sys.modules.pop("remote_control.winhook", None)
        from remote_control import winhook
        self.W = winhook

    def tearDown(self):
        if self._saved_windll is not None:
            ctypes.WinDLL = self._saved_windll
        elif hasattr(ctypes, "WinDLL"):
            del ctypes.WinDLL
        sys.modules.pop("remote_control.winhook", None)
        if self._saved_mod is not None:
            sys.modules["remote_control.winhook"] = self._saved_mod

    def _hook(self):
        h = self.W.KeyboardHook(lambda *a: None, lambda: True, lambda: None)
        h._own_pid = 4242
        return h

    def _set_fg(self, handle):
        self.W._user32.GetForegroundWindow = lambda: handle

    def _set_pid(self, value):
        def _fn(hwnd, ref):
            ref._obj.value = value  # write through byref(DWORD)
            return 1
        self.W._user32.GetWindowThreadProcessId = _fn

    def test_active_when_foreground_belongs_to_us(self):
        h = self._hook(); h._root_hwnd = 0
        self._set_fg(0xABC); self._set_pid(4242)
        self.assertTrue(h._active())

    def test_inactive_for_other_process(self):
        h = self._hook(); h._root_hwnd = 0
        self._set_fg(0xABC); self._set_pid(9999)
        self.assertFalse(h._active())

    def test_hwnd_fallback_still_matches(self):
        h = self._hook(); h._root_hwnd = 0xABC
        self._set_fg(0xABC); self._set_pid(9999)  # pid mismatch, hwnd matches
        self.assertTrue(h._active())

    def test_disabled_is_inactive(self):
        h = self.W.KeyboardHook(lambda *a: None, lambda: False, lambda: None)
        h._own_pid = 4242
        self._set_fg(0xABC); self._set_pid(4242)
        self.assertFalse(h._active())

    def test_no_foreground_is_inactive(self):
        h = self._hook(); h._root_hwnd = 0
        self._set_fg(0); self._set_pid(4242)
        self.assertFalse(h._active())


if __name__ == "__main__":
    unittest.main()
