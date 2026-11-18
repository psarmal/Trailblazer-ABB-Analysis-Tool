"""Run: python -m unittest discover -s tests   (from the Analysis tool folder)"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import multi_file_analyzer as ref  # noqa: E402
import trace_store as ts  # noqa: E402
from test_trace_stats import make_run  # noqa: E402

N = 2_000_000


class ViewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t, cls.mv = make_run(N)
        cls.td = ts.TraceData.from_arrays(cls.t, cls.mv)

    def full(self, **kw):
        return self.td.view(self.t[0], self.t[-1], **kw)

    def test_overviews_built(self):
        self.assertEqual(sorted(self.td.overviews), list(ts.OV_LEVELS))
        ov = ts.build_overview(np.array([3.0, 1.0, 2.0, 9.0, 0.0]), 2)
        self.assertEqual(ov["vmin"].tolist(), [1.0, 2.0, 0.0])
        self.assertEqual(ov["vmax"].tolist(), [3.0, 9.0, 0.0])
        self.assertEqual(ov["imin"].tolist(), [1, 2, 4])
        self.assertEqual(ov["imax"].tolist(), [0, 3, 4])

    def test_raw_when_few_points_visible(self):
        x0, x1 = self.t[300_000], self.t[304_999]
        t, mv, raw = self.td.view(x0, x1)
        rt, rmv, rraw = ref.view_points(self.t, self.mv, x0, x1)
        self.assertTrue(raw and rraw)
        self.assertEqual(len(t), len(rt))
        self.assertTrue(np.array_equal(t, rt))
        self.assertTrue(np.array_equal(mv, rmv))

    def test_reduced_keeps_every_spike(self):
        t, mv, raw = self.full(bins=3000)
        rt, rmv, _ = ref.view_points(self.t, self.mv, self.t[0], self.t[-1], bins=3000)
        self.assertFalse(raw)
        for i in (N // 7, N // 2, N - 3):
            j = np.flatnonzero(np.abs(t - self.t[i]) < 1e-9)
            self.assertTrue(len(j) and (mv[j] == 1972.7706).all(), i)
        self.assertEqual(mv.max(), rmv.max())
        self.assertEqual(mv.min(), rmv.min())

    def test_reduced_is_time_ordered(self):
        for x0, x1 in ((self.t[0], self.t[-1]), (self.t[12345], self.t[1_234_567])):
            t, _, raw = self.td.view(x0, x1)
            self.assertFalse(raw)
            self.assertTrue(np.all(np.diff(t) >= 0))

    def test_reduced_point_count_bounded(self):
        for bins in (400, 3000):
            t, mv, _ = self.full(bins=bins)
            self.assertLessEqual(len(t), 2 * bins + 4)
            self.assertEqual(len(t), len(mv))

    def test_narrow_reduced_uses_raw_slices(self):
        # k < 64: no overview level applies, result equals the reference
        x0, x1 = self.t[10_000], self.t[10_000 + 100_000]
        t, mv, raw = self.td.view(x0, x1, bins=3000)
        rt, rmv, _ = ref.view_points(self.t, self.mv, x0, x1, bins=3000)
        self.assertFalse(raw)
        self.assertTrue(np.array_equal(mv, rmv))
        self.assertTrue(np.array_equal(t, rt))

    def test_each_level_matches_reference_extremes(self):
        for bins in (100, 600, 3000):
            for x0, x1 in ((self.t[0], self.t[-1]), (self.t[777_777], self.t[1_900_001])):
                t, mv, _ = self.td.view(x0, x1, bins=bins)
                _, rmv, _ = ref.view_points(self.t, self.mv, x0, x1, bins=bins)
                self.assertEqual((mv.max(), mv.min()), (rmv.max(), rmv.min()))

    def test_nonuniform_time(self):
        t, mv = make_run(300_000, jitter=True)
        td = ts.TraceData.from_arrays(t, mv)
        gt, gmv, raw = td.view(t[0], t[-1])
        rt, rmv, _ = ref.view_points(t, mv, t[0], t[-1])
        self.assertFalse(raw)
        self.assertEqual((gmv.max(), gmv.min()), (rmv.max(), rmv.min()))
        self.assertTrue(np.all(np.diff(gt) >= 0))

    def test_short_runs_view(self):
        for n in (1, 63, 4095):
            t, mv = make_run(n)
            td = ts.TraceData.from_arrays(t, mv)
            gt, gmv, graw = td.view(t[0], t[-1])
            rt, rmv, rraw = ref.view_points(t, mv, t[0], t[-1])
            self.assertEqual(graw, rraw)
            self.assertLessEqual(np.abs(gt - rt).max(), 1e-12)
            self.assertTrue(np.array_equal(gmv, rmv))


if __name__ == "__main__":
    unittest.main()
