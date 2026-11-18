# Trace cache and fast statistics — design

Date: 2026-11-06 · Tool: `multi_file_analyzer.py` · Status: draft for review

## 1. Why

Loading the 620nm Config runs (Test 008) is slow, and loading the 8–160 runs the
user works with is not possible in practice.

Measured on this PC (15.6 GB RAM, 1.5 GB free at the time):

| | Value |
|---|---|
| One run (`run_001.csv`) | 500 kHz, 7.42 M rows, 118 MB, parses in 0.92 s, 119 MB RAM |
| Folder | 57 runs, 7.3 GB of CSV; runs 33 and 45 are ~230 MB |
| 160 runs | ~1.2 billion points, ~19 GB in RAM as the tool stores data today |

Root causes:
1. Every loaded run is held in RAM as two float64 arrays; past ~10 runs Windows pages to disk.
2. Every load re-parses the CSV text (~1 s per run).
3. Every Calculate and every zoomed-out redraw scans all points of every run, so at
   160 runs each one would read ~19 GB.
4. (Not code) 12 runs (35, 36, 41–50) are OneDrive online-only placeholders, so
   opening one downloads it first.

Facts the design relies on (checked on `run_001.csv`):
- Time is exactly uniform: dt = 2 µs, max deviation from `t0 + i·dt` is 1.8e-15 s.
- mV values are written with 4 decimals and are ADC-quantized (0.1526 mV steps, 71 levels).

## 2. Goals and success criteria

1. **Warm load** (runs already converted) of the 57 real runs in **< 10 s**.
2. **Cold load** (first time): ~1.2 s per run, in the background, with progress and time left.
3. With **160 runs** loaded: Calculate in **< 0.5 s**, zoom or pan redraw in **< 0.5 s**,
   and the app's memory stays well under 2 GB (data is read from disk on demand).
4. **Same answers as today.** Points, Max, Min, Time of max and Time of min are identical;
   Mean and Std agree to ≤ 1e-12 relative (display shows 4 decimals, so the table is unchanged).
5. Nothing else in the UI changes behaviour: file list, sort, rename, legend, results table,
   exports, Excel support.

Out of scope (separate spec): RMS noise and SNR columns and the background-window UI.
This design makes them cheap: the per-block table below already holds what RMS needs.

## 3. Architecture

A new module **`trace_store.py`** next to `multi_file_analyzer.py` owns everything about
how trace data is stored and read. The app only talks to it through a small interface,
so `multi_file_analyzer.py` stops touching raw arrays.

```
multi_file_analyzer.py (GUI)
   │  open_traces(path) ─────────────┐
   │  trace["data"].stats(t0, t1)    │
   │  trace["data"].view(x0, x1, …)  ▼
   │                         trace_store.py
   │                           ├─ cache lookup / convert (parse_file from the app module)
   │                           ├─ TraceData (memory-mapped arrays + summaries)
   │                           └─ block statistics and overview levels
   ▼
%LOCALAPPDATA%\BBAnalyzer\cache\<key>\…   (local disk, not synced)
```

### 3.1 `TraceData` interface

```python
class TraceData:
    n: int                       # number of samples
    t_start: float; t_end: float
    def index_range(self, t0, t1) -> tuple[int, int]   # [i0, i1) with t0 <= t <= t1, as window_slice today
    def times(self, i0, i1) -> np.ndarray              # float64
    def values(self, i0, i1) -> np.ndarray             # float64 mV
    def stats(self, t0, t1, population=True) -> dict   # same keys as window_stats today
    def view(self, x0, x1, raw_limit, bins) -> tuple[np.ndarray, np.ndarray, bool]  # as view_points today
```

Trace dicts keep every current field (`path`, `file`, `folder`, `name`, `meta`, `summary`,
labels, `show`, `color`, `alias`, `load_idx`) but `t` and `mv` are replaced by
`data: TraceData` and `n: int`. The nine places that read `f["t"]` / `f["mv"]` switch to
the interface: points column, status total, time extent, Calculate, Export window data,
draw, zoom refresh, zoomed-out label.

### 3.2 Storage format (one folder per source file)

```
cache\<sha1 of normalised source path | size | mtime_ns | format version>\
                     (a changed source gets a new folder, so an entry that is memory-mapped
                     — and therefore locked on Windows — never has to be replaced in place)
    meta.json        source path, size, mtime_ns, format version, parsed meta/summary/labels,
                     one entry per trace (a workbook has several)
    <trace>\
        mv.npy       int32 = round(mV × 10^d), d = decimals detected at conversion (4 here)
                     — or float64 if any value does not round-trip exactly
        time.npy     only if time is not uniform; otherwise meta.json holds t0 and dt
        blocks.npy   per 4096-sample block: n, mean, M2, min, max, index of first min, index of first max
        ov64.npy / ov512.npy / ov4096.npy   min, max and their indices per 64 / 512 / 4096 samples
```

- **Exact values:** `int / 10^d` in float64 gives the same double as parsing the 4-decimal
  text, so raw values are bit-identical to today. At conversion every value is checked to
  round-trip exactly; if any does not, the trace is stored as float64.
- **Uniform time:** stored as `t0, dt` when `max |t − (t0 + i·dt)| ≤ 1e-6·dt`. A window edge
  within 1e-6·dt of a sample counts as hitting that sample (inclusive, as today), so the
  Points column does not shift by one because of rounding.
- **Size:** ~30 MB per 7.4 M-sample run (mV) + ~1 MB summaries; ~5 GB for 160 runs.
- Arrays are opened with `np.load(mmap_mode="r")`: nothing is read until it is needed.

### 3.3 Fast statistics (exact)

`stats(t0, t1)` = combine whole blocks inside the window from `blocks.npy`, plus the raw
samples in the two partial blocks at the edges (≤ 2 × 4095 values).

- Mean/variance use Chan's pairwise combination of (n, mean, M2), which is numerically stable
  (no Σx² − n·mean² cancellation).
- Max/min and their times: combine block extremes; on ties keep the earliest index, matching
  `argmax`/`argmin` today.
- Population or sample std as today. Also returns `"RMS"` (√(mean² + var_pop)), unused by
  the UI until the RMS/SNR spec.

Cost: ~1,800 block rows + ~8,000 edge samples per run, so 160 runs ≈ a few ms.

### 3.4 Fast drawing

`view()` keeps today's behaviour (all points when ≤ `raw_limit` are visible; otherwise min
and max of each of `bins` slices, in time order, spikes never lost) but builds the slices from
the coarsest overview level whose block size is ≤ the slice width, instead of from raw samples.
Edges of the visible range that do not fill a whole overview block use raw samples.

### 3.5 Conversion and loading

`open_traces(path)`:
1. `os.stat` the source (does **not** trigger a OneDrive download). If the entry folder for
   this path, size, mtime and format version exists and is complete, open it. Cloud-only runs that are already
   cached never download again.
2. Otherwise parse with today's `parse_file`, compute summaries, write to
   `<key>.tmp-<pid>\`, then rename into place (an interrupted conversion leaves no half entry).
3. A missing, unreadable or wrong-version entry is deleted and converted again.

Loading stays on the existing background thread, one file at a time (peak working set about 660 MB while a 7.4 M-sample CSV is parsed, measured).
Progress shows in the status bar with a determinate progress bar:
`Converting 37/160 · run_037.csv · about 2 min left` or `Opening cached 120/160`.
Before parsing, a run whose attributes say online-only (`RECALL_ON_DATA_ACCESS` / `OFFLINE`)
is reported as `run_041.csv is in OneDrive only — downloading…`.

### 3.6 Cache management

- **File → Cache…**: shows the location, size and number of entries; **Clear cache**
  deletes it (asks first; not allowed while loading). Entries of traces currently loaded are
  kept (they are locked while mapped) and the dialog says how many were kept.
- No automatic eviction.

## 4. Error handling

| Situation | Behaviour |
|---|---|
| Cache folder can't be created, or disk is full while writing | Fall back to loading that run in memory as today; status-bar warning once per session; the temp folder is removed |
| Corrupt or unreadable cache entry | Delete it, convert again |
| Source file changed since conversion | Size/mtime differ → convert again |
| Source file deleted | Same "couldn't be opened" error as today (the entry key needs its size and mtime); its old entry is removed by Clear cache |
| Parse errors | Same messages as today (`ReadError`) |

## 5. Testing

Standard-library `unittest` in `tests\` (pytest is not installed), run with
`python -m unittest discover -s tests`.

- **Exactness against today's code:** today's `window_stats` and `view_points` stay in the app
  module and serve as the reference. On synthetic runs (uniform and non-uniform time,
  spikes, ties, values up to ±2000 mV) and on `run_001.csv`, compare results for hundreds of random
  windows, including windows inside one block, across block edges, exactly on samples,
  empty, single point and full range.
- **View:** visible extremes are identical to `view_points`; the raw/reduced decision is the same.
- **Cache:** hit, invalidation on size/mtime change, version bump, corrupt entry, interrupted
  write, float64 fallback, Excel workbook with several columns.
- **Benchmark script** (not a unit test): cold and warm load of the 57 real runs, Calculate
  and redraw times, and process memory at 160 traces (57 real + synthetic cache entries).
  psutil is not installed, so memory is read with the Windows API through `ctypes`.

## 6. Decisions already made with the user

- Option A (cache plus memory-mapping) approved; ~30 MB of local disk per run in
  `%LOCALAPPDATA%` is acceptable.
- Typical use is 8–160 runs at once.
- Rename stays as is (double-click / F2).
- Sorting by run # (↑/↓/load order) is already done (2026-11-06) and must keep working.
- RMS/SNR UI is deferred. When it is designed: background comes from a **separate run**
  (e.g. an LED-off / blank run), not a window in the same run;
  SNR = (mean_signal − mean_background) / σ_background.
- Spec approved by the user on 2026-11-06.

## 7. Results (2026-11-06)

`python tools\benchmark_cache.py "<620nm Config>" --fill 160`: 57 real runs (all locally available,
none skipped) plus 103 synthetic 7.42 M-sample traces, temp cache, full time range,
`draw_budget(160)` = (raw_limit 1875, bins 424). Machine: the development PC, Python 3.13.

| Measure | Target | Before (Task 6) | After (Task 6b) | Final (fix wave) | Result |
|---|---|---|---|---|---|
| Cold convert, per run | ~1.2 s/run (spec §2) | 2.63 s (150.1 s for 57) | 2.74 s (156.1 s for 57) | 1.79 s (102.2 s for 57) | **missed** (1.5x the target) |
| Warm open of 57 runs | < 10 s | 0.29 s | 0.27 s | 0.30 s | met |
| Stats over 160 traces | < 0.5 s | 0.934 s (pass 1), 0.838 s (pass 2) | 0.098 s (pass 1), 0.008 s (pass 2) | 0.096 s (pass 1), 0.008 s (pass 2) | met |
| View over 160 traces | < 0.5 s | 6.160 s (pass 1), 0.021 s (pass 2) | 0.057 s (pass 1), 0.019 s (pass 2) | 0.058 s (pass 1), 0.021 s (pass 2) | met |
| Working set, 160 opened + stats/view | < 2 GB | 652 MB | 145 MB | 144 MB | met |
| Peak working set during cold conversion | (n/a) | 672 MB | 664 MB | 660 MB (whole run peak 660 MB) | - |
| Cache size | ~30 MB/run | 2.02 GB for 57 runs (about 35 MB/run) | 2.02 GB | 2.02 GB | - |

Cold convert, 7.4 M samples (synthetic, same layout as the real runs), split per run:

| Step | Before | After |
|---|---|---|
| pandas CSV parse (`parse_file`) | 1.16 s | 1.16 s (unchanged; cannot be reduced here) |
| `encode_values` (mV to int32 codes) | 0.68 s | 0.12 s |
| `time_decimals` | 0.54 s | 0.08 s |
| uniform check + grid verify | 0.28 s | 0.28 s |
| block/overview tables + save | 0.1 s | 0.1 s |
| conversion total (`from_arrays`) | 1.51 s | 0.57 s |

`encode_values` and `time_decimals` now guess the number of decimals on a 64 K-sample prefix and
then verify on the whole array starting from that guess (a decimals value that fails on the prefix
fails on the whole array, so the result is the same smallest value as before; checked identical on
19 arrays including a late finer sample and NaN). The target is **missed**: pandas parsing alone
is 1.16 s, about the whole ~1.2 s budget, and it is the part this design does not change; with the
conversion at 0.57 s the measured total is 1.79 s per run. Closing the rest would need a faster
parser (out of scope: it must stay today's `parse_file`).

Root causes of the earlier stats and view slowness (Task 6b, measured):
- Stats: the per-block Python merge loop (one memmap row access + scalar Chan merge per block,
  ~1800 blocks per trace) was ~99 % of the time. Whole blocks are now combined in one vectorized
  step (N = sum n, mean = sum(n*mean)/N, M2 = sum M2 + sum n*(mean_b - mean)^2; first-occurrence
  argmin/argmax over the block extremes), then Chan-merged with the raw edge parts.
- View pass 1: not the overview pages (ov4096 is ~58 KB per trace) but `values_at(idx)`: gathering
  ~850 samples spread over the 30 MB mv.npy touched ~840 distinct pages per trace, each a hard page
  fault when the file is not in the file cache (the benchmark writes ~5.5 GB first, which evicts it).
  The overview rows already hold those exact sample values, so view() now takes values from them and
  reads mv.npy only at the two raw edges. No change to the on-disk format.
