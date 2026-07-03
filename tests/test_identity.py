import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from remote_control import identity


class IdentityTest(unittest.TestCase):
    def test_stable_nine_digit_id(self):
        tmp = pathlib.Path(tempfile.mkdtemp()) / "id"
        orig = identity._id_path
        identity._id_path = lambda: tmp
        try:
            a = identity.get_or_create_id()
            b = identity.get_or_create_id()   # second call must return the same
        finally:
            identity._id_path = orig
        self.assertEqual(a, b)
        self.assertTrue(a.isdigit())
        self.assertEqual(len(a), 9)


if __name__ == "__main__":
    unittest.main()
