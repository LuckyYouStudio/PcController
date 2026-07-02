import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
