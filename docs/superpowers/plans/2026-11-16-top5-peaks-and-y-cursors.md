# Top-5 noise peaks with "seen" count, and Y1/Y2 cursors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Report the 5 strongest noise frequencies per run with mV RMS and the number of ~1 s slices each was seen in, and add horizontal cursors Y1/Y2 (level + ΔY) to Cursors mode.

**Architecture:** `noise._welch` optionally keeps the power spectrum of every non-overlapping (even-numbered) Welch segment as float32. A new `noise.seen_count` checks one peak against those slices. `analyze` returns `seen` and `n_slices` alongside the existing peaks. The app's Noise tab, export and noise view show them. `chart_tools.ChartTools` gets a second cursor pair stored in data units (mV) that reuses the A/B machinery: animated lines, blit, markers and entries.

**Tech Stack:** Python 3, numpy, tkinter/ttk, matplotlib, pandas, stdlib `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-16-top5-peaks-and-y-cursors-design.md` (read it with this plan). Background: `docs/superpowers/specs/2026-11-07-noise-and-chart-tools-design.md`.

**Workspace:** the project folder is not a git repo. Copy it (excluding `__pycache__`, cache folders and `Archived/`) to the session scratchpad folder `work/`, `git init`, commit the baseline, and do every task there. Run tests from that folder: `python -m unittest discover -s tests`. The changed files are copied back to the project folder only after the user says OK.

## Global Constraints

- Averaged PSD, peak frequencies and mV RMS stay **bit-identical** to today (Welch L = 2^19, Hann, 50 % overlap).
- Slices = even-numbered Welch segments (non-overlapping). 15 s at 500 kHz → 14 slices. Window shorter than L → 1 slice.
- Seen rule: core = sum of the slice's power over bins j−3…j+3; background = median over bins j−50…j+50 excluding the core, clipped to [1, last bin], divided by ln 2; seen if core ≥ 4 × (number of core bins) × background. Constants: `SEEN_FACTOR = 4.0`, `BG_HALF_BINS = 50`.
- Top N = 5 (`N_PEAKS = 5`), fixed. `top_peaks`' default `n` stays 3 (existing tests rely on it). Callers pass `n=noise.N_PEAKS`.
- "Peak n seen" is a plain integer (never "14/14" in table or export: Excel turns it into a date).
- ΔY = Y2 − Y1. Levels in mV, 4 decimals.
- No new dependencies. Extra memory per analysed run ≤ ~30 MB (float32 slices).

## Review Focus

1. **Peak at the spectrum edge** (bin near f_min or near Nyquist): background window clipped, no IndexError, still a count. Pinned in Task 1 (`test_seen_edges`).
2. **Short window** (fewer samples than L, e.g. a 0.2 s selection): 1 slice, seen ∈ {0, 1}, view shows `seen 1/1 s`. Pinned in Task 1 (`test_short_window_one_slice`) and Task 2.
3. **Fewer than 5 qualifying peaks:** remaining Hz / mV RMS / seen cells show "–", no crash. Pinned in Task 2 (`test_fewer_than_five_peaks_dashes`).
4. **Re-run with a new k in the noise view** must keep the row's peaks, seen counts and Slices (only spike columns change). Pinned in Task 2 (`test_rerun_keeps_seen`).
5. **Y lines survive a full chart redraw** (`ax.clear()` on load/sort/show change) and keep their mV values. Pinned in Task 3 (`test_levels_survive_redraw`).

---

### Task 1: Slice spectra and seen count in `noise.py`

**Files:**
- Modify: `noise.py` (`_welch`, `spectrum`, `trace_spectrum`, `NoiseResult`, `analyze`; new constants and `seen_count`)
- Test: `tests/test_noise_spectrum.py` (new class `SeenTests`), `tests/test_noise_spikes.py`

**Interfaces:**
- Produces:
  - `noise.N_PEAKS = 5`, `noise.SEEN_FACTOR = 4.0`, `noise.BG_HALF_BINS = 50`, `noise.SEEN_RULE = "≥ 4× local background, ±50 bins"`
  - `spectrum(values, fs, seg=SEG, slices=False)` and `trace_spectrum(trace, i0, i1, seg=SEG, slices=False)`. With `slices=False` they return `(freqs, psd, df)` as today. With `slices=True` they return `(freqs, psd, df, sl)`, where `sl` is a float32 array of shape `(n_slices, seg//2+1)` holding single-segment PSDs in mV²/Hz (scale `2 / (fs · Σw²)`).
  - `seen_count(sl, df, f) -> int`: number of slices where the tone at frequency `f` (bin `round(f/df)`) passes the seen rule.
  - `NoiseResult` gains `seen: list = field(default_factory=list)` (one int per entry of `peaks`, same order) and `n_slices: int | None = None`, both after `k`.
  - `analyze(...)` uses `top_peaks(..., n=N_PEAKS)`, fills `seen` and `n_slices`. Non-uniform/empty → `seen=[]`, `n_slices=None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_noise_spectrum.py`:

```python
class SeenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        x = _tone_signal([(60.0, 1.0)])                       # 15 s, noise σ = 1 mV
        t = np.arange(len(x)) / FS
        x += np.where(t < 4.0, np.sqrt(2) * 0.5 * np.sin(2 * np.pi * 15_000 * t), 0.0)
        cls.x = x
        cls.freqs, cls.psd, cls.df, cls.sl = noise.spectrum(x, FS, slices=True)

    def test_psd_unchanged_with_slices(self):
        f2, p2, d2 = noise.spectrum(self.x, FS)
        np.testing.assert_array_equal(p2, self.psd)          # bit-identical
        self.assertEqual(d2, self.df)

    def test_slice_count_and_dtype(self):
        self.assertEqual(self.sl.shape, (14, len(self.psd)))
        self.assertEqual(self.sl.dtype, np.float32)

    def test_constant_tone_seen_every_slice(self):
        self.assertEqual(noise.seen_count(self.sl, self.df, 60.0), 14)

    def test_burst_seen_only_while_on(self):
        self.assertIn(noise.seen_count(self.sl, self.df, 15_000.0), (3, 4))

    def test_noise_only_not_seen(self):
        self.assertLessEqual(noise.seen_count(self.sl, self.df, 33_333.0), 1)

    def test_seen_edges(self):
        for f in (self.df, 2.0, self.freqs[-1], self.freqs[-2]):
            n = noise.seen_count(self.sl, self.df, f)
            self.assertTrue(0 <= n <= 14)

    def test_short_window_one_slice(self):
        x = _tone_signal([(60.0, 1.0)], seconds=0.2)
        _, _, df, sl = noise.spectrum(x, FS, slices=True)
        self.assertEqual(sl.shape[0], 1)
        self.assertEqual(noise.seen_count(sl, df, 60.0), 1)
```

In `tests/test_noise_spikes.py`, extend `test_analyze_full` (tone at 60 Hz): `len(r.peaks) <= noise.N_PEAKS`, `len(r.seen) == len(r.peaks)`, `all(isinstance(s, int) for s in r.seen)`, `r.n_slices >= 1`, and `r.seen[0] == r.n_slices` (the 60 Hz peak is first). Extend `test_analyze_nonuniform` and `test_analyze_empty_window`: `r.seen == []`, `r.n_slices is None`.

- [ ] **Step 2: Run them to see them fail**

Run: `python -m unittest tests.test_noise_spectrum tests.test_noise_spikes -v`
Expected: FAIL (`spectrum() got an unexpected keyword argument 'slices'`, `NoiseResult has no attribute 'seen'`).

- [ ] **Step 3: Implement in `noise.py`**

- `_welch(fill, n, fs, seg, keep_slices=False)`: when `keep_slices`, for each even segment index `i` store `(p * scale).astype(np.float32)` into a preallocated `(nseg + 1) // 2` row array, `scale = 2 / (fs * wpow)`. `acc` and the final `psd` are computed exactly as today (sum in segment order), so the result stays bit-identical. Return the 4-tuple only when `keep_slices`.
- `seen_count`: vectorised over slices.

```python
j = int(round(f / df)); last = sl.shape[1] - 1
core = np.arange(max(j - HALF_BINS, 0), min(j + HALF_BINS, last) + 1)
nb = np.arange(max(j - BG_HALF_BINS, 1), min(j + BG_HALF_BINS, last) + 1)
nb = nb[(nb < core[0]) | (nb > core[-1])]
if len(nb) == 0: return 0
bg = np.median(sl[:, nb], axis=1) / math.log(2)
return int(np.count_nonzero(sl[:, core].sum(axis=1) >= SEEN_FACTOR * len(core) * bg))
```

- `analyze`: `freqs, psd, df, sl = trace_spectrum(trace, i0, i1, slices=True)`. `peaks = top_peaks(..., n=N_PEAKS, f_min=f_min)`. `seen = [seen_count(sl, df, hz) for hz, _ in peaks]`. `n_slices = sl.shape[0]`. Release `sl` (`del sl`) before building the result.

- [ ] **Step 4: Run the tests**

Run: `python -m unittest tests.test_noise_spectrum tests.test_noise_spikes -v`
Expected: all PASS, including the existing bit-for-bit reference test.

- [ ] **Step 5: Commit**

```bash
git add noise.py tests/test_noise_spectrum.py tests/test_noise_spikes.py
git commit -m "noise: keep per-slice spectra, top 5 peaks with seen count"
```

---

### Task 2: Noise tab, export and noise view show top 5 + seen

**Files:**
- Modify: `multi_file_analyzer.py`: `_noise_columns` (~1966), `_noise_cells` (~1974), `noise_table` (~2002), noise view `_start` work() (~692), `_poll` (~717), `_draw_spectrum` (~737), `peak_text` (~760)
- Test: `tests/test_noise_app.py`

**Interfaces:**
- Consumes: Task 1's `N_PEAKS`, `SEEN_RULE`, `trace_spectrum(..., slices=True)`, `seen_count`, `NoiseResult.seen` and `.n_slices`.
- Produces: Noise columns `… "Spacing rate (Hz)", "Slices", then for i in 1..5: "Peak i (Hz)", "Peak i (mV RMS)", "Peak i seen"`, then the watch columns. Export adds `"Slice (s)"` (= 1 / resolution) and `"Seen rule"` (= `noise.SEEN_RULE`). Noise view: `self.seen: list[int]`, `self.n_slices: int`; `peak_text()` lines `#{i}  {hz:.1f} Hz  {mv:.3f} mV RMS  seen {s}/{n} s`; `res_lbl` text `Resolution {df:.1f} Hz · Slice {1/df:.2f} s`.

- [ ] **Step 1: Write the failing tests** in `tests/test_noise_app.py`:
  - `test_noise_columns_top5_and_seen`: after Analyze noise, `app._noise_columns()` contains `"Slices"`, `"Peak 5 (Hz)"`, `"Peak 5 seen"`, and `"Peak 1 seen"` comes right after `"Peak 1 (mV RMS)"`. For a row, `row["Slices"] == ref.n_slices` and `row["Peak 1 seen"] == ref.seen[0]`, and both are `int` (`ref = noise.analyze(...)` as in `test_analyze_fills_noise_tab`).
  - `test_fewer_than_five_peaks_dashes`: patch `noise.analyze` to return a result with `peaks=[(60.0, 1.0)]`, `seen=[3]`, `n_slices=3`. Peak 2–5 Hz / mV RMS / seen cells in the tree are `"–"`.
  - `test_export_has_slice_and_rule`: `app.noise_table(for_export=True)` has `"Slice (s)"` ≈ `1 / res` and `"Seen rule"` == `noise.SEEN_RULE`.
  - `test_rerun_keeps_seen`: open the noise view, `v.rerun(5)`. The row's `seen`, `n_slices` and peaks are unchanged and `k == 5`.
  - Update `test_resolution_label`: expected `"Resolution 0.5 Hz · Slice 2.00 s"` (adjust to that test's fs/seg: slice = 1/df).
  - `test_view_peak_text_has_seen`: `v.peak_text().splitlines()[0]` matches `r"^#1  60\.\d Hz  \d+\.\d{3} mV RMS  seen \d+/\d+ s$"`, and the list has ≤ 5 lines.

- [ ] **Step 2: Run them to see them fail**

Run: `python -m unittest tests.test_noise_app -v`
Expected: the new tests FAIL (no "Slices" column, etc.).

- [ ] **Step 3: Implement**
  - `_noise_columns` / `_noise_cells`: loop `range(1, noise.N_PEAKS + 1)`. Pad peaks with `(None, None)` and seen with `None`. Cells per peak are `[hz, mv, seen]`. Put `r.n_slices` after `spacing_hz`. Error/empty rows: the `n_cells` dash logic is unchanged (it already uses `len(columns)`). Make sure the error cell index (`cells[3] = "error"`) still lands on "Spikes".
  - `noise_table(for_export=True)`: add `"Slice (s)" = 1/res if res else None` and `"Seen rule" = noise.SEEN_RULE`.
  - Noise view work(): `spec = noise.trace_spectrum(trace, i0, i1, slices=True)`. In `_poll`, unpack 4 values, `self.peaks = noise.top_peaks(..., n=noise.N_PEAKS, f_min=self.f_min)`, `self.seen = [noise.seen_count(sl, self.df, hz) for hz, _ in self.peaks]`, `self.n_slices = sl.shape[0]`, then drop `sl` (don't keep it on the view).
  - `peak_text` / `res_lbl` as in Interfaces. `_update_noise_row` needs no change (`dataclasses.replace` keeps `seen` and `n_slices`). The test pins this.

- [ ] **Step 4: Run all noise tests**

Run: `python -m unittest tests.test_noise_app tests.test_noise_spectrum tests.test_noise_spikes -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add multi_file_analyzer.py tests/test_noise_app.py
git commit -m "noise tab/view: top 5 peaks with seen count and slices"
```

---

### Task 3: Y1/Y2 horizontal cursors in Cursors mode

**Files:**
- Modify: `chart_tools.py` (constants, `__init__` entries, `_make_cursor_lines`, `_update_markers`, `_blit_cursors`, `_on_press`, `_on_motion`, `cursors_to_view`, `refresh_readout`, `_apply_cursor_entry`, `cursor_table`; new `set_level`, `level_text`, `_default_levels`)
- Test: `tests/test_chart_tools.py` (new class `LevelCursorTest(ToolsBase)`), `tests/test_axis_entry.py` (entries)

**Interfaces:**
- Produces:
  - `ChartTools.cursor_levels: tuple[float, float] | None`, i.e. (Y1, Y2) in mV, set on first entry into Cursors mode to ⅓ and ⅔ of the visible y-range.
  - `ChartTools.set_level(which: str, y: float, _blit=False)`, `which` ∈ `("Y1", "Y2")`, else `ValueError`.
  - `ChartTools.level_text() -> str`: `"Y1: {y1:.4f} mV | Y2: {y2:.4f} mV | ΔY (Y2−Y1) {y2-y1:.4f} mV"`, or `""` when there are no levels.
  - `readout_text()` is **unchanged** (A/B line only). The readout label shows `readout_text()` and `level_text()` joined by `"\n"` (just `level_text()` when there is no trace).
  - `cursor_entries` gains keys `"Y1"`, `"Y2"` (shown `.4f`). Visual order: `A [ ] B [ ] Y1 [ ] Y2 [ ]  Cursors to view  Copy cursor values`.
  - `COPY_HEADER` gains `"\tY1 (mV)\tY2 (mV)\tΔY (mV)"`. Each row ends with the same three values.
  - `CURSOR_COLORS` gains `"Y1": "#7c3aed"`, `"Y2": "#047857"`.
  - `_dragging` ∈ `{"A", "B", "Y1", "Y2", None}`.

- [ ] **Step 1: Write the failing tests** (`LevelCursorTest`):
  - `test_default_levels_on_first_cursors_mode`: `set_mode("cursors")` → `cursor_levels` == (lo + (hi−lo)/3, lo + 2(hi−lo)/3) of `ax.get_ylim()`, and there are 2 horizontal animated lines among `tools._cursor_lines`.
  - `test_level_text_and_readout`: `set_level("Y1", 10.0)`, `set_level("Y2", 12.5)` → `level_text() == "Y1: 10.0000 mV | Y2: 12.5000 mV | ΔY (Y2−Y1) 2.5000 mV"`, and `_readout_var.get() == readout_text() + "\n" + level_text()`.
  - `test_drag_nearest_horizontal_line`: put A far from the click. Press at the pixel of Y1 + 2 px vertically → `_dragging == "Y1"`. Motion 20 px up → Y1 increases, Y2 unchanged, `cursor_times` unchanged, `canvas.draw` not called (patched, like `test_grab_does_not_render`).
  - `test_levels_survive_zoom_and_back`: set levels, `zoom_x(...)`, `back()`, `reset()` → `cursor_levels` unchanged.
  - `test_levels_survive_redraw`: set levels, call `app.draw(reset_view=True)`. Levels unchanged and the lines are recreated at those y values.
  - `test_levels_hidden_outside_cursors_mode`: `set_mode("select")` → no lines, readout `""`, but `cursor_levels` is kept. Back in cursors mode → same levels.
  - `test_offscreen_level_markers`: Y2 above the ylim → visible marker text `"Y2 ▲"`. Y1 below → `"Y1 ▼"`.
  - `test_cursors_to_view_moves_levels`: after zoom_y, `cursors_to_view()` → levels at ⅓ and ⅔ of the new ylim.
  - `test_copy_table_has_levels`: update the existing `test_copy_table` header expectation to the new `COPY_HEADER`. Each row's last three fields are `f"{y1:.4f}"`, `f"{y2:.4f}"`, `f"{y2-y1:.4f}"`.
  - In `test_axis_entry.py`: `test_typed_level_entry` (type `"15.25"` + Return in `cursor_entries["Y1"]` → `cursor_levels[0] == 15.25` and the entry shows `"15.2500"`; `"abc"` → bg `BAD_BG`, level unchanged). The existing enable/disable test now covers the 4 entries automatically.
  - Update `test_readout` (`test_chart_tools.py:245`): `_readout_var` now equals `exp + "\n" + level_text()`.

- [ ] **Step 2: Run them to see them fail**

Run: `python -m unittest tests.test_chart_tools tests.test_axis_entry -v`
Expected: new tests FAIL (`no attribute 'cursor_levels'`).

- [ ] **Step 3: Implement**
  - Lines: in `_make_cursor_lines` (cursors mode), after A/B add `ax.axhline(y, color=CURSOR_COLORS[w], lw=1.3, ls=(0, (6, 3)), zorder=4, animated=True)` for Y1/Y2. Keep `_cursor_lines` order `[A, B, Y1, Y2]`. Add two more markers (same text style), positioned at the top or bottom edge: `"Y1 ▲"`/`"Y1 ▼"` at x = 0.995 right-aligned, y = 0.97 or 0.03 offset like A/B stacking. `_update_markers` handles both sets.
  - `_on_press`: compute the pixel distance for all four lines (`|x − x_line|` for A/B, `|y − y_line|` for Y1/Y2). Take the nearest within `GRAB_PX`. On a tie, prefer A/B (keeps `test_grab_does_not_render` behaviour). `_on_motion` dispatches to `set_cursor` or `set_level` with the matching data coordinate.
  - `set_level` mirrors `set_cursor` (update ydata, markers, blit or `draw_idle`, `refresh_readout`).
  - Entries: create them in the loop `("Y2", "Y1", "B", "A")` (packed right to left). `_apply_cursor_entry` / `refresh_readout` pick the value and format by key (`.6f` for A/B, `.4f` for Y).
  - `cursors_to_view` also sets levels to ⅓ and ⅔ of `sorted(ax.get_ylim())`.

- [ ] **Step 4: Run all chart tests**

Run: `python -m unittest tests.test_chart_tools tests.test_axis_entry tests.test_app_integration -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add chart_tools.py tests/test_chart_tools.py tests/test_axis_entry.py
git commit -m "cursors: add Y1/Y2 horizontal level cursors with ΔY"
```

---

### Task 4: Real-data check, performance and results

**Files:**
- Modify: `tests/test_noise_real.py`, `docs/superpowers/specs/2026-11-16-top5-peaks-and-y-cursors-design.md` (append a "Results" section)

- [ ] **Step 1: Add the real-data test** `test_run001_mains_seen_everywhere`: run_001 full window → the peak within 1 Hz of 60 Hz has `seen == n_slices`. In `test_run006_peak_and_rate`, change the loop to `for f, _ in r.peaks` (unchanged shape) and add `len(r.peaks) == 5`. Skipped when the file is missing or cloud-only (existing `_local` helper).

- [ ] **Step 2: Run the full suite**

Run: `python -m unittest discover -s tests`
Expected: OK (real-data tests may report skipped).

- [ ] **Step 3: Performance check (not a unit test).** Time `noise.analyze` on run_001 and run_033 (full window, watch 60/120/15550/112440, k = 10, f_min = 2), 5 rounds each, median, against the baseline commit in the same session. Check peak memory with `tracemalloc` for run_033. Target: within ~10 % of baseline time and ≤ ~30 MB extra. Only these 2 runs, not the 57-run batch (it tripped the low-memory guard before).

- [ ] **Step 4: Append the measured results** to the spec under `## 8. Results` (table: item, target, measured, verdict), plus run_006's top-5 peaks with seen counts as an example.

- [ ] **Step 5: Commit**

```bash
git add tests/test_noise_real.py docs/superpowers/specs/2026-11-16-top5-peaks-and-y-cursors-design.md
git commit -m "noise: real-data seen check and performance results"
```
