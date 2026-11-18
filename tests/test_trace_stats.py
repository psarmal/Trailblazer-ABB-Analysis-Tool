"""Run: python -m unittest discover -s tests   (from the Analysis tool folder)"""
import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as ref  # noqa: E402
import trace_store as ts  # noqa: E402

DT = 2e-6


def make_run(n, seed=0, jitter=False):
    rng = np.random.default_rng(seed)
    mv = np.round(rng.normal(98, 2.7, n), 4)
    if n >= 10:
        for i in (n // 7, n // 2, n - 3):
            mv[i] = 1972.7706
    if jitter:
        t = np.cumsum(rng.uniform(1e-6, 3e-6, n))
    else:
        t = np.round(np.arange(n) * DT, 6)   # decimal text, as the instrument writes it
    return t, mv


def assert_stats_equal(tc, got, want):
    tc.assertEqual(set(want), {k for k in got if k != "RMS"})
    tc.assertEqual(got["Points"], want["Points"])
    for k in ("Max", "Min", "Mean", "Std", "Time of max", "Time of min"):
        g, w = got[k], want[k]
        if isinstance(w, float) and math.isnan(w):
            tc.assertTrue(math.isnan(g), (k, g))
        elif k.startswith("Time"):
            tc.assertEqual(g, w, k)
        elif k in ("Max", "Min"):
            tc.assertEqual(g, w, k)
        else:
            tc.assertLessEqual(abs(g - w), 1e-12 * max(1, abs(w)), (k, g, w))


class TimeExactTest(unittest.TestCase):
    def test_text_grid_times_and_time_of_extremes_are_exact(self):
        n = 20_000
        t = np.array([float(f"{i * DT:.6f}") for i in range(n)])   # what parsing the CSV gives
        _, mv = make_run(n)
        td = ts.TraceData.from_arrays(t, mv)
        self.assertIsNotNone(td.dt)
        self.assertTrue(np.array_equal(td.times(0, n), t))
        self.assertTrue(np.array_equal(td.times_at(np.arange(n)), t))
        for i, j in ((0, n), (5, 9000), (4096, 8192)):
            got = td.stats(t[i], t[j - 1])
            want = ref.window_stats(t, mv, t[i], t[j - 1])
            self.assertEqual((got["Time of max"], got["Time of min"]), (want["Time of max"], want["Time of min"]))

    def test_uniform_but_not_decimal_time_is_stored_explicitly(self):
        t = np.arange(1000) * DT            # no short decimal form
        td = ts.TraceData.from_arrays(t, make_run(1000)[1])
        self.assertIsNone(td.dt)
        self.assertTrue(np.array_equal(td.times(0, 1000), t))


class EncodeTest(unittest.TestCase):
    def test_encode_roundtrip_4_decimals(self):
        mv = np.array([-0.8012, 1972.7706, 0.0])
        codes, scale = ts.encode_values(mv)
        self.assertEqual(codes.dtype, np.int32)
        self.assertEqual(scale, 1e4)
        self.assertTrue(np.array_equal(codes / scale, mv))

    def test_encode_falls_back_to_float64(self):
        codes, scale = ts.encode_values(np.array([0.1234567891]))
        self.assertEqual(codes.dtype, np.float64)
        self.assertIsNone(scale)

    def test_uniform_step(self):
        t = np.arange(10) * 2e-6
        self.assertEqual(ts.uniform_step(t), 2e-6)
        t[4] += 1e-9
        self.assertIsNone(ts.uniform_step(t))
        self.assertIsNone(ts.uniform_step(t[:1]))


class StatsTest(unittest.TestCase):
    def check_window(self, td, t, mv, t0, t1):
        for pop in (True, False):
            got = td.stats(t0, t1, population=pop)
            assert_stats_equal(self, got, ref.window_stats(t, mv, t0, t1, population=pop))

    def windows(self, t, rng):
        n = len(t)
        span = t[-1] - t[0]
        w = [(t[0], t[-1]), (t[0] - 1, t[-1] + 1)]
        for _ in range(500):
            a, b = sorted(rng.uniform(t[0] - 0.01 * span, t[-1] + 0.01 * span, 2))
            w.append((a, b))
        for _ in range(50):
            i, j = sorted(rng.integers(0, n, 2))
            w.append((t[i], t[j]))
        w.append((t[100], t[2000]))                      # inside one block
        w.append((t[4000], t[4200]))                     # across one block edge
        w.append((t[4096], t[8191]))                     # exactly one block
        w.append((t[4095], t[8192]))
        return w

    def run_random(self, jitter):
        t, mv = make_run(50_000, jitter=jitter)
        td = ts.TraceData.from_arrays(t, mv)
        self.assertEqual(td.n, len(t))
        rng = np.random.default_rng(1)
        for t0, t1 in self.windows(t, rng):
            self.check_window(td, t, mv, t0, t1)

    def test_stats_match_reference_random_windows(self):
        self.run_random(False)

    def test_stats_nonuniform_time(self):
        self.run_random(True)

    def test_short_runs(self):
        for n in (1, 63, 4095, 4097):
            t, mv = make_run(n)
            td = ts.TraceData.from_arrays(t, mv)
            self.check_window(td, t, mv, t[0], t[-1])
            self.check_window(td, t, mv, t[n // 3], t[(2 * n) // 3])

    def test_empty_and_outside_windows(self):
        t, mv = make_run(1000)
        for jitter_t in (t, np.cumsum(np.full(1000, 2e-6)) + 1e-9 * np.arange(1000) ** 1.5):
            td = ts.TraceData.from_arrays(jitter_t, mv)
            for t0, t1 in ((-1.0, jitter_t[0] - 1e-7), (jitter_t[-1] + 1e-7, 1.0),
                           (jitter_t[10] + 5e-7, jitter_t[10] + 1.5e-6)):
                got = td.stats(t0, t1)
                self.assertEqual(got["Points"], 0)
                for k in ("Max", "Min", "Mean", "Std", "Time of max", "Time of min", "RMS"):
                    self.assertTrue(math.isnan(got[k]), k)
                assert_stats_equal(self, got, ref.window_stats(jitter_t, mv, t0, t1))

    def test_rms(self):
        t, mv = make_run(20_000)
        td = ts.TraceData.from_arrays(t, mv)
        for i, j in ((0, 20_000), (5, 9000), (4096, 8192)):
            seg = mv[i:j]
            want = math.sqrt(float(np.mean(seg ** 2)))
            got = td.stats(t[i], t[j - 1])["RMS"]
            self.assertLessEqual(abs(got - want), 1e-12 * want)


if __name__ == "__main__":
    unittest.main()
