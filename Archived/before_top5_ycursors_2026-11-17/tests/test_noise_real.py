"""Real-data noise checks (skipped unless run_001.csv and run_006.csv are local, not cloud-only)."""
import gc
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as ref  # noqa: E402
import noise  # noqa: E402
import trace_store as ts  # noqa: E402

FOLDER = (r"C:\Users\MannaDoud\Sarmal\Project Trailblazer - Engineering\Bread Board"
          r"\Bread Board Experiments and Test\Test 008_ABB Noise Identification\620nm Config")
RUN1 = os.path.join(FOLDER, "run_001.csv")
RUN6 = os.path.join(FOLDER, "run_006.csv")
WATCH = [60, 120, 15550, 112440]
RUN6_RATE = 11.93  # spikes per second at k = 10 over the full run (measured, see spec section 9)


def _local(p):
    return os.path.exists(p) and not ts.is_cloud_only(p)


@unittest.skipUnless(_local(RUN1) and _local(RUN6), "real run_001/run_006 not available locally")
class RealNoiseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bbcache_noise_")
        cls.traces = {}
        for name, p in (("r1", RUN1), ("r6", RUN6)):
            cls.traces[name] = ts.open_traces(p, ref.parse_file, cls.tmp)[0][0]["data"]

    @classmethod
    def tearDownClass(cls):
        cls.traces = None
        gc.collect()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def full(self, name):
        tr = self.traces[name]
        return noise.analyze(tr, tr.t_start, tr.t_end, WATCH, 10, 2)

    def test_run001_mains_peak(self):
        r = self.full("r1")
        self.assertAlmostEqual(r.peaks[0][0], 60.0, delta=1.0)

    def test_run006_peak_and_rate(self):
        r = self.full("r6")
        self.assertTrue(any(abs(f - 193780) <= 50 for f, _ in r.peaks), r.peaks)
        self.assertAlmostEqual(r.rate, RUN6_RATE, delta=0.05 * RUN6_RATE)


if __name__ == "__main__":
    unittest.main()
