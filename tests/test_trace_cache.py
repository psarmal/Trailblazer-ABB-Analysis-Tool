"""Run: python -m unittest discover -s tests   (from the Analysis tool folder)"""
import gc
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as ref  # noqa: E402
import trace_store as ts  # noqa: E402

N = 20000


def write_csv(path, mv, dt=2e-6):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# run 7\n# experiment cache test\n# sample_rate_hz 500000\n")
        f.write("time_s,value_mV\n")
        for i, v in enumerate(mv):
            f.write(f"{i * dt:.6f},{v:.4f}\n")
        f.write("# peak_mV 12.5\n# note hello\n")


def make_mv(seed=0, n=N):
    return np.round(np.random.default_rng(seed).normal(98, 2.7, n), 4)


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.cleanup)
        self.cache = os.path.join(self.tmp.name, "cache")
        self.csv = os.path.join(self.tmp.name, "run_007.csv")
        write_csv(self.csv, make_mv())
        self.keep = []

    def cleanup(self):
        self.keep.clear()
        gc.collect()
        self.tmp.cleanup()

    def open(self, path=None):
        r = ts.open_traces(path or self.csv, ref.parse_file, self.cache)
        self.keep.append(r)
        return r

    def entries(self):
        return [d for d in os.listdir(self.cache) if ".tmp-" not in d]

    def test_convert_then_cached(self):
        traces, status = self.open()
        self.assertEqual(status, "converted")
        traces2, status2 = self.open()
        self.assertEqual(status2, "cached")
        a, b = traces[0], traces2[0]
        for k in ("path", "file", "folder", "name", "meta", "summary", "t_label", "v_label"):
            self.assertEqual(a[k], b[k], k)
        self.assertEqual(b["n"], N)
        self.assertNotIn("t", b)
        self.assertNotIn("mv", b)
        parsed = ref.parse_file(self.csv)[0]
        exp = ts.TraceData.from_arrays(parsed["t"], parsed["mv"]).stats(parsed["t"][0], parsed["t"][-1])
        got = b["data"].stats(parsed["t"][0], parsed["t"][-1])
        self.assertEqual(exp, got)
        self.assertEqual(a["data"].stats(parsed["t"][0], parsed["t"][-1]), got)

    def test_values_bit_identical(self):
        self.open()
        traces, status = self.open()
        self.assertEqual(status, "cached")
        p = ref.parse_file(self.csv)[0]
        d = traces[0]["data"]
        n = traces[0]["n"]
        self.assertTrue(np.array_equal(d.values(0, n), p["mv"]))
        self.assertIsNotNone(d.dt)    # uniform time, rebuilt from t_start/dt/t_dec
        self.assertTrue(np.array_equal(d.times(0, n), p["t"]))
        s, want = d.stats(p["t"][0], p["t"][-1]), ref.window_stats(p["t"], p["mv"], p["t"][0], p["t"][-1])
        self.assertEqual((s["Time of max"], s["Time of min"]), (want["Time of max"], want["Time of min"]))

    def test_old_entry_without_t_dec_is_rebuilt(self):
        self.open()
        self.keep.clear()
        gc.collect()          # nothing mapped, so the stale entry can be replaced
        meta_path = os.path.join(self.cache, self.entries()[0], "meta.json")
        import json
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        for tr in meta["traces"]:
            del tr["t_dec"]
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        self.assertEqual(self.open()[1], "converted")

    def test_cached_arrays_are_memmapped(self):
        self.open()
        d = self.open()[0][0]["data"]
        self.assertIsInstance(d.codes, np.memmap)
        self.assertIsInstance(d.blocks, np.memmap)
        self.assertTrue(all(isinstance(o, np.memmap) for o in d.overviews.values()))

    def test_source_change_makes_new_entry_while_old_mapped(self):
        first = self.open()[0][0]["data"]
        self.open()  # cached, mapped
        st = os.stat(self.csv)
        new = make_mv(seed=5)
        write_csv(self.csv, new)
        os.utime(self.csv, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        traces, status = self.open()
        self.assertEqual(status, "converted")
        self.assertTrue(np.array_equal(traces[0]["data"].values(0, N), new))
        self.assertEqual(len(self.entries()), 2)
        self.assertEqual(first.n, N)

    def test_corrupt_entry_is_rebuilt(self):
        self.open()
        self.keep.clear()
        gc.collect()
        entry = os.path.join(self.cache, self.entries()[0])
        os.remove(os.path.join(entry, "0", "mv.npy"))
        traces, status = self.open()
        self.assertEqual(status, "converted")
        p = ref.parse_file(self.csv)[0]
        self.assertTrue(np.array_equal(traces[0]["data"].values(0, N), p["mv"]))
        self.assertEqual(self.open()[1], "cached")

    def test_concurrent_rename_uses_existing(self):
        real = os.replace
        calls = []

        def fake(src, dst):
            if not calls:
                calls.append(1)
                real(src, dst)  # another process finished first
                os.makedirs(src)  # ours is still around, rename now collides
                raise PermissionError("exists")
            return real(src, dst)

        with mock.patch("trace_store.os.replace", fake):
            traces, status = self.open()
        self.assertEqual(status, "converted")
        self.assertIsInstance(traces[0]["data"].codes, np.memmap)
        self.assertEqual(os.listdir(self.cache), self.entries())
        p = ref.parse_file(self.csv)[0]
        self.assertTrue(np.array_equal(traces[0]["data"].values(0, N), p["mv"]))

    def test_entry_completed_by_other_during_parse_is_kept(self):
        other = []

        def parse(path):
            if not other:  # another opener converts the same file while we parse
                other.append(ts.open_traces(path, ref.parse_file, self.cache))
            return ref.parse_file(path)

        traces, status = ts.open_traces(self.csv, parse, self.cache)
        self.keep.extend([traces, other])
        self.assertEqual(status, "cached")
        p = ref.parse_file(self.csv)[0]
        self.assertTrue(np.array_equal(traces[0]["data"].values(0, N), p["mv"]))
        self.assertTrue(ts.is_cached(self.csv, self.cache))
        self.assertEqual(len(self.entries()), 1)

    def test_unwritable_cache_falls_back_to_memory(self):
        bad = os.path.join(self.tmp.name, "afile")
        with open(bad, "w") as f:
            f.write("x")
        traces, status = ts.open_traces(self.csv, ref.parse_file, bad)
        self.assertEqual(status, "memory")
        p = ref.parse_file(self.csv)[0]
        self.assertEqual(traces[0]["data"].stats(p["t"][0], p["t"][-1])["Points"], N)
        self.assertTrue(os.path.isfile(bad))

    def test_parse_error_propagates(self):
        with open(self.csv, "w") as f:
            f.write("nothing useful\n")
        with self.assertRaises(ref.ReadError):
            self.open()

    def test_helpers(self):
        self.assertFalse(ts.is_cached(self.csv, self.cache))
        self.open()
        self.assertTrue(ts.is_cached(self.csv, self.cache))
        self.assertEqual(ts.entry_key(self.csv), os.path.basename(
            os.path.join(self.cache, self.entries()[0])))
        self.assertFalse(ts.is_cloud_only(self.csv))
        self.assertTrue(ts.default_cache_dir().endswith(os.path.join("BBAnalyzer", "cache")))

    def test_excel_workbook_several_traces(self):
        import pandas as pd
        path = os.path.join(self.tmp.name, "book.xlsx")
        t = np.arange(500) * 2e-6
        df = pd.DataFrame({"time_s": t, "Channel 1 raw signal (mV)": make_mv(1, 500),
                           "Channel 2 raw signal (mV)": make_mv(2, 500),
                           "Channel 3 raw signal (mV)": make_mv(3, 500)})
        df.to_excel(path, index=False, sheet_name="Data")
        traces, status = self.open(path)
        self.assertEqual((status, len(traces)), ("converted", 3))
        traces2, status2 = self.open(path)
        self.assertEqual((status2, len(traces2)), ("cached", 3))
        parsed = ref.parse_file(path)
        for a, b, p in zip(traces, traces2, parsed):
            self.assertEqual(a["meta"], b["meta"])
            self.assertEqual(a["name"], b["name"])
            self.assertTrue(np.array_equal(b["data"].values(0, b["n"]), p["mv"]))

    def second_csv(self):
        path = os.path.join(self.tmp.name, "run_008.csv")
        write_csv(path, make_mv(seed=8))
        return path

    def test_cache_info_counts_entries_and_bytes(self):
        self.assertEqual(ts.cache_info(self.cache), (0, 0))
        self.open()
        self.open(self.second_csv())
        entries, size = ts.cache_info(self.cache)
        self.assertEqual(entries, 2)
        self.assertGreater(size, 0)

    def test_clear_cache_keeps_entries_in_use(self):
        self.open()
        gone = self.open(self.second_csv())
        self.keep.remove(gone)
        del gone
        gc.collect()
        removed, kept = ts.clear_cache(self.cache)
        if sys.platform == "win32":
            self.assertEqual((removed, kept), (1, 1))
            traces, status = self.open()
            self.assertEqual(status, "cached")
            p = ref.parse_file(self.csv)[0]
            self.assertTrue(np.array_equal(traces[0]["data"].values(0, N), p["mv"]))
            self.assertTrue(os.path.isfile(os.path.join(traces[0]["data"].entry_dir, "meta.json")))
        else:
            self.assertEqual(removed + kept, 2)

    def test_clear_cache_removes_leftover_temp_folders(self):
        self.open()
        self.keep.clear()
        gc.collect()
        os.makedirs(os.path.join(self.cache, "abc.tmp-123"))
        self.assertEqual(ts.clear_cache(self.cache), (1, 0))
        self.assertEqual(os.listdir(self.cache), [])
        self.assertEqual(ts.clear_cache(os.path.join(self.tmp.name, "missing")), (0, 0))

    def test_del_leftovers_not_counted_as_entries(self):
        self.open()
        self.keep.clear()
        gc.collect()
        os.makedirs(os.path.join(self.cache, "abc.del-1"))
        with open(os.path.join(self.cache, "abc.del-1", "x.bin"), "wb") as f:
            f.write(b"x" * 100)
        entries, size = ts.cache_info(self.cache)
        self.assertEqual(entries, 1)
        self.assertGreater(size, 100)
        self.assertEqual(ts.clear_cache(self.cache), (1, 0))
        self.assertEqual(os.listdir(self.cache), [])

    def test_entry_dir(self):
        d = self.open()[0][0]["data"]
        self.assertEqual(d.entry_dir, os.path.join(self.cache, ts.entry_key(self.csv)))
        p = ref.parse_file(self.csv)[0]
        self.assertIsNone(ts.TraceData.from_arrays(p["t"], p["mv"]).entry_dir)

    def test_zero_byte_mv_npy_is_rebuilt(self):
        self.open()
        self.keep.clear()
        gc.collect()
        entry = os.path.join(self.cache, self.entries()[0])
        open(os.path.join(entry, "0", "mv.npy"), "wb").close()   # np.load raises EOFError on this
        self.assertFalse(ts.is_cached(self.csv, self.cache))
        self.assertEqual(ts.cache_info(self.cache)[0], 0)
        traces, status = self.open()
        self.assertEqual(status, "converted")
        p = ref.parse_file(self.csv)[0]
        self.assertTrue(np.array_equal(traces[0]["data"].values(0, N), p["mv"]))
        self.assertTrue(ts.is_cached(self.csv, self.cache))

    def test_disk_full_falls_back_to_memory(self):
        real = np.save
        calls = []

        def boom(file, arr, *a, **kw):
            calls.append(file)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(file, arr, *a, **kw)
        with mock.patch.object(np, "save", boom):
            traces, status = self.open()
        self.assertEqual(status, "memory")
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(os.listdir(self.cache), [])      # temp folder removed, no entry
        p = ref.parse_file(self.csv)[0]
        d = traces[0]["data"]
        self.assertIsNone(d.entry_dir)
        self.assertEqual(d.stats(p["t"][0], p["t"][-1])["Points"], N)
        self.assertTrue(np.array_equal(d.values(0, N), p["mv"]))

    def test_overview_pairs_values_with_indices(self):
        n = 3_000_000
        mv = make_mv(5, n)
        d = ts.TraceData.from_arrays(np.round(np.arange(n) * 2e-6, 6), mv)
        t_end = d.t_end
        for frac in (1.0, 0.4, 0.2, 0.06, 0.013):   # reduced views at every overview level
            x0 = 0.0013
            x1 = x0 + frac * t_end
            t, v, every = d.view(x0, x1, 12000, 3000)
            self.assertFalse(every, frac)
            idx = np.rint(t / 2e-6).astype(np.int64)
            self.assertTrue(np.array_equal(v, d.values_at(idx)), frac)
        self.assertEqual({s for s in ts.OV_LEVELS}, set(d.overviews))

    def test_clear_cache_skips_live_temp_folders(self):
        import subprocess
        self.open()
        self.keep.clear()
        gc.collect()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        live = os.getppid()
        names = {"live.tmp-%d" % live: True, "live.del-%d" % live: True,
                 "dead.tmp-%d" % dead.pid: False, "dead.del-%d" % dead.pid: False,
                 "mine.tmp-%d" % os.getpid(): False, "odd.tmp-xyz": False}
        for nm in names:
            os.makedirs(os.path.join(self.cache, nm))
        self.assertTrue(ts.pid_alive(live))
        self.assertFalse(ts.pid_alive(dead.pid))
        self.assertEqual(ts.clear_cache(self.cache), (1, 0))
        left = set(os.listdir(self.cache))
        self.assertEqual(left, {nm for nm, keep in names.items() if keep})

    def test_cache_summary_text(self):
        self.assertEqual(ref.cache_summary(0, 0), "0 runs \u00b7 0 MB")
        self.assertEqual(ref.cache_summary(1, 35_000_000), "1 run \u00b7 35 MB")
        self.assertEqual(ref.cache_summary(160, 5.6e9), "160 runs \u00b7 5.6 GB")

    def test_cache_dialog_texts(self):
        self.open()
        import tkinter as tk
        try:
            app = ref.App(settings_path=os.path.join(self.tmp.name, "settings.json"))
        except tk.TclError as e:
            self.skipTest(str(e))
        self.addCleanup(app.destroy)
        app.cache_dir = self.cache
        dlg = app.show_cache_dialog()
        texts = []

        def walk(w):
            for c in w.winfo_children():
                try:
                    texts.append(c.cget("text"))
                except tk.TclError:
                    pass
                walk(c)
        walk(dlg)

        def btns_of(w):
            out = []
            for c in w.winfo_children():
                out.append(c)
                out.extend(btns_of(c))
            return out
        self.assertIn("1 run \u00b7 0 MB", texts)
        for t in ("Open folder", "Clear cache", "Close"):
            self.assertIn(t, texts)
        clear_btn = [w for w in btns_of(dlg) if isinstance(w, ref.ttk.Button) and w.cget("text") == "Clear cache"][0]
        app._loading = True
        with mock.patch.object(ref.messagebox, "showinfo") as info,                 mock.patch.object(ref.messagebox, "askyesno") as ask,                 mock.patch.object(ref, "clear_cache") as clr:
            clear_btn.invoke()
        info.assert_called_once()
        ask.assert_not_called()
        clr.assert_not_called()
        dlg.destroy()


if __name__ == "__main__":
    unittest.main()
