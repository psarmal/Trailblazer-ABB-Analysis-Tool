# Top-5 noise peaks with "seen" count, and horizontal cursors — design

Date: 2026-11-16 · Tool: `multi_file_analyzer.py`, `noise.py`, `chart_tools.py` · Status: Draft for approval
Builds on: `2026-11-07-noise-and-chart-tools-design.md`

## 1. Why

- The user wants the **list of noise frequencies seen in a run file**: the strongest ones, how strong
  (mV RMS), and **how often each one appeared**. This separates noise that is always on from noise
  that comes and goes. Today only the top 3 peaks are kept, from a spectrum averaged over the whole
  window, so you can't tell a constant hum from a one-off burst.
- The user wants to **measure peak-to-peak noise by eye** with two horizontal cursors, reading each
  one's level and ΔY. Max − Min on the Statistics tab is thrown off by a single spike, so it isn't
  enough for this.

## 2. Decisions made with the user

- **Top 5** peaks instead of top 3, each with frequency, mV RMS and a "seen" count.
- "How many times it appeared" = **in how many ~1 s slices of the window the frequency was present**
  (option A). Rejected: number of separate bursts (B) and number of cycles (C).
- Horizontal cursors **Y1/Y2 live in the existing Cursors mode**, together with A/B (scope-like), not
  in a separate mode.

## 3. Approaches considered

**Seen count.**
- **Chosen: keep each slice's spectrum during the existing Welch pass.** After the averaged
  spectrum picks the top 5, check each slice at those 5 frequencies. One read of the file, so Analyze
  noise stays about as fast as today. The cost is extra memory while one run is analysed: about
  15 MB for a 15 s run and about 26 MB for the longest (13 M samples) run, stored as float32.
- Rejected: a second pass over the file after the peaks are known (roughly doubles the analysis time,
  which is already about 1 min for 160 runs).
- Rejected: working it out from the averaged spectrum (averaging is what loses the "when").

**Horizontal cursors.** Same mode as A/B, chosen by the user (see 2). Rejected: a separate "Y
cursors" toolbar mode (only one pair visible at a time).

## 4. Design — top-5 peaks and "seen"

### 4.1 Slices
- The Welch pass is unchanged: L = 2^19 samples (≈ 1.05 s at 500 kHz), Hann window, 50 % overlap.
  The averaged PSD, peak frequencies and mV RMS values stay **bit-identical** to today.
- **Slices = the non-overlapping segments**, i.e. the even-numbered Welch segments
  (0, 2, 4, …). A 15 s run gives 14 slices. A window shorter than L has one segment, so 1 slice.
- While the Welch pass runs, keep the power spectrum of each slice (float32, scaled the same way
  as the PSD, but for one segment).

### 4.2 "Seen in a slice" rule (my call)
For a peak at bin j and one slice's power spectrum P:
- **Core power** = sum of P over the 7 bins j−3 … j+3 (the same bins that give the mV RMS).
- **Background** = median of P over the bins j−50 … j+50, leaving out the core bins and clipped to
  bin ≥ 1 and ≤ Nyquist. It is divided by ln 2 to turn the median into a mean per bin, because a
  single-segment noise PSD bin is roughly exponential.
- **Seen** if core power ≥ **4 × 7 × background** (the tone carries at least 4× the noise power in
  its 7 bins, about 6 dB). The factor is fixed, not a user setting.
- Seen count = the number of slices where the rule holds (0 … number of slices).

### 4.3 Top 5
- `top_peaks` with **n = 5** (same f_min and 5 Hz spacing rules as today). Fewer than 5 qualifying
  peaks → the remaining columns show "–".
- N is fixed at 5, not a setting.

### 4.4 Where it appears
- **Noise tab columns:** `… · Slices · Peak 1 (Hz) · Peak 1 (mV RMS) · Peak 1 seen · … · Peak 5 seen ·
  <watch-list columns>`. **Slices** = number of slices in the window (once per row). **Peak n seen**
  is a plain whole number. Not "14/14", because Excel turns that into a date when pasted or
  exported. The table already scrolls horizontally.
- **Noise view list:** `#1  193.8 kHz  0.452 mV RMS  seen 14/14 s`. Slices are about 1.05 s, so
  "s" is approximate. The label shows the slice length, e.g. `Slice 1.05 s`, next to
  `Resolution 0.95 Hz`.
- **Copy for Excel / Export results…** carry the new columns. The export header also carries the
  slice length and the seen rule ("≥ 4× local background, ±50 bins").
- Traces without uniform time: all peak and seen columns "–" (as today).

### 4.5 Non-goals
- No "seen" count for watch-list frequencies (can come later; same code).
- No timeline of *which* slices a peak was seen in (that would be a spectrogram; out of scope as
  before).

## 5. Design — horizontal cursors Y1/Y2

- In **Cursors** mode the chart shows **A/B (vertical)** and **Y1/Y2 (horizontal)** together. Y1/Y2
  have their own colours, different from A/B and from the red dotted noise threshold line.
- **First time** in Cursors mode: Y1 at ⅓ and Y2 at ⅔ of the visible amplitude range. After that they
  keep their mV values through zoom, Back, Reset and mode switches. Leaving Cursors mode hides them,
  the same as A/B.
- **Dragging:** a press within 6 px of any of the four lines grabs the **nearest** line by pixel
  distance. A horizontal line moves only up/down. Clicking elsewhere does nothing (as today).
- **Typed entry:** two more boxes **Y1** and **Y2** (mV, 4 decimals) next to the A/B time boxes, with
  the same Enter-to-apply and bad-value behaviour.
- **Off-screen:** a line outside the view isn't drawn. Its readout still works and it gets an edge
  marker, mirroring what A/B do today.
- **Readout:** a second line under today's:
  `Y1: 12.3400 mV | Y2: 10.1200 mV | ΔY (Y2−Y1) −2.2200 mV`.
  It doesn't depend on any trace.
- **Cursors to view** (existing button) also moves Y1/Y2 to ⅓ and ⅔ of the visible amplitude range.
- **Copy cursor values:** each row gets three extra columns `Y1 (mV) · Y2 (mV) · ΔY (mV)` (same
  values on every row, so the paste stays one rectangular table).

## 6. Testing (stdlib unittest, as before)

- Averaged PSD, top-3 frequencies and mV RMS are unchanged versus today (bit-identical regression).
- Seen count: 15 s synthetic signal with a 60 Hz tone on the whole time and a 15 kHz tone only in
  the first 4 s, plus white noise. Expect 60 Hz seen in 14/14 slices and 15 kHz seen in 3–4 slices
  (slice edges). A noise-only frequency is never in the top 5 with seen > 1. Short window → 1 slice.
- Top 5 ordering and "–" when fewer than 5 peaks qualify.
- Noise tab / export: new columns present, seen is an integer, Slices column correct.
- Y cursors: default positions, drag picks the nearest line, typed entry, values kept across
  zoom/Back, readout text, copy columns, hidden outside Cursors mode.
- Real data (skipped if missing/cloud-only): run_001 → 60 Hz seen in every slice.
- Performance: Analyze noise per run is still within about 10 % of today on run_001 and run_033.

## 7. Left to the builder

Colours of Y1/Y2, exact layout of the two extra entry boxes, the internal structure for passing
slice spectra out of `_welch`.
