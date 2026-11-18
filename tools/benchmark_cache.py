"""Benchmark the trace cache.

    python tools\benchmark_cache.py <folder> [--fill 160] [--cache-dir DIR]

Cold-converts every locally available CSV in <folder> (OneDrive online-only files are skipped,
never downloaded), warm-opens them, optionally adds synthetic 7.4 M-sample entries up to --fill
traces, then times stats and view over all traces. The temp cache is deleted at the end.
"""
import argparse
import ctypes
import gc
import glob
import os
import shutil
import sys
import tempfile
import time
from ctypes import wintypes

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multi_file_analyzer as ref  # noqa: E402
import trace_store as ts  # noqa: E402


class PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def memory():
    """(working set, peak working set) in bytes."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    if not psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return c.WorkingSetSize, c.PeakWorkingSetSize


def mb(b):
    return f"{b / 1048576:,.0f} MB"


def synth_trace(n, seed):
    rng = np.random.default_rng(seed)
    t = np.round(np.arange(n) * 2e-6, 6)
    mv = np.round(np.round(rng.normal(98, 2.7, n) / 0.1526) * 0.1526, 4)
    return ts.TraceData.from_arrays(t, mv)


def add_synthetic(cache_dir, count, n):
    """Save `count` synthetic traces (distinct values, same layout) and open them memory-mapped."""
    out = []
    base = synth_trace(n, 0)
    for i in range(count):
        d = base if i == 0 else synth_trace(n, i)
        folder = os.path.join(cache_dir, f"synthetic{i:03d}")
        info = d.save(os.path.join(folder, "0"))
        out.append(ts.TraceData.open(os.path.join(folder, "0"), info))
    return out


def timed(fn):
    t0 = time.perf_counter()
    r = fn()
    return time.perf_counter() - t0, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--fill", type=int, default=0, help="pad to this many traces with synthetic entries")
    ap.add_argument("--cache-dir", default=None, help="parent for the temp cache (default: system temp)")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.folder, "*.csv")))
    cloud = [f for f in files if ts.is_cloud_only(f)]
    local = [f for f in files if f not in cloud]
    print(f"{len(files)} CSV files: {len(local)} local, {len(cloud)} online-only (skipped, not downloaded)")
    for f in cloud:
        print("  skipped:", os.path.basename(f))

    cache = tempfile.mkdtemp(prefix="bbbench_", dir=a.cache_dir)
    print("cache:", cache)
    try:
        ws0, _ = memory()
        print(f"working set at start: {mb(ws0)}")

        # cold conversion
        t_cold, _ = timed(lambda: [ts.open_traces(f, ref.parse_file, cache) for f in local] and None)
        gc.collect()
        ws, peak = memory()
        print(f"cold convert {len(local)} runs: {t_cold:.1f} s total, {t_cold / max(1, len(local)):.2f} s/run")
        print(f"  working set after cold: {mb(ws)}; peak working set during cold: {mb(peak)}")
        entries, size = ts.cache_info(cache)
        print(f"  cache: {entries} entries, {size / 1e9:.2f} GB")

        # warm open
        t_warm, res = timed(lambda: [ts.open_traces(f, ref.parse_file, cache) for f in local])
        statuses = {s for _, s in res}
        datas = [tr["data"] for traces, _ in res for tr in traces]
        print(f"warm open {len(local)} runs: {t_warm:.2f} s (status {sorted(statuses)})")

        # synthetic fill
        if a.fill > len(datas):
            k = a.fill - len(datas)
            n = datas[0].n if datas else 7_400_000
            t_syn, syn = timed(lambda: add_synthetic(cache, k, n))
            datas += syn
            print(f"added {k} synthetic traces of {n:,} samples in {t_syn:.1f} s")
        total = len(datas)

        t_end = max(d.t_end for d in datas)
        t_start = min(d.t_start for d in datas)
        raw_limit, bins = ref.draw_budget(total)
        print(f"{total} traces, window {t_start:g}..{t_end:g} s, draw_budget -> raw_limit={raw_limit}, bins={bins}")
        for label in ("pass 1", "pass 2"):
            ts_, _ = timed(lambda: [d.stats(t_start, t_end) for d in datas])
            tv_, _ = timed(lambda: [d.view(t_start, t_end, raw_limit, bins) for d in datas])
            print(f"{label}: stats over {total}: {ts_:.3f} s   view over {total}: {tv_:.3f} s")
        gc.collect()
        ws, peak = memory()
        print(f"working set after warm-open of {total} + stats/view: {mb(ws)}; peak (whole run): {mb(peak)}")
    finally:
        datas = res = syn = None
        gc.collect()
        shutil.rmtree(cache, ignore_errors=True)
        print("cache deleted:", not os.path.exists(cache))


if __name__ == "__main__":
    main()
