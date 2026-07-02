import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pynput.mouse import Button
from pynput.keyboard import Key, KeyCode

from remote_control.input_handler import InputHandler


class FakeMouse:
    def __init__(self):
        self.position = (0, 0)
        self.pressed = []
        self.released = []
        self.scrolled = []

    def press(self, button):
        self.pressed.append(button)

    def release(self, button):
        self.released.append(button)

    def scroll(self, dx, dy):
        self.scrolled.append((dx, dy))


class FakeKeyboard:
    def __init__(self):
        self.pressed = []
        self.released = []

    def press(self, key):
        self.pressed.append(key)

    def release(self, key):
        self.released.append(key)


class InputHandlerTest(unittest.TestCase):
    def setUp(self):
        self.mouse = FakeMouse()
        self.kbd = FakeKeyboard()
        # 1000x500 screen offset at (100, 50) to exercise the mapping math
        self.h = InputHandler(1000, 500, left=100, top=50,
                              mouse=self.mouse, keyboard=self.kbd)

    def test_move_maps_normalized_to_absolute(self):
        self.h.handle({"t": "move", "x": 0.5, "y": 0.5})
        self.assertEqual(self.mouse.position, (100 + 500, 50 + 250))

    def test_move_corner(self):
        self.h.handle({"t": "move", "x": 1.0, "y": 0.0})
        self.assertEqual(self.mouse.position, (1100, 50))

    def test_mouse_down_and_up(self):
        self.h.handle({"t": "down", "x": 0.0, "y": 0.0, "button": "right"})
        self.h.handle({"t": "up", "x": 0.0, "y": 0.0, "button": "right"})
        self.assertEqual(self.mouse.pressed, [Button.right])
        self.assertEqual(self.mouse.released, [Button.right])
        self.assertEqual(self.mouse.position, (100, 50))

    def test_scroll(self):
        self.h.handle({"t": "scroll", "dx": 0, "dy": -3})
        self.assertEqual(self.mouse.scrolled, [(0, -3)])

    def test_char_key(self):
        self.h.handle({"t": "key_down", "key": "a"})
        self.h.handle({"t": "key_up", "key": "a"})
        self.assertEqual(self.kbd.pressed, [KeyCode.from_char("a")])
        self.assertEqual(self.kbd.released, [KeyCode.from_char("a")])

    def test_special_key(self):
        self.h.handle({"t": "key_down", "special": "enter"})
        self.assertEqual(self.kbd.pressed, [Key.enter])

    def test_cmd_key_maps_to_command(self):
        self.h.handle({"t": "key_down", "special": "cmd"})
        self.assertEqual(self.kbd.pressed, [Key.cmd])

    def test_unknown_event_raises(self):
        with self.assertRaises(ValueError):
            self.h.handle({"t": "nonsense"})

    def test_release_all_releases_held_button_and_key(self):
        self.h.handle({"t": "down", "x": 0.1, "y": 0.1, "button": "left"})
        self.h.handle({"t": "key_down", "special": "ctrl"})
        self.h.release_all()
        self.assertIn(Button.left, self.mouse.released)
        self.assertIn(Key.ctrl, self.kbd.released)

    def test_release_all_skips_already_released(self):
        self.h.handle({"t": "down", "x": 0, "y": 0, "button": "left"})
        self.h.handle({"t": "up", "x": 0, "y": 0, "button": "left"})
        self.mouse.released.clear()
        self.h.release_all()  # nothing still held -> no extra releases
        self.assertEqual(self.mouse.released, [])


if __name__ == "__main__":
    unittest.main()
