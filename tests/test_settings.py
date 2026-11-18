"""Run: python -m unittest discover -s tests   (from the Analysis tool folder)"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import settings as s  # noqa: E402


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "sub", "settings.json")

    def write_raw(self, text):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_roundtrip(self):
        data = {"watch_hz": [60.0, 15550.0], "spike_k": 7.5, "f_min_hz": 3.0}
        s.save_settings(self.path, data)
        self.assertEqual(s.load_settings(self.path), data)

    def test_parse_removes_duplicates_keeping_order(self):
        new, errors = s.parse_settings("120, 60 120, 60.0, 193780.3", "10", "2")
        self.assertEqual(errors, [])
        self.assertEqual(new["watch_hz"], [120.0, 60.0, 193780.3])

    def test_missing_file_gives_defaults(self):
        self.assertEqual(s.load_settings(self.path), s.DEFAULTS)

    def test_corrupt_file_gives_defaults(self):
        self.write_raw("{not json")
        self.assertEqual(s.load_settings(self.path), s.DEFAULTS)

    def test_wrong_types_give_defaults(self):
        self.write_raw('{"spike_k": "x"}')
        self.assertEqual(s.load_settings(self.path), s.DEFAULTS)

    def test_parse_valid(self):
        cfg, errors = s.parse_settings("60, 120 15550", "10", "2")
        self.assertEqual(errors, [])
        self.assertEqual(cfg, {"watch_hz": [60.0, 120.0, 15550.0], "spike_k": 10.0, "f_min_hz": 2.0})

    def test_parse_errors(self):
        cfg, errors = s.parse_settings("60, abc, -5", "0.1", "0")
        self.assertGreaterEqual(len(errors), 4)
        joined = "\n".join(errors)
        for entry in ("abc", "-5", "0.1"):
            self.assertIn(entry, joined)
        self.assertTrue(any("0" in e and "f_min" in e.lower() or "lowest" in e.lower() for e in errors))

    def test_defaults_not_mutated(self):
        loaded = s.load_settings(self.path)
        loaded["watch_hz"].append(50.0)
        self.assertEqual(s.DEFAULTS["watch_hz"], [])
        self.assertEqual(s.load_settings(self.path)["watch_hz"], [])


if __name__ == "__main__":
    unittest.main()
