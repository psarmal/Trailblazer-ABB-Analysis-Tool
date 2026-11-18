#!/usr/bin/env python3
"""
Time Range Analyzer
-------------------
Load an Excel/CSV file, pick a time column (absolute time in seconds, e.g. 0, 1, 2.5 ...)
and one or more data columns, enter a start and end time, and get
count, min, max, average and standard deviation for that time range.

Requirements:
    pip install pandas openpyxl matplotlib
    (add `xlrd` if you need old .xls files)

Run:
    python time_range_analyzer.py
"""

import math
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
def to_seconds(series: pd.Series) -> pd.Series:
    """Convert a time column to numeric seconds.

    Numbers are used as-is (absolute time in seconds).
    If the column holds date/times or time-of-day values instead, they are
    converted to seconds elapsed since the first timestamp.
    """
    num = pd.to_numeric(series, errors="coerce")
    if num.notna().sum() >= max(1, 0.5 * series.notna().sum()):
        return num.astype(float)

    # Fallback: datetime / timedelta / "hh:mm:ss" strings
    td = pd.to_timedelta(series.astype(str), errors="coerce")
    if td.notna().sum() >= max(1, 0.5 * series.notna().sum()):
        return td.dt.total_seconds()
    dt = pd.to_datetime(series, errors="coerce")
    if dt.notna().any():
        return (dt - dt.min()).dt.total_seconds()
    return num.astype(float)


def range_stats(time_s: pd.Series, data: pd.DataFrame, t_start: float, t_end: float,
                population: bool = False) -> pd.DataFrame:
    """Statistics for every data column where t_start <= time <= t_end."""
    mask = (time_s >= t_start) & (time_s <= t_end)
    ddof = 0 if population else 1
    rows = []
    for col in data.columns:
        vals_all = data.loc[mask, col]
        vals = pd.to_numeric(vals_all, errors="coerce")
        skipped = int(vals_all.notna().sum() - vals.notna().sum())
        valid = vals.dropna()
        n = int(valid.size)
        if n:
            i_min, i_max = valid.idxmin(), valid.idxmax()
            std = float(valid.std(ddof=ddof)) if n > ddof else float("nan")
            rows.append({
                "Column": col, "Points": n, "Skipped (non-numeric)": skipped,
                "Min": float(valid[i_min]), "Time of min (s)": float(time_s[i_min]),
                "Max": float(valid[i_max]), "Time of max (s)": float(time_s[i_max]),
                "Average": float(valid.mean()), "Std. deviation": std,
            })
        else:
            rows.append({
                "Column": col, "Points": 0, "Skipped (non-numeric)": skipped,
                "Min": np.nan, "Time of min (s)": np.nan, "Max": np.nan,
                "Time of max (s)": np.nan, "Average": np.nan, "Std. deviation": np.nan,
            })
    return pd.DataFrame(rows)


class ReadError(Exception):
    pass


def _read_text_table(path):
    """Read a delimited text file, trying common encodings and separators."""
    last = None
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as fh:
                sample = fh.read(20000)
            if "\x00" in sample:
                continue
        except (UnicodeError, UnicodeDecodeError) as e:
            last = e
            continue
        lines = [ln for ln in sample.splitlines() if ln.strip()][:50]
        # pick the separator that splits lines most consistently
        best, best_score = ",", -1
        for sep in (",", "\t", ";", "|"):
            counts = [ln.count(sep) for ln in lines]
            if counts and max(counts) > 0:
                score = sum(1 for c in counts if c == max(counts))
                if score > best_score:
                    best, best_score = sep, score
        try:
            if best_score <= 0:  # no separator found: whitespace-separated
                return pd.read_csv(path, header=None, sep=r"\s+", engine="python", encoding=enc)
            return pd.read_csv(path, header=None, sep=best, engine="python", encoding=enc,
                               on_bad_lines="skip")
        except Exception as e:
            last = e
    raise ReadError(f"Couldn't read it as a text table ({last}).")


def read_any(path):
    """Return {sheet_name: DataFrame} for xlsx/xlsm/xls/xlsb/ods/csv/txt/html,
    detecting the real format from the file contents rather than the extension."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except PermissionError:
        raise ReadError("The file is locked. Close it in Excel (or any other program) and try again.")
    except OSError as e:
        raise ReadError(f"The file couldn't be opened: {e}")

    ext = path.lower().rsplit(".", 1)[-1] if "." in path else ""
    try:
        if head.startswith(b"PK"):                       # xlsx / xlsm / ods (zip)
            engine = "odf" if ext == "ods" else "openpyxl"
            return pd.read_excel(path, sheet_name=None, header=None, engine=engine)
        if head.startswith(b"\xD0\xCF\x11\xE0"):          # old binary .xls (or encrypted xlsx)
            if ext == "xlsb":
                return pd.read_excel(path, sheet_name=None, header=None, engine="pyxlsb")
            try:
                return pd.read_excel(path, sheet_name=None, header=None, engine="xlrd")
            except ImportError:
                raise
            except Exception as e:
                raise ReadError("This looks like an old .xls file or a password-protected workbook. "
                                "If it has a password, open it in Excel, remove the password and save "
                                f"as .xlsx.\n\nDetail: {e}")
    except ImportError as e:
        raise ReadError(f"A package is missing: {e}\n\n"
                        "Run:  python -m pip install openpyxl xlrd pyxlsb odfpy")

    # Not a real Excel file: many data loggers save HTML or text with an .xls name
    with open(path, "rb") as fh:
        start = fh.read(2048).lower()
    if b"<html" in start or b"<table" in start or b"<?xml" in start:
        try:
            tables = pd.read_html(path)
            out = {}
            for i, t in enumerate(tables):
                cols = [" ".join(map(str, c)) if isinstance(c, tuple) else c for c in t.columns]
                if any(not str(c).isdigit() for c in cols):   # real header: put it back as row 1
                    t = pd.concat([pd.DataFrame([cols]), pd.DataFrame(t.values)], ignore_index=True)
                out[f"Table {i + 1}"] = t
            return out
        except Exception:
            pass
    return {"Data": _read_text_table(path)}


def fmt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, (int, np.integer)):
        return f"{v:,}"
    a = abs(v)
    if a != 0 and (a >= 1e7 or a < 1e-4):
        return f"{v:.4e}"
    return f"{v:,.6g}" if a < 1000 else f"{v:,.2f}"


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
class App(tk.Tk):
    RESULT_COLS = ["Column", "Points", "Max", "Min", "Average", "Std. deviation"]
    STAT_ORDER = ["Max", "Min", "Average", "Std. deviation"]

    def __init__(self):
        super().__init__()
        self.title("Time Range Analyzer")
        self.geometry("1200x780")
        self.minsize(900, 600)

        self.path = None
        self.raw = None          # full sheet without header processing
        self.df = None           # data with headers
        self.time_s = None       # time column in seconds
        self.result = None       # last results DataFrame
        self.span = None
        self.band = None

        self._build_ui()

    # ---------- layout ----------
    def _build_ui(self):
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=24)
        style.configure("Accent.TButton", font=("TkDefaultFont", 10, "bold"))

        root = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        root.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(root, padding=6)
        right = ttk.Frame(root, padding=6)
        root.add(left, weight=0)
        root.add(right, weight=1)

        # 1. File
        f1 = ttk.LabelFrame(left, text="1. Load a spreadsheet", padding=8)
        f1.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(f1, text="Open file…", command=self.open_file).pack(fill=tk.X)
        self.file_lbl = ttk.Label(f1, text="No file loaded", foreground="#666", wraplength=280)
        self.file_lbl.pack(fill=tk.X, pady=(4, 6))
        row = ttk.Frame(f1); row.pack(fill=tk.X)
        ttk.Label(row, text="Sheet").grid(row=0, column=0, sticky="w")
        ttk.Label(row, text="Header row").grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.sheet_cb = ttk.Combobox(row, state="disabled", width=18)
        self.sheet_cb.grid(row=1, column=0, sticky="ew")
        self.sheet_cb.bind("<<ComboboxSelected>>", lambda e: self.load_sheet())
        self.hdr_var = tk.IntVar(value=1)
        self.hdr_sb = ttk.Spinbox(row, from_=1, to=1000, textvariable=self.hdr_var, width=6,
                                  command=self.apply_header, state="disabled")
        self.hdr_sb.grid(row=1, column=1, sticky="ew", padx=(8, 0))
        self.hdr_sb.bind("<Return>", lambda e: self.apply_header())
        row.columnconfigure(0, weight=1)

        # 2. Columns
        f2 = ttk.LabelFrame(left, text="2. Choose columns", padding=8)
        f2.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        ttk.Label(f2, text="Time column (seconds)").pack(anchor="w")
        self.tcol_cb = ttk.Combobox(f2, state="readonly")
        self.tcol_cb.pack(fill=tk.X)
        self.tcol_cb.bind("<<ComboboxSelected>>", lambda e: self.on_time_col())
        self.tinfo = ttk.Label(f2, text="", foreground="#666", wraplength=280)
        self.tinfo.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(f2, text="Data columns (Ctrl/Shift-click for several)").pack(anchor="w")
        lb_frame = ttk.Frame(f2); lb_frame.pack(fill=tk.BOTH, expand=True)
        self.data_lb = tk.Listbox(lb_frame, selectmode=tk.EXTENDED, exportselection=False, height=8)
        sb = ttk.Scrollbar(lb_frame, orient=tk.VERTICAL, command=self.data_lb.yview)
        self.data_lb.configure(yscrollcommand=sb.set)
        self.data_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.data_lb.bind("<<ListboxSelect>>", lambda e: self.refresh_plot_choices())
        btns = ttk.Frame(f2); btns.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btns, text="Select all numeric", command=self.select_numeric).pack(side=tk.LEFT)
        ttk.Button(btns, text="Clear", command=lambda: (self.data_lb.selection_clear(0, tk.END),
                                                        self.refresh_plot_choices())).pack(side=tk.LEFT, padx=4)

        # 3. Range
        f3 = ttk.LabelFrame(left, text="3. Set the time range (seconds)", padding=8)
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
        self.pop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f3, text="Population std. dev. (divide by n, not n−1)",
                        variable=self.pop_var, command=self._recalc_if_done).pack(anchor="w", pady=(6, 0))
        ttk.Button(f3, text="Calculate statistics", style="Accent.TButton",
                   command=self.calculate).pack(fill=tk.X, pady=(8, 0))

        # Right: chart
        chart_box = ttk.LabelFrame(right, text="Data over time", padding=6)
        chart_box.pack(fill=tk.BOTH, expand=True)
        top = ttk.Frame(chart_box); top.pack(fill=tk.X)
        ttk.Label(top, text="Plot:").pack(side=tk.LEFT)
        self.plot_cb = ttk.Combobox(top, state="readonly", width=30)
        self.plot_cb.pack(side=tk.LEFT, padx=4)
        self.plot_cb.bind("<<ComboboxSelected>>", lambda e: self.draw())
        ttk.Label(top, text="Drag on the chart to set the range.", foreground="#666").pack(side=tk.LEFT, padx=10)

        if HAS_MPL:
            self.fig = Figure(figsize=(7, 3.6), dpi=100)
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=chart_box)
            NavigationToolbar2Tk(self.canvas, chart_box, pack_toolbar=True).update()
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self._empty_chart("Load a file to see the chart.")
        else:
            ttk.Label(chart_box, text="Install matplotlib to see the chart:  pip install matplotlib",
                      foreground="#a33").pack(pady=40)

        # Right: results
        res_box = ttk.LabelFrame(right, text="Statistics for the selected range", padding=6)
        res_box.pack(fill=tk.BOTH, expand=False, pady=(8, 0))
        self.range_lbl = ttk.Label(res_box, text="Results appear here after you calculate.", foreground="#666")
        self.range_lbl.pack(anchor="w")
        tv_frame = ttk.Frame(res_box); tv_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        self.tree = ttk.Treeview(tv_frame, columns=self.RESULT_COLS, show="headings", height=6)
        for c in self.RESULT_COLS:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=150 if c == "Column" else 110,
                             anchor="w" if c == "Column" else "e", stretch=True)
        xs = ttk.Scrollbar(tv_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(xscrollcommand=xs.set)
        self.tree.pack(fill=tk.BOTH, expand=True); xs.pack(fill=tk.X)
        act = ttk.Frame(res_box); act.pack(fill=tk.X)
        ttk.Button(act, text="Export results…", command=self.export_results).pack(side=tk.LEFT)
        ttk.Button(act, text="Copy to clipboard", command=self.copy_results).pack(side=tk.LEFT, padx=6)
        ttk.Button(act, text="Export range data…", command=self.export_range_data).pack(side=tk.LEFT)

        self.status = ttk.Label(self, text="Open an Excel or CSV file to begin.", anchor="w", padding=(8, 2))
        self.status.pack(fill=tk.X, side=tk.BOTTOM)

    # ---------- loading ----------
    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open spreadsheet",
            filetypes=[("Spreadsheets and data files", "*.xlsx *.xlsm *.xls *.xlsb *.ods *.csv *.txt *.tsv *.dat"),
                       ("All files", "*.*")])
        if not path:
            return
        self.path = path
        try:
            sheets = read_any(path)
        except ReadError as e:
            messagebox.showerror("Couldn't read file", str(e))
            return
        except Exception as e:
            messagebox.showerror("Couldn't read file",
                                 f"{type(e).__name__}: {e}\n\nPaste this message to get help fixing it.")
            return
        sheets = {k: v for k, v in sheets.items() if not v.dropna(how="all").empty}
        if not sheets:
            messagebox.showerror("Couldn't read file", "The file opened, but every sheet in it is empty.")
            return
        self.sheets = sheets
        names = list(self.sheets.keys())
        self.file_lbl.config(text=path.replace("\\", "/").split("/")[-1])
        self.sheet_cb.config(values=names, state="readonly")
        self.sheet_cb.current(0)
        self.hdr_sb.config(state="normal")
        self.hdr_var.set(1)
        self.load_sheet()

    def load_sheet(self):
        self.raw = self.sheets[self.sheet_cb.get()]
        self.apply_header()

    def apply_header(self):
        if self.raw is None:
            return
        try:
            h = max(1, int(self.hdr_var.get())) - 1
        except (tk.TclError, ValueError):
            h = 0
        if h >= len(self.raw):
            messagebox.showwarning("Header row", "That header row is past the end of the sheet.")
            return
        hdr = self.raw.iloc[h].tolist()
        names, seen = [], {}
        for i, v in enumerate(hdr):
            n = f"Column {i + 1}" if (v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == "") \
                else str(v).strip()
            if n in seen:
                seen[n] += 1; n = f"{n} ({seen[n]})"
            else:
                seen[n] = 1
            names.append(n)
        df = self.raw.iloc[h + 1:].copy()
        df.columns = names
        df = df.dropna(how="all").reset_index(drop=True)
        self.df = df

        # guess time column: name hint first, then first mostly-numeric column
        guess = 0
        for i, n in enumerate(names):
            if any(k in n.lower() for k in ("time", "sec", "t (s)", "timestamp")):
                guess = i; break
        else:
            for i, n in enumerate(names):
                if pd.to_numeric(df[n], errors="coerce").notna().mean() > 0.5:
                    guess = i; break
        self.tcol_cb.config(values=names)
        self.tcol_cb.current(guess)
        self.on_time_col(auto_select=True)
        self.status.config(text=f"Loaded {len(df):,} rows × {len(names)} columns.")

    def _is_numeric(self, col):
        s = self.df[col]
        return s.notna().any() and pd.to_numeric(s, errors="coerce").notna().sum() >= 0.5 * s.notna().sum()

    def on_time_col(self, auto_select=False):
        tcol = self.tcol_cb.get()
        prev = set(self.selected_cols())
        self.time_s = to_seconds(self.df[tcol])
        ok = self.time_s.dropna()
        if ok.empty:
            self.tinfo.config(text="No numeric time values in this column. Pick the column with your time in seconds.")
            self.tmin = self.tmax = float("nan")
        else:
            self.tmin, self.tmax = float(ok.min()), float(ok.max())
            bad = len(self.time_s) - len(ok)
            self.tinfo.config(text=f"{len(ok):,} time points, {fmt(self.tmin)} s to {fmt(self.tmax)} s"
                                   + (f". {bad:,} rows without a time are ignored." if bad else "."))
            self.full_range(calc=False)

        self.data_cols = [c for c in self.df.columns if c != tcol]
        self.data_lb.delete(0, tk.END)
        for i, c in enumerate(self.data_cols):
            numeric = self._is_numeric(c)
            self.data_lb.insert(tk.END, c if numeric else f"{c}   (text)")
            if (auto_select and numeric) or (not auto_select and c in prev):
                self.data_lb.selection_set(i)
        self.refresh_plot_choices()

    def selected_cols(self):
        if not hasattr(self, "data_cols"):
            return []
        return [self.data_cols[i] for i in self.data_lb.curselection()]

    def select_numeric(self):
        self.data_lb.selection_clear(0, tk.END)
        for i, c in enumerate(self.data_cols):
            if self._is_numeric(c):
                self.data_lb.selection_set(i)
        self.refresh_plot_choices()

    def refresh_plot_choices(self):
        cols = self.selected_cols()
        cur = self.plot_cb.get()
        self.plot_cb.config(values=cols)
        if cols:
            self.plot_cb.set(cur if cur in cols else cols[0])
        else:
            self.plot_cb.set("")
        self.draw()

    # ---------- range ----------
    def full_range(self, calc=True):
        if self.time_s is None or math.isnan(getattr(self, "tmin", float("nan"))):
            return
        self.t0_var.set(f"{self.tmin:g}")
        self.t1_var.set(f"{self.tmax:g}")
        if calc:
            self.calculate()
        else:
            self.draw()

    def _get_range(self):
        try:
            t0 = float(self.t0_var.get()); t1 = float(self.t1_var.get())
        except ValueError:
            messagebox.showwarning("Time range", "Enter start and end times as numbers of seconds, e.g. 1 or 2.5.")
            return None
        if t1 < t0:
            messagebox.showwarning("Time range", "The end time is before the start time.")
            return None
        return t0, t1

    def _recalc_if_done(self):
        if self.result is not None:
            self.calculate()

    # ---------- stats ----------
    def calculate(self):
        if self.df is None:
            messagebox.showinfo("No data", "Open a spreadsheet first.")
            return
        cols = self.selected_cols()
        if not cols:
            messagebox.showwarning("No data columns", "Select at least one data column in step 2.")
            return
        rng = self._get_range()
        if rng is None:
            return
        t0, t1 = rng
        pop = self.pop_var.get()
        self.result = range_stats(self.time_s, self.df[cols], t0, t1, population=pop)
        self.result_meta = (t0, t1, pop)

        self.tree.delete(*self.tree.get_children())
        for _, r in self.result.iterrows():
            self.tree.insert("", tk.END, values=[
                r["Column"], fmt(int(r["Points"])),
                *[fmt(r[k]) for k in self.STAT_ORDER]])
        self.range_lbl.config(
            text=f"Range {t0:g} s to {t1:g} s  (duration {t1 - t0:g} s),  "
                 f"{'population' if pop else 'sample'} standard deviation", foreground="")
        skipped = int(self.result["Skipped (non-numeric)"].sum())
        msg = f"Calculated statistics for {len(cols)} column(s)."
        if skipped:
            msg += f" {skipped:,} non-numeric cells in range were skipped."
        if (self.result["Points"] == 0).all():
            msg = "No numeric values fall inside this range. Widen the range or check the time column."
        self.status.config(text=msg)
        self.draw()

    # ---------- export ----------
    def _results_table(self):
        if self.result is None:
            messagebox.showinfo("Nothing to export", "Calculate statistics first.")
            return None
        t0, t1, pop = self.result_meta
        out = self.result[["Column", "Points"] + self.STAT_ORDER].copy()
        out["Range start (s)"] = t0
        out["Range end (s)"] = t1
        out["Std. dev. type"] = "population" if pop else "sample"
        return out

    def export_results(self):
        out = self._results_table()
        if out is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export results", defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx"), ("CSV", "*.csv")],
            initialfile="range_statistics.xlsx")
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                out.to_csv(path, index=False)
            else:
                out.to_excel(path, index=False, sheet_name="Statistics")
            self.status.config(text=f"Exported results to {path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def export_range_data(self):
        if self.df is None:
            return
        rng = self._get_range()
        if rng is None:
            return
        t0, t1 = rng
        mask = (self.time_s >= t0) & (self.time_s <= t1)
        cols = [self.tcol_cb.get()] + self.selected_cols()
        sub = self.df.loc[mask, cols]
        path = filedialog.asksaveasfilename(
            title="Export rows in range", defaultextension=".xlsx",
            filetypes=[("Excel workbook", "*.xlsx"), ("CSV", "*.csv")],
            initialfile=f"data_{t0:g}s_to_{t1:g}s.xlsx")
        if not path:
            return
        try:
            sub.to_csv(path, index=False) if path.lower().endswith(".csv") else sub.to_excel(path, index=False)
            self.status.config(text=f"Exported {len(sub):,} rows to {path}")
        except Exception as e:
            messagebox.showerror("Export failed", str(e))

    def copy_results(self):
        if self.result is None:
            messagebox.showinfo("Nothing to copy", "Calculate statistics first.")
            return
        out = self.result[["Column"] + self.STAT_ORDER]
        self.clipboard_clear()
        self.clipboard_append(out.to_csv(sep="\t", index=False, lineterminator="\n").rstrip("\n"))
        self.status.config(text="Results copied. Paste into Excel.")

    # ---------- chart ----------
    def _empty_chart(self, msg):
        self.ax.clear()
        self.ax.text(0.5, 0.5, msg, ha="center", va="center", color="#777", transform=self.ax.transAxes)
        self.ax.set_xticks([]); self.ax.set_yticks([])
        self.canvas.draw_idle()

    def draw(self):
        if not HAS_MPL:
            return
        col = self.plot_cb.get()
        if self.df is None or not col or self.time_s is None:
            self._empty_chart("Load a file and choose a data column to see the chart.")
            return
        y = pd.to_numeric(self.df[col], errors="coerce")
        d = pd.DataFrame({"t": self.time_s, "y": y}).dropna().sort_values("t")
        if d.empty:
            self._empty_chart("This column has no numeric values to plot.")
            return
        ax = self.ax
        ax.clear()
        ax.plot(d["t"].values, d["y"].values, lw=1.2, color="#0E6F7E")
        ax.set_xlabel("Time (s)"); ax.set_ylabel(col)
        ax.grid(True, alpha=0.3)
        try:
            t0, t1 = float(self.t0_var.get()), float(self.t1_var.get())
            ax.axvspan(min(t0, t1), max(t0, t1), color="#E0A100", alpha=0.18)
            ax.axvline(t0, color="#C48C00", lw=1); ax.axvline(t1, color="#C48C00", lw=1)
        except ValueError:
            pass
        self.fig.tight_layout()
        self.span = SpanSelector(ax, self._on_span, "horizontal", useblit=True,
                                 props=dict(alpha=0.2, facecolor="#555"), interactive=False)
        self.canvas.draw_idle()

    def _on_span(self, xmin, xmax):
        if xmax - xmin <= 0:
            return
        self.t0_var.set(f"{xmin:.6g}")
        self.t1_var.set(f"{xmax:.6g}")
        self.after(10, self.calculate)


if __name__ == "__main__":
    App().mainloop()
