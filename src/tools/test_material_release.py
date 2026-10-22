"""Reject incomplete evidence and changes after the material review."""

from copy import deepcopy
import unittest

from check_material_release import require_capture, source_equal


class MaterialReleaseTests(unittest.TestCase):
    def test_identical_source(self):
        self.assertTrue(source_equal(b"a = 1\n", b"a = 1\r\n"))

    def test_changed_source(self):
        self.assertFalse(source_equal(b"a = 1\n", b"a = 2\n"))

    def test_version_only(self):
        self.assertTrue(source_equal(b'APP_VERSION = "3.3"\n', b'APP_VERSION = "3.4"\n', True))

    def test_version_does_not_hide_other_changes(self):
        self.assertFalse(source_equal(b'APP_VERSION = "3.3"\nx = 1\n', b'APP_VERSION = "3.4"\nx = 2\n', True))

    def test_version_downgrade(self):
        with self.assertRaises(ValueError):
            source_equal(b'APP_VERSION = "3.3"\n', b'APP_VERSION = "3.2"\n', True)

    def fixture(self):
        hidden = {"IsWindow": True, "IsWindowVisible": False, "IsIconic": False}
        return {"completed": True, "passed": True, "source_unchanged": True,
                "cases": [{}], "frames": [{"native_hidden": hidden}],
                "hidden_at_creation": hidden, "hidden_at_finish": hidden}

    def test_completed_capture(self):
        require_capture(self.fixture(), 1, 1)

    def test_partial_or_failed_capture(self):
        for key in ("completed", "passed", "source_unchanged"):
            row = deepcopy(self.fixture())
            row[key] = False
            with self.assertRaises(ValueError):
                require_capture(row, 1, 1)

    def test_missing_frame(self):
        with self.assertRaises(ValueError):
            require_capture(self.fixture(), 1, 2)

    def test_visible_or_minimized_window(self):
        for key in ("IsWindowVisible", "IsIconic"):
            row = deepcopy(self.fixture())
            row["frames"][0]["native_hidden"][key] = True
            with self.assertRaises(ValueError):
                require_capture(row, 1, 1)


if __name__ == "__main__":
    unittest.main()
