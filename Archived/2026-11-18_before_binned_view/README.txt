Archived 2026-11-18 - versions before the Binned window was added.

What changed after this archive:
- New Binned window (Binned... button / View menu): each shown run's signal averaged in fixed time
  bins (default 0.5 s), all runs overlaid, with the main chart's zoom, Back and A/B + Y1/Y2 cursors
  on the overlay; optional stacked raw panels (up to 8); CSV export; image save.
  New files: binning.py, binned_view.py and their tests (test_binning, test_binned_view,
  test_binned_real).
- multi_file_analyzer.py: Binned button and menu item; refreshes the Binned window when the main
  chart redraws; closes it on Clear all.
- chart_tools.py: Save image now includes the Y1/Y2 cursor lines and edge markers (they were
  missing from saved images).
- Design: docs/superpowers/specs/2026-11-18-binned-view-design.md

To go back: copy the files in this folder over the ones in the Analysis tool folder, and delete
binning.py and binned_view.py (and the three test files named above).
