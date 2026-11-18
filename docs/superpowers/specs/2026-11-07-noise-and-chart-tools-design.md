# Noise analysis, measurement cursors and axis zoom — design

Date: 2026-11-07 · Tool: `multi_file_analyzer.py` (+ `trace_store.py` cache) · Status: Implemented 2026-11-07

## 1. Why

The user (Test 008 "ABB Noise Identification", 500 kHz runs of ~7.4 M samples, 8–160 runs
loaded) wants to find out **what noise is in the signal and how often it happens**, compare that
across runs, and measure features on the chart by hand.

What the real data already shows (throwaway analysis of runs 1 and 6, 2026-11-07):

| | Run 1 (LED 0 %) | Run 6 |
|---|---|---|
| Baseline / noise σ | 0.15 mV / 0.85 mV | 98.4 mV / 2.44 mV |
| Strongest periodic noise | 60.1 Hz, 0.926 mV RMS (mains) + 120 Hz harmonic | 193.8 kHz, 112.4 kHz, 209.3 kHz, ~0.45 mV RMS each |
| Spikes (k = 10) | 0 | 179 (11.9 /s), median spacing 54.5 ms |

Two kinds of "frequency" are needed, and both were requested:
- **Periodic noise** → a spectrum: which frequencies, how strong (mV RMS).
- **Spikes / events** → a rate: how many per second, how far apart.

Plus two chart-interaction requests:
- **Two draggable vertical cursors** reading time and amplitude at each line, Δt, 1/Δt and ΔV.
- **Axis zoom** instead of box zoom: horizontal-only or vertical-only drag zoom, and a
  **Back** button that undoes one zoom step instead of jumping back to fully zoomed out.

## 2. Decisions made with the user

- Both spectrum and spike analysis; both table columns (compare runs) and a per-run view (investigate one run).
- Frequency columns: **top 3 auto-detected peaks** *and* a **user watch-list** of frequencies.
- Spike threshold **relative to each run's own noise**: baseline + k·σ (not a fixed mV level).
- Noise analysis is a **separate "Analyze noise" step** (approach A); Calculate stays as fast as today.
- Cursors: readout for the **selected trace**, plus **Copy cursor values** for every shown trace.
- Partial zoom-out = **Back** (undo the last zoom step, like a browser's back button).
- No box zoom.
- Deferred from earlier work, still out of scope here: RMS/SNR columns with background from a
  separate run.

## 3. Noise analysis

### 3.1 Trigger and scope
- New toolbar button **Analyze noise** next to **Calculate**.
- Runs on the traces Calculate would use (shown traces, or all if "Shown files only" is unticked),
  over the **current window** (`t0`–`t1` boxes).
- Runs on a background thread, one trace at a time, with the existing status-bar progress bar
  (`Analyzing noise 12/160 · run_012.csv`) and a **Cancel** button. Cancel keeps the rows
  already finished.
- Results are kept until the next Analyze noise. Changing the window, the shown set, k, the
  watch-list or the lowest frequency marks them **outdated** (a banner on the Noise tab:
  `Window or settings changed — press Analyze noise to update.`); they are never recomputed
  automatically.

### 3.2 Spectrum (periodic noise)
- Only for traces with uniform time (`TraceData.dt` is not None). Other traces get "–" in every
  spectrum column and a note in the noise view: `Spectrum needs evenly spaced samples.`
- Averaged periodogram (Welch): segment length **L = 2^19 samples** (≈ 1.05 s at 500 kHz,
  ≈ 0.95 Hz resolution), Hann window, 50 % overlap, each segment's mean removed.
  If the window has fewer than L samples, one segment of the whole window is used (coarser
  resolution, shown as `Resolution 6.7 Hz` in the noise view).
- One-sided PSD in mV²/Hz, scaled with the window's power (`2 / (fs · Σw²)`), so that a pure sine
  of amplitude A mV RMS integrates to A².
- **Component amplitude (mV RMS)** at a frequency f = `sqrt(Σ PSD · Δf)` over the 7 bins
  centred on the highest bin within ±3 bins of f (captures the Hann main lobe). Reported
  frequency = that highest bin's frequency.
- **Top 3 peaks:** bins at or above **f_min** (default **2 Hz**, user-adjustable), taken in
  descending PSD order; a candidate within **5 Hz** of an already chosen peak is skipped. Each
  peak is reported as (frequency Hz, amplitude mV RMS).
- **Watch-list:** user list of frequencies in Hz. Each gets an amplitude column measured with the
  same ±3-bin rule. A watch frequency above Nyquist (fs/2) or below the resolution gives "–".
- Cost: reads every sample in the window (~30 MB per run) plus ≈ 27 Welch segments (50 % overlap)
  of 2^19 points for a 15 s run → about 0.3–0.5 s per full run; ~1 min for 160 runs. The segments
  are computed on worker threads, one segment's samples decoded at a time. This is why it is a
  separate step.

### 3.3 Spikes (events)
- **Baseline** = median of the per-block means, and **noise σ** = median of the per-block standard
  deviations (`sqrt(m2/n)`), over the whole 4096-sample cache blocks inside the window. A window
  with no whole block uses the raw samples of the window (median and the median-of-64-sample-chunk
  std). Spikes affect few blocks, so neither value is pulled up by them; both come from the
  cache's block table, so no raw data is read for them.
- **Threshold** = baseline + **k**·σ, k default **10**, user-adjustable (0.5–1000).
- Only **upward** spikes. A spike starts where the signal goes from ≤ threshold to > threshold.
  Crossings within **50 µs** after the previous spike's last sample above threshold belong to the
  same spike (dead time; fixed). A spike already above the threshold at the window start, or
  still above it when cut off at the window end, is counted (the implemented behaviour).
- Only blocks whose `vmax` exceeds the threshold (plus the partial blocks at the window edges) are
  read from disk, so spike detection costs little even for 160 runs.
- Per spike: time of its highest sample and height = that sample − baseline.
- Per run: Spikes (count), Rate (/s) = count / window duration, Median spacing (s) and its
  inverse (Hz) when there are ≥ 2 spikes (else "–").
- The spike list is capped at **10 000** spikes per run for display (count and rate still use all);
  the noise view says `Showing the first 10,000 of N spikes.` when capped.

### 3.4 Settings (Noise settings… dialog, also reachable from the Noise tab)
- **Watch-list** (comma- or space-separated Hz, e.g. `60, 120, 15550, 112440`), **k**, **f_min**.
- Saved in `%LOCALAPPDATA%\BBAnalyzer\settings.json` (`{"watch_hz": [...], "spike_k": 10,
  "f_min_hz": 2}`) and restored at start-up. An unreadable settings file is ignored (defaults
  used) and overwritten on the next save.
- Invalid entries (non-numbers, ≤ 0, k outside 0.5–1000) are rejected with a message naming the
  entry; nothing is saved until all are valid.

### 3.5 Where results appear
- The results card gets two tabs: **Statistics** (today's table, unchanged) and **Noise**.
- Noise table, one row per analyzed trace, in the current sort order, columns:
  `Trace · Baseline (mV) · Noise σ (mV) · Threshold (mV) · Spikes · Rate (/s) ·
  Median spacing (s) · Spacing rate (Hz) · Peak 1 (Hz) · Peak 1 (mV RMS) · Peak 2 … · Peak 3 … ·
  <f> Hz (mV RMS)` for each watch-list entry. Horizontal scrolling when it is wider than the card.
- **Copy for Excel** and **Export results…** export the tab that is showing; the Noise export also
  carries the window, k, f_min and the spectrum resolution.
- Double-click a Noise row (or **Noise view…** in the View menu for the selected trace) opens the
  noise view (3.6).

### 3.6 Noise view (one window per trace)
- **Spectrum** plot: log–log, amplitude in mV RMS per bin (`sqrt(PSD·Δf)`) against frequency;
  top-3 peaks marked ▼ and listed (`#1 60.1 Hz 0.926 mV RMS`); watch-list frequencies as dashed
  lines; resolution shown. Right-click near a peak → **Add to watch-list** (adds that bin's
  frequency, rounded to 0.1 Hz, and marks results outdated).
- **Spike list**: Time (s) · Height (mV) · Spacing (ms). Clicking a spike centres the main chart
  on it (±2 ms, pushing a zoom step so Back returns) and selects that trace.
- **k** box with **Re-run** for this trace (updates this view and the trace's Noise row).
- While a noise view is open for the selected trace, the main chart draws that trace's
  **threshold** as a dotted red horizontal line.

## 4. Chart interaction

### 4.1 Toolbar (replaces matplotlib's NavigationToolbar2Tk)
```
[ Select window ] [ Zoom ↔ ] [ Zoom ↕ ] [ Cursors ]   [ Back ] [ Reset zoom ]   [ Save image… ]
```
- The first four are **modes**; exactly one is active (default **Select window**). The active one
  is highlighted. A drag on the chart always does what the active mode says.
- **Select window**: today's SpanSelector behaviour (sets t0/t1 and recalculates).
- **Zoom ↔**: drag across a time range → x-limits become that range; y autofits to the data
  visible in the new range (using the drawn min/max points). A drag narrower than 3 px is ignored.
- **Zoom ↕**: drag up or down across an amplitude range → y-limits become that range; x unchanged.
- **Back**: restores the view (x- and y-limits) before the last zoom step. Every zoom (↔, ↕,
  spike click in 3.6) pushes the current view onto a history stack (max 50). Back is disabled
  when the stack is empty. **Reset zoom** shows everything and clears the stack. Loading or
  removing traces clears it too.
- The mouse wheel keeps scrolling the results table only; no wheel zoom.
- Keyboard: `W` select window, `X` zoom ↔, `Y` zoom ↕, `C` cursors, `Backspace` Back,
  `Home` reset (when the chart has focus).
- **Save image…** keeps today's toolbar ability to save the chart as PNG.
- The old toolbar's pan tool goes away (Zoom ↔ + Back cover its use).

### 4.2 Cursors
- **Cursors** mode shows two vertical lines **A** and **B** (first time: at ⅓ and ⅔ of the
  visible time range; afterwards they keep their times). Dragging near a line (within 6 px)
  moves it; clicking elsewhere does nothing. Lines stay at their times when zooming/panning;
  if a line is outside the view, its readout still works and the line is not drawn.
- Leaving Cursors mode hides the lines (times are remembered for next time).
- **Readout** (one line under the chart, monospace), for the trace selected in the file list
  (or the first shown trace if none is selected):
  `run 6   A: 0.195454 s, 136.6805 mV | B: 0.202492 s, 122.9476 mV | Δt 7.038 ms (1/Δt = 142.1 Hz) | ΔV −13.7329 mV`
  - V_A / V_B are the **real sample values** at the nearest sample to each cursor time
    (`TraceData.index_range` + `values_at`), even when the chart shows the min–max band.
  - Times use 6 decimals, mV 4 decimals; Δt in ms below 1 s, else s; 1/Δt in Hz (kHz above 10 kHz);
    Δt = 0 → `1/Δt —`.
  - A cursor outside a trace's time range gives `–` for that value.
- **Copy cursor values** (button at the end of the readout) puts a tab-separated table on the
  clipboard, one row per **shown** trace in sort order:
  `Trace  t_A (s)  V_A (mV)  t_B (s)  V_B (mV)  ΔV (mV)  Δt (s)  1/Δt (Hz)`.

## 5. Architecture

| File | Responsibility |
|---|---|
| `noise.py` (new) | Pure numpy, no Tk: `spectrum(values, fs) -> (freqs, psd, df)`, `component_rms(freqs, psd, df, f) -> (f_peak, mV_rms)`, `top_peaks(freqs, psd, df, n=3, f_min=2, sep=5)`, `spike_baseline(trace, i0, i1) -> (baseline, sigma)`, `find_spikes(trace, t0, t1, k, dead_time=50e-6) -> SpikeResult`, `analyze(trace, t0, t1, settings) -> NoiseResult`. |
| `chart_tools.py` (new) | The toolbar, mode handling, Zoom ↔/↕ with history (Back), cursors and readout. Talks to the app through a small interface (axes, canvas, a callback for "selected trace", a callback for span selection). |
| `settings.py` (new, tiny) | Load/save `settings.json` with validation. |
| `trace_store.py` | One accessor: `TraceData.block_rows(i0, i1)` → the whole-block rows inside `[i0, i1)` and the partial-edge index ranges. No format change; FORMAT_VERSION stays 1. |
| `multi_file_analyzer.py` | Analyze noise button + worker, Noise tab, noise view window, settings dialog, wiring chart_tools in place of NavigationToolbar2Tk/SpanSelector. |

No new dependencies (numpy FFT; scipy is not installed and not needed).

## 6. Error handling

| Situation | Behaviour |
|---|---|
| Window shorter than 2 samples or no samples in it | Row shows "–" everywhere, note `No samples in the window`. |
| Trace without uniform time | Spectrum columns "–", spikes still computed. |
| σ = 0 (flat signal) | Threshold = baseline; spikes = samples strictly above baseline (documented); no crash. |
| Cancel during Analyze noise | Finished rows kept, others absent, status `Noise analysis cancelled (12 of 160 done).` |
| Exception in one trace | That row shows "error" and the message in its noise view; the batch continues. |
| Settings file unreadable | Defaults used; overwritten on next save. |

## 7. Testing

Stdlib `unittest`, `python -m unittest discover -s tests`.

- **Spectrum accuracy:** 60 Hz sine of 1.000 mV RMS + white noise σ = 1 mV, 15 s at 500 kHz →
  peak 1 at 60 ± 1 Hz, amplitude 1.000 ± 0.01 mV RMS; a 100 kHz tone likewise; a frequency
  between two bins (60.4 Hz) still within ±1 %.
- **Top peaks:** tones at 60, 120, 15 550 Hz with different amplitudes → correct order; two tones
  3 Hz apart → reported as one peak; content below f_min ignored.
- **Watch-list:** amplitude at a listed frequency matches the injected amplitude; above Nyquist → "–".
- **Spikes:** injected spikes at known times and heights (including one that wobbles across the
  threshold within 50 µs, one at a block boundary, one in a partial edge block) → exact count,
  times, heights and rate; baseline/σ unaffected by 1 % of samples being spikes; σ = 0 case.
- **Settings:** round-trip, invalid entries rejected, corrupt file → defaults.
- **Chart tools (GUI):** Zoom ↔ / ↕ set the expected limits; Back restores the previous view
  exactly; Reset clears history; mode exclusivity (a drag in Zoom mode does not change t0/t1);
  cursor readout values equal the raw samples at the nearest index; copy format.
- **Noise worker (GUI):** Analyze noise fills the Noise tab, Cancel keeps finished rows, window
  change marks results outdated.
- **Real data (skipped if the file is missing or cloud-only):** run_001 → peak 1 within 1 Hz of
  60 Hz; run_006 → spike rate within 5 % of the value recorded at implementation time, top peaks
  include ~193.8 kHz.
- **Performance check (not a unit test):** Analyze noise on the 57 local runs, full window —
  report seconds per run; target ≤ 0.6 s/run on this PC.

## 8. Out of scope

- RMS/SNR columns with background from a separate run (deferred earlier).
- Downward spikes, fixed-mV thresholds, spike shape/width statistics.
- Cursors on the spectrum plot; snapping cursors to peaks.
- Spectrograms (frequency vs time).

## 9. Results

Measured 2026-11-07 on the 57 local runs of the 620nm Config folder (none cloud-only, none skipped).

| Item | Target | Measured | Verdict |
|---|---|---|---|
| Analyze noise, full window, per run (watch 60/120/15550/112440 Hz, k = 10, f_min = 2; trace already cached) | <= 0.6 s | mean 0.871 s, max 1.395 s (run_033), min 0.702 s, total 49.6 s for 57 runs | Missed (about 45 % over on average) |
| run_001 top spectrum peak | within 1 Hz of 60 Hz | within 1 Hz of 60 Hz (test passes) | Met |
| run_006 peak near 193 780 Hz | within 50 Hz | present among the top peaks (test passes) | Met |
| run_006 spike rate, k = 10, full run | recorded; within 5 % of the measured value | 11.93 /s (179 spikes), stored as a constant in `tests/test_noise_real.py` | Met (consistent with the earlier throwaway figure, about 11.9 /s) |

Found while measuring: `find_spikes` raised `ValueError` for a block-aligned window with no
candidate blocks (empty list for `np.concatenate`); fixed with a regression test.

### Task 8b: performance follow-up (2026-11-07)

Root cause: `analyze` time is ~85-90 % the Welch spectrum and scales with samples (~0.10 s per
million samples, single-threaded); run_033 is slow only because it has 13.2 M samples. Timing
also varied ~2x between repeats on the hybrid CPU (Core Ultra 7 355). Real vs synthetic data
made no difference. float32 rfft (slower) and batched 2-D rfft (slower) were rejected.
Fix: Welch segments run on up to 4 threads (numpy's FFT releases the GIL) and are summed in
segment order; segments are decoded straight from the int32 cache into a buffer
(`TraceData.values_into`, `noise.trace_spectrum`); the Hann window and its power are cached.
Results are bit-identical to the previous code (PSD, peaks, watch amplitudes, spikes checked
on run_001/006/020/033; max relative top-3 peak difference 0).

| Run (samples) | Before (s) | After (s), same session |
|---|---|---|
| run_001 (7.42 M) | 0.440 | 0.145 |
| run_006 | 0.772 | 0.277 |
| run_020 (7.51 M) | 0.676 | 0.291 |
| run_033 (13.21 M) | 1.040 | 0.464 |

Spectrum-only interleaved medians (7 rounds): run_001 0.698 -> 0.225 s, run_033 1.326 -> 0.389 s.
All four runs, including the longest, are now under the 0.6 s target. The full 57-run
re-measurement was started but stopped by the host for low system memory during conversion;
it has not been repeated yet, so the 57-run mean/max after the fix is still to be measured.
