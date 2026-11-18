"""Real Test 015 luciferase data (skipped unless the files are local, not cloud-only).
Run: python -m unittest tests.test_histogram_real -v"""
import gc
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as m  # noqa: E402
import noise  # noqa: E402
import run_info  # noqa: E402
import trace_store as ts  # noqa: E402

TEST = (r"C:\Users\MannaDoud\Sarmal\Project Trailblazer - Engineering\Bread Board"
        r"\Bread Board Experiments and Test\Test 015 LLC in solution\261117 Second test")
FOLDER = os.path.join(TEST, "Luciferace 2026-11-17")
RUN6 = os.path.join(FOLDER, "run_006.csv")
RUN7 = os.path.join(FOLDER, "run_007.csv")
SUMMARY = os.path.join(TEST, "Summary.xlsx")
THR = 5.101


def _local(p):
    return os.path.exists(p) and not ts.is_cloud_only(p)


def _copy(src, dst):
    """Copy even while Excel has the workbook open (Python's open is refused then)."""
    try:
        shutil.copyfile(src, dst)
        return True
    except PermissionError:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Copy-Item -LiteralPath '{src}' -Destination '{dst}'"], capture_output=True)
        return r.returncode == 0 and os.path.exists(dst)


@unittest.skipUnless(_local(RUN6) and _local(RUN7), "Test 015 runs not available locally")
class RealHistogramTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bbcache_hist_")
        cls.tr = {name: ts.open_traces(p, m.parse_file, cls.tmp)[0][0]["data"] for name, p in (("r6", RUN6), ("r7", RUN7))}

    @classmethod
    def tearDownClass(cls):
        cls.tr = None
        gc.collect()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rate(self, name, a, b):
        tr = self.tr[name]
        sp = noise.find_spikes_at(tr, a, b, THR)
        return len(sp.times), len(sp.times) / sp.duration

    def test_run7_loads(self):
        tr = self.tr["r7"]
        self.assertEqual(tr.n, 600_064)
        self.assertAlmostEqual(tr.dt, 1e-4, delta=1e-12)

    def test_rates(self):
        n6, _ = self.rate("r6", self.tr["r6"].t_start, self.tr["r6"].t_end)
        self.assertEqual(n6, 411)
        _, after = self.rate("r7", 4.0, 60.0)
        self.assertAlmostEqual(after, 1423.0, delta=0.01 * 1423)
        _, before = self.rate("r7", 0.0, 2.8)
        self.assertAlmostEqual(before, 252.0, delta=0.02 * 252)

    @unittest.skipUnless(os.path.exists(SUMMARY), "Summary.xlsx not available")
    def test_summary_import(self):
        dst = os.path.join(self.tmp, "Summary.xlsx")
        if not _copy(SUMMARY, dst):
            self.skipTest("Summary.xlsx couldn't be copied")
        sheets = run_info.read_workbook(dst)
        choice = run_info.guess_choice(sheets)
        rows = {r.run: r for r in run_info.build_preview(sheets, choice, SUMMARY, [(RUN6, 6), (RUN7, 7)])}
        self.assertEqual((rows[6].gain, rows[6].role, rows[6].status), (1200.0, "negative", "ok"))
        self.assertEqual((rows[7].gain, rows[7].role, rows[7].status), (1200.0, "positive", "ok"))


if __name__ == "__main__":
    unittest.main()
