# Trace Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `multi_file_analyzer.py` load, calculate and redraw 8–160 runs of 7.4 M samples quickly by converting each run once to a memory-mapped disk cache with precomputed block statistics and min/max overviews.

**Architecture:** A new module `trace_store.py` owns storage: `TraceData` (memory-mapped mV + uniform or explicit time + block stats + overviews) and `open_traces()` (cache lookup / convert / fallback). The GUI stops touching raw arrays and calls `TraceData.stats()`, `.view()`, `.index_range()`, `.times()`, `.values()`. Today's `window_stats` and `view_points` stay in the app module as the reference the new code is tested against.

**Tech Stack:** Python 3.13, numpy, pandas, tkinter, matplotlib; tests with stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-06-trace-cache-design.md`

## Global Constraints

- No new dependencies (no pytest, no psutil). Tests: `python -m unittest discover -s tests` from the `Analysis tool` folder.
- `trace_store.py` must not import `multi_file_analyzer` (no circular import); the parser is passed in.
- Cache root: `os.path.join(os.environ["LOCALAPPDATA"], "BBAnalyzer", "cache")`, overridable by argument (tests use a temp dir).
- `BLOCK = 4096`; `OV_LEVELS = (64, 512, 4096)`; `FORMAT_VERSION = 1`; uniform-time tolerance `1e-6 * dt`.
- mV stored as `int32 = round(mV × 10**d)` with the smallest `d` in 0..6 that round-trips every value exactly and fits int32; otherwise float64.
- Exactness vs today: Points, Max, Min, Time of max, Time of min identical; Mean and Std within `1e-12 * max(1, |value|)`.
- Entry folder name: `sha1(f"{normcase(abspath(path))}|{size}|{mtime_ns}|{FORMAT_VERSION}".encode("utf-8")).hexdigest()`. Entries are never replaced in place.
- Status texts (verbatim): `Converting {i}/{n} · {file} · about {eta} left`, `Opening cached {i}/{n}`, `{file} is in OneDrive only — downloading…`.
- This folder is **not a git repository**: there are no commit steps. Before Task 4, copy `multi_file_analyzer.py` to `Archived\multi_file_analyzer_<date>_before_cache.py`.
- Sorting by run # (`sorted_files`, `tests\test_sorting.py`) must keep passing.

## Review Focus

1. **Run shorter than one block or one overview cell** (n = 1, 63, 4095): stats and view must still match the reference. Test in Task 1 and Task 2.
2. **Window outside the data or between two samples:** Points 0 and NaNs, exactly like `window_stats`. Test in Task 1.
3. **Source CSV edited after it was cached while the old entry is still mapped:** must convert into a new folder and never fail because the old one is locked. Test in Task 3.
4. **Clear cache while traces are loaded:** in-use entries are kept and counted, nothing raises. Test in Task 5.
5. **Two app windows converting the same file at once:** the second rename finds the folder already there and opens it instead of failing. Test in Task 3.

---

### Task 1: `TraceData` core — encoding, time, block statistics

**Files:**
- Create: `trace_store.py`
- Test: `tests\test_trace_stats.py`

**Interfaces:**
- Produces:
  - `encode_values(mv: np.ndarray) -> tuple[np.ndarray, float | None]` — `(int32 array, scale = 10.0**d)` or `(float64 array, None)`.
  - `uniform_step(t: np.ndarray) -> float | None` — `dt` if `max|t − (t[0] + i·dt)| ≤ 1e-6·dt` (dt from `(t[-1]-t[0])/(n-1)`), else None; None for n < 2.
  - `build_blocks(mv_f64: np.ndarray, block=BLOCK) -> np.ndarray` — structured dtype `[("n","i4"),("mean","f8"),("m2","f8"),("vmin","f8"),("vmax","f8"),("imin","i8"),("imax","i8")]`, absolute first-occurrence indices, last block may be partial.
  - `class TraceData` with attributes `n`, `t_start`, `t_end` and methods `index_range(t0, t1) -> (i0, i1)`, `times(i0, i1)`, `values(i0, i1)`, `stats(t0, t1, population=True) -> dict` (keys of `window_stats` plus `"RMS"`).
  - `TraceData.from_arrays(t: np.ndarray, mv: np.ndarray) -> TraceData` (in memory, builds everything).

- [ ] **Step 1: Write the failing tests** in `tests\test_trace_stats.py` (import `multi_file_analyzer as ref` for `window_stats`):
  - `test_encode_roundtrip_4_decimals`: `mv = np.array([-0.8012, 1972.7706, 0.0])` → int32 and scale 1e4, `codes / scale` `==` `mv` bitwise.
  - `test_encode_falls_back_to_float64`: `mv = np.array([0.1234567891])` → dtype float64, scale None.
  - `test_uniform_step`: `t = np.arange(10)*2e-6` → `2e-6`; one sample shifted by 1e-9 → None.
  - `test_stats_match_reference_random_windows`: seed 0; uniform run n = 50_000 at 500 kHz, values `np.round(rng.normal(98, 2.7, n), 4)` with spikes of 1972.7706 at 3 indices and a duplicated max (tie); 500 random windows plus windows exactly on samples, inside one block, across one block edge, full range. For every key: ints and times equal; Mean/Std within `1e-12*max(1,|v|)`; both `population=True` and `False`.
  - `test_stats_nonuniform_time`: same with `t` jittered (`np.cumsum` of random positive steps), so explicit time is used.
  - `test_short_runs`: n in (1, 63, 4095, 4097) — full range and a mid window match the reference.
  - `test_empty_and_outside_windows`: window before data, after data, and between two samples → `Points == 0`, other values NaN (as reference).
  - `test_rms`: `RMS == sqrt(mean(seg**2))` within 1e-12 relative.
- [ ] **Step 2: Run** `python -m unittest tests.test_trace_stats -v` → FAIL (`No module named 'trace_store'`).
- [ ] **Step 3: Implement** in `trace_store.py`. `index_range` for uniform time: `i0 = ceil((t0 − t_start)/dt − 1e-6)`, `i1 = floor((t1 − t_start)/dt + 1e-6) + 1`, clamped to `[0, n]` and `i1 ≥ i0`; explicit time uses `np.searchsorted` like `window_slice`. `stats`: whole blocks fully inside `[i0, i1)` come from `blocks`; the partial head and tail use `values()`; combine (n, mean, M2) with Chan's formula: `δ = μb − μa; n = na+nb; μ = μa + δ·nb/n; M2 = M2a + M2b + δ²·na·nb/n`. Max/min: take the larger/smaller value; on equal values keep the smaller index. Std = `sqrt(M2/(n − ddof))` if `n > ddof` else NaN. `RMS = sqrt(μ² + M2/n)`.
- [ ] **Step 4: Run** `python -m unittest tests.test_trace_stats -v` → all PASS; `python -m unittest discover -s tests` → sorting tests still PASS.

### Task 2: Overview levels and `TraceData.view`

**Files:**
- Modify: `trace_store.py`
- Test: `tests\test_trace_view.py`

**Interfaces:**
- Consumes: `TraceData`, `build_blocks` from Task 1.
- Produces:
  - `build_overview(mv_f64: np.ndarray, size: int) -> np.ndarray` — dtype `[("vmin","f8"),("vmax","f8"),("imin","i8"),("imax","i8")]`, one row per `size` samples (last partial).
  - `TraceData.overviews: dict[int, np.ndarray]` for each size in `OV_LEVELS`, built by `from_arrays`.
  - `TraceData.view(x0, x1, raw_limit=12000, bins=3000) -> (t: np.ndarray, mv: np.ndarray, raw: bool)`.

- [ ] **Step 1: Write the failing tests** in `tests\test_trace_view.py`, comparing against `ref.view_points` on the Task 1 synthetic run (n = 2_000_000 so overviews are used):
  - `test_raw_when_few_points_visible`: a 5000-sample range → `raw is True`, `t` and `mv` equal the reference arrays exactly.
  - `test_reduced_keeps_every_spike`: full range, bins 3000 → `raw is False`; every spike time is in returned `t` with its value; `mv.max()`/`mv.min()` equal the reference's.
  - `test_reduced_is_time_ordered`: `np.all(np.diff(t) >= 0)`.
  - `test_reduced_point_count_bounded`: `len(t) <= 2*bins + 4`.
  - `test_short_runs_view`: n in (1, 63, 4095) full range equals reference.
- [ ] **Step 2: Run** `python -m unittest tests.test_trace_view -v` → FAIL.
- [ ] **Step 3: Implement `view`.** Same raw/reduced decision as `view_points` (visible count from `index_range` widened by 1 sample each side). Reduced: slice width `k = m // bins`; pick the largest `size` in `OV_LEVELS` with `size <= k` (none → raw samples, i.e. the reference algorithm); per slice, take the min and max of the overview rows it covers plus raw samples in the uncovered partial edges, then emit both points in index order; `t` from `times()` at the chosen indices.
- [ ] **Step 4: Run** `python -m unittest discover -s tests -v` → all PASS.

### Task 3: Disk cache — save, open, `open_traces`

**Files:**
- Modify: `trace_store.py`
- Test: `tests\test_trace_cache.py`

**Interfaces:**
- Consumes: `TraceData.from_arrays`, `encode_values`, `uniform_step` (Tasks 1–2).
- Produces:
  - `default_cache_dir() -> str`.
  - `entry_key(path: str) -> str` (rule in Global Constraints; uses `os.stat`).
  - `is_cached(path: str, cache_dir: str) -> bool`.
  - `is_cloud_only(path: str) -> bool` — `st_file_attributes & (0x400000 | 0x1000)`; False where the attribute doesn't exist.
  - `TraceData.save(folder: str) -> dict` (entry info for meta.json) and `TraceData.open(folder: str, info: dict) -> TraceData` (all `.npy` opened with `mmap_mode="r"`).
  - `open_traces(path: str, parse: Callable[[str], list[dict]], cache_dir: str | None = None) -> tuple[list[dict], str]` — trace dicts as `parse` returns them but with `t`/`mv` removed and `data: TraceData`, `n: int` added; status `"cached" | "converted" | "memory"`.

- [ ] **Step 1: Write the failing tests** in `tests\test_trace_cache.py` (temp cache dir via `tempfile.TemporaryDirectory`, a small CSV written in the instrument format with a footer, `parse = ref.parse_file`):
  - `test_convert_then_cached`: first call → `"converted"`; second → `"cached"`; `meta`, `summary`, `name`, `file`, `folder`, labels equal the first call's; `data.stats` over full range equal to the reference on parsed arrays.
  - `test_values_bit_identical`: `data.values(0, n)` `==` parsed `mv` exactly (`np.array_equal`), `data.times(0, n)` within 1e-12 of parsed `t`.
  - `test_cached_arrays_are_memmapped`: the mV array behind a cached trace is a `np.memmap`.
  - `test_source_change_makes_new_entry_while_old_mapped`: keep the first result alive, rewrite the CSV with different data (and bump mtime with `os.utime`) → `"converted"`, new values, two entry folders exist, no exception.
  - `test_corrupt_entry_is_rebuilt`: delete `mv.npy` in the entry → next call `"converted"` and correct data.
  - `test_concurrent_rename_uses_existing`: create the final entry folder (complete) between conversion and rename (patch `os.replace` to raise `FileExistsError`/`PermissionError` once after creating it) → returns data from the existing entry, temp folder removed.
  - `test_unwritable_cache_falls_back_to_memory`: `cache_dir` pointing at an existing *file* → status `"memory"`, stats still correct.
  - `test_excel_workbook_several_traces`: a 3-channel `.xlsx` written with pandas → 3 traces, each round-trips via cache.
- [ ] **Step 2: Run** `python -m unittest tests.test_trace_cache -v` → FAIL.
- [ ] **Step 3: Implement.** Layout per spec §3.2: `<entry>\meta.json` = `{"version", "source", "size", "mtime_ns", "traces": [{"dir": "0", fields…, "scale", "t_start", "dt"}, …]}`; per trace folder `"0"`, `"1"`, … with `mv.npy`, optional `time.npy`, `blocks.npy`, `ov64.npy`, `ov512.npy`, `ov4096.npy`. Write into `<entry>.tmp-<pid>` then `os.replace` to `<entry>`; `meta.json` written last, and an entry counts as complete only if `meta.json` parses and every listed file exists. Fields stored in meta.json are every key of the parsed dict except `t`, `mv`.
- [ ] **Step 4: Run** `python -m unittest discover -s tests -v` → all PASS.

### Task 4: Use `TraceData` in the GUI and show load progress

**Files:**
- Modify: `multi_file_analyzer.py` — `load_paths`, `_poll_loader`, `refresh_files`, `_time_extent`, `calculate`, `export_window_data`, `draw`, `_refresh_view`, `_update_view_label`, status bar in `_build_ui`.
- Test: `tests\test_app_integration.py`

**Interfaces:**
- Consumes: `open_traces`, `is_cached`, `is_cloud_only`, `default_cache_dir`, `TraceData` methods (Tasks 1–3).
- Produces: trace dicts in `App.files` carry `data` and `n` (no `t`/`mv`); `App.cache_dir: str` (set in `__init__` from `default_cache_dir()`); loader queue messages `("progress", (i, n, file, state, eta_s))` with `state in ("converting", "cached", "cloud")`.

- [ ] **Step 1: Back up** `multi_file_analyzer.py` to `Archived\multi_file_analyzer_<date>_before_cache.py`.
- [ ] **Step 2: Write the failing test** `tests\test_app_integration.py`: build `App()` with `cache_dir` overridden to a temp dir, load three small CSVs (runs 3, 1, 2) via `load_paths`, pump `update()` until `_loading` is False, then assert: list order is run 1, 2, 3; `app.files[0]["n"]` equals the CSV row count; `calculate()` results table `Mean/Std/Max/Min/Points` equal `ref.window_stats` on parsed arrays; `draw()` and `_refresh_view()` run without error; `"t" not in app.files[0]`. Load again in a second `App` → status text contains `"Opening cached"` at some point (capture via a wrapper on `status.config`). Destroy apps in `tearDown`.
- [ ] **Step 3: Run** `python -m unittest tests.test_app_integration -v` → FAIL.
- [ ] **Step 4: Implement.** Loader thread: for each path, `cloud = is_cloud_only(p) and not is_cached(p, cache_dir)`; send progress; call `open_traces(p, parse_file, self.cache_dir)`; ETA = mean seconds of conversions so far × uncached files remaining (no ETA text until one conversion finished: drop `· about … left`). ETA format: `"{s} s"` under 90 s else `"{round(s/60)} min"`. Status texts verbatim from Global Constraints. Add a `ttk.Progressbar` (determinate, length 160) packed right in the status frame, shown only while loading. Replace the nine raw-array uses with `f["n"]`, `data.t_start/t_end`, `data.stats(...)`, `data.view(...)`, `data.index_range/times/values`. A `"memory"` status shows once per session: `Couldn't write the cache folder; loading in memory (slower, uses more RAM).`
- [ ] **Step 5: Run** `python -m unittest discover -s tests -v` → all PASS.
- [ ] **Step 6: Manual check** with `python multi_file_analyzer.py`: drop `run_001.csv`–`run_008.csv` from the 620nm Config folder; status shows converting with ETA; Calculate works; close, reopen, add the same files → "Opening cached", loads in seconds.

### Task 5: File → Cache… dialog

**Files:**
- Modify: `trace_store.py`, `multi_file_analyzer.py` (`_build_menu`, new `show_cache_dialog`)
- Test: `tests\test_trace_cache.py` (add)

**Interfaces:**
- Consumes: entry layout from Task 3; `App.files[i]["data"]`.
- Produces:
  - `cache_info(cache_dir: str) -> tuple[int, int]` — (complete entries, total bytes incl. temp folders).
  - `clear_cache(cache_dir: str) -> tuple[int, int]` — (removed, kept); an entry that can't be deleted (locked because mapped) is kept and counted, never raises; leftover `*.tmp-*` folders are removed when possible.
  - `TraceData.entry_dir: str | None` (None for `"memory"` traces).

- [ ] **Step 1: Write failing tests**: `test_cache_info_counts_entries_and_bytes` (two converted files → `(2, >0)`); `test_clear_cache_keeps_entries_in_use` (one trace kept alive and mapped, one released with `del` + `gc.collect()` → `(1, 1)` on Windows; on a non-Windows OS assert `removed + kept == 2`).
- [ ] **Step 2: Run** `python -m unittest tests.test_trace_cache -v` → FAIL.
- [ ] **Step 3: Implement** the two functions and the dialog: a `tk.Toplevel` showing location (selectable), `"{entries} runs · {size:.1f} GB"`, buttons **Open folder** (`os.startfile`), **Clear cache** (disabled while `_loading`; `askyesno` first; afterwards `"Removed {r}. Kept {k} in use by loaded traces — Clear all, then clear the cache again."` when k > 0), **Close**. Menu item `Cache…` in File, above the separator before Exit.
- [ ] **Step 4: Run** `python -m unittest discover -s tests -v` → all PASS.

### Task 6: Real-data verification and benchmark

**Files:**
- Create: `tools\benchmark_cache.py`
- Test: `tests\test_real_run.py` (skipped when the real file is missing)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write** `tests\test_real_run.py`: `@unittest.skipUnless(os.path.exists(REAL))` with `REAL = r"C:\Users\MannaDoud\Sarmal\Project Trailblazer - Engineering\Bread Board\Bread Board Experiments and Test\Test 008_ABB Noise Identification\620nm Config\run_001.csv"`; convert into a temp cache; compare `stats` to `ref.window_stats` on the parsed arrays for full range and 200 random windows (exactness rule in Global Constraints); `view` full range extremes equal reference.
- [ ] **Step 2: Run** `python -m unittest tests.test_real_run -v` → PASS (≈ 1 min).
- [ ] **Step 3: Write** `tools\benchmark_cache.py <folder> [--fill 160]`: cold-converts every CSV in `<folder>` into a temp cache (prints total and per-run seconds), then warm-opens all (seconds), then with `--fill` adds synthetic 7.4 M-sample `TraceData.save` entries up to 160 traces and times `stats` over all traces (full window) and `view` over all traces (full range, `draw_budget(160)`); prints process working set and peak via `ctypes` `GetProcessMemoryInfo`.
- [ ] **Step 4: Run** it on the 620nm Config folder with `--fill 160`. Expected (spec §2): warm open of 57 runs < 10 s; stats over 160 < 0.5 s; view over 160 < 0.5 s; working set < 2 GB. Record the numbers in the spec under a new "Results" heading. If a target is missed, stop and report — don't tune blindly.
