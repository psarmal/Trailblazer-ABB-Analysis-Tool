# Intensity histogram, spike threshold and control comparison — design

Date: 2026-11-18 · Tool: `multi_file_analyzer.py` (+ `trace_store.py`, `noise.py`) · Status: Draft for approval (builder check done 2026-11-18)
Builds on: `2026-11-07-noise-and-chart-tools-design.md`, `2026-11-16-top5-peaks-and-y-cursors-design.md`

## 1. Why

The user runs luciferase tests (Test 015 "LLC in solution"): a **negative control** (buffer injected) and
a **positive control** (master mix that starts the light reaction) are measured with a PMT at several
PMT gains. The light shows up as **spikes** (PMT pulses) on a flat baseline. The user wants to:

1. **See** the difference: a histogram of signal intensity for negative vs positive runs.
2. **Choose a threshold** (typed or dragged) that defines a spike, and count spikes per run.
3. **Prove** there is signal: compare positive runs with negative runs **at the same PMT gain**
   (detection limit, how many SD above negative), and compare **before vs after** injection within
   one run (run 7: master mix injected at ~3 s).

What the real data showed (2026-11-17, threshold 5.1 mV, 10 kHz runs, folder
`Test 015 LLC in solution\261117 Second test\Luciferace 2026-11-17`):

| Gain (mV) | Buffer CH3 | Buffer CH4 | Master mix CH7 | Master mix CH8 |
|---|---|---|---|---|
| 1200 | run 6: 27 /s | run 13: 17 /s | run 7 (from 4 s): 1,423 /s | run 19: 742 /s |
| 700 | run 1: 6 /s | run 18: 3 /s | run 12: 78 /s | run 24: 92 /s |

Run 7 before injection (0–2.8 s): 252 /s (the user believes this is PMT noise).

Facts about the data that shape the design:
- **File headers are wrong** for PMT gain and sample (all say `pmt_gain_mv 700/1000/1100`,
  `concentration_nm 0`). The truth is in `261117 Second test\Summary.xlsx`: Sheet1 column L "PMT gain",
  column C "CH"; Sheet2 maps CH → "Negative control" (3–6) / "Positive control" (7–12).
- **Run 7 was re-saved in Excel** (quoted `"# …"` header lines, trailing commas on comment lines, three
  extra columns, `#DIV/0!` cells). Today the loader rejects it: *"The data rows aren't all numbers
  (could not convert string to float: 'time_s')"*.
- The DAQ outputs values in steps of ~0.1525 mV and **skips some codes** (e.g. it never outputs
  103.979 mV). A histogram must bin on the actual output levels or it shows false combs/gaps.
- Values span −5 to ~4,700 mV; most samples sit within ±3 mV of the baseline.
- Runs differ in length (15 s vs 60 s), so counts must be **per second**.

## 2. Decisions made with the user

- Plots: **A = histogram of every sample** and **B = histogram of spike heights**, side by side, one
  shared threshold (shown with real data on runs 6/7; user said yes).
- Threshold: **typed as a number or dragged** as a line on plot A.
- "Peak" means **spike**.
- Built as a **separate Histogram window** (option 1), not a plot-type switch on the main chart and not
  inside the noise view.
- **Per-run From/To** times in that window (run 6 0–15 s vs run 7 4–60 s).
- First version includes **all three** extras: (1) mark runs negative/positive → per-gain detection
  limit and "× / SD above negative"; (2) read gain and sample from a summary sheet like Summary.xlsx,
  or correct them by hand; (3) before/after rates using per-run From/To.
- Excel-re-saved CSVs like run 7 must load.

## 3. Approaches considered

- **Chosen: separate Histogram window** (like the noise view), opened from the toolbar. Main chart stays
  visible for checking the signal while the threshold moves.
- Rejected: a Time ↔ Histogram switch on the main chart (loses the time view; zoom/cursor/select tools
  would all need histogram versions).
- Rejected: inside the noise view (one run per view; can't overlay negative vs positive).

## 4. Design

### 4.1 Loading Excel-re-saved CSVs
- Comment lines may be wrapped in double quotes and carry trailing commas (`"# BB DAQ run 7 · …",,,,`,
  `# sample_rate_hz 10000,,,,`). Strip one pair of surrounding quotes (un-doubling `""` inside) and
  trailing commas before parsing a comment line, in both the header and the footer.
- Data: only the first two columns are used (as today). Rows whose first two cells are not both numbers
  (e.g. a quoted footer line) are dropped. Extra columns and their contents (`#DIV/0!`, blanks) are
  ignored.
- Checked on the real run 7 (2026-11-18): after the Excel re-save its time column is still uniform
  (dt = 0.0001 s) and its mV values still sit on the DAQ's 0.1525 mV grid with 4 decimals, so no
  "snapping" of values is needed.
- Files that load today must give **bit-identical** `t`, `mv`, `meta` and `summary` (regression test on
  the existing sample files). No cache format change (`FORMAT_VERSION` unchanged).

### 4.2 Run info (sample and gain overrides)
- Per-file record: `role` ∈ {"negative", "positive", ""}, `label` (free text, e.g. "Negative control"),
  `gain_mv` (number or none).
- Stored in `%LOCALAPPDATA%\BBAnalyzer\run_info.json`, keyed by the file's normalised absolute path
  (my call: survives restarts; the CSV files are never modified). Unreadable file → empty, overwritten
  on next save.
- **Effective gain** = override if set, else the header's `pmt_gain_mv`, else none. The window shows
  which (`1200` vs `700 (file)`).
- Used by the Histogram window and its export only. The main Statistics/Noise tabs and Info dialog are
  unchanged in this version.

### 4.3 Import run info…
- Button in the Histogram window. Choose an `.xlsx`; a dialog shows:
  - **Sheet** (default: first), **Run column** (default: header equal to "run"), **Gain column**
    (default: header containing "gain"), **Sample column** (default: header "conditions", "sample",
    "ch" or "channel", first match).
  - Optional **lookup**: sheet + key column + label column (default: another sheet with exactly two
    filled columns whose first column's values match the sample column's values, e.g. Sheet2 CH →
    label). When set, a run's label = lookup[sample value]; else label = the sample value itself.
  - Rows with an empty run cell are skipped. Header row = first row (my call; the dialog shows the
    headers it found so a wrong guess is visible).
- **Role from label** (case-insensitive): contains "neg" → negative; contains "pos" → positive; else "".
- **Matching to loaded traces**: run # (`run_number(f)`) equal, **and** the trace's file lies in the
  Excel file's folder or any subfolder. If several loaded traces match one run #, that row is a conflict.
- **Preview** table before applying: Run · File · Gain · Label · Role · Status (`ok`, `no loaded trace`,
  `conflict: 2 files`, `gain not a number`). Each row has a tick (conflicts unticked). **Apply** writes
  the ticked rows to run info and saves.
- By hand: double-click a Sample or Gain cell in the Histogram table to edit (Sample = role
  dropdown Negative / Positive / — plus label text; Gain = number or empty = use file).

### 4.4 Histogram window
- **Histogram…** toolbar button next to *Analyze noise*, and in the View menu. One window per app
  (opening again raises it). Non-modal.
- Opens with **one row per shown trace** in the current sort order, From/To = the main window
  (`t0`–`t1`) clipped to that trace's time range.
- Row actions: **Duplicate row** (same trace, editable From/To — e.g. run 7 0–2.8 and 4–60),
  **Remove row**, **Add shown runs** (adds rows for shown traces not yet in the table). Each row has a
  **Plot** tick; at most **12** rows are plotted (my call: readability); rows beyond 12 are unticked
  when added, with a note.
- Row colour = the trace's colour; a duplicated row gets a dashed line style in the plots.
- **Each row has its own Sample role**, starting from the file's run info (§4.2) and changeable per
  row: Negative / Positive / **— (not compared)**. Changing it on a row affects that row only; "Save as
  run info" (row context menu) writes it back to the file's record. Use: run 7 0–2.8 s (before
  injection) set to "— (not compared)" so it doesn't count as a positive. (My call.)
- Rows (their windows and row roles) live while the window is open; they are not saved between
  sessions (my call). Run info per file is saved (§4.2).
- Removing a trace from the app removes its rows; clearing all closes the window.
- Layout (top to bottom): threshold bar · plots A | B · table · comparison summary · buttons
  (Duplicate row, Remove row, Add shown runs, Import run info…, Export…, Save image…).

### 4.5 Reference level and axes
- **Reference** = median of the rows' baselines among *negative* rows; if none are negative, among all
  rows. Row baseline = `noise.spike_baseline` over the row's window (median of block means).
- Both plots use **x = signal − reference (mV)** so data at 100 mV (noise runs) and at 0 mV
  (luciferase) both work. The axis label shows the reference, e.g. "signal − 0.06 mV".
- x axis is **symlog**: linear within ±5 mV of the reference, logarithmic beyond (my call: fixed 5 mV;
  matches the real data's noise width of a few mV).
- **Plot A**: in the linear part, **one bin per DAQ output level** (the distinct values present in the
  rows' data, so skipped codes show as true gaps); in the log parts, 30 bins per decade. Y = **samples
  per second per mV** (counts / duration / bin width), log scale.
- **Plot B**: x = spike peak − reference, log-spaced bins (30 per decade) from the threshold upward;
  Y = **spikes per second per bin**, log. If the threshold is not above the reference, B shows
  "Set the threshold above the reference level (… mV)".

### 4.6 Threshold
- Entry in **mV, absolute** (with "= reference + x mV" shown beside it), plus a draggable vertical line
  on plot A (and drawn on B). Enter applies; invalid input → red, ignored.
- Default when the window opens: max over rows of (row baseline + 10 × row noise σ) (σ from
  `spike_baseline`), rounded to 0.1 mV (my call; ≈ 5–6 mV on the luciferase data).
- While dragging, the line moves live; counts, plot B and the table update on release (and on Enter).

### 4.7 Spikes
- A spike = samples above the threshold, reported at its highest sample. Above-threshold samples join
  one spike when the gap between them is **≤ max(50 µs, 1.5 × the sample interval)** — i.e. neighbouring
  samples always join, and at high rates the existing 50 µs dead time still applies.
  (Today's `find_spikes` only uses 50 µs, so at 1–10 kHz every above-threshold sample is a separate
  spike. On run 7 that is 98,076 "spikes" instead of 80,892 pulses.)
- **The rule is shared**: `find_spikes` and the new explicit-threshold `find_spikes_at(trace, t0, t1,
  threshold, dead_time=DEAD_TIME)` use the same grouping, keeping the block-skipping speed-up. This
  changes Noise-tab spike counts **only for runs sampled below ~30 kHz** (500 kHz runs are unchanged
  because 50 µs > 1.5 × 2 µs) — my call, as a pulse spanning several samples is one event. Existing
  500 kHz tests and the run_006 (Test 008) rate constant must stay unchanged.
- The real-data numbers in section 1 were computed with this rule ("consecutive samples join").
- Per row: Spikes (count), Rate = count / duration (duration = To − From clipped to the data),
  ± = √count / duration, Median and Max spike peak above the row baseline.

### 4.8 Table and comparison
- Columns: Plot · Run · Sample · Gain (mV) · From (s) · To (s) · Baseline (mV) · Spikes ·
  Rate (/s) · ± · Median peak (mV) · Max peak (mV) · × negative · SD above negative · Above limit.
- **Groups by effective gain** (rounded to 1 mV; rows without a gain form one "gain unknown" group).
  Within a group:
  - Negative rows give n, mean rate, SD (sample SD, ddof = 1), **limit = mean + 3 SD**. Each negative
    row counts once (a duplicated negative row counts twice — the user controls the rows).
  - Every non-negative row gets **× negative** = rate / mean, **SD above negative** = (rate − mean)/SD,
    **Above limit** yes/no. "–" when the group has no negatives (or SD needs n ≥ 2).
- **Comparison summary** under the table, one line per gain, e.g.
  `1200 mV: 2 negative (mean 22.3/s, SD 7.3, limit 44.1/s) · 2 positive · weakest positive ÷ strongest
  negative = 27×` — the last part shown whenever both roles are present (robust when n is small).
- Rows are listed grouped by gain (descending), then by run #, then From.
- Roles used for the comparison are the **row** roles (§4.4); "— (not compared)" rows get "–" in the
  comparison columns and don't count anywhere.
- **Before/after**: when one trace has two or more rows, the summary adds a line per later row,
  e.g. `run 7: 4–60 s ÷ 0–2.8 s = 5.6× (1,423 ± 5 vs 252 ± 10 /s)` — the ratio of each row to that
  trace's earliest-starting row (my call; no significance test beyond the shown ± values).

### 4.9 Computing
- Per row, on a worker thread: baseline/σ, the level histogram (`np.unique` on the window's values,
  cached per (trace, i0, i1)), spikes at the current threshold. A threshold change recomputes only
  spikes, B, the table and the summary.
- Progress in the window's status line (`Computing 3/24 · run_007.csv`); the table fills as rows finish.
  A threshold change while computing restarts the spike pass with the new value.
- Cost: histogram ~0.5 s per 7.4 M-sample window; spikes read only blocks above threshold.

### 4.10 Export
- **Export…** writes CSV (or .xlsx): the table, then the comparison summary lines, then threshold,
  reference, file paths and the run-info source of each gain/sample (override vs file).
- **Save image…** saves both plots as PNG.

### 4.11 Non-goals
- Comparing across different gains (no gain normalisation); downward spikes; pile-up correction for
  overlapping pulses; editing the CSV files; showing run info in the main Statistics/Noise tabs.

## 5. Architecture

| File | Responsibility |
|---|---|
| `histogram.py` (new, no Tk) | Level histogram of a trace window; bin edges (DAQ levels + log bins); reference level; per-gain comparison stats. |
| `noise.py` | `find_spikes_at` (explicit threshold); `find_spikes` calls it. |
| `run_info.py` (new, no Tk) | Load/save run_info.json; effective gain/role; Excel import: column guessing, lookup, matching, preview rows. |
| `histogram_view.py` (new) | The Histogram window, import dialog, editing, threading. |
| `multi_file_analyzer.py` | Tolerant CSV parsing; toolbar/menu entry; open/close/raise; removal hooks. |

## 6. Error handling

| Situation | Behaviour |
|---|---|
| Row window has < 2 samples | Row shows "–", note "No samples in the window"; not plotted. |
| From ≥ To or not a number | Entry red, row keeps its previous window. |
| Excel file locked/unreadable | Message naming the file ("Close it in Excel and try again"), nothing changed. |
| Import column missing/unmatched | Preview shows status per row; Apply disabled if nothing ticked. |
| run_info.json unreadable | Treated as empty; overwritten on next save. |
| Exception in one row | Row shows "error" with the message on hover/in status; others continue. |

## 7. Testing (stdlib unittest)

- Loader: run-7-style CSV (quoted comments, trailing commas, extra columns, `#DIV/0!`, quoted footer) →
  correct `t`, `mv`, `meta` (run 7, sample rate) and summary; clean sample files bit-identical.
- Level histogram: synthetic values with a skipped code → that level absent (no bin); per-second-per-mV
  scaling; symlog bin edges contiguous.
- `find_spikes_at` equals `find_spikes` for threshold = baseline + kσ; consecutive samples group.
- Comparison: hand-computed mean/SD/limit/×/SD above; n = 1 → SD "–"; no negatives → "–"; gain groups.
- Run info: json round trip; corrupt file → empty; effective gain precedence.
- Import: a Summary.xlsx-shaped workbook (Sheet1 Run/CH/PMT gain, Sheet2 CH→label) → guessed columns,
  roles, gains; subfolder matching; conflict when two loaded files share a run #.
- Window (GUI): opens with one row per shown trace; Duplicate row + From/To; typed and dragged threshold
  update counts; Plot tick limit 12; trace removal drops rows; export columns.
- Real data (skipped if missing/cloud-only), folder
  `C:\Users\MannaDoud\Sarmal\Project Trailblazer - Engineering\Bread Board\Bread Board Experiments and
  Test\Test 015 LLC in solution\261117 Second test\Luciferace 2026-11-17`: run 7 loads (600,064
  samples, uniform 0.0001 s); at 5.1 mV run 6 → 411 spikes (27.4 /s), run 7 from 4 s → about 1,423 /s,
  run 7 0–2.8 s → about 252 /s. Import from `..\Summary.xlsx` (Sheet1 headers in row 1: Run, Flow cell,
  CH, Conditions, …, PMT gain (col L), …; Sheet2 col A "CH" / col B label) → run 7 gain 1200, positive;
  run 6 gain 1200, negative.
- Spike grouping: 10 kHz pulse of 3 consecutive samples → 1 spike; 500 kHz existing spike tests and the
  Test 008 run_006 rate unchanged.

## 8. Left to the builder
Colours and styles beyond the rules above, exact widget layout, the debounce details, how the import
dialog is laid out.

## 9. Results

Measured 2026-11-18 on this PC.

| Item | Target | Measured | Verdict |
|---|---|---|---|
| Run 7 (Excel re-saved) loads | loads | 600,064 samples, dt = 0.0001 s | Met |
| Clean CSVs parse identically to before | bit-identical | 15 files (sample data + real Test 008/015 runs): 0 differences | Met |
| Run 6 spikes at 5.101 mV | 411 | 411 | Met |
| Run 7 rate 4–60 s / 0–2.8 s | ≈ 1,423 / 252 per s | within 1 % / 2 % (unit test) | Met |
| Summary.xlsx import | run 6 negative 1200, run 7 positive 1200 | yes (after typo-tolerant roles, below) | Met |
| First compute, Test 015 (24 rows, 4.1 M samples) | ≤ 1 s per 7.4 M samples | 0.74 s total (0.03 s per row) | Met |
| Threshold change, Test 015 (24 rows) | ≤ 0.3 s per row | 0.52 s total (0.02 s per row) | Met |
| First compute, Test 008 (3 rows, 28.1 M samples) | ≤ 1 s per 7.4 M samples | 1.73 s (0.46 s per 7.4 M) | Met |
| Threshold change, Test 008 (3 rows) | ≤ 0.3 s per row | 0.46 s per row | Missed — see note |

Note on the miss: those three runs have baselines of ~0.15, ~98 and ~128 mV. One shared absolute
threshold (default 11.6 mV, from the quietest row) lies below two baselines, so nearly every sample is
"above threshold" and the whole run is read (0.75 s for the 13.2 M-sample run_033). With the threshold
above the baselines (the luciferase case) only blocks containing spikes are read.

Changes from the design made while building (all in the ledger):
- Row baseline/noise = median and 1.4826 × MAD of the window's samples, not block statistics (block
  statistics assume sparse spikes; on real runs 6/7 they gave default thresholds of 85 / 1,090 mV
  instead of 4.6 / 4.7 mV).
- Default threshold = highest baseline + 10σ among negative rows, else the lowest among all rows.
- Role from label tolerates typos: the real Summary.xlsx (edited 2026-11-17 19:04) now fills
  "Conditions" with "Nagative Control"; that column is chosen as the sample column and read as negative.
