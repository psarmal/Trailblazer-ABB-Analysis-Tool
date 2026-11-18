"""Chart toolbar: drag modes (select window / zoom x / zoom y / cursors), view history, save.

Standalone: knows nothing about the analyzer. The app supplies callbacks and calls attach()
after every redraw, because ax.clear() throws away the drag selector.
"""
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
from matplotlib.widgets import SpanSelector

MODES = ("select", "zoom_x", "zoom_y", "cursors")
MODE_LABELS = (("select", "Select window"), ("zoom_x", "Zoom \u2194"), ("zoom_y", "Zoom \u2195"),
               ("cursors", "Cursors"))
KEYS = {"w": "select", "x": "zoom_x", "y": "zoom_y", "c": "cursors"}
HISTORY_MAX = 50
MIN_DRAG_PX = 3
ACCENT = "#0f766e"       # same as the app accent; passed to the selector and the active-mode button
CARD = "#ffffff"
MODE_STYLE = "Mode.Toolbutton"
GRAB_PX = 6                # cursor grab distance
DASH = "\u2013"
COPY_HEADER = "Trace\tt_A (s)\tV_A (mV)\tt_B (s)\tV_B (mV)\t\u0394V (mV)\t\u0394t (s)\t1/\u0394t (Hz)"
CURSOR_COLORS = {"A": "#c2410c", "B": "#1d4ed8"}


def format_dt(dt):
    """'\u0394t 7.038 ms (1/\u0394t = 142.1 Hz)'; ms below 1 s else s; kHz above 10 kHz."""
    a = abs(dt)
    txt = f"\u0394t {dt * 1e3:.3f} ms" if a < 1 else f"\u0394t {dt:.6f} s"
    if a == 0:
        return f"{txt} (1/\u0394t —)"  # em dash, as in the spec
    f = 1.0 / a
    return f"{txt} (1/\u0394t = {f / 1e3:.3f} kHz)" if f > 10_000 else f"{txt} (1/\u0394t = {f:.1f} Hz)"


def sample_at(data, t):
    """Value of the sample nearest to time t, or None when t is outside the trace's time range."""
    if data.n == 0 or not (data.t_start <= t <= data.t_end):
        return None
    i = data.index_range(t, data.t_end)[0]
    cand = np.array(sorted({min(max(i - 1, 0), data.n - 1), min(i, data.n - 1)}))
    j = int(cand[int(np.argmin(np.abs(data.times_at(cand) - t)))])
    return float(data.values_at(np.array([j]))[0])


class ChartTools:
    def __init__(self, parent, ax, canvas, *, on_window_selected, on_reset, autoscale_y,
                 get_selected_trace=lambda: None, get_shown_traces=lambda: [],
                 label_of=lambda f: f.get("name", ""), on_mode_changed=None):
        self._on_mode_changed = on_mode_changed
        self.ax = ax
        self.canvas = canvas
        self._on_window_selected = on_window_selected
        self._on_reset = on_reset
        self._autoscale_y = autoscale_y
        self._mode = "select"
        self._history = []              # previous (xlim, ylim) views, oldest first
        self._selector = None           # kept referenced so it is not garbage-collected
        self._get_selected = get_selected_trace
        self._get_shown = get_shown_traces
        self._label_of = label_of
        self.cursor_times = None        # (tA, tB) once the cursors have been placed
        self._cursor_lines = []
        self._dragging = None           # "A" / "B" while a cursor is being dragged
        self._bg = None                 # cached background while dragging (blitting)

        self.frame = ttk.Frame(parent, style="Card.TFrame")
        self._mode_var = tk.StringVar(master=parent, value=self._mode)
        st = ttk.Style(master=parent)
        st.configure(MODE_STYLE, background=CARD, padding=(8, 3))
        st.map(MODE_STYLE, background=[("selected", ACCENT), ("active", "#e6f2f1")],
               foreground=[("selected", "#ffffff")])
        self.mode_buttons = {}
        for mode, text in MODE_LABELS:
            b = ttk.Radiobutton(self.frame, text=text, value=mode, variable=self._mode_var,
                                style=MODE_STYLE, command=lambda m=mode: self.set_mode(m))
            b.pack(side=tk.LEFT, padx=(0, 4))
            self.mode_buttons[mode] = b
        self.back_button = ttk.Button(self.frame, text="Back", command=self.back)
        self.back_button.pack(side=tk.LEFT, padx=(8, 4))
        self._update_back()
        ttk.Button(self.frame, text="Reset zoom", command=self.reset).pack(side=tk.LEFT, padx=4)
        ttk.Button(self.frame, text="Save image\u2026", command=self.save_image).pack(side=tk.LEFT, padx=4)

        w = canvas.get_tk_widget()
        w.bind("<Button-1>", lambda e: w.focus_set(), add="+")
        for key, mode in KEYS.items():
            for k in (key, key.upper()):
                w.bind(f"<KeyPress-{k}>", lambda e, m=mode: self.set_mode(m))
        w.bind("<BackSpace>", lambda e: self.back())
        w.bind("<Home>", lambda e: self.reset())

        self.readout_frame = ttk.Frame(parent, style="Card.TFrame")
        self._readout_var = tk.StringVar(master=parent, value="")
        ttk.Label(self.readout_frame, textvariable=self._readout_var, style="Muted.Card.TLabel"
                  ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.copy_button = ttk.Button(self.readout_frame, text="Copy cursor values",
                                      command=self.copy_cursor_values)
        self.copy_button.pack(side=tk.RIGHT, padx=(8, 0))
        for name, handler in (("draw_event", self._on_draw), ("button_press_event", self._on_press),
                              ("motion_notify_event", self._on_motion),
                              ("button_release_event", self._on_release)):
            canvas.mpl_connect(name, handler)
        self.attach()

    # ---- state ----
    @property
    def mode(self):
        return self._mode

    @property
    def can_go_back(self):
        return bool(self._history)

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self._mode = mode
        self._mode_var.set(mode)
        self.attach()
        self.canvas.draw_idle()
        if self._on_mode_changed:
            self._on_mode_changed(mode)

    # ---- drag selector ----
    def attach(self):
        """(Re)create the drag selector on the current axes; call after every redraw."""
        if self._selector is not None:
            self._selector.disconnect_events()
        self._selector = None
        self._make_selector()
        self._make_cursor_lines()
        self.refresh_readout()

    def _make_selector(self):
        mode = self._mode
        if mode == "cursors":           # cursors have their own press/motion/release handlers
            self._selector = None
            return
        direction = "vertical" if mode == "zoom_y" else "horizontal"
        self._selector = SpanSelector(self.ax, self._on_drag, direction, useblit=True,
                                      props=dict(alpha=0.2, facecolor=ACCENT), interactive=False)

    def detach(self):
        """Drop the drag selector and the history (the axes no longer show a trace view)."""
        if self._selector is not None:
            self._selector.disconnect_events()
        self._selector = None
        self._cursor_lines = []
        self._dragging = None
        self._bg = None
        self.clear_history()
        self.refresh_readout()

    def _on_drag(self, lo, hi):
        mode = self._mode
        axis = 1 if mode == "zoom_y" else 0
        p0 = self.ax.transData.transform((lo, lo) if axis else (lo, 0))[axis]
        p1 = self.ax.transData.transform((hi, hi) if axis else (hi, 0))[axis]
        if abs(p1 - p0) < MIN_DRAG_PX or hi <= lo:
            return
        if mode == "select":
            self._on_window_selected(lo, hi)
        elif mode == "zoom_x":
            self.zoom_x(lo, hi)
        elif mode == "zoom_y":
            self.zoom_y(lo, hi)

    # ---- measurement cursors ----
    def _default_times(self):
        lo, hi = self.ax.get_xlim()
        return lo + (hi - lo) / 3, lo + 2 * (hi - lo) / 3

    def _make_cursor_lines(self):
        """(Re)create the A/B lines; ax.clear() removed the old ones. Only visible in cursors mode."""
        for ln in self._cursor_lines:       # ax.clear() may already have dropped them
            try:
                ln.remove()
            except (ValueError, NotImplementedError, AttributeError):
                pass
        self._cursor_lines = []
        if self._mode != "cursors":
            return
        if self.cursor_times is None:
            self.cursor_times = self._default_times()
        for which, t in zip("AB", self.cursor_times):      # animated: drawn by blit, never by a render
            self._cursor_lines.append(self.ax.axvline(t, color=CURSOR_COLORS[which], lw=1.3, zorder=4,
                                                      animated=True))

    def _on_draw(self, _ev=None):
        """After every full render: cache the background (without cursors), then blit the cursors."""
        if not self._cursor_lines:
            self._bg = None
            return
        try:
            self._bg = self.canvas.copy_from_bbox(self.ax.bbox)
            self._blit_cursors(restore=False)
        except Exception:
            self._bg = None

    def _blit_cursors(self, restore=True):
        if restore:
            self.canvas.restore_region(self._bg)
        for ln in self._cursor_lines:
            self.ax.draw_artist(ln)
        self.canvas.blit(self.ax.bbox)

    def set_cursor(self, which, t, _blit=False):
        if which not in ("A", "B"):
            raise ValueError(f"unknown cursor {which!r}")
        ta, tb = self.cursor_times or self._default_times()
        self.cursor_times = (float(t), tb) if which == "A" else (ta, float(t))
        if self._cursor_lines:
            line = self._cursor_lines["AB".index(which)]
            line.set_xdata([float(t), float(t)])
            if _blit and self._bg is not None:
                self._blit_cursors()
            else:
                self.canvas.draw_idle()
        self.refresh_readout()

    def _on_press(self, ev):
        self._dragging = None
        if self._mode != "cursors" or ev.button != 1 or not self._cursor_lines or ev.x is None:
            return
        if not self.ax.bbox.contains(ev.x, ev.y):
            return
        best = None
        for which, t in zip("AB", self.cursor_times):
            d = abs(self.ax.transData.transform((t, 0))[0] - ev.x)
            if d <= GRAB_PX and (best is None or d < best[0]):
                best = (d, which)
        self._dragging = best[1] if best else None     # no render: the cached background is reused

    def _on_motion(self, ev):
        if self._dragging is None or self._mode != "cursors" or ev.x is None:
            return
        self.set_cursor(self._dragging, self.ax.transData.inverted().transform((ev.x, ev.y))[0], _blit=True)

    def _on_release(self, ev):
        self._dragging = None

    def _readout_trace(self):
        shown = self._get_shown()
        if not shown:
            return None
        sel = self._get_selected()
        return sel if sel is not None and any(sel is f for f in shown) else shown[0]

    def _measure(self, f):
        """(tA, vA, tB, vB) with None for a value outside the trace's time range."""
        ta, tb = self.cursor_times
        return ta, sample_at(f["data"], ta), tb, sample_at(f["data"], tb)

    def readout_text(self):
        f = self._readout_trace()
        if f is None or self.cursor_times is None:
            return ""
        ta, va, tb, vb = self._measure(f)
        fmt = lambda v: DASH if v is None else f"{v:.4f}"
        dv = DASH if va is None or vb is None else f"{vb - va:.4f}"
        return (f"{self._label_of(f)}   A: {ta:.6f} s, {fmt(va)} mV | B: {tb:.6f} s, {fmt(vb)} mV | "
                f"{format_dt(tb - ta)} | \u0394V {dv} mV")

    def refresh_readout(self):
        on = self._mode == "cursors"
        self._readout_var.set(self.readout_text() if on else "")
        self.copy_button.state(["!disabled" if on else "disabled"])

    def cursor_table(self):
        shown = self._get_shown()
        if not shown or self.cursor_times is None:
            return ""
        rows = [COPY_HEADER]
        num = lambda v: "" if v is None else f"{v:.4f}"
        for f in shown:
            ta, va, tb, vb = self._measure(f)
            dt = tb - ta
            dv = "" if va is None or vb is None else f"{vb - va:.4f}"
            hz = "" if dt == 0 else f"{1 / abs(dt):.1f}"
            rows.append("\t".join([self._label_of(f), f"{ta:.6f}", num(va), f"{tb:.6f}", num(vb),
                                   dv, f"{dt:.6f}", hz]))
        return "\n".join(rows)

    def copy_cursor_values(self):
        text = self.cursor_table()
        if text:
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)

    # ---- view changes ----
    def _push(self):
        self._history.append((tuple(self.ax.get_xlim()), tuple(self.ax.get_ylim())))
        del self._history[:-HISTORY_MAX]
        self._update_back()

    def _update_back(self):
        self.back_button.state(["!disabled" if self._history else "disabled"])

    def zoom_x(self, x0, x1):
        """Zoom the x axis to [x0, x1] and fit y to the points drawn there (via autoscale_y)."""
        if x1 <= x0:
            return
        self._push()
        self.ax.set_xlim(x0, x1)
        fit = self._autoscale_y(x0, x1)
        if fit is not None and fit[1] > fit[0]:
            self.ax.set_ylim(*fit)
        self.canvas.draw_idle()

    def zoom_y(self, y0, y1):
        if y1 <= y0:
            return
        self._push()
        self.ax.set_ylim(y0, y1)
        self.canvas.draw_idle()

    def back(self):
        if not self._history:
            return
        xl, yl = self._history.pop()
        self.ax.set_xlim(xl)
        self.ax.set_ylim(yl)
        self._update_back()
        self.canvas.draw_idle()

    def clear_history(self):
        self._history.clear()
        self._update_back()

    def reset(self):
        self.clear_history()
        self._on_reset()

    def save_image(self):
        path = filedialog.asksaveasfilename(
            parent=self.frame, title="Save chart image", defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")])
        if path:
            for ln in self._cursor_lines:       # animated artists are skipped by savefig
                ln.set_animated(False)
            try:
                self.ax.figure.savefig(path, dpi=150, facecolor=self.ax.figure.get_facecolor())
            finally:
                for ln in self._cursor_lines:
                    ln.set_animated(True)
