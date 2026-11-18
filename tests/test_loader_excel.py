"""CSV files re-saved by Excel (quoted comment lines, trailing commas, extra columns, #DIV/0!).
Run: python -m unittest tests.test_loader_excel -v"""
import glob
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import multi_file_analyzer as m  # noqa: E402
import trace_store as ts  # noqa: E402

N = 2000
LSB = 0.1525


def _t_text(i):
    return repr(round(i * 1e-4, 4)) if i else "0"     # Excel writes the shortest form: 0, 0.0001, ...


def write_excel_style(path):
    rng = np.random.default_rng(3)
    mv = np.round(rng.integers(-20, 200, N) * LSB, 4)
    lines = ['"# BB DAQ run 7 · experiment ""Luciferace"" · started 2026-11-17 14:55:49",,,,',
             "# sample_rate_hz 10000,,,,",
             "# pmt_gain_mv 700,,,,",
             "time_s,value_mV,,value_mV,"]
    for i in range(N):
        c = f"{i * 0.01:.3f}" if i % 3 == 0 else ""
        e = "0.36314" if i < N // 2 else "#DIV/0!"
        lines.append(f"{_t_text(i)},{mv[i]:.4f},{c},{-mv[i]:.4f},{e}")
    lines += ['"# mean_mv 24.6334",,#DIV/0!,,#DIV/0!', "# stopped_early no,,#DIV/0!,,#DIV/0!"]
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return np.array([float(_t_text(i)) for i in range(N)]), np.array([float(f"{v:.4f}") for v in mv])


class ExcelResaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_excel_resaved_loads(self):
        p = os.path.join(self.tmp.name, "run_007.csv")
        t, mv = write_excel_style(p)
        d = m.parse_instrument_csv(p)
        self.assertEqual(len(d["t"]), N)
        np.testing.assert_array_equal(d["t"], t)
        np.testing.assert_array_equal(d["mv"], mv)
        self.assertEqual(d["meta"]["run"], "7")
        self.assertEqual(d["meta"]["experiment"], "Luciferace")
        self.assertEqual(d["meta"]["sample_rate_hz"], "10000")
        self.assertEqual(d["meta"]["pmt_gain_mv"], "700")
        self.assertEqual(d["summary"]["mean_mv"], "24.6334")
        self.assertEqual(d["summary"]["stopped_early"], "no")
        self.assertAlmostEqual(ts.uniform_step(d["t"]), 1e-4, delta=1e-12)

    def test_clean_files_match_direct_read(self):
        files = glob.glob(os.path.join(os.path.dirname(HERE), "simulated_data", "**", "*.csv"), recursive=True)
        self.assertTrue(files)
        for p in files:
            d = m.parse_instrument_csv(p)
            raw = pd.read_csv(p, comment="#", usecols=[0, 1], dtype="float64").dropna()
            np.testing.assert_array_equal(d["t"], raw.iloc[:, 0].to_numpy(), err_msg=p)
            np.testing.assert_array_equal(d["mv"], raw.iloc[:, 1].to_numpy(), err_msg=p)

    def test_unquote_line(self):
        self.assertEqual(m._unquote_line('"# a ""b"" c",,,'), '# a "b" c')
        self.assertEqual(m._unquote_line("# x 1,,,,"), "# x 1")
        self.assertEqual(m._unquote_line("# plain"), "# plain")
        self.assertEqual(m._unquote_line("  # spaced ,, "), "# spaced")


if __name__ == "__main__":
    unittest.main()
