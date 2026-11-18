# Binned view — design

Date: 2026-11-18 · Tool: `multi_file_analyzer.py` (+ `chart_tools.py`, `trace_store.py`) · Status: Draft for approval (builder check done 2026-11-18)

## 1. Why

On long, pulse-dense runs (Test 015 luciferase, 10 kHz) the main chart shows a solid block of
pulses; the underlying light level is invisible. The user wants **binning**: each run cut into fixed
time bins, each bin reduced to its **average signal**, and all runs overlaid — exactly the bottom
panel of the user's reference picture ("All runs overlaid · 0.5 s binned mean": run 7 rising from
~4 to ~30 mV after injection at ~3 s, runs 5/6 flat near 0, run 8 flat near 10 mV). The picture also
shows a **stacked raw panel per run** above the overlay, each titled with run, start time, position,
mean and max.

## 2. Decisions made with the user

- **Binning instead of a moving average.**
- Each bin holds the **average signal** (option A), not a spike count.
- Built as a **separate "Binned" window** (option 2), not a setting on the main chart and not inside the
  Histogram window.
- The window also shows the **stacked raw panels per run**, with an **option to hide** them.

## 3. Approaches considered

- **Chosen: separate Binned window** — the raw main chart stays visible beside it.
- Rejected by the user: a bin setting on the main chart (would have reused all chart tools for free);
  a third plot in the Histogram window (cramped, wrong subject).

## 4. Design

### 4.1 Opening and runs
- **Binned…** toolbar button (after *Histogram…*) and View-menu item. One window per app; opening again
  raises it. Non-modal.
- Runs = the traces **shown** in the main chart, in the main sort order, with the main chart's colours
  and legend labels (`App._label`). Showing/hiding, loading, removing, sorting, renaming or changing the
  legend style updates the window (the App calls the window's `refresh()` whenever the main chart is
  fully redrawn, i.e. from `App.draw`). Clear all closes it.

### 4.2 Binning
- **Bin size** box in seconds, default **0.5**. Valid: a number > 0. A bin size giving more than
  **200,000 bins** for any run, or one shorter than that run's sample interval, is rejected with the
  box turning red and a status message `Bin too small for run_007.csv (dt 0.0001 s).` (my call).
- Bins start at **each run's own first sample time** (`t_start`): bin k covers
  `[t_start + k·b, t_start + (k+1)·b)` (my call; the luciferase runs all start at 0, so they line up).
- Each bin's value = mean of the samples in it; plotted at the bin's **centre time**. A shorter last bin
  is kept (mean of the samples it has); bins with no samples (gaps in non-uniform time) are left out.
- **From/To** boxes (seconds) apply to every run: only bins whose centre lies in [From, To] are shown and
  exported. Defaults: From = 0 (or the earliest run start), To = end of the longest run. Empty To = end.
- Computed per run on a worker thread for runs over 1 M samples; status line `Binning 3/8 · run_007.csv`.
  Results are cached per (trace, bin size) and dropped when a trace is removed. Target: < 0.5 s per
  7.4 M-sample run.

### 4.3 Overlay plot
- One line per run (colour, label = main chart's), x = time (s), y = "mean mV (bin b s)".
- **Toolbar reuse (my call):** the existing `chart_tools.ChartTools` drives the overlay axes — Select
  window (sets From/To), Zoom ↔ / ↕, Back, Reset zoom, Save image, cursors A/B and Y1/Y2 with their
  readout and Copy cursor values. Cursor values read the **binned** series: each run's binned series is
  wrapped as a `TraceData` (from its bin centres and means) so `chart_tools.sample_at` works unchanged.
- Legend at upper right (my call), same labels as the main chart.

### 4.4 Raw panels
- **"Raw panels" tick box**, ticked by default. When ticked, the window shows one panel per run above
  the overlay, **at most 8** (the first 8 shown runs in sort order; a note `Showing raw panels for the
  first 8 of 23 runs.` when more). When unticked, the overlay takes the full height.
- Each panel: the run's raw signal over [From, To] drawn with `TraceData.view` (min/max envelope, same
  as the main chart), in the run's colour, its own x range (run lengths differ, as in the picture).
  Title: `{label} · started {started} · position {position_mm} mm · mean {mean:.2f} mV, max {max:.0f} mV`
  (mean/max over [From, To] via `TraceData.stats`; parts with no metadata are left out).
- Panels are static (no zoom); the overlay keeps all tools.

### 4.5 Export and image
- **Export…** writes CSV: for each run two columns `"{label} time (s)"`, `"{label} mean (mV)"`, rows =
  bins (runs with fewer bins leave blanks). A first comment line `# bin_s 0.5, from 0, to 60`.
- **Save image…** (toolbar) saves the whole figure (panels + overlay) as PNG.

### 4.6 Non-goals
- Spike counts per bin, standard-deviation bands, moving average, time alignment by injection time,
  binning in the main chart, changing Statistics/Noise/Histogram results.

## 5. Architecture

| File | Responsibility |
|---|---|
| `binning.py` (new, no Tk) | `bin_means(trace, bin_s) -> (centres, means)`; validation helper. |
| `binned_view.py` (new) | The Binned window: controls, panels, overlay, ChartTools wiring, worker, export. |
| `multi_file_analyzer.py` | Toolbar/menu entry, `open_binned()`, refresh hook in `draw`, close in `clear_all`. |

## 6. Error handling

| Situation | Behaviour |
|---|---|
| Bin size not a number / ≤ 0 / too small | Box red, previous plot kept, status message. |
| From ≥ To | Box red, previous plot kept. |
| No shown runs | Overlay shows "Show a run in the main window to bin it."; panels empty. |
| Run with fewer than 2 samples in [From, To] | Left out of overlay and panels; note in status. |

## 7. Testing

- `bin_means`: uniform trace with known values → exact means; last partial bin; non-uniform time with a
  gap → empty bin dropped; bin smaller than dt rejected; > 200,000 bins rejected.
- Window: opens with one line per shown run in sort order and colours; bin size change updates lines;
  From/To filter; hide/show raw panels changes the number of axes; > 8 runs → 8 panels + note; showing a
  hidden run in the main window adds its line; cursor A readout equals the bin mean at the nearest bin
  centre; export CSV columns and values; Save image writes a PNG.
- Real data (skipped if missing): Test 015 run 7 at 0.5 s → mean of bins 20–60 s between 20 and 35 mV,
  bins 0–2.5 s below 6 mV; run 6 all bins below 2 mV.
- Performance: 3 × 500 kHz runs (7.4–13.2 M samples) bin in < 0.5 s each.

## 7a. Calls settled after the builder check (my calls)
- **Select window** sets only this window's From/To, never the main window's time window.
- **Reset zoom** fits the plot to the data; it does not change From/To.
- A refresh from the main window (show/hide, sort, rename) **keeps the overlay's zoom and cursor
  positions**; only loading/removing runs or changing bin size/From/To refits the view.
- Sample interval for runs with uneven time = median gap between samples (used only for the
  "bin too small" check).
- Position in panel titles is written as stored, e.g. `position -13.2585 -59.3259 2.56486 mm`.
- More than 12 runs → the legend is shown in 2 columns; more than 24 → no legend (the main window's
  list identifies colours), with a note.
- Bin anchoring stays per run (each run's own start); export keeps two columns per run so rows never
  claim a shared time they don't have.

## 8. Left to the builder
Exact layout, panel height, fonts, colours beyond the main chart's, worker threshold details.

## 9. Results

Measured 2026-11-18 on this PC.

| Item | Target | Measured | Verdict |
|---|---|---|---|
| `bin_means` at 0.5 s, Test 008 run_001 / 006 / 033 (7.4 / 7.5 / 13.2 M samples) | < 0.5 s each | 0.04 / 0.06 / 0.07 s | Met |
| Open the window on 24 Test 015 runs (bin + draw, 8 panels) | — | 0.83 s | — |
| Change bin to 0.1 s on those 24 runs | — | 0.37 s | — |
| Run 7 at 0.5 s: mean of bins 20–60 s | 20–35 mV | within (unit test) | Met |
| Run 7 bins before 2.5 s | < 6 mV | all (unit test) | Met |
| Run 6 bins | < 2 mV | all (unit test) | Met |

Note: with 8 raw panels in the default window size matplotlib warns that tight layout cannot fit every
panel title; enlarging the window or hiding panels avoids it.
