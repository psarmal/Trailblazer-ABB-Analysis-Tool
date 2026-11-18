# Intensity histogram, spike threshold and control comparison — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Histogram window that overlays per-run intensity histograms (every sample, and spike heights) with one typed/dragged threshold, counts spikes per row window, and compares positive vs negative rows at matched PMT gain, using run info imported from a summary workbook. Excel-re-saved CSVs load.

**Architecture:** Pure-numpy/pandas logic in new `histogram.py` (bins, reference, comparison) and `run_info.py` (per-file overrides + Excel import), spike grouping in `noise.py`, the Tk window in new `histogram_view.py`. `multi_file_analyzer.py` gets a tolerant CSV parser and the toolbar/menu/removal hooks.

**Tech Stack:** Python 3.13, numpy, pandas (+openpyxl for .xlsx), tkinter/ttk, matplotlib, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-18-intensity-histogram-design.md` (read it with this plan).

**Workspace:** the project folder is not a git repo. Copy it (excluding `__pycache__`, `Archived/`, cache folders) into the session scratchpad as `work2/`, `git init`, set `core.autocrlf false` **before** the baseline commit (files are CRLF), commit, and work there. Tests: `python -m unittest discover -s tests` from that folder. Copy back only after the user's OK.

## Global Constraints

- Spike grouping: above-threshold samples join when the gap is **≤ max(50 µs, 1.5 × dt)**; non-uniform traces use 50 µs. 500 kHz results unchanged (Test 008 run_006 rate 11.93 /s constant stays).
- Symlog x axis: linear within **±5 mV** of the reference, log beyond, **30 bins per decade** (A's log parts and B).
- Plot A y = samples per second per mV; plot B y = spikes per second per bin; both log.
- x = signal − reference; reference = median of negative rows' baselines, else of all rows' baselines (baseline = `noise.spike_baseline`).
- At most **12** rows plotted.
- Default threshold = max over rows of (baseline + 10·σ), rounded to 0.1 mV.
- Detection limit = negative mean + 3·SD (sample SD, ddof = 1); comparisons only within one gain (rounded to 1 mV); rows without gain form "Gain unknown".
- Row roles: `"negative"`, `"positive"`, `"none"` (shown "— (not compared)"); only positive rows get × negative / SD above / Above limit.
- Run info stored in `%LOCALAPPDATA%\BBAnalyzer\run_info.json` keyed by `os.path.normcase(os.path.abspath(path))`; CSVs never modified.
- Clean CSVs parse **bit-identically**; `trace_store.FORMAT_VERSION` unchanged.
- No new dependencies beyond pandas' existing `openpyxl` use.

## Review Focus

1. **Rows with different DAQ steps** (a 0.1525 mV-step run next to a float-valued or 4-decimal-step run): A's linear bins use one grid; no crash, edges strictly increasing. Pinned in Task 3 (`test_edges_mixed_steps`).
2. **Threshold at or below the reference**: B shows the message text, A still draws, table counts still update. Pinned in Task 5 (`test_threshold_below_reference_message`).
3. **Row window with no data** (From beyond the trace's end): row shows "No samples in the window", is not plotted, other rows unaffected. Pinned in Task 5 (`test_empty_row_window`).
4. **Same run # loaded from two folders under the workbook's folder**: import marks it `conflict: 2 files`, unticked. Pinned in Task 4 (`test_preview_conflict`).
5. **Trace removed while the window computes**: its rows vanish, no exception, others finish. Pinned in Task 5 (`test_remove_trace_while_computing`).

---

### Task 1: Load Excel-re-saved CSVs

**Files:**
- Modify: `multi_file_analyzer.py` (`_parse_comment` callers, `_footer_lines`, `parse_instrument_csv`)
- Test: `tests/test_loader_excel.py` (new)

**Interfaces:**
- Produces: `_unquote_line(s: str) -> str` (strip whitespace; if it starts with `"`, take the first CSV field with `csv.reader`, i.e. remove the surrounding quotes and un-double `""`; then strip trailing commas and whitespace). `parse_instrument_csv(path)` keeps its return dict.

- [ ] **Step 1: Failing tests** (`ExcelResaveTest`). Helper `write_excel_style(path)` writes exactly the run-7 shape:
```
"# BB DAQ run 7 · experiment ""Luciferace"" · started 2026-11-17 14:55:49",,,,
# sample_rate_hz 10000,,,,
# pmt_gain_mv 700,,,,
time_s,value_mV,,value_mV,
0,0.2135,,0.3661,0.36314
0.0001,4.0282,,-0.0917,0.36176
... (2,000 rows, t = i*1e-4 printed with repr-style shortest form, mv on a 0.1525 grid, col C sometimes a number, col E "#DIV/0!" after row 1,000)
"# mean_mv 24.6334",,#DIV/0!,,#DIV/0!
# stopped_early no,,#DIV/0!,,#DIV/0!
```
  - `test_excel_resaved_loads`: `d = m.parse_instrument_csv(p)` → `len(d["t"]) == 2000`, `d["mv"]` equals the written column B, `d["meta"]["run"] == "7"`, `d["meta"]["experiment"] == "Luciferace"`, `d["meta"]["sample_rate_hz"] == "10000"`, `d["summary"]["mean_mv"] == "24.6334"`, `d["summary"]["stopped_early"] == "no"`; `trace_store.uniform_step(d["t"]) == 1e-4` (within 1e-12).
  - `test_clean_files_bit_identical`: for every `simulated_data/**/*.csv`, the result equals the baseline parser's (load the baseline `multi_file_analyzer.py` from the baseline commit via `git show` into a temp module in `setUpClass`; compare `t`, `mv` with `np.array_equal`, `meta`, `summary` with `==`).
  - `test_unquote_line`: `'"# a ""b"" c",,,' → '# a "b" c'`; `'# x 1,,,,' → '# x 1'`; `'# plain' → '# plain'`.
- [ ] **Step 2: Run** `python -m unittest tests.test_loader_excel -v` → `test_excel_resaved_loads` fails with `ReadError` "data rows aren't all numbers"; `test_unquote_line` fails (`AttributeError`).
- [ ] **Step 3: Implement.** Apply `_unquote_line` to each header-scan line and footer line before the `#` test and `_parse_comment`. Data: keep the fast `pd.read_csv(..., usecols=[0, 1], dtype="float64")`; on `ValueError` fall back to `pd.read_csv(..., usecols=[0, 1], dtype=str)` → `pd.to_numeric(errors="coerce")` → drop rows where either is NaN. `skip` must point at the real `time_s,…` header row.
- [ ] **Step 4: Run** the new test file and `tests.test_app_integration tests.test_sorting tests.test_trace_cache` → all PASS.
- [ ] **Step 5: Commit** `feat(loader): read CSVs re-saved by Excel`.

---

### Task 2: Shared spike grouping and `find_spikes_at`

**Files:**
- Modify: `noise.py` (`find_spikes`)
- Test: `tests/test_noise_spikes.py`

**Interfaces:**
- Produces: `noise.join_gap(dt: float | None, dead_time: float = DEAD_TIME) -> float` = `max(dead_time, 1.5 * dt)` (dt None → dead_time). `noise.find_spikes_at(trace, t0, t1, threshold: float, dead_time=DEAD_TIME) -> SpikeResult` (baseline/sigma from `spike_baseline`, `threshold` as given). `find_spikes(trace, t0, t1, k, dead_time)` = `find_spikes_at(..., baseline + k*sigma, ...)`.

- [ ] **Step 1: Failing tests** (class `GroupingTests`):
  - `test_consecutive_samples_join_at_10khz`: 10 kHz trace (`_trace`-style helper with dt = 1e-4), one pulse of 3 consecutive samples at 50, 80, 60 above a flat 0 baseline with tiny noise → `find_spikes_at(tr, t0, t1, 5.0)` gives 1 spike at the 80 sample's time, height 80 − baseline.
  - `test_gap_of_two_samples_splits_at_10khz`: samples i and i+2 above → 2 spikes.
  - `test_join_gap`: `join_gap(1e-4) == 1.5e-4`, `join_gap(2e-6) == 50e-6`, `join_gap(None) == 50e-6`.
  - `test_find_spikes_equals_find_spikes_at`: existing 500 kHz helper trace with spikes; `find_spikes(tr, a, b, 10)` times/heights equal `find_spikes_at(tr, a, b, base + 10*sigma)`.
- [ ] **Step 2: Run** `python -m unittest tests.test_noise_spikes -v` → new tests fail (`AttributeError: find_spikes_at`).
- [ ] **Step 3: Implement** by moving `find_spikes`' body into `find_spikes_at` and replacing `dead_time + 1e-12` with `join_gap(trace.dt, dead_time) + 1e-12`.
- [ ] **Step 4: Run** `python -m unittest tests.test_noise_spikes tests.test_noise_app tests.test_noise_real -v` → all PASS (real run_006 rate constant unchanged).
- [ ] **Step 5: Commit** `noise: group neighbouring samples into one spike; find_spikes_at`.

---

### Task 3: `histogram.py` — levels, bins, reference, comparison

**Files:**
- Create: `histogram.py`
- Test: `tests/test_histogram.py`

**Interfaces:**
- Produces:
  - Constants `LIN_MV = 5.0`, `PER_DECADE = 30`, `MAX_PLOTTED = 12`.
  - `level_counts(trace, i0, i1) -> tuple[np.ndarray, np.ndarray]`: distinct values (float64 mV, ascending) and their counts in samples `[i0, i1)`; computed with `np.unique` on `trace.codes[i0:i1]` then divided by `trace.scale` when not None.
  - `daq_step(levels: np.ndarray) -> float`: the most frequent positive difference between consecutive levels, rounded to 1e-6 (fallback: median difference; 1.0 if fewer than 2 levels).
  - `sample_edges(level_sets: list[np.ndarray], reference: float, top: float, bottom: float) -> np.ndarray`: edges in **relative** mV (value − reference). Linear part: bins of width `step = min(daq_step(each))`, centred on the grid `reference_level_nearest + k*step` covering [−LIN_MV, +LIN_MV]; log parts: `PER_DECADE` per decade from ±LIN_MV out to `top − reference` / `bottom − reference` (only if beyond ±LIN_MV). Strictly increasing.
  - `sample_density(levels, counts, edges, reference, duration) -> np.ndarray` = `np.histogram(levels − reference, edges, weights=counts)[0] / duration / np.diff(edges)`.
  - `spike_edges(threshold_rel: float, top_rel: float) -> np.ndarray | None`: geomspace with `PER_DECADE` per decade from `threshold_rel` to `top_rel` (at least 2 edges); `None` when `threshold_rel <= 0`.
  - `reference_level(baselines: list[float], roles: list[str]) -> float`.
  - `@dataclass RowStat: row_id: int; run: int | None; gain: float | None; role: str; t_from: float; t_to: float; n_spikes: int; duration: float` with properties `rate` (n/duration) and `err` (√n/duration).
  - `@dataclass Comparison: x_negative: float | None; sd_above: float | None; above_limit: bool | None`.
  - `compare(rows: list[RowStat]) -> tuple[dict[int, Comparison], list[str]]` — per-row comparisons (positive rows only; others all None) and the summary lines (gain groups descending, "Gain unknown" last, then before/after lines).
- Summary line formats (exact):
  - `"{gain:g} mV: {n} negative (mean {mean:.1f}/s, SD {sd:.1f}, limit {limit:.1f}/s) · {m} positive"` + `" · weakest positive ÷ strongest negative = {r:.0f}×"` when both roles present. n = 1 → `SD –, limit –`. Gain None → prefix `"Gain unknown:"`.
  - Before/after (one per later row of a run with ≥ 2 rows, ordered by From): `"run {run}: {from:g}–{to:g} s ÷ {from0:g}–{to0:g} s = {ratio:.1f}× ({rate:,.0f} ± {err:,.0f} vs {rate0:,.0f} ± {err0:,.0f} /s)"`.

- [ ] **Step 1: Failing tests:**
  - `test_level_counts_exact`: trace from `TraceData.from_arrays` with values on a 0.1525 grid, one level never used → returned levels exclude it; counts sum to the window length.
  - `test_skipped_level_is_empty_bin`: density over `sample_edges` has a 0 in the bin of the missing level and > 0 on both neighbours.
  - `test_density_scaling`: 1,000 samples all at one level, duration 2 s, bin width = step → density = 1000 / 2 / step.
  - `test_edges_mixed_steps` (Review Focus 1): level sets with steps 0.1525 and 0.0001 plus a level at 300 mV → strictly increasing edges, linear part step 0.0001… capped: if `(2*LIN_MV)/step > 20_000`, use `step = 2*LIN_MV/20_000` (state this cap in the docstring).
  - `test_spike_edges`: `spike_edges(5.0, 5000.0)` spans 5→5000, 90 ± 1 bins; `spike_edges(0.0, 100.0) is None`.
  - `test_reference_level`: negatives present → median of negatives only; none → median of all.
  - `test_compare_matches_hand_values`: gain 1200: negatives 27.4/s and 17.1/s, positives 1423.2 and 742.3 (RowStats with duration 10 s and n = 274, 171, 14232, 7423) → mean 22.25, SD 7.283, limit 44.10; × negative for 1423.2 = 63.96; SD above = 192.4; above_limit True; line ends with `= 27×`.
  - `test_compare_n1_and_no_negatives`, `test_role_none_excluded`, `test_gain_unknown_group`, `test_before_after_line` (run 7 rows 0–2.8 s with 707 spikes and 4–60 s with 79,708 → `"run 7: 4–60 s ÷ 0–2.8 s = 5.6× (1,423 ± 5 vs 252 ± 9 /s)"`).
- [ ] **Step 2: Run** `python -m unittest tests.test_histogram -v` → fail (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `histogram.py` per the interfaces (no Tk, no app imports).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `histogram: levels, bins, reference and per-gain comparison`.

---

### Task 4: `run_info.py` — per-file overrides and Excel import

**Files:**
- Create: `run_info.py`
- Test: `tests/test_run_info.py`

**Interfaces:**
- Produces:
  - `@dataclass RunInfo: role: str = ""; label: str = ""; gain_mv: float | None = None`.
  - `default_path() -> str`; `file_key(path) -> str`; `load(path) -> dict[str, RunInfo]` (unreadable/invalid → `{}`); `save(path, store)` (atomic, like `settings.save_settings`).
  - `effective_gain(info: RunInfo | None, meta: dict) -> tuple[float | None, str | None]` → `(value, "override" | "file" | None)`; file value from meta key `pmt_gain_mv` (case-insensitive, numeric part).
  - `role_from_label(label: str) -> str`: contains "neg" → "negative", "pos" → "positive", else "".
  - `class ImportFailed(Exception)`; `read_workbook(path) -> dict[str, pd.DataFrame]` (header = first row; `PermissionError` → `ImportFailed("The file is locked. Close it in Excel and try again.")`).
  - `@dataclass ImportChoice: sheet: str; run_col: str; gain_col: str | None; sample_col: str | None; lookup: tuple[str, str, str] | None` (sheet, key col, label col).
  - `guess_choice(sheets) -> ImportChoice`: first sheet; run = header equal to "run" (case-insensitive, stripped); gain = first header containing "gain"; sample = first of "conditions", "sample", "ch", "channel" **whose column has any non-empty cell** (so Summary.xlsx's empty "Conditions" is skipped and "CH" chosen); lookup = another sheet with exactly 2 non-empty columns whose first column's values (as str of int when integral) cover ≥ 1 of the sample values.
  - `@dataclass PreviewRow: run: int; path: str | None; gain: float | None; label: str; role: str; status: str; ticked: bool`.
  - `build_preview(sheets, choice, excel_path: str, traces: list[tuple[str, int | None]]) -> list[PreviewRow]` — `traces` are (file path, run #) of loaded traces; match when run # equal and the file lies under `os.path.dirname(excel_path)`. Status strings exactly: `"ok"`, `"no loaded trace"`, `"conflict: {n} files"`, `"gain not a number"`; `ticked = status == "ok"`. Run cells like `7`, `7.0`, `"7"` all match run 7.
  - `apply_preview(store, rows) -> int` — writes ticked rows (role, label, gain) to `store[file_key(path)]`, returns the count.

- [ ] **Step 1: Failing tests** (build a Summary.xlsx-shaped workbook with `pandas.ExcelWriter` in a temp dir: Sheet1 headers `Run, Flow cell, CH, Conditions, LED power, …, PMT gain, Points` with runs 1–24, CH 3/7/4/8 per block of 6, Conditions empty, gains as in the spec table; Sheet2 `CH` / unnamed second column with 3–6 "Negative control", 7–12 "Positive control"; plus CSV stubs `sub/run_006.csv`, `sub/run_007.csv`):
  - `test_guess_choice_summary_shape`: run_col "Run", gain_col "PMT gain", sample_col "CH", lookup ("Sheet2", "CH", <second column name>).
  - `test_preview_roles_and_gains`: run 6 → gain 1200, label "Negative control", role "negative", status "ok"; run 7 → 1200, positive; runs without a loaded trace → "no loaded trace", unticked.
  - `test_preview_conflict` (Review Focus 4): two loaded paths with run 7 under the folder → `"conflict: 2 files"`, unticked.
  - `test_gain_not_a_number`, `test_apply_and_round_trip` (apply → save → load equals), `test_corrupt_store_is_empty`, `test_effective_gain_precedence` (override 1200 beats meta 700 → `(1200.0, "override")`; no override → `(700.0, "file")`; neither → `(None, None)`), `test_locked_workbook_message` (patch `pd.read_excel` to raise `PermissionError`).
- [ ] **Step 2: Run** `python -m unittest tests.test_run_info -v` → fail (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `run_info.py` (no Tk, no app import).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `run_info: per-file sample/gain overrides and workbook import`.

---

### Task 5: Histogram window — rows, threshold, plots, table, comparison

**Files:**
- Create: `histogram_view.py`
- Modify: `multi_file_analyzer.py` (toolbar button after `noise_btn`: `self.hist_btn = ttk.Button(bar, text="Histogram…", command=self.open_histogram)`; View menu item "Histogram…"; `open_histogram()`; in `remove_selected` and the load/remove paths call `self._histogram.drop_missing()` when open; `clear_all` closes it; App attribute `self._histogram = None`)
- Test: `tests/test_histogram_view.py`

**Interfaces:**
- Consumes: Task 2 `find_spikes_at`, `spike_baseline`; Task 3 everything; Task 4 `RunInfo`, `load`, `effective_gain`, `default_path` (App gets `run_info_path` constructor kwarg like `settings_path`, default `run_info.default_path()`).
- Produces (used by tests and Task 6):
  - `class HistogramWindow(tk.Toplevel)` with `__init__(self, app)`.
  - `@dataclass Row: id: int; trace: dict; t_from: float; t_to: float; role: str; plot: bool; dashed: bool = False`.
  - Attributes/methods: `rows: list[Row]`, `threshold: float`, `reference: float`, `busy: bool`, `set_threshold(mv: float) -> bool`, `duplicate_row(row_id) -> Row`, `remove_row(row_id)`, `add_shown_runs() -> int`, `set_row_window(row_id, t_from, t_to) -> bool`, `set_row_role(row_id, role)`, `set_row_plot(row_id, on) -> bool` (False when it would exceed 12), `drop_missing()`, `table() -> pd.DataFrame` (columns exactly: `Plot, Run, Sample, Gain (mV), From (s), To (s), Baseline (mV), Spikes, Rate (/s), ±, Median peak (mV), Max peak (mV), × negative, SD above negative, Above limit`), `summary_lines() -> list[str]`, `ax_a`, `ax_b`, `thr_line_a`, `b_message() -> str`.
  - Row order in `table()`: gain descending, "Gain unknown" last, then run #, then From.
  - Sample cell text: `"Negative"`, `"Positive"`, `"— (not compared)"`; Gain cell: `"1200"` (override) or `"700 (file)"`.
  - B message text exactly: `f"Set the threshold above the reference level ({reference:.2f} mV)"`.
  - Empty-window row note: `"No samples in the window"` in the Spikes column, row not plotted.

- [ ] **Step 1: Failing tests** (app fixture like `tests/test_noise_app.py`: temp settings + temp run_info path; three 10 kHz CSVs: neg-like (rare 50–500 mV pulses, ~20/s), pos-like (~500/s), and a 4 s run whose pulses start at 2 s; run info pre-written: runs 1 negative 1200, 2 positive 1200, 3 positive 1200):
  - `test_opens_with_shown_rows_and_default_threshold`: one row per shown trace in sort order, From/To = main window clipped; threshold = max(baseline+10σ) rounded to 0.1.
  - `test_typed_threshold_updates_counts`: `set_threshold(t)` then wait `not busy` → Spikes equal `noise.find_spikes_at` per row; invalid text in the entry → red, threshold unchanged.
  - `test_drag_threshold_line`: press/move/release on `ax_a` at the line → threshold equals the release x + reference (within one pixel's worth); counts updated after release.
  - `test_duplicate_row_before_after`: duplicate run 3, set windows 0–1.9 and 2–4, set first to role "none" → its comparison cells "–"; `summary_lines()` contains a `"run 3: 2–4 s ÷ 0–1.9 s = "` line.
  - `test_comparison_columns`: positive rows get × negative and SD above computed by `histogram.compare` on the same RowStats; Sample/Gain cell texts as specified.
  - `test_plot_limit_12`: 14 rows → `set_row_plot` refuses the 13th; only 12 lines per axes.
  - `test_threshold_below_reference_message` (Review Focus 2), `test_empty_row_window` (Review Focus 3), `test_remove_trace_while_computing` (Review Focus 5: patch `histogram.level_counts` to sleep 0.2 s, remove a trace mid-run → no exception, its rows gone, others filled).
  - `test_toolbar_and_raise`: `app.hist_btn` exists; `open_histogram()` twice returns the same window; `clear_all()` closes it.
- [ ] **Step 2: Run** `python -m unittest tests.test_histogram_view -v` → fail (`ModuleNotFoundError: histogram_view`).
- [ ] **Step 3: Implement.** Worker thread per compute pass, results via `queue` polled with `after(50)` (same pattern as `NoiseView`); level counts cached per `(id(trace), i0, i1)`; a threshold change during a pass restarts the spike pass. Threshold line drag uses matplotlib press/motion/release with blitting like `chart_tools`. Row colours = `trace["color"]`; dashed rows use `ls="--"`.
- [ ] **Step 4: Run** the new tests plus `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `histogram window: rows, threshold, plots, comparison`.

---

### Task 6: Editing, import dialog, export and image

**Files:**
- Modify: `histogram_view.py`
- Test: `tests/test_histogram_view.py`

**Interfaces:**
- Consumes: Task 4 `read_workbook`, `guess_choice`, `build_preview`, `apply_preview`, `save`, `ImportFailed`; Task 5 window API.
- Produces: `HistogramWindow.edit_gain(row_id, text: str) -> bool` (empty → clear override; non-number → False), `edit_sample(row_id, role: str, label: str | None = None)`, `save_row_as_run_info(row_id)`, `open_import(path: str | None = None) -> ImportDialog`, `export(path: str)`, `save_image(path: str)`. `class ImportDialog(tk.Toplevel)` with `choice: ImportChoice`, `preview: list[PreviewRow]`, `set_ticked(i, on)`, `apply() -> int`.
- Export layout (CSV; `.xlsx` → same table on sheet "Histogram" and the lines on sheet "Summary"): the `table()` rows; one blank line; `summary_lines()`; blank; `Threshold (mV),{threshold}`, `Reference (mV),{reference}`; then per row `File,{path},Gain source,{override|file|none}`.

- [ ] **Step 1: Failing tests:** `test_edit_gain_moves_row_between_groups`; `test_edit_gain_rejects_text`; `test_save_row_as_run_info_persists` (reopen app → same role/gain); `test_import_dialog_preview_and_apply` (Summary-shaped workbook from Task 4's helper; dialog's guessed choice; apply → table roles/gains updated, run_info.json written); `test_import_locked_file_message` (patched `read_workbook` raising `ImportFailed` → `messagebox.showerror` called with that text, nothing changed); `test_export_csv_contents` (table header row, summary lines, threshold/reference lines present); `test_save_image_png` (file exists, PNG signature).
- [ ] **Step 2: Run** → fail (`AttributeError`).
- [ ] **Step 3: Implement** (double-click on Sample/Gain cells opens an in-place editor; Sample = dropdown Negative / Positive / — (not compared) plus label entry; row context menu "Save as run info").
- [ ] **Step 4: Run** `python -m unittest tests.test_histogram_view tests.test_run_info -v` and the full suite → PASS.
- [ ] **Step 5: Commit** `histogram window: editing, run-info import, export`.

---

### Task 7: Real data, performance and results

**Files:**
- Create: `tests/test_histogram_real.py`
- Modify: spec (append `## 9. Results`)

- [ ] **Step 1: Real-data test** (skip if folder missing or files cloud-only; path from spec §7): run 7 loads (600,064 samples, `dt == 1e-4`); at threshold 5.101 mV: run 6 full → 411 spikes; run 7 4–60 s → rate within 1 % of 1,423 /s; run 7 0–2.8 s → within 2 % of 252 /s; `guess_choice` + `build_preview` on `..\Summary.xlsx` (copy it to a temp file first; it may be open in Excel) → run 6 negative 1200, run 7 positive 1200.
- [ ] **Step 2: Run** `python -m unittest tests.test_histogram_real -v` → PASS; full suite → OK.
- [ ] **Step 3: Performance (not a unit test):** open the window on the 24 Test 015 runs and on 8 Test 008 500 kHz runs (7.4 M samples each); record first-compute time and threshold-change time. Target: ≤ 1 s per 7.4 M-sample row first time, ≤ 0.3 s per row on threshold change.
- [ ] **Step 4: Append results** to the spec (table: item, target, measured, verdict).
- [ ] **Step 5: Commit** `histogram: real-data check and results`.
