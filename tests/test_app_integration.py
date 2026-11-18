"""Run: python -m unittest discover -s tests   (from the Analysis tool folder)"""
import gc
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as m  # noqa: E402
import trace_store as ts  # noqa: E402

N = 20000


def write_csv(path, run, seed):
    mv = np.round(np.random.default_rng(seed).normal(98, 2.7, N), 4)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# run {run}\n# experiment app test\n# sample_rate_hz 500000\n")
        f.write("time_s,value_mV\n")
        for i, v in enumerate(mv):
            f.write(f"{i * 2e-6:.6f},{v:.4f}\n")
        f.write("# peak_mV 12.5\n")


class AppIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.paths = []
        for run in (3, 1, 2):          # the order a file dialog might give
            p = os.path.join(self.tmp.name, f"run_{run:03d}.csv")
            write_csv(p, run, run)
            self.paths.append(p)
        self.apps = []

    def cleanup(self):
        self.close_apps()
        gc.collect()
        self.tmp.cleanup()

    def close_apps(self):
        for a in self.apps:
            a.update()               # let pending idle redraws run before the window goes
            a._lines.clear()         # the chart and the file list hold the memory-mapped traces
            a.files.clear()
            a._result_files.clear()
            a._res_rows.clear()
            a.destroy()
        self.apps.clear()

    def make_app(self):
        app = m.App(settings_path=os.path.join(self.tmp.name, "settings.json"))
        self.apps.append(app)
        app.cache_dir = self.cache
        app.texts = []
        orig = app.status.config

        def spy(*a, **kw):
            if "text" in kw:
                app.texts.append(kw["text"])
            return orig(*a, **kw)
        app.status.config = spy
        return app

    def load(self, app):
        app.load_paths(self.paths)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            app.update()
            if not app._loading:
                return
            time.sleep(0.01)
        self.fail("loading did not finish")

    def test_load_calculate_draw(self):
        app = self.make_app()
        self.load(app)
        self.assertEqual([m.run_number(f) for f in app.files], [1, 2, 3])
        f0 = app.files[0]
        self.assertEqual(f0["n"], N)
        self.assertNotIn("t", f0)
        self.assertNotIn("mv", f0)
        self.assertTrue(any("Converting" in t for t in app.texts), app.texts)
        app.t0_var.set("0.01"); app.t1_var.set("0.03")
        app.calculate()
        for row, f in zip(app.results.to_dict("records"), app.files):
            p = m.parse_file(f["path"])[0]
            ref = m.window_stats(p["t"], p["mv"], 0.01, 0.03)
            self.assertEqual(row["Points"], ref["Points"])
            self.assertEqual(row["Max"], ref["Max"])
            self.assertEqual(row["Min"], ref["Min"])
            self.assertAlmostEqual(row["Mean"], ref["Mean"], delta=1e-12 * max(1, abs(ref["Mean"])))
            self.assertAlmostEqual(row["Std"], ref["Std"], delta=1e-12 * max(1, abs(ref["Std"])))
        self.assertNotIn("RMS", app.results.columns)
        app.draw()
        app._refresh_view()

    def test_window_export_reproduces_the_source(self):
        app = self.make_app()
        self.load(app)
        out, total = app._window_data(app.files, 0.0, 1.0)
        self.assertEqual(total, N * 3)
        for k, f in enumerate(app.files):
            p = m.parse_file(f["path"])[0]
            lab = app._label(f)
            self.assertTrue(np.array_equal(out[f"{lab} time_s"].to_numpy(), p["t"]))
            self.assertTrue(np.array_equal(out[f"{lab} mV"].to_numpy(), p["mv"]))
        del f, p, out

    def test_non_finite_window_rejected(self):
        app = self.make_app()
        app.t0_var.set("0"); app.t1_var.set("inf")
        self.assertIsNone(app._get_range(quiet=True))
        app.t0_var.set("nan"); app.t1_var.set("1")
        self.assertIsNone(app._get_range(quiet=True))
        app.t0_var.set("0"); app.t1_var.set("1")
        self.assertEqual(app._get_range(quiet=True), (0.0, 1.0))

    def test_second_session_opens_cached(self):
        app = self.make_app()
        self.load(app)
        self.close_apps()            # like closing the program and opening it again
        app2 = self.make_app()
        self.load(app2)
        self.assertTrue(any("Opening cached" in t for t in app2.texts), app2.texts)
        self.assertEqual([f["n"] for f in app2.files], [N] * 3)

    def test_zero_byte_cache_file_does_not_hang_loading(self):
        app = self.make_app()
        self.load(app)
        self.close_apps()
        gc.collect()
        victim = self.paths[0]
        entry = os.path.join(self.cache, ts.entry_key(victim))
        open(os.path.join(entry, "0", "mv.npy"), "wb").close()
        app2 = self.make_app()
        self.load(app2)              # fails the test if _loading never clears
        self.assertFalse(app2._loading)
        self.assertEqual([f["n"] for f in app2.files], [N] * 3)
        self.assertEqual(str(app2.add_btn.cget("state")), "normal")

    def test_unexpected_error_in_worker_still_finishes(self):
        app = self.make_app()
        with mock.patch.object(m, "is_cached", side_effect=RuntimeError("boom")):
            self.load(app)
        self.assertFalse(app._loading)


if __name__ == "__main__":
    unittest.main()
