# Noise Analysis, Cursors and Axis Zoom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add spectrum + spike noise analysis (Noise tab, noise view, settings), draggable A/B measurement cursors, and an axis-zoom toolbar with Back to the analyzer.

**Architecture:** Pure numpy maths in a new `noise.py`; persisted user settings in a tiny `settings.py`; all chart interaction (modes, axis zoom, history, cursors) in a new `chart_tools.py` that replaces matplotlib's NavigationToolbar2Tk and the app's SpanSelector; `multi_file_analyzer.py` only wires these together (Analyze noise worker, Noise tab, noise view, settings dialog). `trace_store.py` gains one read-only accessor.

**Tech Stack:** Python 3.13, numpy (FFT), pandas, tkinter, matplotlib; stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-07-noise-and-chart-tools-design.md`

## Global Constraints

- No new dependencies (no scipy, pytest, psutil). Tests: `python -m unittest discover -s tests` from the project folder; all 62 existing tests must keep passing.
- `noise.py`, `settings.py`, `chart_tools.py` and `trace_store.py` must not import `multi_file_analyzer`.
- Cache format unchanged; `FORMAT_VERSION` stays 1.
- Spectrum: `SEG = 2**19` samples, Hann window, 50 % overlap, segment mean removed, one-sided PSD scaled by `2 / (fs * sum(w**2))` (mV²/Hz); component amplitude = `sqrt(sum(psd[j-3:j+4]) * df)` around the highest bin `j` within ±3 bins of f.
- Peaks: top 3, `f_min` default 2 Hz, peaks within 5 Hz of a chosen one skipped.
- Spikes: threshold = baseline + k·σ, k default 10 (valid 0.5–1000); upward only; dead time 50 µs (fixed); spike list display cap 10 000.
- Settings file: `%LOCALAPPDATA%\BBAnalyzer\settings.json` = `{"watch_hz": [...], "spike_k": 10, "f_min_hz": 2}`.
- Noise table columns (verbatim, in order): `Trace`, `Baseline (mV)`, `Noise σ (mV)`, `Threshold (mV)`, `Spikes`, `Rate (/s)`, `Median spacing (s)`, `Spacing rate (Hz)`, `Peak 1 (Hz)`, `Peak 1 (mV RMS)`, `Peak 2 (Hz)`, `Peak 2 (mV RMS)`, `Peak 3 (Hz)`, `Peak 3 (mV RMS)`, then `<f:g> Hz (mV RMS)` per watch frequency.
- Texts (verbatim): `Analyzing noise {i}/{n} · {file}`; `Noise analysis cancelled ({done} of {n} done).`; `Window or settings changed — press Analyze noise to update.`; `Spectrum needs evenly spaced samples.`; `No samples in the window`; `Showing the first 10,000 of {n:,} spikes.`; `Resolution {r:.1f} Hz`.
- Toolbar (left to right): `Select window`, `Zoom ↔`, `Zoom ↕`, `Cursors`, `Back`, `Reset zoom`, `Save image…`. Keys: `W`, `X`, `Y`, `C`, `BackSpace`, `Home`. History max 50. Drags narrower than 3 px ignored. Cursor grab distance 6 px. No mouse-wheel zoom; no pan tool.
- Cursor readout format: `{label}   A: {tA:.6f} s, {vA:.4f} mV | B: {tB:.6f} s, {vB:.4f} mV | Δt {dt} (1/Δt = {f}) | ΔV {dv:.4f} mV` — Δt in ms (3 decimals) below 1 s else s (6 decimals); 1/Δt in Hz (1 decimal), kHz (3 decimals) above 10 kHz, `1/Δt —` when Δt = 0; `–` for a value outside the trace's time range. Copy header: `Trace\tt_A (s)\tV_A (mV)\tt_B (s)\tV_B (mV)\tΔV (mV)\tΔt (s)\t1/Δt (Hz)`.
- Keep each file's existing line endings (CRLF in the existing .py files).
- The project folder is not a git repo: execution happens in a git working copy in the scratchpad and is copied back at the end with the user's OK.

## Review Focus

1. **Window shorter than one FFT segment** (e.g. a 0.1 s window): spectrum still works with one segment and reports its coarser resolution. Test in Task 2.
2. **Watch frequency above Nyquist or below the resolution**: that column shows "–", the others still fill. Test in Task 2.
3. **Trace without uniform time** (Excel column, jittered time): spectrum columns "–" with the note, spikes still computed. Test in Task 3.
4. **Redraws while a mode is active** (sort change, show/hide, legend change call `App.draw()`, which clears the axes): mode, cursor times and zoom history survive; cursors are redrawn. Test in Tasks 4 and 5.
5. **Traces removed or re-sorted after Analyze noise**: Noise rows follow the current sort, rows of removed traces disappear, nothing crashes; Analyze noise is disabled while loading. Test in Task 6.

---

### Task 1: Settings persistence

**Files:**
- Create: `settings.py`
- Test: `tests\test_settings.py`

**Interfaces:**
- Produces:
  - `DEFAULTS = {"watch_hz": [], "spike_k": 10.0, "f_min_hz": 2.0}`
  - `default_settings_path() -> str` (`%LOCALAPPDATA%\BBAnalyzer\settings.json`)
  - `load_settings(path: str) -> dict` — defaults merged with the file; any read/JSON/type problem → `dict(DEFAULTS)` (fresh copy).
  - `save_settings(path: str, settings: dict) -> None` — creates the folder; writes JSON atomically (temp file + `os.replace`).
  - `parse_settings(watch_text: str, k_text: str, f_min_text: str) -> tuple[dict, list[str]]` — returns (settings, errors); watch entries split on commas/whitespace, each must be a number > 0; k in 0.5–1000; f_min > 0. Each error names the offending entry, e.g. `Watch-list entry "abc" is not a number.`; settings is None-free only when errors is empty.

- [ ] **Step 1: Write failing tests** `test_roundtrip` (save then load gives the same dict), `test_missing_file_gives_defaults`, `test_corrupt_file_gives_defaults` (file containing `{not json`), `test_wrong_types_give_defaults` (`{"spike_k": "x"}`), `test_parse_valid` (`"60, 120 15550"`, `"10"`, `"2"` → `[60.0, 120.0, 15550.0]`, 10.0, 2.0, no errors), `test_parse_errors` (`"60, abc, -5"`, `"0.1"`, `"0"` → three-plus errors each naming its entry), `test_defaults_not_mutated` (mutating a loaded dict's list doesn't change `DEFAULTS`).
- [ ] **Step 2: Run** `python -m unittest tests.test_settings -v` → FAIL (`No module named 'settings'`).
- [ ] **Step 3: Implement** `settings.py` with the interface above.
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add settings persistence for noise analysis"`.

### Task 2: Spectrum, component amplitude, top peaks

**Files:**
- Create: `noise.py`
- Test: `tests\test_noise_spectrum.py`

**Interfaces:**
- Produces (in `noise.py`): constants `SEG = 1 << 19`, `HALF_BINS = 3`, `PEAK_SEP_HZ = 5.0`; 
  - `spectrum(values: np.ndarray, fs: float, seg: int = SEG) -> tuple[np.ndarray, np.ndarray, float]` → (freqs, psd mV²/Hz, df). Uses `min(seg, len(values))` as segment length; at least 2 samples required (else `ValueError`).
  - `component_rms(freqs, psd, df, f: float) -> tuple[float, float] | None` → (peak bin frequency, mV RMS) or None when `f > freqs[-1]` or `f < df`.
  - `top_peaks(freqs, psd, df, n: int = 3, f_min: float = 2.0, sep: float = PEAK_SEP_HZ) -> list[tuple[float, float]]` → up to n (Hz, mV RMS), strongest first.
  - `amplitude_spectrum(psd, df) -> np.ndarray` = `sqrt(psd * df)` (used by the noise view plot).

- [ ] **Step 1: Write failing tests** (fs = 500 000, 15 s unless stated, `rng = np.random.default_rng(0)`):
  - `test_sine_amplitude`: `sqrt(2)*1.0*sin(2π·60t) + rng.normal(0, 1, n)` → `top_peaks(...)[0]` frequency within 1 Hz of 60, amplitude within 1 % of 1.000.
  - `test_high_frequency_tone`: 0.5 mV RMS at 100 000 Hz → amplitude within 1 % of 0.5.
  - `test_between_bins`: 1 mV RMS at 60.4 Hz → amplitude within 1 % of 1.0.
  - `test_peak_order_and_merge`: tones 60 Hz (1.0), 120 Hz (0.3), 15 550 Hz (0.6), 63 Hz (0.2) → peaks ≈ [60, 15550, 120] (63 Hz merged into 60 by the 5 Hz rule).
  - `test_f_min`: a 1 Hz tone of 5 mV RMS plus 60 Hz of 1 mV → with f_min 2 the first peak is 60 Hz.
  - `test_short_window_uses_one_segment` (Review Focus 1): 0.1 s of a 1 kHz 1 mV RMS tone → `df == fs / n` (≈ 10 Hz), amplitude within 2 % of 1.0.
  - `test_component_out_of_range` (Review Focus 2): `component_rms` at 300 000 Hz (> Nyquist) and at `df / 2` → None.
- [ ] **Step 2: Run** `python -m unittest tests.test_noise_spectrum -v` → FAIL.
- [ ] **Step 3: Implement.** Welch loop over segments with hop `seg // 2` (vectorise with a strided view or a Python loop over ≤ ~30 segments — either is fine); peaks via `np.argsort(psd)[::-1]` restricted to `freqs >= f_min`, each candidate's amplitude from `component_rms` at its frequency.
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add spectrum and peak detection"`.

### Task 3: Spikes and per-trace noise analysis

**Files:**
- Modify: `trace_store.py` (add `TraceData.block_rows`)
- Modify: `noise.py`
- Test: `tests\test_noise_spikes.py`

**Interfaces:**
- Consumes: `spectrum`, `component_rms`, `top_peaks` (Task 2); `TraceData` (`n`, `dt`, `block`, `blocks` with fields n/mean/m2/vmin/vmax/imin/imax, `index_range`, `values`, `times`, `times_at`, `values_at`, `from_arrays`).
- Produces:
  - `TraceData.block_rows(i0: int, i1: int) -> tuple[np.ndarray, tuple[int, int], tuple[int, int]]` → (rows of whole blocks inside [i0, i1), head index range, tail index range).
  - In `noise.py`: `DEAD_TIME = 50e-6`, `SPIKE_LIST_CAP = 10_000`;
  - `@dataclass SpikeResult: baseline: float; sigma: float; threshold: float; times: np.ndarray; heights: np.ndarray; duration: float` (`times`/`heights` hold every spike, uncapped).
  - `spike_baseline(trace, i0: int, i1: int) -> tuple[float, float]` — median of whole-block means and of whole-block `sqrt(m2/n)`; with no whole block, median and median-of-64-sample-chunk std of the raw samples.
  - `find_spikes(trace, t0: float, t1: float, k: float, dead_time: float = DEAD_TIME) -> SpikeResult`.
  - `@dataclass NoiseResult: baseline, sigma, threshold: float; n_spikes: int; rate: float; median_spacing: float | None; spacing_hz: float | None; peaks: list[tuple[float, float]]; watch: dict[float, float | None]; resolution_hz: float | None; spikes: SpikeResult | None; note: str` (`note` = `""`, `"Spectrum needs evenly spaced samples."` or `"No samples in the window"`).
  - `analyze(trace, t0: float, t1: float, watch_hz: list[float], k: float, f_min: float) -> NoiseResult` (the spectrum arrays are not kept; the noise view recomputes them).

- [ ] **Step 1: Write failing tests** on `TraceData.from_arrays` traces (uniform 2 µs time written with 6 decimals, baseline 98 mV, noise σ 2 mV, values rounded to 4 decimals):
  - `test_block_rows_partition`: head + whole blocks + tail exactly cover [i0, i1) for windows inside one block, across one edge, and full range.
  - `test_known_spikes`: 50 spikes of 30–200 mV height at known indices, including one at a block boundary and one in the partial head block → count 50, `times` equal the injected peak times exactly, `heights` within 1e-9 of value − baseline.
  - `test_wobble_is_one_spike`: a spike that crosses the threshold 3 times within 40 µs → 1 spike; two spikes 60 µs apart → 2.
  - `test_baseline_robust`: 1 % of samples replaced by 500 mV spikes → baseline within 0.05 mV of 98, σ within 5 % of 2.
  - `test_flat_signal`: constant 5.0 mV → σ 0, threshold 5.0, 0 spikes, no exception.
  - `test_rate_and_spacing`: spikes every 10 ms over 1 s → rate 100/s (±1 %), median spacing 0.010, spacing_hz 100.
  - `test_analyze_nonuniform` (Review Focus 3): jittered explicit time → `peaks == []`, every watch value None, `note == "Spectrum needs evenly spaced samples."`, spikes still counted.
  - `test_analyze_empty_window`: window outside the data → `note == "No samples in the window"`, `n_spikes == 0`.
  - `test_analyze_full`: 60 Hz 1 mV RMS + spikes → peaks[0] ≈ 60 Hz, watch {60.0: ≈1.0, 300000.0: None}, `resolution_hz ≈ 0.95`.
- [ ] **Step 2: Run** `python -m unittest tests.test_noise_spikes -v` → FAIL.
- [ ] **Step 3: Implement.** `find_spikes` reads raw values only for the head/tail ranges and for whole blocks whose `vmax > threshold`; join adjacent candidate blocks so a spike spanning a block edge is one spike; apply the dead time across block joins.
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add spike detection and per-trace noise analysis"`.

### Task 4: Chart toolbar — modes, axis zoom, Back

**Files:**
- Create: `chart_tools.py`
- Modify: `multi_file_analyzer.py` (`_build_chart`, `draw`, `_on_span`, View menu "Reset zoom", key bindings; remove `NavigationToolbar2Tk`/`SpanSelector` use)
- Test: `tests\test_chart_tools.py`

**Interfaces:**
- Produces (in `chart_tools.py`):
  - `MODES = ("select", "zoom_x", "zoom_y", "cursors")`
  - `class ChartTools:` `__init__(self, parent: tk.Widget, ax, canvas, *, on_window_selected: Callable[[float, float], None], on_reset: Callable[[], None], autoscale_y: Callable[[float, float], tuple[float, float] | None])`; `mode: str` (read-only property); `set_mode(mode: str) -> None`; `zoom_x(x0: float, x1: float) -> None`; `zoom_y(y0: float, y1: float) -> None`; `back() -> None`; `reset() -> None` (clears history, calls `on_reset`); `clear_history() -> None`; `can_go_back: bool`; `attach() -> None` — re-creates the drag selector (and, from Task 5, cursor artists) after the app's `draw()` cleared the axes; `save_image() -> None`.
  - The app passes `autoscale_y` = a method returning (ymin, ymax) of the plotted points within [x0, x1] with 3 % padding, or None if no points.
- [ ] **Step 1: Write failing tests** (build `App()` with a temp `cache_dir`, load one synthetic CSV, pump events; drive the tools through their methods and through synthetic mouse events on the canvas where practical):
  - `test_default_mode_select_sets_window`: a span from 1.0 to 2.0 in select mode sets the t0/t1 boxes.
  - `test_zoom_x_sets_xlim_and_autofits_y`; `test_zoom_y_keeps_x`.
  - `test_back_restores_exact_view`: zoom_x, zoom_y, back, back → limits equal the originals; `can_go_back` False at the end.
  - `test_history_cap_50`.
  - `test_reset_clears_history`.
  - `test_zoom_mode_does_not_change_window`: a drag in zoom_x mode leaves t0/t1 unchanged.
  - `test_redraw_keeps_mode_and_history` (Review Focus 4): set zoom_x, zoom once, call `app.apply_sort()` / `app.draw()` → mode still zoom_x, `can_go_back` True, a further zoom works.
  - `test_narrow_drag_ignored` (< 3 px).
- [ ] **Step 2: Run** `python -m unittest tests.test_chart_tools -v` → FAIL.
- [ ] **Step 3: Implement** `ChartTools` (ttk buttons; the active mode button uses a highlighted style; drag via `matplotlib.widgets.SpanSelector` with `direction="horizontal"`/`"vertical"` re-created in `attach()`); wire into the app: `draw()` ends with `self.tools.attach()`; "Reset zoom" (button, menu, Home key) → `tools.reset()`; loading/removing traces → `tools.clear_history()`; key bindings W/X/Y/C/BackSpace/Home on the chart canvas widget. `Save image…` → `filedialog.asksaveasfilename` + `figure.savefig`.
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Replace box zoom with axis zoom, Back and modes"`.

### Task 5: Measurement cursors

**Files:**
- Modify: `chart_tools.py`, `multi_file_analyzer.py` (readout label + Copy button under the chart; pass trace callbacks)
- Test: `tests\test_chart_tools.py` (add)

**Interfaces:**
- Consumes: `ChartTools` (Task 4); `TraceData.index_range`, `values_at`, `times_at`, `t_start`, `t_end`.
- Produces: extra `ChartTools.__init__` keyword args `get_selected_trace: Callable[[], dict | None]`, `get_shown_traces: Callable[[], list[dict]]`, `label_of: Callable[[dict], str]`; `cursor_times: tuple[float, float] | None`; `set_cursor(which: str, t: float) -> None` (`which` in "A"/"B"); `readout_text() -> str`; `cursor_table() -> str`; `copy_cursor_values() -> None` (clipboard).
- [ ] **Step 1: Write failing tests:**
  - `test_cursors_start_at_thirds`: entering cursors mode with view [0, 3] → times (1.0, 2.0).
  - `test_readout_uses_nearest_sample`: set A/B to times between samples → V_A/V_B equal `values_at` of the nearest index; the string matches the verbatim format (check one exact expected string built from known synthetic values).
  - `test_dt_formatting`: Δt 7.038 ms → `Δt 7.038 ms (1/Δt = 142.1 Hz)`; Δt 2 s → `Δt 2.000000 s (1/Δt = 0.5 Hz)`; Δt 50 µs → `(1/Δt = 20.000 kHz)`; equal times → `1/Δt —`.
  - `test_outside_range_dash`: cursor beyond the trace end → `–` for that value.
  - `test_copy_table`: 2 shown traces → header line verbatim + 2 rows in sort order, tab-separated.
  - `test_cursors_survive_redraw` (Review Focus 4): cursors set, `app.draw()` → same times, lines present on the axes.
  - `test_leaving_cursors_hides_lines_keeps_times`.
- [ ] **Step 2: Run** the focused tests → FAIL.
- [ ] **Step 3: Implement.** Cursor lines are `ax.axvline` artists re-created in `attach()`; dragging uses `button_press_event`/`motion_notify_event`/`button_release_event` with the 6 px grab distance in display coordinates; readout refresh on drag, on selection change and on draw.
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add A/B measurement cursors with readout and copy"`.

### Task 6: Analyze noise, Noise tab, settings dialog

**Files:**
- Modify: `multi_file_analyzer.py` (toolbar button, worker, results notebook with Statistics/Noise tabs, export/copy per tab, outdated marking, Noise settings… dialog, settings load at start-up)
- Test: `tests\test_noise_app.py`

**Interfaces:**
- Consumes: `noise.analyze`, `NoiseResult` (Task 3); `settings.load_settings/save_settings/parse_settings/default_settings_path` (Task 1).
- Produces: `App.settings: dict`; `App.settings_path: str` (overridable in tests); `App.analyze_noise() -> None`; `App.cancel_noise() -> None`; `App.noise_rows: list[tuple[dict, NoiseResult]]`; `App.noise_meta: tuple[float, float, float, float, tuple[float, ...], float | None] | None` (t0, t1, k, f_min, watch, resolution); `App.noise_outdated: bool`; `App.noise_table(for_export: bool = False) -> pd.DataFrame` (columns verbatim from Global Constraints; with `for_export=True` add `Window start (s)`, `Window end (s)`, `k`, `f_min (Hz)`, `Resolution (Hz)`); a trace whose analysis raised gets a row with `Spikes` = `"error"`, other cells "–", and the message kept for its noise view; `App.show_noise_settings() -> None`.
- [ ] **Step 1: Write failing tests** (temp cache_dir and temp settings_path; synthetic CSVs with a 60 Hz tone and known spikes; poll until the worker finishes):
  - `test_analyze_fills_noise_tab`: 2 traces → `noise_table()` has the verbatim columns + one `60 Hz (mV RMS)` column, values match `noise.analyze` on the same traces.
  - `test_cancel_keeps_done_rows`: cancel after the first trace → 1 row, status text verbatim.
  - `test_outdated_on_window_change`: change t0 → `noise_outdated` True and the banner text verbatim; values unchanged.
  - `test_rows_follow_sort_and_removal` (Review Focus 5): analyze 3 traces, switch sort to Run # ↓ → rows reversed; remove one trace → its row gone.
  - `test_disabled_while_loading` (Review Focus 5): `_loading` True → `analyze_noise()` does nothing.
  - `test_export_noise_tab`: `noise_table(for_export=True)` includes the window/k/f_min/resolution columns; Copy for Excel / Export results act on the Noise tab when it is showing.
  - `test_error_in_one_trace_continues`: patch `noise.analyze` to raise for the 2nd of 3 traces → 3 rows, the 2nd shows `error`, the others are filled, the worker finishes.
  - `test_settings_dialog_validation`: invalid k → error shown, settings unchanged; valid → saved to settings_path and `noise_outdated` True.
- [ ] **Step 2: Run** `python -m unittest tests.test_noise_app -v` → FAIL.
- [ ] **Step 3: Implement.** Worker mirrors the loader pattern (thread + `self._q`-style queue polled with `after`); progress bar reused; Cancel button shown while running; Noise tab is a `ttk.Treeview` with horizontal and vertical scrollbars; numbers formatted like the Statistics table (`fmt`); "–" for None.
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add Analyze noise, Noise tab and noise settings"`.

### Task 7: Noise view window

**Files:**
- Modify: `multi_file_analyzer.py` (new `open_noise_view(trace)`; View menu "Noise view…"; double-click on Noise rows; threshold line in `draw()` when a view is open for the selected trace)
- Test: `tests\test_noise_app.py` (add)

**Interfaces:**
- Consumes: `noise.spectrum`, `amplitude_spectrum`, `top_peaks`, `find_spikes`, `SPIKE_LIST_CAP`; `ChartTools.zoom_x` (Task 4); `App.noise_rows`, `App.settings`.
- Produces: `App.open_noise_view(trace: dict) -> NoiseView`; `class NoiseView` (Toplevel) with `rerun(k: float) -> None`, `add_watch(freq: float) -> None`, `goto_spike(index: int) -> None`, attribute `spikes: SpikeResult`, `freqs`, `psd`.
- [ ] **Step 1: Write failing tests:**
  - `test_view_marks_peaks_and_watch`: view for a 60 Hz trace → peak list text starts with `#1  60.` and a dashed line exists at each watch frequency.
  - `test_resolution_label`: short window → `Resolution {r:.1f} Hz` text.
  - `test_spike_click_zooms_and_selects`: `goto_spike(0)` → main xlim = spike time ± 2 ms, `tools.can_go_back` True, the trace selected in the file list.
  - `test_cap_message`: trace with 10 050 spikes → list has 10 000 rows and the verbatim cap text.
  - `test_add_watch_marks_outdated`: `add_watch(15550.04)` → `settings["watch_hz"]` contains 15550.0, saved, `noise_outdated` True.
  - `test_rerun_updates_row`: `rerun(5)` → the trace's Noise row threshold changes accordingly.
  - `test_threshold_line_drawn`: with the view open for the selected trace, the main axes contain a horizontal line at that threshold; closing the view removes it on the next draw.
  - `test_nonuniform_note`: explicit-time trace → the note `Spectrum needs evenly spaced samples.` and no spectrum plot lines.
- [ ] **Step 2: Run** the focused tests → FAIL.
- [ ] **Step 3: Implement.** Spectrum recomputed in a background thread when the view opens (status text in the view while computing); log–log plot of `amplitude_spectrum`; right-click within 5 % of the plot width of a peak → context menu "Add to watch-list".
- [ ] **Step 4: Run** `python -m unittest discover -s tests` → all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add noise view with spectrum, spike list and threshold line"`.

### Task 8: Real-data check, performance, docs

**Files:**
- Create: `tests\test_noise_real.py`
- Modify: `multi_file_analyzer.py` module docstring (short "Noise analysis" and "Chart tools" paragraphs: what the buttons do, where settings are saved); spec `docs\superpowers\specs\2026-11-07-noise-and-chart-tools-design.md` (append `## 9. Results`)

- [ ] **Step 1: Write** `tests\test_noise_real.py`, skipped unless `run_001.csv` and `run_006.csv` exist and are not cloud-only (folder: `C:\Users\MannaDoud\Sarmal\Project Trailblazer - Engineering\Bread Board\Bread Board Experiments and Test\Test 008_ABB Noise Identification\620nm Config`): run_001 full window → peaks[0] within 1 Hz of 60 Hz; run_006 → some peak within 50 Hz of 193 780 Hz, and spike rate recorded (assert it is within 5 % of the value measured in Step 2, written into the test as a constant).
- [ ] **Step 2: Run** it, record run_006's spike rate, fix the constant, re-run → PASS.
- [ ] **Step 3: Measure** Analyze noise over the 57 local runs (skip cloud-only files; temp cache dir under the scratchpad, deleted afterwards) with a one-off script: seconds per run. Target ≤ 0.6 s/run. Record the number in spec §9 (met/missed, honestly).
- [ ] **Step 4: Update** the module docstring paragraphs.
- [ ] **Step 5: Run** `python -m unittest discover -s tests` → all PASS; commit `git commit -m "Real-data noise checks, results and docs"`.
