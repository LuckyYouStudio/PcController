import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import clientutil as U


class DisplayRectTest(unittest.TestCase):
    def test_exact_fit(self):
        self.assertEqual(U.compute_display_rect(800, 600, 800, 600),
                         (0, 0, 800, 600))

    def test_letterbox_wide_image_in_tall_canvas(self):
        # 1000x500 image in a 500x500 canvas -> width-limited, centered vertically
        off_x, off_y, w, h = U.compute_display_rect(500, 500, 1000, 500)
        self.assertEqual((w, h), (500, 250))
        self.assertEqual(off_x, 0)
        self.assertEqual(off_y, 125)

    def test_zero_size_is_safe(self):
        self.assertEqual(U.compute_display_rect(0, 0, 100, 100), (0, 0, 0, 0))


class NormalizeTest(unittest.TestCase):
    def test_center(self):
        rect = (0, 0, 800, 600)
        self.assertEqual(U.to_normalized(400, 300, rect), (0.5, 0.5))

    def test_offset_rect(self):
        rect = (100, 50, 200, 100)  # image drawn with a margin
        self.assertEqual(U.to_normalized(200, 100, rect), (0.5, 0.5))

    def test_outside_returns_none(self):
        rect = (100, 50, 200, 100)
        self.assertIsNone(U.to_normalized(0, 0, rect))
        self.assertIsNone(U.to_normalized(400, 300, rect))


class WheelStepsTest(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(U.wheel_steps(120, is_mac=False), 1)
        self.assertEqual(U.wheel_steps(-240, is_mac=False), -2)
        self.assertEqual(U.wheel_steps(30, is_mac=False), 1)   # hi-res -> min 1

    def test_mac(self):
        self.assertEqual(U.wheel_steps(3, is_mac=True), 3)
        self.assertEqual(U.wheel_steps(-1, is_mac=True), -1)

    def test_linux_buttons(self):
        self.assertEqual(U.wheel_steps(0, num=4), 1)
        self.assertEqual(U.wheel_steps(0, num=5), -1)

    def test_no_motion(self):
        self.assertEqual(U.wheel_steps(0), 0)


class TranslateKeyTest(unittest.TestCase):
    def test_special(self):
        self.assertEqual(U.translate_key("Return", "\r"), {"special": "enter"})
        self.assertEqual(U.translate_key("Left", ""), {"special": "left"})
        self.assertEqual(U.translate_key("Control_L", ""), {"special": "ctrl"})

    def test_mac_command_key(self):
        self.assertEqual(U.translate_key("Meta_L", ""), {"special": "cmd"})
        self.assertEqual(U.translate_key("Command", ""), {"special": "cmd"})

    def test_printable_char(self):
        self.assertEqual(U.translate_key("a", "a"), {"key": "a"})
        self.assertEqual(U.translate_key("at", "@"), {"key": "@"})

    def test_ignored(self):
        self.assertIsNone(U.translate_key("XF86AudioPlay", ""))


if __name__ == "__main__":
    unittest.main()
