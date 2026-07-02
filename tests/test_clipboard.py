import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import clipboard


class ClipboardSyncTest(unittest.TestCase):
    def setUp(self):
        self._orig_avail = clipboard._AVAILABLE
        self._orig_pc = clipboard.pyperclip
        clipboard._AVAILABLE = True
        self.pc = mock.MagicMock()
        self.pc.paste.return_value = ""
        clipboard.pyperclip = self.pc

    def tearDown(self):
        clipboard._AVAILABLE = self._orig_avail
        clipboard.pyperclip = self._orig_pc

    def test_local_change_sends_once(self):
        sent = []
        cs = clipboard.ClipboardSync(send_text=sent.append)
        cs._last = "old"
        self.pc.paste.return_value = "hello"
        cs._poll_once()
        cs._poll_once()   # unchanged -> must not resend
        self.assertEqual(sent, ["hello"])

    def test_apply_remote_sets_clipboard_and_no_echo(self):
        sent = []
        cs = clipboard.ClipboardSync(send_text=sent.append)
        cs._last = None
        cs.apply_remote("frompeer")
        self.pc.copy.assert_called_once_with("frompeer")
        # poller now sees the applied text -> it must NOT bounce back to the peer
        self.pc.paste.return_value = "frompeer"
        cs._poll_once()
        self.assertEqual(sent, [])

    def test_apply_remote_same_as_last_skips_copy(self):
        cs = clipboard.ClipboardSync(send_text=lambda t: None)
        cs._last = "x"
        cs.apply_remote("x")
        self.pc.copy.assert_not_called()

    def test_empty_and_oversized_skipped(self):
        sent = []
        cs = clipboard.ClipboardSync(send_text=sent.append)
        cs._last = None
        self.pc.paste.return_value = ""
        cs._poll_once()
        self.pc.paste.return_value = "x" * (clipboard.MAX_LEN + 1)
        cs._poll_once()
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
