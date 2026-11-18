# Binned view Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Binned" window that overlays each shown run's bin-averaged signal (default 0.5 s bins) with the main chart's tools on the overlay, optional stacked raw panels (up to 8), CSV export and PNG save.

**Architecture:** Pure numpy binning in new `binning.py`; the Tk window in new `binned_view.py`, which reuses `chart_tools.ChartTools` on the overlay axes by wrapping each run's binned series as a `TraceData`. `multi_file_analyzer.py` adds the button/menu, `open_binned()`, a refresh call at the end of `App.draw`, and closing in `clear_all`.

**Tech Stack:** Python 3.13, numpy, tkinter/ttk, matplotlib, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-18-binned-view-design.md` (read with this plan; §7a holds settled calls).

**Workspace:** copy the project folder (excluding `__pycache__`, `Archived/`, caches) to the scratchpad as `work3/`, `git init`, `git config core.autocrlf false` before the baseline commit, keep files CRLF. Tests: `python -m unittest discover -s tests`. Copy back only after the user's OK, archiving replaced files in `Archived\2026-11-18_before_binned_view\` with a README.

## Global Constraints

- Bin value = mean of the samples in `[t_start + k·b, t_start + (k+1)·b)`; plotted at the bin centre; last partial bin kept; empty bins dropped.
- Default bin **0.5 s**; reject ≤ 0, non-numbers, > **200,000** bins in any run, or bin < that run's sample interval (dt, or median gap for uneven time). Message exactly: `Bin too small for {file} (dt {dt:g} s).`
- From/To apply to all runs by bin centre; defaults From = min run start, To = max run end; empty To = end.
- Raw panels: tick box "Raw panels", ticked by default, at most **8** panels; note exactly `Showing raw panels for the first 8 of {n} runs.`
- Panel title: `{label} · started {started} · position {position_mm} mm · mean {mean:.2f} mV, max {max:.0f} mV`, omitting parts without metadata.
- Legend: ≤ 12 runs one column; 13–24 two columns; > 24 none, note `Legend hidden for more than 24 runs.`
- No-runs message exactly: `Show a run in the main window to bin it.`
- Raw data, Statistics, Noise, Histogram untouched.

## Review Focus

1. **Run shorter than one bin** (0.3 s run, 0.5 s bin): one partial bin plotted, no crash. Pinned in Task 1 (`test_run_shorter_than_bin`).
2. **Bin size changed after zooming and placing cursors**: view refits, cursors keep their times, no exception. Pinned in Task 2 (`test_bin_change_refits_keeps_cursors`).
3. **All runs hidden in the main window**: overlay shows the no-runs message, no panels, export writes only the comment line. Pinned in Task 3 (`test_no_shown_runs`).
4. **Run removed while it is being binned on the worker**: no exception, its line never appears. Pinned in Task 2 (`test_remove_while_binning`).
5. **Float-valued trace with uneven time and a gap**: bins correct, empty bin dropped. Pinned in Task 1 (`test_uneven_time_gap_dropped`).

---

### Task 1: `binning.py`

**Files:** Create `binning.py`; Test `tests/test_binning.py`

**Interfaces:**
- Produces: `MAX_BINS = 200_000`; `sample_interval(trace) -> float` (trace.dt, else median of `diff(trace.t)`); `check_bin(trace, bin_s: float, name: str) -> str | None` (the exact message above, or `None` when valid; raises nothing); `bin_means(trace, bin_s: float) -> tuple[np.ndarray, np.ndarray]` (centres s, means mV; float64).

- [ ] **Step 1: Failing tests:** `test_uniform_exact_means` (10 kHz, values `arange(n)%10` → 0.001 s bins of 10 samples have mean 4.5 each, centres `t_start + (k+0.5)*0.001`); `test_last_partial_bin` (n = 25, 10-sample bins → 3 bins, last mean of 5 samples); `test_run_shorter_than_bin` (0.3 s run, bin 0.5 → 1 bin, centre t_start + 0.25, mean = overall mean); `test_uneven_time_gap_dropped` (float values with 7 decimals so `scale is None`, times with a 2 s gap → bins inside the gap absent, others correct); `test_check_bin` (bin < dt → message `"Bin too small for run_001.csv (dt 0.0001 s)."`; 0.5 s on a 7.4 M-sample-equivalent short trace → None; bin giving 200,001 bins → message; 0 and -1 → message `"Bin size must be a positive number of seconds."`); `test_large_uniform_matches_reference` (1.2 M samples, compare with a plain reshape/mean reference to 1e-9).
- [ ] **Step 2: Run** `python -m unittest tests.test_binning -v` → `ModuleNotFoundError`.
- [ ] **Step 3: Implement.** Uniform: bin edge indices `ceil((k*b)/dt - 1e-9)`, sums by `np.add.reduceat` over `trace.values` read in chunks of ≤ 2 M samples (no full float copy of a 13 M run at once); uneven: `np.searchsorted(trace.t, edges)`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `binning: bin means per run`.

---

### Task 2: Binned window — controls, overlay, chart tools, app hooks

**Files:** Create `binned_view.py`; Modify `multi_file_analyzer.py` (button `self.bin_btn = ttk.Button(bar, text="Binned…", command=self.open_binned)` after `hist_btn`; View menu "Binned…"; `open_binned()`; `self._binned = None`; at the end of `App.draw`: `if self._binned is not None: self._binned.refresh()`; `clear_all`: close it); Test `tests/test_binned_view.py`

**Interfaces:**
- Consumes: Task 1 `bin_means`, `check_bin`; `chart_tools.ChartTools`; `App._label`, `App._selected_files`, file dicts (`"data"`, `"color"`, `"show"`, `"file"`, `"meta"`).
- Produces: `class BinnedWindow(tk.Toplevel)`, `__init__(self, app)`; attributes `bin_s`, `t_from`, `t_to` (None = end), `busy`, `tools`, `ax` (overlay), `series: dict[int, tuple]` keyed by `id(trace dict)`; methods `set_bin(text) -> bool`, `set_range(from_text, to_text) -> bool`, `refresh()`, `overlay_lines() -> list[Line2D]`, `status_text() -> str`, `close()`. Entries `bin_entry`, `from_entry`, `to_entry` turn `#fecaca` on invalid input.

- [ ] **Step 1: Failing tests** (fixture: 3 CSVs at 10 kHz, 4 s, run levels 0, 10, 20 mV + noise; run 3 shifts +10 mV after 2 s):
  - `test_opens_with_one_line_per_shown_run`: lines in sort order, colours = traces' colours, labels = `app._label(f)`; 8 points each at 0.5 s.
  - `test_bin_change_updates_lines`: `set_bin("0.25")` → 16 points; `set_bin("0")` → False, entry red, previous lines kept.
  - `test_range_filter`: `set_range("1", "3")` → only centres in [1, 3]; `set_range("3", "1")` → False.
  - `test_hidden_run_added_on_show`: hide run 2 in the main window → 2 lines; show again → 3.
  - `test_cursor_reads_bin_mean`: cursors mode, A at 2.25 s → readout value = run's bin mean at centre 2.25 (4 decimals).
  - `test_bin_change_refits_keeps_cursors` (Review Focus 2): zoom ↔, place A/B, `set_bin("0.25")` → `tools.cursor_times` unchanged, x-limits = data range.
  - `test_refresh_keeps_zoom`: zoom ↔, rename a trace → x-limits unchanged.
  - `test_select_window_sets_from_to_only`: span 1–2 s in select mode → window From/To 1, 2; `app.t0_var` unchanged.
  - `test_legend_columns`: 13 shown runs (duplicate dicts) → legend `_ncols == 2`; 25 → no legend and status note.
  - `test_remove_while_binning` (Review Focus 4): patch `binning.bin_means` slow; remove run 2 mid-pass → no callback exceptions, 2 lines.
  - `test_toolbar_open_raise_clear`: `app.bin_btn` text "Binned…"; second `open_binned()` returns the same window; `clear_all()` closes it.
- [ ] **Step 2: Run** `python -m unittest tests.test_binned_view -v` → fail (`ModuleNotFoundError`).
- [ ] **Step 3: Implement.** Worker thread + queue + generation counter like `histogram_view` (runs ≥ 1 M samples on the worker, others inline); cache `(trace data object, bin_s) -> series` storing the object and checking identity (not `id()` alone); ChartTools gets `get_shown_traces` returning `{"data": TraceData.from_arrays(centres, means), "name": label, "color": ...}` dicts and `on_window_selected=set_range`-equivalent, `on_reset` = refit.
- [ ] **Step 4: Run** the new tests and the full suite → PASS.
- [ ] **Step 5: Commit** `binned window: overlay, controls, chart tools`.

---

### Task 3: Raw panels, export, image

**Files:** Modify `binned_view.py`; Test `tests/test_binned_view.py`

**Interfaces:**
- Produces: `raw_var` (tk.BooleanVar), `set_raw_panels(on: bool)`, `panel_axes: list`, `export(path)`, `save_image(path)`.

- [ ] **Step 1: Failing tests:** `test_raw_panels_shown_and_hidden` (3 panels + overlay = 4 axes; `set_raw_panels(False)` → 1 axes; overlay keeps lines and cursors); `test_panel_title` (meta `started`, `position_mm` present → exact title with mean/max from `TraceData.stats` over From/To; without metadata → only label + mean/max); `test_eight_panel_limit` (10 shown runs → 8 panels, `status_text()` contains `Showing raw panels for the first 8 of 10 runs.`); `test_export_csv` (first line `# bin_s 0.5, from 0, to 3.9999`; header `"{label} time (s)","{label} mean (mV)"` per run; values equal `series`); `test_no_shown_runs` (Review Focus 3: hide all → overlay text `Show a run in the main window to bin it.`, 0 panels, export has only the comment line); `test_save_image_png`.
- [ ] **Step 2: Run** → fail (`AttributeError`).
- [ ] **Step 3: Implement** (panels drawn with `TraceData.view(From, To, bins=1500)`, static; Save image = ChartTools' button saving the whole figure).
- [ ] **Step 4: Run** the new tests and the full suite → PASS.
- [ ] **Step 5: Commit** `binned window: raw panels, export`.

---

### Task 4: Real data, performance, results

**Files:** Create `tests/test_binned_real.py`; Modify spec (append `## 9. Results`)

- [ ] **Step 1: Real-data test** (paths from `tests/test_histogram_real.py`; skip if missing/cloud-only): run 7 at 0.5 s → mean of bins with centres 20–60 s between 20 and 35 mV, bins with centres < 2.5 s all below 6 mV; run 6 all bins below 2 mV.
- [ ] **Step 2: Run** `python -m unittest tests.test_binned_real -v` → PASS; full suite → OK.
- [ ] **Step 3: Performance** (not a unit test): `bin_means` at 0.5 s on cached Test 008 run_001/006/033 (7.4–13.2 M samples); target < 0.5 s each; and opening the window on the 24 Test 015 runs.
- [ ] **Step 4: Append results** to the spec (item, target, measured, verdict).
- [ ] **Step 5: Commit** `binned view: real-data check and results`.
