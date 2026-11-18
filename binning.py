"""Bin means of a trace (numpy only, no Tk).

Bin k covers [t_start + k*b, t_start + (k+1)*b); its value is the mean of the samples in it, placed
at the bin centre. The last, shorter bin is kept; bins with no samples (gaps in uneven time) are
dropped. Samples are read in chunks so a 13 M-sample run is never copied to float64 in one piece.
"""
import math

import numpy as np

MAX_BINS = 200_000
CHUNK = 2_000_000
MSG_POSITIVE = "Bin size must be a positive number of seconds."


def sample_interval(trace):
    """dt for uniform time, else the median gap between samples (0.0 with fewer than 2)."""
    if trace.dt is not None:
        return float(trace.dt)
    if trace.n < 2:
        return 0.0
    return float(np.median(np.diff(trace.t)))


def _n_bins(trace, bin_s):
    return int(math.floor((trace.t_end - trace.t_start) / bin_s + 1e-9)) + 1 if trace.n else 0


def check_bin(trace, bin_s, name):
    """None when bin_s is usable for this trace, else the message to show."""
    try:
        b = float(bin_s)
    except (TypeError, ValueError):
        return MSG_POSITIVE
    if not math.isfinite(b) or b <= 0:
        return MSG_POSITIVE
    dt = sample_interval(trace)
    if b < dt or _n_bins(trace, b) > MAX_BINS:
        return f"Bin too small for {name} (dt {dt:g} s)."
    return None


def _edge_index(trace, nb, b):
    """Sample index where each bin starts (nb + 1 entries, last = n)."""
    k = np.arange(nb + 1)
    if trace.dt is not None:
        idx = np.ceil(k * b / trace.dt - 1e-9).astype(np.int64)
    else:
        idx = np.searchsorted(trace.t, trace.t_start + k * b, "left").astype(np.int64)
    idx = np.clip(idx, 0, trace.n)
    idx[-1] = trace.n
    return idx


def bin_means(trace, bin_s):
    """(bin centres in s, mean mV per bin), both float64."""
    b = float(bin_s)
    nb = _n_bins(trace, b)
    if nb == 0:
        return np.empty(0), np.empty(0)
    edges = _edge_index(trace, nb, b)
    sums = np.zeros(nb)
    counts = np.zeros(nb, dtype=np.int64)
    for a in range(0, trace.n, CHUNK):
        c = min(a + CHUNK, trace.n)
        vals = np.asarray(trace.values(a, c), dtype=np.float64)
        inner = edges[(edges > a) & (edges < c)] - a
        starts = np.concatenate(([0], inner)).astype(np.int64)
        seg_sum = np.add.reduceat(vals, starts)
        seg_cnt = np.diff(np.append(starts, len(vals)))
        first = int(np.searchsorted(edges, a, "right")) - 1
        ids = first + np.arange(len(starts))
        np.add.at(sums, ids, seg_sum)
        np.add.at(counts, ids, seg_cnt)
    keep = counts > 0
    centres = trace.t_start + (np.arange(nb) + 0.5) * b
    return centres[keep], sums[keep] / counts[keep]
