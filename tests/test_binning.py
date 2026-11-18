"""binning.py: bin means per run. Run: python -m unittest tests.test_binning -v"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binning  # noqa: E402
from trace_store import TraceData  # noqa: E402


def uniform(values, dt=1e-4, t0=0.0):
    v = np.asarray(values, dtype=float)
    t = np.round(t0 + np.arange(len(v)) * dt, 6)
    return TraceData.from_arrays(t, np.round(v, 4))


class BinMeansTest(unittest.TestCase):
    def test_uniform_exact_means(self):
        tr = uniform(np.arange(1000) % 10)
        self.assertIsNotNone(tr.dt)
        c, m = binning.bin_means(tr, 0.001)
        self.assertEqual(len(c), 100)
        np.testing.assert_allclose(m, 4.5)
        np.testing.assert_allclose(c, tr.t_start + (np.arange(100) + 0.5) * 0.001, atol=1e-12)

    def test_last_partial_bin(self):
        tr = uniform(np.arange(25, dtype=float))
        c, m = binning.bin_means(tr, 0.001)
        self.assertEqual(len(c), 3)
        np.testing.assert_allclose(m, [4.5, 14.5, 22.0])

    def test_run_shorter_than_bin(self):
        tr = uniform(np.arange(3000) * 0.01)                     # 0.3 s
        c, m = binning.bin_means(tr, 0.5)
        self.assertEqual(len(c), 1)
        self.assertAlmostEqual(c[0], 0.25)
        self.assertAlmostEqual(m[0], np.mean(np.round(np.arange(3000) * 0.01, 4)))

    def test_uneven_time_gap_dropped(self):
        rng = np.random.default_rng(0)
        t = np.concatenate([np.sort(rng.uniform(0, 1, 500)), np.sort(rng.uniform(3, 4, 500))])
        v = rng.normal(5, 1, 1000) + 1e-7                         # 7+ decimals: stored as float64
        tr = TraceData.from_arrays(t, v)
        self.assertIsNone(tr.dt)
        self.assertIsNone(tr.scale)
        c, m = binning.bin_means(tr, 0.5)
        edges = tr.t_start + np.arange(0, 10) * 0.5
        exp_c, exp_m = [], []
        for a, b in zip(edges[:-1], edges[1:]):
            sel = (t >= a) & (t < b)
            if sel.any():
                exp_c.append((a + b) / 2); exp_m.append(v[sel].mean())
        np.testing.assert_allclose(c, exp_c[:len(c)], atol=1e-12)
        np.testing.assert_allclose(m, exp_m[:len(m)], rtol=1e-12)
        self.assertEqual(len(c), len(exp_c))
        self.assertLess(len(c), int((tr.t_end - tr.t_start) / 0.5) + 1)   # the empty gap bins are gone

    def test_large_uniform_matches_reference(self):
        rng = np.random.default_rng(1)
        v = np.round(rng.normal(100, 2, 1_200_000), 4)
        tr = uniform(v, dt=2e-6)
        c, m = binning.bin_means(tr, 0.01)                        # 5,000 samples per bin
        ref = v[:240 * 5000].reshape(240, 5000).mean(axis=1)
        self.assertEqual(len(m), 240)
        np.testing.assert_allclose(m, ref, rtol=1e-12)


class CheckBinTest(unittest.TestCase):
    def test_check_bin(self):
        tr = uniform(np.zeros(20000))
        self.assertEqual(binning.check_bin(tr, 0.00005, "run_001.csv"), "Bin too small for run_001.csv (dt 0.0001 s).")
        self.assertIsNone(binning.check_bin(tr, 0.5, "run_001.csv"))
        long = uniform(np.zeros(10), dt=1.0)                      # 9 s
        self.assertIsNotNone(binning.check_bin(long, 9 / 200_001, "x.csv"))
        for bad in (0, -1, float("nan")):
            self.assertEqual(binning.check_bin(tr, bad, "run_001.csv"), "Bin size must be a positive number of seconds.")

    def test_sample_interval(self):
        self.assertAlmostEqual(binning.sample_interval(uniform(np.zeros(100))), 1e-4)
        t = np.array([0, 0.1, 0.3, 0.4, 0.5])
        self.assertAlmostEqual(binning.sample_interval(TraceData.from_arrays(t, np.zeros(5) + 1e-7)), 0.1)


if __name__ == "__main__":
    unittest.main()
