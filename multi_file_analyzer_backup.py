#!/usr/bin/env python3
"""
Multi-File Analyzer
-------------------
Load one or more BB DAQ run CSV files, see the data points on one plot
(superimposed), drag or type a time window, and get max, min, mean and
standard deviation of mV for every file inside that window.

Expected CSV layout (csv_simulator.py writes the same thing):

    # BB DAQ run 1 · experiment "Test pars data" · started 2026-10-16 08:25:50
    # sample_rate_hz 50000
    # led 2 at 100%
    # pmt_gain_mv 800
    # concentration_nm 15
    # position_mm -33 -56 5e-06
    # recipe "..." step 7 channel CH12          (optional)
    time_s,value_mV
    0.00000,-0.9375
    ...
    # samples 250880
    # duration_s 5.01758
    # max_mv / min_mv / mean_mv / std_mv ...
    # stopped_early no

Excel workbooks (.xlsx / .xlsm) exported by the instrument are read too:

    Data      Time (s) | Channel 1 raw signal (mV) | Channel 2 raw signal (mV) | ...
              (column "Channel N" is run N; columns may be different lengths)
    Metadata  one row per run: Experiment name, Run #, Date collected,
              Sample rate (Hz), PMT gain (mV), LED channel, X, Y, Z, ...
    Readouts  one row per run: Max (mV), Min (mV), Average (mV), Std dev (mV), ...

Every signal column becomes its own trace, as if it were a separate CSV file.
A workbook with only a data sheet (time in the first column) also works.

Large files (millions of rows) are fine: statistics always use every point;
the plot shows every point when zoomed in, and the min and max of each small
slice when zoomed out (so spikes are never hidden).

Requirements:
    pip install pandas matplotlib openpyxl

Run:
    python multi_file_analyzer.py
"""

import math
import os
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.widgets import SpanSelector
    HAS_MPL = True
except Exception:  # chart is optional
    HAS_MPL = False


# --------------------------------------------------------------------------
# Core logic (no GUI), easy to reuse or test on its own
# --------------------------------------------------------------------------
class ReadError(Exception):
    pass


TITLE_RE = re.compile(r'run\s+(\d+).*?experiment\s+"([^"]*)"(?:.*?started\s+(.+))?', re.I)
RECIPE_RE = re.compile(r'^"?([^"]*)"?\s*(?:step\s+(\S+))?\s*(?:channel\s+(\S+))?', re.I)


def _parse_comment(line, dest):
    """'# key value...' -> dest[key] = value. Handles the 'BB DAQ run' title and recipe lines."""
    s = line.lstrip("#").strip()
    if not s:
        return
    m = TITLE_RE.search(s)
    if m and s.lower().startswith(("bb daq", "run")):
        dest["title"] = s
        dest["run"] = m.group(1)
        dest["experiment"] = m.group(2)
        if m.group(3):
            dest["started"] = m.group(3).strip()
        return
    parts = s.split(None, 1)
    key, val = parts[0], (parts[1].strip() if len(parts) > 1 else "")
    dest[key] = val
    if key.lower() == "recipe":
        r = RECIPE_RE.match(val)
        if r:
            dest["recipe_name"] = r.group(1).strip()
            if r.group(2):
                dest["step"] = r.group(2)
            if r.group(3):
                dest["channel"] = r.group(3)


def _footer_lines(path, nbytes=8192):
    """Comment lines at the very end of the file, read without loading the whole file."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(max(0, size - nbytes))
        tail = fh.read().decode("utf-8", errors="replace").splitlines()
    out = []
    for ln in reversed(tail):
        if not ln.strip():
            continue
        if not ln.lstrip().startswith("#"):
            break
        out.append(ln)
    return list(reversed(out))


def parse_instrument_csv(path):
    """Return dict(path, file, name, meta, summary, t_label, v_label, t, mv)."""
    try:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    except PermissionError:
        raise ReadError("The file is locked. Close it in Excel (or any other program) and try again.")
    except OSError as e:
        raise ReadError(f"The file couldn't be opened: {e}")

    meta, header, skip = {}, None, 0
    with fh:
        for i, ln in enumerate(fh):
            if i > 200:
                break
            s = ln.strip()
            if not s:
                continue
            if s.startswith("#"):
                _parse_comment(s, meta)
                continue
            header, skip = s, i
            break
    if header is None:
        raise ReadError("Couldn't find the header row (e.g. 'time_s,value_mV').")

    try:
        df = pd.read_csv(path, skiprows=skip, header=0, comment="#", usecols=[0, 1],
                         skip_blank_lines=True, encoding="utf-8", encoding_errors="replace",
                         engine="c", dtype="float64")
    except ValueError as e:
        raise ReadError(f"The data rows aren't all numbers ({e}).")
    except Exception as e:
        raise ReadError(f"Couldn't read the data rows: {e}")
    df = df.dropna()
    if df.empty:
        raise ReadError("No numeric data rows were found under the header row.")

    t = df.iloc[:, 0].to_numpy()
    mv = df.iloc[:, 1].to_numpy()
    if np.any(np.diff(t) < 0):              # time must be sorted for fast windowing
        order = np.argsort(t, kind="stable")
        t, mv = t[order], mv[order]

    summary = {}
    for ln in _footer_lines(path):
        _parse_comment(ln, summary)

    cols = [c.strip() for c in df.columns]
    file = os.path.basename(path)
    return {
        "path": path,
        "file": file,
        "folder": os.path.basename(os.path.dirname(path)),
        "name": f"run {meta['run']}" if "run" in meta else os.path.splitext(file)[0],
        "meta": meta,
        "summary": summary,
        "t_label": _pretty_label(cols[0], "Time (s)"),
        "v_label": _pretty_label(cols[1], "Signal (mV)"),
        "t": t,
        "mv": mv,
    }


EXCEL_EXTS = (".xlsx", ".xlsm")

# Metadata-sheet column (lower case) -> key used by field(), the same keys as the CSV header
META_KEYS = {
    "run #": "run", "run": "run",
    "experiment name": "experiment", "experiment": "experiment",
    "date collected": "started",
    "sample rate (hz)": "sample_rate_hz",
    "data file": "data_file",
    "pmt gain (mv)": "pmt_gain_mv",
    "led power (ma)": "led_power_ma",
    "concentration (nm)": "concentration_nm",
    "collection duration (s)": "duration_s",
    "recording delay (s)": "recording_delay_s",
    "recipe": "recipe_name",
    "recipe step": "step",
    "channel": "channel",
    "record while step": "record_while_step",
    "stopped early": "stopped_early",
}
# Readouts-sheet column (lower case) -> footer key, the same keys as the CSV footer
READOUT_KEYS = {
    "max (mv)": "max_mv", "min (mv)": "min_mv", "average (mv)": "mean_mv",
    "mean (mv)": "mean_mv", "std dev (mv)": "std_mv", "duration (s)": "duration_s",
    "timestamp": "timestamp",
}


def _cell_text(v):
    """Excel cell -> text for metadata ('' for empty, 400.0 -> '400')."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v).strip()


def _find_sheet(sheets, *names):
    low = {k.strip().lower(): k for k in sheets}
    for n in names:
        if n in low:
            return sheets[low[n]]
    return None


def _rows_by_run(df):
    """Metadata/Readouts sheet -> {run text: row dict}, plus the rows in order."""
    if df is None or df.empty:
        return {}, []
    run_col = next((c for c in df.columns if str(c).strip().lower() in ("run #", "run")), None)
    rows = [r for r in df.to_dict("records") if any(_cell_text(v) for v in r.values())]
    by_run = {_cell_text(r[run_col]): r for r in rows} if run_col is not None else {}
    return by_run, rows


def _excel_meta(row):
    meta = {}
    if not row:
        return meta
    low = {str(k).strip().lower(): v for k, v in row.items()}
    for col, v in row.items():
        key = META_KEYS.get(str(col).strip().lower())
        if key is None and str(col).strip().lower() in ("led channel", "led power (%)", "x", "y", "z"):
            continue                                   # combined below, like the CSV header
        if _cell_text(v):
            meta[key or str(col).strip()] = _cell_text(v)
    if _cell_text(low.get("led channel")):
        pct = _cell_text(low.get("led power (%)"))
        meta["led"] = _cell_text(low["led channel"]) + (f" at {pct}%" if pct else "")
    xyz = [_cell_text(low.get(k)) for k in ("x", "y", "z")]
    if any(xyz):
        meta["position_mm"] = " ".join(x or "–" for x in xyz)
    return meta


def _excel_summary(row, n):
    summary = {"samples": str(n)}
    for col, v in (row or {}).items():
        key = READOUT_KEYS.get(str(col).strip().lower())
        if key and _cell_text(v):
            summary[key] = _cell_text(v)
    return summary


def parse_instrument_excel(path):
    """One dict per signal column (same shape as parse_instrument_csv returns)."""
    try:
        sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    except PermissionError:
        raise ReadError("The workbook is locked. Close it in Excel (or any other program) and try again.")
    except ImportError:
        raise ReadError("Reading Excel files needs openpyxl:  pip install openpyxl")
    except Exception as e:
        raise ReadError(f"The workbook couldn't be read: {e}")

    data = _find_sheet(sheets, "data", "raw data")
    if data is None:                                   # first sheet that isn't Metadata/Readouts
        data = next((df for name, df in sheets.items()
                     if name.strip().lower() not in ("metadata", "readouts")), None)
    if data is None or data.shape[1] < 2:
        raise ReadError("Couldn't find a data sheet with a time column and at least one signal column.")
    meta_by_run, meta_rows = _rows_by_run(_find_sheet(sheets, "metadata"))
    read_by_run, read_rows = _rows_by_run(_find_sheet(sheets, "readouts"))

    cols = [str(c).strip() for c in data.columns]
    time = pd.to_numeric(data.iloc[:, 0], errors="coerce").to_numpy(dtype="float64")
    tv = time[~np.isnan(time)]
    if tv.size == 0:
        raise ReadError(f"The first column ('{cols[0]}') of the data sheet has no numbers; it should be time.")
    dt_time = float(np.median(np.diff(tv))) if tv.size > 1 else np.nan

    book = os.path.basename(path)
    out = []
    for j in range(1, data.shape[1]):
        v = pd.to_numeric(data.iloc[:, j], errors="coerce").to_numpy(dtype="float64")
        ok = ~np.isnan(v)
        if not ok.any():
            continue
        # "Channel 12 raw signal (mV)" is run 12; otherwise match the metadata rows in order
        m = re.search(r"(?:channel|run|ch)\s*#?\s*(\d+)", cols[j], re.I)
        run = m.group(1) if m else None
        mrow = meta_by_run.get(run) if run else None
        rrow = read_by_run.get(run) if run else None
        if mrow is None and j - 1 < len(meta_rows):
            mrow = meta_rows[j - 1]
        if rrow is None and j - 1 < len(read_rows):
            rrow = read_rows[j - 1]
        meta = _excel_meta(mrow)
        if "run" not in meta and run:
            meta["run"] = run
        meta.setdefault("experiment", os.path.splitext(book)[0])
        meta["sheet_column"] = cols[j]

        # Columns can be longer than the time column: carry on at the sample rate
        n = int(np.flatnonzero(ok)[-1]) + 1
        try:
            dt = 1.0 / float(meta.get("sample_rate_hz", ""))
        except (ValueError, ZeroDivisionError):
            dt = dt_time
        t = tv[0] + np.arange(n) * (dt if np.isfinite(dt) else 1.0)
        k = min(n, len(time))
        t[:k] = np.where(np.isnan(time[:k]), t[:k], time[:k])
        t, mv = t[ok[:n]], v[:n][ok[:n]]
        if np.any(np.diff(t) < 0):
            order = np.argsort(t, kind="stable")
            t, mv = t[order], mv[order]

        out.append({
            "path": path,
            "file": meta.get("data_file") or cols[j],
            "folder": book,
            "name": f"run {meta['run']}" if "run" in meta else cols[j],
            "meta": meta,
            "summary": _excel_summary(rrow, len(t)),
            "t_label": _pretty_label(cols[0], "Time (s)"),
            "v_label": "Signal (mV)" if "mv" in cols[j].lower() else cols[j],
            "t": t,
            "mv": mv,
        })
    if not out:
        raise ReadError("The data sheet has no numeric signal columns.")
    return out


def parse_file(path):
    """List of trace dicts: one for a CSV file, one per signal column for a workbook."""
    if path.lower().endswith(EXCEL_EXTS):
        return parse_instrument_excel(path)
    if path.lower().endswith(".xls"):
        raise ReadError("Old .xls workbooks aren't supported. Save it as .xlsx in Excel and add it again.")
    return [parse_instrument_csv(path)]


def _pretty_label(col, default):
    c = col.lower()
    if c in ("time_s", "time (s)", "time"):
        return "Time (s)"
    if c in ("value_mv", "mv", "value (mv)"):
        return "Signal (mV)"
    return col or default


def window_slice(t, t0, t1):
    """Index range [i0, i1) of the sorted time array inside t0 <= t <= t1."""
    return int(np.searchsorted(t, t0, "left")), int(np.searchsorted(t, t1, "right"))


def window_stats(t, mv, t0, t1, population=True):
    """Max, min, mean, std of mV where t0 <= t <= t1 (every point is used).

    population=True (divide by n) matches the std_mv the instrument writes in the footer."""
    i0, i1 = window_slice(t, t0, t1)
    seg = mv[i0:i1]
    n = len(seg)
    ddof = 0 if population else 1
    if n == 0:
        return {"Points": 0, "Max": np.nan, "Min": np.nan, "Mean": np.nan, "Std": np.nan,
                "Time of max": np.nan, "Time of min": np.nan}
    imax, imin = int(seg.argmax()), int(seg.argmin())
    return {
        "Points": n,
        "Max": float(seg[imax]), "Min": float(seg[imin]),
        "Mean": float(seg.mean()),
        "Std": float(seg.std(ddof=ddof)) if n > ddof else np.nan,
        "Time of max": float(t[i0 + imax]), "Time of min": float(t[i0 + imin]),
    }


def view_points(t, mv, x0, x1, raw_limit=12000, bins=3000):
    """Points to draw for the visible range. Returns (t, mv, every_point_shown).

    If there are more than raw_limit points visible, keep only the min and max of
    each of `bins` equal slices, in time order, so the shape and all spikes stay visible."""
    i0, i1 = window_slice(t, x0, x1)
    i0, i1 = max(0, i0 - 1), min(len(t), i1 + 1)
    m = i1 - i0
    if m <= raw_limit:
        return t[i0:i1], mv[i0:i1], True
    k = m // bins
    end = i0 + k * bins
    ys = mv[i0:end].reshape(bins, k)
    amin, amax = ys.argmin(axis=1), ys.argmax(axis=1)
    base = i0 + np.arange(bins) * k
    idx = np.column_stack([base + np.minimum(amin, amax), base + np.maximum(amin, amax)]).ravel()
    if end < i1:
        idx = np.append(idx, [end + int(mv[end:i1].argmin()), end + int(mv[end:i1].argmax())])
        idx[-2:] = np.sort(idx[-2:])
    return t[idx], mv[idx], False


def fmt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    a = abs(v)
    if a != 0 and (a >= 1e7 or a < 1e-4):
        return f"{v:.4e}"
    return f"{v:,.6g}" if a < 1000 else f"{v:,.2f}"


def field(f, *keys):
    """First matching metadata value (case-insensitive key match), else ''."""
    for d in (f["meta"], f["summary"]):
        low = {k.lower(): v for k, v in d.items()}
        for k in keys:
            if k in low:
                return low[k]
    return ""


def is_stopped(f):
    return str(field(f, "stopped_early", "stopped early")).strip().lower() in ("yes", "y", "true", "1")


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
          "#17becf", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22",
          "#393b79", "#ad494a", "#637939", "#7b4173", "#e6550d",
          "#3182bd", "#843c39", "#31a354", "#de9ed6", "#8c6d31"]

LEGEND_MODES = ["Auto", "Run", "Run + experiment", "Folder / file", "PMT gain",
                "Concentration", "Channel", "Position"]


class App(tk.Tk):
    FILE_COLS = ("Show", "Run", "Experiment", "Rate (Hz)", "Gain (mV)", "Conc. (nM)",
                 "Channel", "Points", "Stopped early")
    RES_COLS = ("Label", "Points", "Max", "Min", "Mean", "Std")

    def __init__(self):
        super().__init__()
        self.title("Multi-File Analyzer")
        self.geometry("1400x880")
        self.minsize(1050, 680)
        self.files = []          # parsed dicts, each also has "show" and "color"
        self.results = None
        self.span = None
        self._lines = []         # (Line2D, file) currently on the chart
        self._xlim_job = None
        self._loading = False
        self._q = queue.Queue()
        self._build_ui()

    # ---------- layout ----------
    def _build_ui(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=22)
        style.configure("Accent.TButton", font=("TkDefaultFont", 10, "bold"))

        root = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        left = ttk.Frame(root, padding=6)
        right = ttk.Frame(root, padding=6)
        root.add(left, weight=0)
        root.add(right, weight=1)

        # 1. Files
        f1 = ttk.LabelFrame(left, text="1. Files (click ☑ to hide/show)", padding=8)
        f1.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        b = ttk.Frame(f1); b.pack(fill=tk.X)
        self.add_btn = ttk.Button(b, text="Add files…", command=self.add_files)
        self.add_btn.pack(side=tk.LEFT)
        ttk.Button(b, text="Remove", command=self.remove_selected).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="Clear all", command=self.clear_all).pack(side=tk.LEFT)
        ttk.Button(b, text="Show all", command=lambda: self.set_all_shown(True)).pack(side=tk.RIGHT)
        ttk.Button(b, text="Hide all", command=lambda: self.set_all_shown(False)).pack(side=tk.RIGHT, padx=4)

        tf = ttk.Frame(f1); tf.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self.ftree = ttk.Treeview(tf, columns=self.FILE_COLS, show="headings", height=10, selectmode="extended")
        widths = {"Show": 44, "Run": 40, "Experiment": 170, "Rate (Hz)": 70, "Gain (mV)": 70,
                  "Conc. (nM)": 70, "Channel": 60, "Points": 80, "Stopped early": 86}
        for c in self.FILE_COLS:
            self.ftree.heading(c, text=c)
            self.ftree.column(c, width=widths[c], anchor="w" if c == "Experiment" else "center", stretch=False)
        ys = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.ftree.yview)
        xs = ttk.Scrollbar(tf, orient=tk.HORIZONTAL, command=self.ftree.xview)
        self.ftree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.ftree.grid(row=0, column=0, sticky="nsew"); ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1); tf.columnconfigure(0, weight=1)
        self.ftree.bind("<Button-1>", self._on_tree_click)
        self.ftree.bind("<<TreeviewSelect>>", lambda e: self.show_info())
        self.ftree.tag_configure("stopped", foreground="#b03030")

        ttk.Label(f1, text="File details (header and footer lines)").pack(anchor="w", pady=(8, 0))
        self.info = tk.Text(f1, height=14, width=60, wrap="none", font=("Consolas", 9),
                            relief="solid", borderwidth=1)
        self.info.pack(fill=tk.X)
        self.info.config(state="disabled")

        # 2. Plot options
        f2 = ttk.LabelFrame(left, text="2. Plot", padding=8)
        f2.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(f2, text="Style").grid(row=0, column=0, sticky="w")
        self.style_var = tk.StringVar(value="Points")
        cb = ttk.Combobox(f2, textvariable=self.style_var, state="readonly", width=16,
                          values=["Points", "Line", "Points + line"])
        cb.grid(row=0, column=1, sticky="w", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.draw())
        ttk.Label(f2, text="Legend").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.legend_var = tk.StringVar(value="Auto")
        cb2 = ttk.Combobox(f2, textvariable=self.legend_var, state="readonly", width=24, values=LEGEND_MODES)
        cb2.grid(row=1, column=1, sticky="w", padx=6, pady=(4, 0))
        cb2.bind("<<ComboboxSelected>>", lambda e: (self.draw(), self._recalc_if_done()))
        ttk.Button(f2, text="Reset zoom", command=lambda: self.draw(reset_view=True)).grid(
            row=0, column=2, rowspan=2, padx=(8, 0))

        # 3. Window
        f3 = ttk.LabelFrame(left, text="3. Window (time range, s)", padding=8)
        f3.pack(fill=tk.X)
        g = ttk.Frame(f3); g.pack(fill=tk.X)
        ttk.Label(g, text="Start").grid(row=0, column=0, sticky="w")
        ttk.Label(g, text="End").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.t0_var, self.t1_var = tk.StringVar(), tk.StringVar()
        e0 = ttk.Entry(g, textvariable=self.t0_var, width=14); e0.grid(row=1, column=0, sticky="ew")
        e1 = ttk.Entry(g, textvariable=self.t1_var, width=14); e1.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        g.columnconfigure(0, weight=1); g.columnconfigure(1, weight=1)
        for e in (e0, e1):
            e.bind("<Return>", lambda ev: self.calculate())
        ttk.Button(f3, text="Use full range", command=self.full_range).pack(anchor="w", pady=(6, 0))
        self.sample_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f3, text="Sample std. dev. (divide by n−1). Off = divide by n,\nthe same as the instrument's std_mv",
                        variable=self.sample_var, command=self._recalc_if_done).pack(anchor="w", pady=(6, 0))
        self.visible_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(f3, text="Statistics for shown files only",
                        variable=self.visible_only, command=self._recalc_if_done).pack(anchor="w")
        ttk.Button(f3, text="Calculate statistics", style="Accent.TButton",
                   command=self.calculate).pack(fill=tk.X, pady=(8, 0))

        # Right: chart
        chart_box = ttk.LabelFrame(right, text="Data (drag on the chart to set the window)", padding=6)
        chart_box.pack(fill=tk.BOTH, expand=True)
        self.view_lbl = ttk.Label(chart_box, text="", foreground="#666", wraplength=900)
        self.view_lbl.pack(anchor="w")
        if HAS_MPL:
            self.fig = Figure(figsize=(8, 4.5), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=chart_box)
            NavigationToolbar2Tk(self.canvas, chart_box, pack_toolbar=True).update()
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self._empty_chart("Add one or more CSV or Excel files to see the data.")
        else:
            ttk.Label(chart_box, text="Install matplotlib to see the chart:  pip install matplotlib",
                      foreground="#a33").pack(pady=40)

        # Right: results
        res_box = ttk.LabelFrame(right, text="Statistics in the window (mV)", padding=6)
        res_box.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.range_lbl = ttk.Label(res_box, text="Results appear here after you calculate.", foreground="#666")
        self.range_lbl.pack(anchor="w")
        rf = ttk.Frame(res_box); rf.pack(fill=tk.BOTH, expand=True, pady=4)
        self.rtree = ttk.Treeview(rf, columns=self.RES_COLS, show="headings", height=7)
        for c in self.RES_COLS:
            self.rtree.heading(c, text=c)
            self.rtree.column(c, width=260 if c == "Label" else 110,
                              anchor="w" if c == "Label" else "e", stretch=True)
        rys = ttk.Scrollbar(rf, orient=tk.VERTICAL, command=self.rtree.yview)
        self.rtree.configure(yscrollcommand=rys.set)
        self.rtree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); rys.pack(side=tk.RIGHT, fill=tk.Y)
        act = ttk.Frame(res_box); act.pack(fill=tk.X)
        ttk.Button(act, text="Export results…", command=self.export_results).pack(side=tk.LEFT)
        ttk.Button(act, text="Copy to clipboard", command=self.copy_results).pack(side=tk.LEFT, padx=6)
        ttk.Button(act, text="Export window data…", command=self.export_window_data).pack(side=tk.LEFT)

        self.status = ttk.Label(self, text="Add CSV files or Excel workbooks to begin.", anchor="w", padding=(8, 2))
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    # ---------- files ----------
    def add_files(self):
        if self._loading:
            return
        paths = filedialog.askopenfilenames(
            title="Add CSV files or Excel workbooks",
            filetypes=[("Data files", "*.csv *.txt *.tsv *.dat *.xlsx *.xlsm"),
                       ("CSV / text files", "*.csv *.txt *.tsv *.dat"),
                       ("Excel workbooks", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if not paths:
            return
        loaded = {os.path.normcase(os.path.abspath(f["path"])) for f in self.files}
        paths = [p for p in paths if os.path.normcase(os.path.abspath(p)) not in loaded]
        if not paths:
            self.status.config(text="Those files are already loaded.")
            return
        self.load_paths(paths)

    def load_paths(self, paths):
        """Parse files on a background thread so big files don't freeze the window."""
        self._loading = True
        self.add_btn.config(state="disabled")
        self.config(cursor="watch")

        def work():
            for i, p in enumerate(paths):
                mb = os.path.getsize(p) / 1e6 if os.path.exists(p) else 0
                self._q.put(("progress", f"Loading {i + 1}/{len(paths)}: {os.path.basename(p)} ({mb:,.0f} MB)…"))
                try:
                    for f in parse_file(p):
                        self._q.put(("file", f))
                except ReadError as e:
                    self._q.put(("error", f"{os.path.basename(p)}: {e}"))
                except Exception as e:
                    self._q.put(("error", f"{os.path.basename(p)}: {type(e).__name__}: {e}"))
            self._q.put(("done", None))

        self._load_errors, self._load_added = [], 0
        threading.Thread(target=work, daemon=True).start()
        self.after(100, self._poll_loader)

    def _poll_loader(self):
        done = False
        try:
            while True:
                kind, val = self._q.get_nowait()
                if kind == "progress":
                    self.status.config(text=val)
                elif kind == "error":
                    self._load_errors.append(val)
                elif kind == "file":
                    val["show"] = True
                    val["color"] = self._next_color()
                    self.files.append(val)
                    self._load_added += 1
                elif kind == "done":
                    done = True
        except queue.Empty:
            pass
        if not done:
            self.after(100, self._poll_loader)
            return
        self._loading = False
        self.add_btn.config(state="normal")
        self.config(cursor="")
        if self._load_added:
            self.refresh_files()
            if not self.t0_var.get() or not self.t1_var.get():
                self.full_range(calc=False)
            self.draw(reset_view=True)
            self._recalc_if_done()
        self.status.config(text=f"Added {self._load_added} trace(s). {len(self.files)} loaded, "
                                f"{sum(len(f['t']) for f in self.files):,} points in total.")
        if self._load_errors:
            messagebox.showwarning("Some files were skipped", "\n".join(self._load_errors))

    def _next_color(self):
        used = {f["color"] for f in self.files}
        for c in COLORS:
            if c not in used:
                return c
        return COLORS[len(self.files) % len(COLORS)]

    def refresh_files(self):
        sel = set(self.ftree.selection())
        self.ftree.delete(*self.ftree.get_children())
        for i, f in enumerate(self.files):
            iid = str(i)
            self.ftree.insert("", tk.END, iid=iid, values=(
                "☑" if f["show"] else "☐",
                field(f, "run"),
                field(f, "experiment") or f["file"],
                field(f, "sample_rate_hz", "sample rate (hz)"),
                field(f, "pmt_gain_mv", "pmt gain"),
                field(f, "concentration_nm", "concentration"),
                field(f, "channel") or "–",
                f"{len(f['t']):,}",
                field(f, "stopped_early", "stopped early") or "–"),
                tags=("stopped",) if is_stopped(f) else ())
            if iid in sel:
                self.ftree.selection_add(iid)

    def _on_tree_click(self, event):
        if self.ftree.identify_region(event.x, event.y) != "cell":
            return
        if self.ftree.identify_column(event.x) != "#1":   # the Show column
            return
        iid = self.ftree.identify_row(event.y)
        if not iid:
            return
        f = self.files[int(iid)]
        f["show"] = not f["show"]
        self.ftree.set(iid, "Show", "☑" if f["show"] else "☐")
        self.draw()
        self._recalc_if_done()
        return "break"

    def set_all_shown(self, show):
        for f in self.files:
            f["show"] = show
        self.refresh_files()
        self.draw()
        self._recalc_if_done()

    def remove_selected(self):
        idx = sorted((int(i) for i in self.ftree.selection()), reverse=True)
        if not idx:
            messagebox.showinfo("Remove", "Select one or more files in the list first.")
            return
        for i in idx:
            del self.files[i]
        self.ftree.selection_set(())
        self.refresh_files()
        self.show_info()
        self.draw(reset_view=True)
        self._recalc_if_done()

    def clear_all(self):
        self.files.clear()
        self.results = None
        self.refresh_files()
        self.rtree.delete(*self.rtree.get_children())
        self.range_lbl.config(text="Results appear here after you calculate.", foreground="#666")
        self.t0_var.set(""); self.t1_var.set("")
        self.show_info()
        self.draw(reset_view=True)

    def show_info(self):
        sel = self.ftree.selection()
        self.info.config(state="normal")
        self.info.delete("1.0", tk.END)
        if sel:
            f = self.files[int(sel[0])]
            hide = {"title", "recipe_name", "step", "channel", "sheet_column"}   # derived keys; raw lines are shown
            meta = {k: v for k, v in f["meta"].items() if k not in hide}
            w = max([len(k) for k in list(meta) + list(f["summary"])] + [10])
            lines = [f"File:   {f['path']}"]
            if "sheet_column" in f["meta"]:
                lines.append(f"Column: {f['meta']['sheet_column']}")
            if "title" in f["meta"]:
                lines.append(f["meta"]["title"])
            lines.append("")
            lines += [f"{k:<{w}}  {v}" for k, v in meta.items()]
            lines += ["", "From the file footer:"]
            lines += [f"{k:<{w}}  {v}" for k, v in f["summary"].items()] or ["(none)"]
            self.info.insert("1.0", "\n".join(lines))
        self.info.config(state="disabled")

    # ---------- window ----------
    def _time_extent(self, files=None):
        files = self.files if files is None else files
        if not files:
            return None
        return min(float(f["t"][0]) for f in files), max(float(f["t"][-1]) for f in files)

    def full_range(self, calc=True):
        ext = self._time_extent()
        if ext is None:
            return
        self.t0_var.set(f"{ext[0]:g}")
        self.t1_var.set(f"{ext[1]:g}")
        if calc:
            self.calculate()
        else:
            self.draw()

    def _get_range(self, quiet=False):
        try:
            t0 = float(self.t0_var.get()); t1 = float(self.t1_var.get())
        except ValueError:
            if not quiet:
                messagebox.showwarning("Window", "Enter start and end times as numbers of seconds, e.g. 1 or 2.5.")
            return None
        if t1 < t0:
            if not quiet:
                messagebox.showwarning("Window", "The end time is before the start time.")
            return None
        return t0, t1

    def _recalc_if_done(self):
        if self.results is not None and self.files:
            self.calculate()

    def _stat_files(self):
        return [f for f in self.files if f["show"] or not self.visible_only.get()]

    # ---------- stats ----------
    def calculate(self):
        if not self.files:
            messagebox.showinfo("No data", "Add one or more CSV or Excel files first.")
            return
        rng = self._get_range()
        if rng is None:
            return
        t0, t1 = rng
        pop = not self.sample_var.get()
        rows = []
        for f in self._stat_files():
            s = window_stats(f["t"], f["mv"], t0, t1, population=pop)
            rows.append({
                "Label": self._label(f),
                "Folder": f["folder"], "File": f["file"],
                "Run": field(f, "run"), "Experiment": field(f, "experiment"),
                "Sample rate (Hz)": field(f, "sample_rate_hz"),
                "LED": field(f, "led"),
                "PMT gain (mV)": field(f, "pmt_gain_mv"),
                "Concentration (nM)": field(f, "concentration_nm"),
                "Position (mm)": field(f, "position_mm"),
                "Recipe": field(f, "recipe_name"), "Step": field(f, "step"), "Channel": field(f, "channel"),
                **s, "color": f["color"]})
        self.results = pd.DataFrame(rows)
        self.result_meta = (t0, t1, pop)

        self.rtree.delete(*self.rtree.get_children())
        for i, r in enumerate(rows):
            tag = f"c{i}"
            self.rtree.insert("", tk.END, values=(
                r["Label"], fmt(int(r["Points"])),
                fmt(r["Max"]), fmt(r["Min"]), fmt(r["Mean"]), fmt(r["Std"])), tags=(tag,))
            self.rtree.tag_configure(tag, foreground=r["color"])
        self.range_lbl.config(
            text=f"Window {t0:g} s to {t1:g} s  (width {t1 - t0:g} s),  "
                 f"{'population' if pop else 'sample'} standard deviation, every data point used",
            foreground="")
        empty = [r["Label"] for r in rows if r["Points"] == 0]
        msg = f"Calculated statistics for {len(rows)} file(s)."
        if empty:
            msg += f" No points in the window for: {', '.join(empty)}."
        self.status.config(text=msg)
        self.draw()

    # ---------- export ----------
    def _results_table(self):
        if self.results is None or self.results.empty:
            messagebox.showinfo("Nothing to export", "Calculate statistics first.")
            return None
        t0, t1, pop = self.result_meta
        out = self.results.drop(columns=["color"]).copy()
        out["Window start (s)"] = t0
        out["Window end (s)"] = t1
        out["Std. dev. type"] = "population" if pop else "sample"
        return out

    def export_results(self):
        out = self._results_table()
        if out is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export results", defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Excel workbook", "*.xlsx")],
            initialfile="window_statistics.csv")
        if not path:
            return
        try:
            if path.lower().endswith(".xlsx"):
                out.to_excel(path, index=False, sheet_name="Statistics")
            else:
                out.to_csv(path, index=False)
            self.status.config(text=f"Exported results to {path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def copy_results(self):
        out = self._results_table()
        if out is None:
            return
        cols = ["Label", "Run", "PMT gain (mV)", "Concentration (nM)", "Channel",
                "Points", "Max", "Min", "Mean", "Std"]
        self.clipboard_clear()
        self.clipboard_append(out[cols].to_csv(sep="\t", index=False, lineterminator="\n").rstrip("\n"))
        self.status.config(text="Results copied. Paste into Excel.")

    def export_window_data(self):
        """Every point inside the window, one Time/mV column pair per file."""
        files = self._stat_files()
        if not files:
            messagebox.showinfo("Nothing to export", "Add and show at least one file.")
            return
        rng = self._get_range()
        if rng is None:
            return
        t0, t1 = rng
        parts, total = [], 0
        for f in files:
            i0, i1 = window_slice(f["t"], t0, t1)
            total += i1 - i0
            lab = self._label(f)
            parts.append(pd.DataFrame({f"{lab} time_s": f["t"][i0:i1], f"{lab} mV": f["mv"][i0:i1]}))
        if total > 1_000_000 and not messagebox.askyesno(
                "Large export", f"The window holds {total:,} points. Exporting may take a while "
                                "and Excel can only open 1,048,576 rows. Continue?"):
            return
        out = pd.concat(parts, axis=1)
        path = filedialog.asksaveasfilename(
            title="Export window data", defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Excel workbook", "*.xlsx")],
            initialfile=f"window_{t0:g}s_to_{t1:g}s.csv")
        if not path:
            return
        try:
            self.config(cursor="watch"); self.update_idletasks()
            out.to_excel(path, index=False) if path.lower().endswith(".xlsx") else out.to_csv(path, index=False)
            self.status.config(text=f"Exported window data for {len(files)} file(s) to {path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))
        finally:
            self.config(cursor="")

    # ---------- chart ----------
    def _empty_chart(self, msg):
        self.ax.clear()
        self._lines = []
        self.ax.text(0.5, 0.5, msg, ha="center", va="center", color="#777", transform=self.ax.transAxes)
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.view_lbl.config(text="")
        self.canvas.draw_idle()

    def _label(self, f):
        mode = self.legend_var.get()
        run = f["name"]
        if mode == "Auto":
            exps = {field(x, "experiment") for x in self.files}
            folders = {x["folder"] for x in self.files}
            if len(exps) > 1:
                return f"{run} · {field(f, 'experiment')}"
            if len(folders) > 1:
                return f"{f['folder']}/{f['file']}"
            return run
        if mode == "Run + experiment":
            return f"{run} · {field(f, 'experiment')}"
        if mode == "Folder / file":
            return f"{f['folder']}/{f['file']}"
        if mode == "PMT gain":
            return f"{run} · {field(f, 'pmt_gain_mv')} mV gain"
        if mode == "Concentration":
            return f"{run} · {field(f, 'concentration_nm')} nM"
        if mode == "Channel":
            return f"{run} · {field(f, 'channel') or 'no channel'}"
        if mode == "Position":
            return f"{run} · {field(f, 'position_mm')} mm"
        return run

    def _apply_style(self, line, raw):
        """Every point shown: the chosen style. Zoomed out (min/max per slice): a solid
        zig-zag line, which reads as the min-to-max band, like an oscilloscope."""
        style = self.style_var.get()
        if not raw:
            line.set(ls="-", lw=0.6, marker="None", alpha=0.45)
        elif style == "Line":
            line.set(ls="-", lw=1.0, marker="None", alpha=1.0)
        elif style == "Points + line":
            line.set(ls="-", lw=0.7, marker="o", ms=2.5, alpha=1.0)
        else:
            line.set(ls="None", marker="o", ms=2.5, alpha=0.8)

    def draw(self, reset_view=False):
        if not HAS_MPL:
            return
        shown = [f for f in self.files if f["show"]]
        if not shown:
            self._empty_chart("Tick ☑ next to a file to see its data." if self.files
                              else "Add one or more CSV or Excel files to see the data.")
            return
        ax = self.ax
        keep = bool(self._lines) and not reset_view
        if keep:
            xl, yl = ax.get_xlim(), ax.get_ylim()
            x0, x1 = xl
        else:
            x0, x1 = self._time_extent(shown)
        ax.clear()
        self._lines = []
        for f in shown:
            t, y, raw = view_points(f["t"], f["mv"], x0, x1)
            line, = ax.plot(t, y, color=f["color"], label=self._label(f))
            self._apply_style(line, raw)
            self._lines.append((line, f))
        ax.set_xlabel(shown[0]["t_label"]); ax.set_ylabel(shown[0]["v_label"])
        ax.grid(True, alpha=0.3)
        leg = ax.legend(loc="best", fontsize=8, markerscale=2, ncol=1 + (len(shown) - 1) // 12)
        for h in leg.legend_handles:
            h.set(lw=3, alpha=1.0)
        rng = self._get_range(quiet=True)
        if rng:
            t0, t1 = rng
            ax.axvspan(t0, t1, color="#E0A100", alpha=0.15)
            ax.axvline(t0, color="#C48C00", lw=1); ax.axvline(t1, color="#C48C00", lw=1)
        if keep:
            ax.set_xlim(xl); ax.set_ylim(yl)
        else:
            ax.set_xlim(x0, x1)
        ax.callbacks.connect("xlim_changed", self._on_xlim)   # ax.clear() drops old callbacks
        self.fig.tight_layout()
        self.span = SpanSelector(ax, self._on_span, "horizontal", useblit=True,
                                 props=dict(alpha=0.2, facecolor="#555"), interactive=False)
        self._update_view_label()
        self.canvas.draw_idle()

    def _on_xlim(self, _ax):
        """Zoom/pan changed: re-pick which points to draw (debounced)."""
        if self._xlim_job is not None:
            self.after_cancel(self._xlim_job)
        self._xlim_job = self.after(60, self._refresh_view)

    def _refresh_view(self):
        self._xlim_job = None
        if not self._lines:
            return
        x0, x1 = self.ax.get_xlim()
        for line, f in self._lines:
            t, y, raw = view_points(f["t"], f["mv"], x0, x1)
            line.set_data(t, y)
            self._apply_style(line, raw)
        self._update_view_label()
        self.canvas.draw_idle()

    def _update_view_label(self):
        x0, x1 = self.ax.get_xlim()
        reduced = []
        for _, f in self._lines:
            i0, i1 = window_slice(f["t"], x0, x1)
            if i1 - i0 > 12000:
                reduced.append(f"{f['name']} ({i1 - i0:,} pts)")
        if reduced:
            self.view_lbl.config(
                text="Zoomed out: drawing the min-to-max band (every spike kept) for " + ", ".join(reduced)
                     + ". Zoom in (magnifier tool) to see every point. Statistics always use every point.",
                foreground="#a06000")
        else:
            self.view_lbl.config(text="Showing every data point.", foreground="#666")

    def _on_span(self, xmin, xmax):
        if xmax - xmin <= 0:
            return
        self.t0_var.set(f"{xmin:.6g}")
        self.t1_var.set(f"{xmax:.6g}")
        self.after(10, self.calculate)


if __name__ == "__main__":
    App().mainloop()
