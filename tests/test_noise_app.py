"""Run: python -m unittest tests.test_noise_app -v   (from the Analysis tool folder)"""
import gc
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as m  # noqa: E402
import noise  # noqa: E402

N, DT = 20000, 1e-4
BASE_COLS = ["Trace", "Baseline (mV)", "Noise σ (mV)", "Threshold (mV)", "Spikes", "Rate (/s)",
             "Median spacing (s)", "Spacing rate (Hz)", "Slices"]
for _i in range(1, 6):
    BASE_COLS += [f"Peak {_i} (Hz)", f"Peak {_i} (mV RMS)", f"Peak {_i} seen"]
OUTDATED = "Window or settings changed — press Analyze noise to update."


def write_csv(path, run):
    rng = np.random.default_rng(run)
    mv = 98 + rng.normal(0, 1.0, N) + 3.0 * np.sin(2 * np.pi * 60 * np.arange(N) * DT)
    for i in range(1000 * run, N, 4000):
        mv[i] += 40 + run
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# run {run}\n# experiment noise app\n# sample_rate_hz 10000\ntime_s,value_mV\n")
        for i, v in enumerate(np.round(mv, 4)):
            f.write(f"{i * DT:.6f},{v:.4f}\n")


class NoiseAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.settings_path = os.path.join(self.tmp.name, "settings.json")
        with open(self.settings_path, "w") as f:
            json.dump({"watch_hz": [60.0], "spike_k": 10.0, "f_min_hz": 2.0}, f)
        self.paths = []
        for run in (1, 2, 3):
            p = os.path.join(self.tmp.name, f"run_{run:03d}.csv")
            write_csv(p, run)
            self.paths.append(p)
        self.apps = []

    def cleanup(self):
        for a in self.apps:
            a.update()
            a._lines.clear()
            a.files.clear()
            a.noise_rows.clear()
            a._result_files.clear()
            a._res_rows.clear()
            a.destroy()
        self.apps.clear()
        gc.collect()
        self.tmp.cleanup()

    def make_app(self):
        app = m.App(settings_path=self.settings_path)
        self.apps.append(app)
        app.cache_dir = self.cache
        app.load_paths(self.paths)
        self.wait(lambda: not app._loading)
        app.t0_var.set("0"); app.t1_var.set("2")
        return app

    def wait(self, cond):
        app = self.apps[-1]
        end = time.monotonic() + 60
        while time.monotonic() < end:
            app.update()
            if cond():
                return
            time.sleep(0.01)
        self.fail("timed out")

    def analyze(self, app):
        app.analyze_noise()
        self.wait(lambda: not app._noise_running)

    def test_analyze_fills_noise_tab(self):
        app = self.make_app()
        self.analyze(app)
        df = app.noise_table()
        self.assertEqual(list(df.columns), BASE_COLS + ["60 Hz (mV RMS)"])
        self.assertEqual(len(df), 3)
        self.assertFalse(app.noise_outdated)
        self.assertEqual(len(app.ntree.get_children()), 3)
        for (_, row), f in zip(df.iterrows(), app.files):
            ref = noise.analyze(f["data"], 0.0, 2.0, [60.0], 10.0, 2.0)
            self.assertEqual(row["Spikes"], ref.n_spikes)
            self.assertAlmostEqual(row["Baseline (mV)"], ref.baseline)
            self.assertAlmostEqual(row["Noise σ (mV)"], ref.sigma)
            self.assertAlmostEqual(row["60 Hz (mV RMS)"], ref.watch[60.0])
            self.assertAlmostEqual(row["Peak 1 (Hz)"], ref.peaks[0][0])
        self.assertAlmostEqual(df["60 Hz (mV RMS)"].iloc[0], 3.0 / np.sqrt(2), delta=0.3)

    def test_cancel_keeps_done_rows(self):
        app = self.make_app()
        real = noise.analyze
        calls = []

        def wrapped(*a, **kw):
            calls.append(1)
            r = real(*a, **kw)
            if len(calls) == 1:
                app.cancel_noise()
            return r
        with mock.patch.object(noise, "analyze", wrapped):
            self.analyze(app)
        self.assertEqual(len(app.noise_table()), 1)
        self.assertEqual(app.status.cget("text"), "Noise analysis cancelled (1 of 3 done).")

    def test_progress_text(self):
        app = self.make_app()
        texts = []
        orig = app.status.config
        app.status.config = lambda *a, **kw: (texts.append(kw.get("text")), orig(*a, **kw))[1]
        self.analyze(app)
        self.assertIn(f"Analyzing noise 1/3 · {app.files[0]['file']}", texts)

    def test_outdated_on_window_change(self):
        app = self.make_app()
        self.analyze(app)
        before = app.noise_table().copy()
        self.assertEqual(app.noise_banner.cget("text"), "")
        app.t0_var.set("0.5")
        self.assertTrue(app.noise_outdated)
        self.assertEqual(app.noise_banner.cget("text"), OUTDATED)
        self.assertTrue(app.noise_table().equals(before))
        self.analyze(app)
        self.assertFalse(app.noise_outdated)
        self.assertEqual(app.noise_banner.cget("text"), "")
        app.set_all_shown(False)
        self.assertTrue(app.noise_outdated)

    def test_rows_follow_sort_and_removal(self):
        app = self.make_app()
        self.analyze(app)
        names = list(app.noise_table()["Trace"])
        app.sort_var.set([s for s in m.SORT_MODES if "↓" in s and "Run" in s][0])
        app.apply_sort()
        self.assertEqual(list(app.noise_table()["Trace"]), names[::-1])
        self.assertFalse(app.noise_outdated)
        gone = app.files[0]
        app.ftree.selection_set("0")
        app.remove_selected()
        df = app.noise_table()
        self.assertEqual(len(df), 2)
        self.assertNotIn(app._label(gone), list(df["Trace"]))
        self.assertEqual(len(app.ntree.get_children()), 2)

    def test_disabled_while_loading(self):
        app = self.make_app()
        app._loading = True
        app.analyze_noise()
        self.assertFalse(app._noise_running)
        self.assertEqual(app.noise_rows, [])

    def test_noise_columns_top5_and_seen(self):
        app = self.make_app()
        self.analyze(app)
        cols = app._noise_columns()
        for c in ("Slices", "Peak 5 (Hz)", "Peak 5 seen"):
            self.assertIn(c, cols)
        self.assertEqual(cols.index("Peak 1 seen"), cols.index("Peak 1 (mV RMS)") + 1)
        df = app.noise_table()
        for (_, row), f in zip(df.iterrows(), app.files):
            ref = noise.analyze(f["data"], 0.0, 2.0, [60.0], 10.0, 2.0)
            self.assertEqual(row["Slices"], ref.n_slices)
            self.assertEqual(row["Peak 1 seen"], ref.seen[0])
        cells = app._noise_cells()[0]
        self.assertIsInstance(cells[cols.index("Slices")], int)
        self.assertIsNone(cells[cols.index("Peak 1 seen")])    # 2 s test runs = one slice

    def test_export_seen_stays_whole_numbers(self):
        app = self.make_app()
        real = noise.analyze
        calls = []

        def fake(*a, **kw):
            r = real(*a, **kw)
            calls.append(1)
            if len(calls) == 1:                               # first trace: only 4 peaks
                r.peaks, r.seen = [(60.0, 1.0), (120.0, 0.5), (180.0, 0.2), (240.0, 0.1)], [14, 14, 3, None]
            else:
                r.peaks = [(60.0, 1.0), (120.0, 0.5), (180.0, 0.2), (240.0, 0.1), (300.0, 0.1)]
                r.seen = [14, 12, 3, 2, 1]
            r.n_slices = 14
            return r
        with mock.patch.object(noise, "analyze", side_effect=fake):
            self.analyze(app)
        csv = app.noise_table(for_export=True).to_csv(index=False)
        self.assertNotIn("14.0", csv)
        self.assertNotIn("12.0", csv)
        df = app.noise_table()
        self.assertEqual(list(df["Peak 2 seen"])[:2], [14, 12])
        self.assertTrue(all(isinstance(v, int) for v in df["Slices"]))
        self.assertTrue(all(v is None or isinstance(v, int) for v in df["Peak 4 seen"]))

    def test_fewer_than_five_peaks_dashes(self):
        app = self.make_app()
        real = noise.analyze

        def one_peak(*a, **kw):
            r = real(*a, **kw)
            r.peaks, r.seen, r.n_slices = [(60.0, 1.0)], [3], 3
            return r
        with mock.patch.object(noise, "analyze", side_effect=one_peak):
            self.analyze(app)
        cols = list(app.ntree["columns"])
        vals = app.ntree.item(app.ntree.get_children()[0], "values")
        self.assertEqual(vals[cols.index("Peak 1 seen")], "3")
        for i in range(2, 6):
            for c in (f"Peak {i} (Hz)", f"Peak {i} (mV RMS)", f"Peak {i} seen"):
                self.assertEqual(vals[cols.index(c)], "–", c)

    def test_export_has_slice_and_rule(self):
        app = self.make_app()
        self.analyze(app)
        df = app.noise_table(for_export=True)
        res = df["Resolution (Hz)"].iloc[0]
        self.assertAlmostEqual(df["Slice (s)"].iloc[0], 1 / res)
        self.assertEqual(df["Seen rule"].iloc[0], noise.SEEN_RULE)

    def test_export_noise_tab(self):
        app = self.make_app()
        self.analyze(app)
        df = app.noise_table(for_export=True)
        extra = ["Window start (s)", "Window end (s)", "k", "f_min (Hz)", "Resolution (Hz)",
                 "Slice (s)", "Seen rule"]
        self.assertEqual(list(df.columns), BASE_COLS + ["60 Hz (mV RMS)"] + extra)
        self.assertEqual(df["Window end (s)"].iloc[0], 2.0)
        self.assertEqual(df["k"].iloc[0], 10.0)
        app.results_nb.select(app.noise_tab)
        app.update()
        app.copy_results()
        clip = app.clipboard_get()
        self.assertTrue(clip.startswith("Trace\tBaseline (mV)"), clip[:40])
        out = os.path.join(self.tmp.name, "n.csv")
        with mock.patch.object(m.filedialog, "asksaveasfilename", return_value=out) as dlg:
            app.export_results()
        self.assertEqual(dlg.call_args.kwargs["initialfile"], "noise_analysis.csv")
        with open(out, encoding="utf-8") as f:
            self.assertIn("Resolution (Hz)", f.readline())

    def test_error_in_one_trace_continues(self):
        app = self.make_app()
        real = noise.analyze
        calls = []

        def wrapped(*a, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("boom")
            return real(*a, **kw)
        with mock.patch.object(noise, "analyze", wrapped):
            self.analyze(app)
        df = app.noise_table()
        self.assertEqual(len(df), 3)
        self.assertEqual(df["Spikes"].iloc[1], "error")
        self.assertEqual(df["Baseline (mV)"].iloc[1], "–")
        self.assertNotEqual(df["Spikes"].iloc[0], "error")
        self.assertNotEqual(df["Spikes"].iloc[2], "error")
        self.assertIsInstance(app.noise_rows[1][1], ValueError)

    def test_clear_all_blocked_during_run(self):
        app = self.make_app()
        real = noise.analyze
        seen = []

        def wrapped(*a, **kw):
            time.sleep(0.2)
            return real(*a, **kw)
        with mock.patch.object(noise, "analyze", wrapped):
            app.analyze_noise()
            app.update()
            app.clear_all()
            seen.append(len(app.files))
            self.wait(lambda: not app._noise_running)
        self.assertEqual(seen, [3])
        self.assertEqual(len(app.noise_table()), 3)
        self.assertEqual(app.status.cget("text"), "Analyzed noise for 3 trace(s).")

    def test_clear_all_during_run_says_why_and_is_disabled(self):
        app = self.make_app()
        real = noise.analyze

        def wrapped(*a, **kw):
            time.sleep(0.2)
            return real(*a, **kw)
        with mock.patch.object(noise, "analyze", wrapped):
            app.analyze_noise()
            app.update()
            self.assertEqual(str(app.clear_btn.cget("state")), "disabled")
            self.assertEqual(app._file_menu.entrycget("Clear all", "state"), "disabled")
            app.clear_all()
            self.assertEqual(app.status.cget("text"), "Noise analysis running — press Cancel first.")
            self.assertEqual(len(app.files), 3)
            self.wait(lambda: not app._noise_running)
        self.assertEqual(str(app.clear_btn.cget("state")), "normal")
        self.assertEqual(app._file_menu.entrycget("Clear all", "state"), "normal")

    def test_remove_during_run_drops_its_row(self):
        app = self.make_app()
        real = noise.analyze

        def wrapped(*a, **kw):
            time.sleep(0.2)
            return real(*a, **kw)
        with mock.patch.object(noise, "analyze", wrapped):
            app.analyze_noise()
            app.update()
            gone = app.files[0]
            app.ftree.selection_set("0")
            app.remove_selected()
            self.wait(lambda: not app._noise_running)
        self.assertEqual(len(app.files), 2)
        self.assertEqual(len(app.noise_rows), 2)
        self.assertFalse(any(r[0] is gone for r in app.noise_rows))
        self.assertEqual(app.status.cget("text"), "Analyzed noise for 2 trace(s).")

    def test_rerun_validates_k_range(self):
        app = self.make_app()
        self.analyze(app)
        v = app.open_noise_view(app.files[0])
        self.wait(lambda: v.ready)
        for bad in ("0.1", "5000", "abc", "-3"):
            v.k_var.set(bad)
            v._on_rerun()
            self.assertEqual(v.k, 10.0, bad)
            self.assertIn("Spike k", v.status_lbl.cget("text"))
        v.k_var.set("5000")
        v._on_rerun()
        self.assertIn("between 0.5 and 1000", v.status_lbl.cget("text"))
        v.k_var.set("5")
        v._on_rerun()
        self.assertEqual(v.k, 5.0)

    def test_watch_precision_roundtrip(self):
        app = self.make_app()
        app.settings = {**app.settings, "watch_hz": [193780.3, 193780.0]}
        self.analyze(app)
        cols = list(app.noise_table().columns)
        self.assertEqual(cols[-2:], ["193780.3 Hz (mV RMS)", "193780 Hz (mV RMS)"])
        self.assertFalse(app.noise_outdated)
        app.show_noise_settings()
        d = app._nset
        self.assertEqual(d["watch"].get(), "193780.3, 193780")
        d["save"]()
        self.assertEqual(app.settings["watch_hz"], [193780.3, 193780.0])
        self.assertFalse(app.noise_outdated)

    def test_empty_window_row_is_dashes(self):
        app = self.make_app()
        app.t0_var.set("50"); app.t1_var.set("60")
        self.analyze(app)
        df = app.noise_table()
        self.assertEqual(len(df), 3)
        for col in df.columns[1:]:
            self.assertEqual(set(df[col]), {"–"}, col)
        vals = app.ntree.item(app.ntree.get_children()[0], "values")
        self.assertEqual(set(vals[1:]), {"–"})

    def test_poll_tolerates_cleared_run(self):
        app = self.make_app()
        real = noise.analyze

        def wrapped(*a, **kw):
            time.sleep(0.2)
            return real(*a, **kw)
        with mock.patch.object(noise, "analyze", wrapped):
            app.analyze_noise()
            app.noise_meta = None          # as if the results were wiped mid-run
            self.wait(lambda: not app._noise_running)
        self.assertEqual(app.noise_rows, [])

    def test_loading_traces_marks_outdated(self):
        app = self.make_app()
        self.analyze(app)
        self.assertFalse(app.noise_outdated)
        extra = os.path.join(self.tmp.name, "run_009.csv")
        write_csv(extra, 9)
        app.load_paths([extra])
        self.wait(lambda: not app._loading)
        self.assertTrue(app.noise_outdated)
        self.assertEqual(app.noise_banner.cget("text"), OUTDATED)

    def test_settings_dialog_validation(self):
        app = self.make_app()
        self.analyze(app)
        app.show_noise_settings()
        d = app._nset
        d["k"].set("abc")
        d["save"]()
        self.assertIn("Spike k", d["err"].cget("text"))
        self.assertEqual(app.settings["spike_k"], 10.0)
        self.assertFalse(app.noise_outdated)
        d["k"].set("8"); d["watch"].set("60, 120"); d["fmin"].set("3")
        d["save"]()
        self.assertEqual(app.settings, {"watch_hz": [60.0, 120.0], "spike_k": 8.0, "f_min_hz": 3.0})
        with open(self.settings_path) as f:
            self.assertEqual(json.load(f)["spike_k"], 8.0)
        self.assertTrue(app.noise_outdated)
        self.assertEqual(list(app.noise_table().columns)[-1], "60 Hz (mV RMS)")  # old run keeps its columns


def write_dense_csv(path, n_spikes=10050):
    """Quiet noise with n_spikes single-sample spikes packed into the first third of the file."""
    rng = np.random.default_rng(7)
    n = 60000
    mv = 98 + rng.normal(0, 1.0, n)
    mv[0:2 * n_spikes:2] += 40
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# run 4\n# sample_rate_hz 10000\ntime_s,value_mV\n")
        for i, v in enumerate(np.round(mv, 4)):
            f.write(f"{i * DT:.6f},{v:.4f}\n")


def write_irregular_csv(path):
    rng = np.random.default_rng(3)
    t = np.cumsum(rng.uniform(5e-5, 2e-4, 3000))
    mv = 98 + rng.normal(0, 1.0, 3000)
    mv[1500] += 40
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# run 5\ntime_s,value_mV\n")
        for a, b in zip(t, np.round(mv, 4)):
            f.write(f"{a:.7f},{b:.4f}\n")


class NoiseViewTest(unittest.TestCase):
    cleanup = NoiseAppTest.cleanup
    make_app = NoiseAppTest.make_app
    wait = NoiseAppTest.wait
    analyze = NoiseAppTest.analyze

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.settings_path = os.path.join(self.tmp.name, "settings.json")
        with open(self.settings_path, "w") as f:
            json.dump({"watch_hz": [60.0], "spike_k": 10.0, "f_min_hz": 2.0}, f)
        self.paths = []
        for run in (1, 2):
            p = os.path.join(self.tmp.name, f"run_{run:03d}.csv")
            write_csv(p, run)
            self.paths.append(p)
        self.apps = []

    def open_view(self, app, f):
        v = app.open_noise_view(f)
        self.wait(lambda: v.ready)
        return v

    def single_file_app(self, path, t1):
        app = m.App(settings_path=self.settings_path)
        self.apps.append(app)
        app.cache_dir = self.cache
        app.load_paths([path])
        self.wait(lambda: not app._loading)
        app.t0_var.set("0"); app.t1_var.set(str(t1))
        return app

    def test_view_marks_peaks_and_watch(self):
        app = self.make_app()
        v = self.open_view(app, app.files[0])
        self.assertTrue(v.peak_text().startswith("#1  60."), v.peak_text())
        dashed = [ln for ln in v.spec_ax.lines if ln.get_linestyle() == "--"]
        self.assertEqual([float(ln.get_xdata()[0]) for ln in dashed], [60.0])
        self.assertTrue(any(ln.get_marker() == "v" for ln in v.spec_ax.lines))
        self.assertIs(app.open_noise_view(app.files[0]), v)       # one window per trace

    def test_resolution_label(self):
        app = self.make_app()
        v = self.open_view(app, app.files[0])
        self.assertEqual(v.res_lbl.cget("text"), "Resolution 0.5 Hz · Slice 2.00 s")

    def test_view_peak_text_has_seen(self):
        app = self.make_app()
        v = self.open_view(app, app.files[0])
        lines = v.peak_text().splitlines()
        self.assertRegex(lines[0], r"^#1  60\.\d Hz  \d+\.\d{3} mV RMS  seen \u2013/1 s$")
        self.assertLessEqual(len(lines), noise.N_PEAKS)
        self.assertEqual(len(v.seen), len(v.peaks))
        self.assertEqual(v.n_slices, 1)

    def test_spike_click_zooms_and_selects(self):
        app = self.make_app()
        self.analyze(app)
        v = self.open_view(app, app.files[1])
        v.goto_spike(0)
        t = float(v.spikes.times[0])
        x0, x1 = app.ax.get_xlim()
        self.assertAlmostEqual(x0, t - 0.002)
        self.assertAlmostEqual(x1, t + 0.002)
        self.assertTrue(app.tools.can_go_back)
        self.assertEqual(app._selected_files(), [app.files[1]])
        self.assertFalse(app.noise_outdated)

    def test_spike_list_columns(self):
        app = self.make_app()
        v = self.open_view(app, app.files[0])
        rows = [v.spike_tree.item(i, "values") for i in v.spike_tree.get_children()]
        self.assertEqual(len(rows), len(v.spikes.times))
        self.assertEqual(rows[0][0], f"{v.spikes.times[0]:.6f}")
        self.assertEqual(rows[0][1], f"{v.spikes.heights[0]:.4f}")
        self.assertEqual(rows[0][2], "")
        self.assertEqual(rows[1][2], f"{(v.spikes.times[1] - v.spikes.times[0]) * 1000:.3f}")

    def test_cap_message(self):
        p = os.path.join(self.tmp.name, "dense.csv")
        write_dense_csv(p)
        app = self.single_file_app(p, 6)
        v = self.open_view(app, app.files[0])
        n = len(v.spikes.times)
        self.assertGreater(n, 10000)
        self.assertEqual(len(v.spike_tree.get_children()), 10000)
        self.assertEqual(v.cap_lbl.cget("text"), f"Showing the first 10,000 of {n:,} spikes.")

    def test_add_watch_marks_outdated(self):
        app = self.make_app()
        self.analyze(app)
        v = self.open_view(app, app.files[0])
        v.add_watch(15550.04)
        self.assertIn(15550.0, app.settings["watch_hz"])
        with open(self.settings_path) as f:
            self.assertIn(15550.0, json.load(f)["watch_hz"])
        self.assertTrue(app.noise_outdated)
        xs = [float(ln.get_xdata()[0]) for ln in v.spec_ax.lines if ln.get_linestyle() == "--"]
        self.assertEqual(sorted(xs), [60.0, 15550.0])
        v.add_watch(15550.0)                                       # duplicate ignored
        self.assertEqual(app.settings["watch_hz"].count(15550.0), 1)

    def test_rerun_keeps_seen(self):
        app = self.make_app()
        self.analyze(app)
        v = self.open_view(app, app.files[0])
        r0 = next(r for f, r in app.noise_rows if f is app.files[0])
        v.rerun(5)
        r1 = next(r for f, r in app.noise_rows if f is app.files[0])
        self.assertEqual((r1.peaks, r1.seen, r1.n_slices), (r0.peaks, r0.seen, r0.n_slices))
        self.assertEqual(r1.k, 5)

    def test_rerun_updates_row(self):
        app = self.make_app()
        self.analyze(app)
        v = self.open_view(app, app.files[0])
        before = app.noise_table()["Threshold (mV)"].tolist()
        v.rerun(5)
        after = app.noise_table()["Threshold (mV)"].tolist()
        sp = v.spikes
        self.assertAlmostEqual(after[0], sp.baseline + 5 * sp.sigma)
        self.assertAlmostEqual(after[0], before[0] - 5 * sp.sigma)
        self.assertEqual(after[1], before[1])
        self.assertEqual(app.settings["spike_k"], 10.0)
        self.assertFalse(app.noise_outdated)
        self.assertEqual(app.noise_table()["Spikes"][0], len(sp.times))
        self.assertEqual(len(v.spike_tree.get_children()), len(sp.times))
        self.assertIn("k = 5", v.title())

    def test_rerun_disabled_until_ready(self):
        app = self.make_app()
        v = app.open_noise_view(app.files[0])
        self.assertEqual(str(v.rerun_btn.cget("state")), "disabled")
        self.assertEqual(str(v.k_entry.cget("state")), "disabled")
        v.rerun(5)                                   # no-op while computing
        self.assertIsNone(v.spikes)
        self.assertEqual(v.k, 10.0)
        self.wait(lambda: v.ready)
        self.assertEqual(str(v.rerun_btn.cget("state")), "normal")
        self.assertAlmostEqual(v.spikes.threshold, v.spikes.baseline + 10 * v.spikes.sigma)
        v.rerun(5)
        self.assertAlmostEqual(v.spikes.threshold, v.spikes.baseline + 5 * v.spikes.sigma)

    def test_rerun_row_carries_its_k(self):
        app = self.make_app()
        self.analyze(app)
        v = self.open_view(app, app.files[0])
        v.rerun(5)
        df = app.noise_table(for_export=True)
        self.assertEqual(df["k"].tolist(), [5.0, 10.0])
        self.assertEqual(df["Trace"][0], app._label(app.files[0]) + " (k=5)")
        self.assertEqual(df["Trace"][1], app._label(app.files[1]))
        first = app.ntree.item(app.ntree.get_children()[0], "values")[0]
        self.assertTrue(first.endswith("(k=5)"))
        self.assertIn("spike k 10", app.noise_sub.cget("text"))

    def _threshold_lines(self, app, y):
        return [ln for ln in app.ax.lines if len(ln.get_ydata()) == 2
                and ln.get_ydata()[0] == ln.get_ydata()[1] and abs(ln.get_ydata()[0] - y) < 1e-9
                and ln.get_linestyle() == ":"]

    def test_threshold_line_drawn(self):
        app = self.make_app()
        app._select_file(app.files[0])
        app.update()
        v = self.open_view(app, app.files[0])
        self.assertEqual(len(self._threshold_lines(app, v.spikes.threshold)), 1)
        v.rerun(5)
        self.assertEqual(len(self._threshold_lines(app, v.spikes.threshold)), 1)
        thr = v.spikes.threshold
        v.close()
        self.assertEqual(self._threshold_lines(app, thr), [])
        self.assertEqual(app._noise_views, {})

    def test_threshold_only_for_selected_trace(self):
        app = self.make_app()
        app._select_file(app.files[1])
        app.update()
        v = self.open_view(app, app.files[0])
        self.assertEqual(self._threshold_lines(app, v.spikes.threshold), [])

    def test_peak_pick_near_marker(self):
        app = self.make_app()
        v = self.open_view(app, app.files[0])
        v.canvas.draw()
        px = v.spec_ax.transData.transform((v.peaks[0][0], 1.0))[0]
        self.assertEqual(v.peak_near(px + 3), v.peaks[0][0])
        self.assertIsNone(v.peak_near(px + 0.2 * v.spec_ax.bbox.width))

    def test_nonuniform_note(self):
        p = os.path.join(self.tmp.name, "irr.csv")
        write_irregular_csv(p)
        app = self.single_file_app(p, 1)
        v = self.open_view(app, app.files[0])
        self.assertEqual(v.note_lbl.cget("text"), "Spectrum needs evenly spaced samples.")
        self.assertEqual(len(v.spec_ax.lines), 0)

    def test_removing_trace_closes_its_view(self):
        app = self.make_app()
        self.open_view(app, app.files[1])
        app.ftree.selection_set("1")
        app.remove_selected()
        app.update()
        self.assertEqual(app._noise_views, {})


if __name__ == "__main__":
    unittest.main()
