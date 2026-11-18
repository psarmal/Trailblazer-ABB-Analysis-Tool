"""Real-data check of the trace cache against the reference code (skipped without the file)."""
import math
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as ref  # noqa: E402
import trace_store as ts  # noqa: E402

REAL = (r"C:\Users\MannaDoud\Sarmal\Project Trailblazer - Engineering\Bread Board"
        r"\Bread Board Experiments and Test\Test 008_ABB Noise Identification\620nm Config\run_001.csv")


@unittest.skipUnless(os.path.exists(REAL) and not ts.is_cloud_only(REAL), "real run_001.csv not available locally")
class RealRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bbcache_real_")
        cls.parsed = ref.parse_file(REAL)[0]
        traces, status = ts.open_traces(REAL, ref.parse_file, cls.tmp)
        cls.status = status
        cls.data = traces[0]["data"]

    @classmethod
    def tearDownClass(cls):
        cls.data = None
        import gc
        gc.collect()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, t0, t1):
        t, mv = self.parsed["t"], self.parsed["mv"]
        exp = ref.window_stats(t, mv, t0, t1)
        got = self.data.stats(t0, t1)
        for k in ("Points", "Max", "Min", "Time of max", "Time of min"):
            if isinstance(exp[k], float) and math.isnan(exp[k]):
                self.assertTrue(math.isnan(got[k]), (k, t0, t1))
            else:
                self.assertEqual(got[k], exp[k], (k, t0, t1))
        for k in ("Mean", "Std"):
            if math.isnan(exp[k]):
                self.assertTrue(math.isnan(got[k]), (k, t0, t1))
            else:
                self.assertLessEqual(abs(got[k] - exp[k]), 1e-12 * max(1, abs(exp[k])), (k, t0, t1))

    def test_converted(self):
        self.assertEqual(self.status, "converted")
        self.assertEqual(self.data.n, len(self.parsed["t"]))

    def test_full_range(self):
        t = self.parsed["t"]
        self.check(float(t[0]), float(t[-1]))

    def test_random_windows(self):
        t = self.parsed["t"]
        rng = np.random.default_rng(1)
        for _ in range(200):
            a, b = sorted(rng.uniform(t[0], t[-1], 2))
            self.check(float(a), float(b))

    def test_view_full_range_extremes(self):
        t, mv = self.parsed["t"], self.parsed["mv"]
        raw_limit, bins = ref.draw_budget(160)
        et, ev, eraw = ref.view_points(t, mv, float(t[0]), float(t[-1]), raw_limit, bins)
        gt, gv, graw = self.data.view(float(t[0]), float(t[-1]), raw_limit, bins)
        self.assertEqual(graw, eraw)
        self.assertEqual(float(gv.max()), float(ev.max()))
        self.assertEqual(float(gv.min()), float(ev.min()))


if __name__ == "__main__":
    unittest.main()
