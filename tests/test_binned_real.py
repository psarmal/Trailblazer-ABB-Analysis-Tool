"""Binning on real Test 015 runs (skipped unless the files are local, not cloud-only).
Run: python -m unittest tests.test_binned_real -v"""
import gc
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binning  # noqa: E402
import multi_file_analyzer as m  # noqa: E402
import trace_store as ts  # noqa: E402
from test_histogram_real import RUN6, RUN7, _local  # noqa: E402


@unittest.skipUnless(_local(RUN6) and _local(RUN7), "Test 015 runs not available locally")
class RealBinnedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bbcache_bin_")
        cls.tr = {n: ts.open_traces(p, m.parse_file, cls.tmp)[0][0]["data"] for n, p in (("r6", RUN6), ("r7", RUN7))}

    @classmethod
    def tearDownClass(cls):
        cls.tr = None
        gc.collect()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_run7_light_rises_after_injection(self):
        c, mns = binning.bin_means(self.tr["r7"], 0.5)
        late = mns[(c >= 20) & (c <= 60)]
        self.assertTrue(20 <= late.mean() <= 35, late.mean())
        self.assertTrue(np.all(mns[c < 2.5] < 6), mns[c < 2.5])

    def test_run6_stays_dark(self):
        _, mns = binning.bin_means(self.tr["r6"], 0.5)
        self.assertTrue(np.all(mns < 2), mns)


if __name__ == "__main__":
    unittest.main()
