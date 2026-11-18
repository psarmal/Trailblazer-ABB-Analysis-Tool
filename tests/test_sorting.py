"""Run: python -m unittest discover -s tests   (from the Analysis tool folder)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as m  # noqa: E402


def trace(run=None, file="x.csv", folder="f", load_idx=0, name=None):
    meta = {"run": str(run)} if run is not None else {}
    return {"meta": meta, "summary": {}, "file": file, "folder": folder, "load_idx": load_idx,
            "name": name or (f"run {run}" if run is not None else os.path.splitext(file)[0])}


def runs(files):
    return [m.run_number(f) for f in files]


class RunNumberTest(unittest.TestCase):
    def test_from_metadata(self):
        self.assertEqual(m.run_number(trace(run=12)), 12)

    def test_from_file_name_when_no_metadata(self):
        self.assertEqual(m.run_number(trace(file="run_007.csv")), 7)

    def test_none_when_no_number_anywhere(self):
        self.assertIsNone(m.run_number(trace(file="baseline.csv")))


class SortFilesTest(unittest.TestCase):
    def setUp(self):
        # Windows' file dialog order from the bug report: 6 7 8 1 2 3 4 5
        self.files = [trace(run=r, load_idx=i) for i, r in enumerate([6, 7, 8, 1, 2, 3, 4, 5])]

    def test_ascending(self):
        self.assertEqual(runs(m.sorted_files(self.files, "Run # ↑")), [1, 2, 3, 4, 5, 6, 7, 8])

    def test_descending(self):
        self.assertEqual(runs(m.sorted_files(self.files, "Run # ↓")), [8, 7, 6, 5, 4, 3, 2, 1])

    def test_numeric_not_text(self):
        files = [trace(run=r, load_idx=i) for i, r in enumerate([10, 9, 100, 2])]
        self.assertEqual(runs(m.sorted_files(files, "Run # ↑")), [2, 9, 10, 100])

    def test_load_order(self):
        self.assertEqual(runs(m.sorted_files(self.files, "Load order")), [6, 7, 8, 1, 2, 3, 4, 5])

    def test_unnumbered_last_in_both_directions(self):
        files = self.files + [trace(file="baseline.csv", load_idx=99)]
        self.assertIsNone(m.run_number(m.sorted_files(files, "Run # ↑")[-1]))
        self.assertIsNone(m.run_number(m.sorted_files(files, "Run # ↓")[-1]))

    def test_same_run_in_two_folders_keeps_both_grouped_by_folder(self):
        files = [trace(run=1, folder="b", load_idx=0), trace(run=2, folder="a", load_idx=1),
                 trace(run=1, folder="a", load_idx=2)]
        got = [(m.run_number(f), f["folder"]) for f in m.sorted_files(files, "Run # ↑")]
        self.assertEqual(got, [(1, "a"), (1, "b"), (2, "a")])

    def test_does_not_modify_input(self):
        before = list(self.files)
        m.sorted_files(self.files, "Run # ↑")
        self.assertEqual(self.files, before)


if __name__ == "__main__":
    unittest.main()
