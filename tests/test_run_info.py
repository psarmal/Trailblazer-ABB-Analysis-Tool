"""run_info.py: per-file sample/gain overrides and Summary-workbook import.
Run: python -m unittest tests.test_run_info -v"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_info as ri  # noqa: E402

GAINS = [700, 800, 900, 1000, 1100, 1200, 1200, 1100, 1000, 900, 800, 700,
         1200, 1100, 1000, 900, 800, 700, 1200, 1100, 1000, 900, 800, 700]
CHS = [3] * 6 + [7] * 6 + [4] * 6 + [8] * 6


def write_summary(folder):
    """Workbook shaped like Test 015's Summary.xlsx."""
    s1 = pd.DataFrame({"Run": list(range(1, 25)) + [25], "Flow cell": ["Flow cell#1"] + [None] * 24,
                       "CH": CHS + [None], "Conditions": [None] * 25, "LED power": [None] * 25,
                       "PMT gain": GAINS + [1200], "Points": [60001] * 24 + [None]})
    s2 = pd.DataFrame({"CH": [3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
                       "Unnamed: 1": ["Negative control"] * 4 + ["Positive control"] * 6})
    path = os.path.join(folder, "Summary.xlsx")
    with pd.ExcelWriter(path) as w:
        s1.to_excel(w, sheet_name="Sheet1", index=False)
        s2.to_excel(w, sheet_name="Sheet2", index=False)
    return path


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.xlsx = write_summary(self.tmp.name)
        self.sub = os.path.join(self.tmp.name, "Luciferace 2026-11-17")
        os.makedirs(self.sub)
        self.p6 = os.path.join(self.sub, "run_006.csv")
        self.p7 = os.path.join(self.sub, "run_007.csv")
        self.sheets = ri.read_workbook(self.xlsx)

    def test_guess_choice_summary_shape(self):
        c = ri.guess_choice(self.sheets)
        self.assertEqual((c.sheet, c.run_col, c.gain_col, c.sample_col), ("Sheet1", "Run", "PMT gain", "CH"))
        self.assertEqual(c.lookup, ("Sheet2", "CH", "Unnamed: 1"))

    def test_preview_roles_and_gains(self):
        c = ri.guess_choice(self.sheets)
        rows = {r.run: r for r in ri.build_preview(self.sheets, c, self.xlsx, [(self.p6, 6), (self.p7, 7)])}
        self.assertEqual((rows[6].gain, rows[6].label, rows[6].role, rows[6].status, rows[6].ticked),
                         (1200.0, "Negative control", "negative", "ok", True))
        self.assertEqual((rows[7].gain, rows[7].role, rows[7].path), (1200.0, "positive", self.p7))
        self.assertEqual(rows[1].status, "no loaded trace")
        self.assertFalse(rows[1].ticked)
        self.assertEqual(rows[25].label, "")                     # no CH -> no label

    def test_preview_conflict(self):
        other = os.path.join(self.tmp.name, "other", "run_007.csv")
        c = ri.guess_choice(self.sheets)
        rows = {r.run: r for r in ri.build_preview(self.sheets, c, self.xlsx, [(self.p7, 7), (other, 7)])}
        self.assertEqual(rows[7].status, "conflict: 2 files")
        self.assertFalse(rows[7].ticked)

    def test_trace_outside_folder_not_matched(self):
        outside = os.path.join(os.path.dirname(self.tmp.name), "elsewhere", "run_006.csv")
        c = ri.guess_choice(self.sheets)
        rows = {r.run: r for r in ri.build_preview(self.sheets, c, self.xlsx, [(outside, 6)])}
        self.assertEqual(rows[6].status, "no loaded trace")

    def test_gain_not_a_number(self):
        df = self.sheets["Sheet1"].copy()
        df["PMT gain"] = df["PMT gain"].astype(object)
        df.loc[5, "PMT gain"] = "high"
        sheets = dict(self.sheets, Sheet1=df)
        c = ri.guess_choice(sheets)
        rows = {r.run: r for r in ri.build_preview(sheets, c, self.xlsx, [(self.p6, 6)])}
        self.assertEqual(rows[6].status, "gain not a number")
        self.assertFalse(rows[6].ticked)

    def test_run_cells_as_text_and_float(self):
        df = self.sheets["Sheet1"].copy()
        df["Run"] = df["Run"].astype(object)
        df.loc[5, "Run"] = "6"
        df.loc[6, "Run"] = 7.0
        sheets = dict(self.sheets, Sheet1=df)
        rows = {r.run: r for r in ri.build_preview(sheets, ri.guess_choice(sheets), self.xlsx,
                                                   [(self.p6, 6), (self.p7, 7)])}
        self.assertEqual((rows[6].status, rows[7].status), ("ok", "ok"))

    def test_apply_and_round_trip(self):
        c = ri.guess_choice(self.sheets)
        rows = ri.build_preview(self.sheets, c, self.xlsx, [(self.p6, 6), (self.p7, 7)])
        store = {}
        self.assertEqual(ri.apply_preview(store, rows), 2)
        path = os.path.join(self.tmp.name, "run_info.json")
        ri.save(path, store)
        back = ri.load(path)
        self.assertEqual(back, store)
        self.assertEqual(back[ri.file_key(self.p7)], ri.RunInfo("positive", "Positive control", 1200.0))

    def test_locked_workbook_message(self):
        with mock.patch.object(ri.pd, "read_excel", side_effect=PermissionError("locked")):
            with self.assertRaises(ri.ImportFailed) as cm:
                ri.read_workbook(self.xlsx)
        self.assertEqual(str(cm.exception), "The file is locked. Close it in Excel and try again.")


class StoreTests(unittest.TestCase):
    def test_corrupt_store_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "run_info.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(ri.load(p), {})
            with open(p, "w") as f:
                f.write('{"a": {"role": 5}}')
            self.assertEqual(ri.load(p), {})
            self.assertEqual(ri.load(os.path.join(d, "missing.json")), {})

    def test_effective_gain_precedence(self):
        meta = {"PMT_gain_mV": "700"}
        self.assertEqual(ri.effective_gain(ri.RunInfo(gain_mv=1200.0), meta), (1200.0, "override"))
        self.assertEqual(ri.effective_gain(None, meta), (700.0, "file"))
        self.assertEqual(ri.effective_gain(ri.RunInfo(), {"pmt_gain_mv": "1000 mV"}), (1000.0, "file"))
        self.assertEqual(ri.effective_gain(None, {}), (None, None))

    def test_role_from_label(self):
        self.assertEqual(ri.role_from_label("Negative control"), "negative")
        self.assertEqual(ri.role_from_label("POSITIVE"), "positive")
        self.assertEqual(ri.role_from_label("blank"), "")
        self.assertEqual(ri.role_from_label("Nagative Control"), "negative")      # typo in the real Summary.xlsx
        self.assertEqual(ri.role_from_label("Postive control"), "positive")
        self.assertEqual(ri.role_from_label("Positve"), "positive")
        self.assertEqual(ri.role_from_label("CH3"), "")

    def test_file_key_normalised(self):
        self.assertEqual(ri.file_key("C:/A/b.csv"), ri.file_key(r"c:\a\B.csv"))


if __name__ == "__main__":
    unittest.main()
