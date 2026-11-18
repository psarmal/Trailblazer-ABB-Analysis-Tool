Archived 2026-11-18 - versions before the Histogram window was added.

What changed after this archive:
- New Histogram window (Histogram... button): intensity histograms of every sample (A) and of
  spike peaks (B), a typed/dragged spike threshold, per-run From/To rows, positive vs negative
  comparison at matched PMT gain, run info (gain/sample) import from a summary workbook.
  New files: histogram.py, histogram_view.py, run_info.py and their tests.
- multi_file_analyzer.py: loads CSV files re-saved by Excel (quoted header lines, extra columns,
  #DIV/0! cells); Histogram button and menu item.
- noise.py: neighbouring samples above the threshold now count as one spike
  (gap <= max(50 us, 1.5 sample intervals)). Noise-tab spike counts change for runs sampled
  below ~30 kHz; 500 kHz runs are unchanged.
- Design: docs/superpowers/specs/2026-11-18-intensity-histogram-design.md

To go back: copy the files in this folder over the ones in the Analysis tool folder, and delete
histogram.py, histogram_view.py and run_info.py.
